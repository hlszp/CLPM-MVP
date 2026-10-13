"""KPI capacity benchmark（R05/R06 返修版：预检编排 + 冻结 + 绑定核验）.

两种互斥模式（03 号文 §8 P5-05 / P0-03 v2 §3；2026-10-13 17 号文 R05/R06 返修）：

1. ``--mode standard-hourly``（唯一计入标准小时容量 SLO 的模式）
   经既有 Celery 注册任务 ``app.tasks.kpi_calc.calculate_hourly_kpi`` 投递
   冻结窗口（``.delay(ts_start=...)``，复用唯一 lifespan Worker，不新增开放
   触发 API）。本返修把通过条件从"数投影行数"升级为完整业务正确性核验：

   **R05 投递前统一预检（latest 单调规则编排守卫）**：
   - `_preflight_latest_conflict`：冻结集内任一回路的 ``loop_confidence_latest.
     data_ts_end`` 晚于被测窗终点（DEC-09 迟到窗不覆盖 latest）→ 拒绝投递并
     列明冲突回路与其当前 latest 窗（屏障条件在该窗结构性不可满足）；
   - ``--plan-windows``：多窗编排级预检（纯函数 `_check_plan_windows`）——
     时序必须**严格单调升序**，且每个被测窗对当前 latest 状态无冲突；
     升序 + 首窗无冲突 ⇒ 每个后续新窗自然可更新 latest（连续多窗闭合的
     结构性证明）。降序/乱序/迟到冲突一律拒绝；
   - 迟到规则与投影屏障不放宽（不改 kpi_calc，禁止为跑分删 latest）。

   **R06 投递前冻结 + 结果绑定核验**：
   - 冻结：eligible/excluded 清单（三条件参评口径逐回路 + 不可参评原因）+
     预定节点集（结构推导：is_kpi_enabled 祖先 × 子树含冻结集回路，**不依赖
     运行中快照**）+ 独立 manifest（``--manifest``，scenario 数据文件）逐回路
     expectedStatus + 算法版本（import 时读 ``app.tasks.kpi_calc.ALGORITHM_
     VERSION``，只读不改动）；冻结件在投递前落盘 ``--freeze-dir``；
   - 绑定：本批父任务 ID（= logicalRunId，Celery 任务重试同 ID）逐行核验
     ``kpi_snapshot_hourly.result_record_id → calculation_result_record.
     logical_run_id``；算法版本逐行核验；latest 投影行同样绑定 run+版本；
   - 核验：不可变结果（calculation_result_record 按冻结集）+ 小时账本行数 +
     manifest expectedStatus 逐回路终态（EVALUATED→SUCCESS+score 非空；
     INCONCLUSIVE(DATA_INSUFFICIENT)→INCONCLUSIVE+fitness_tags 含
     DATA_INSUFFICIENT）+ latest 投影 + 预定节点行（含版本）+ 任务终态
     （TaskRecord=SUCCESS + Celery 父任务 SUCCESS——父任务投递完≠完成）；
   - 反例防伪：旧结果污染（同窗行绑定到其他 logicalRunId）/错版本/全部
     INCONCLUSIVE（正常组不许）/缺节点/pending/missing/父子任务未终结——
     均不得 PASS（单测逐一断言）；
   - SLO 判定保留，输出完整分母（成功/失败/跳过计数，不缩分母）；
   - 节点子任务 ID：轮询 ``celery inspect active`` 尽力取证（not-observed
     如实登记）——kpi_calc 级联节点任务不带父 run 标识（2B 域不改动），
     子任务完成以节点结果行核验为准，取证仅作辅助记录。

2. ``--mode custom``（loop-evals 吞吐回放，独立单列，**不计 600s 标准 SLO**）
   走 POST /tasks/custom/evaluate；少量回路多窗口重放仅在显式 ``--allow-replay``
   下放行，报告必须标注 unique_loops / loop_evals，不得表述为标准小时容量。

Usage::

    cd backend && uv run python scripts/kpi_capacity_benchmark.py \
        --mode standard-hourly --ts-start 2026-10-13T02:00:00 \
        --manifest capacity_dataset/manifest_a_2026101302.json \
        --expect-eligible 1000 --barrier-loop-prefix PERF- --dry-run

    # 多窗编排预检（投递前一次性校验十批计划）
    uv run python scripts/kpi_capacity_benchmark.py --mode standard-hourly \
        --ts-start 2026-10-13T02:00:00 --manifest ... --dry-run \
        --plan-windows 2026-10-13T02:00,2026-10-13T03:00,2026-10-13T04:00

    cd backend && uv run python scripts/kpi_capacity_benchmark.py --mode custom --loops 1000
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_BASE_URL = "http://localhost:17101/api/v1"  # MVP 隔离端口（原 7101 是另一项目）
DEFAULT_TIMEOUT_SECONDS = 3600.0
DEFAULT_SLO_SECONDS = 600.0
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_PAGE_SIZE = 100  # API /loops pageSize le=100
DEFAULT_RETRY_SECONDS = 60.0
DEFAULT_BATCH_SPACING_SECONDS = 2.0
DEFAULT_FREEZE_DIR = Path("capacity_dataset/run_freezes")

#: 屏障允许的快照来源（G5 前置立卡项1 修复后语义）：API 手动触发（带任务
#: 记录）=MANUAL_STANDARD；无记录触发（Beat 整点与 .delay 直投在 Celery
#: 入口不可区分）=SCHEDULED——.delay 直投自建系统记录后快照行
#: source=SCHEDULED + source_task_id=系统记录 ID（两值均在允许集内）。
_ALLOWED_SOURCES = ("SCHEDULED", "MANUAL_STANDARD")

#: 冻结件格式版本（结构变更时递增，读端按版本拒绝不认识的格式）
FREEZE_FORMAT_VERSION = 2

_VIOLATION_SAMPLE_LIMIT = 20  # 报告中逐项违约明细的打印上限（全量计数不截断）


def _percentile(values: list[float], percentile: float) -> float:
    """Return nearest-rank percentile for a non-empty sample."""
    ordered = sorted(values)
    index = max(0, int(len(ordered) * percentile + 0.999999) - 1)
    return ordered[index]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KPI 容量基准（standard-hourly / custom 单列）")
    parser.add_argument(
        "--mode",
        choices=("standard-hourly", "custom"),
        default="standard-hourly",
        help="standard-hourly=经 Celery 入口的标准小时容量（唯一计 SLO）；"
        "custom=loop-evals 回放单列",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--loops", type=int, default=1000, help="custom 模式目标 loop-evals 数")
    parser.add_argument(
        "--ts-start",
        default=None,
        help="standard-hourly 冻结窗口起点（ISO 8601，naive UTC，整点）；缺省=上一个完整小时",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="standard-hourly 独立 manifest（generate_capacity_dataset 产物；"
        "冻结 eligible/expectedStatus 的对照正本，缺失即拒绝执行）",
    )
    parser.add_argument(
        "--plan-windows",
        default=None,
        help="多窗编排预检：逗号分隔的全部被测窗（ISO 8601 整点）。"
        "校验单调升序 + 每窗对当前 latest 无迟到冲突（R05）",
    )
    parser.add_argument(
        "--freeze-dir",
        default=str(DEFAULT_FREEZE_DIR),
        help="冻结件落盘目录（投递前写入；dry-run 不落盘）",
    )
    parser.add_argument(
        "--report-out",
        default=None,
        help="可选：运行报告 JSON 落盘路径（含屏障条件/分母/绑定核验明细）",
    )
    parser.add_argument(
        "--expect-eligible",
        type=int,
        default=1000,
        help="standard-hourly 前置断言：三条件参评回路数（is_active+READY+include_in_evaluation）",
    )
    parser.add_argument(
        "--barrier-loop-prefix",
        default=None,
        help="屏障核算限定回路位号前缀（如 PERF-，混合 dev 环境下只对生成集对账）",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--slo-seconds", type=float, default=DEFAULT_SLO_SECONDS)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--retry-seconds", type=float, default=DEFAULT_RETRY_SECONDS)
    parser.add_argument("--batch-spacing", type=float, default=DEFAULT_BATCH_SPACING_SECONDS)
    parser.add_argument(
        "--allow-replay",
        action="store_true",
        help="custom 模式：可用回路数 < 目标时允许少 ID 多窗口重放（报告显式标注 loop-evals）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做前置检查与计划打印，不投递任何任务/不产生任何写入",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# HTTP helpers（custom 模式 + preflight 交叉核对）
# ---------------------------------------------------------------------------


def _post_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    json: dict,
    retry_seconds: float,
) -> httpx.Response:
    """POST with 429 backoff handling."""
    deadline = time.perf_counter() + retry_seconds
    while True:
        response = client.post(url, headers=headers, json=json)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if time.perf_counter() >= deadline:
            response.raise_for_status()
        sleep_for = min(5.0, max(0.5, float(response.headers.get("Retry-After", "1") or 1)))
        time.sleep(sleep_for)


def _get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    retry_seconds: float,
) -> httpx.Response:
    """GET with 429 backoff handling."""
    deadline = time.perf_counter() + retry_seconds
    while True:
        response = client.get(url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if time.perf_counter() >= deadline:
            response.raise_for_status()
        sleep_for = min(5.0, max(0.5, float(response.headers.get("Retry-After", "1") or 1)))
        time.sleep(sleep_for)


def _load_loops(
    client: httpx.Client, base_url: str, headers: dict[str, str], page_size: int
) -> list[str]:
    """Fetch all loop IDs via pagination.

    P5 批 α 修复：参数名 ``pageSize``（原 ``size`` 被 API 静默忽略回退默认 20，
    且 ``len(items)<page_size`` 早停——实际只拉到约 20 个回路）；单页上限
    le=100，逐页拉全量并断言非空页与去重计数。
    """
    page_size = max(1, min(page_size, 100))  # API le=100 硬上限
    loop_ids: list[str] = []
    seen: set[str] = set()
    page = 1
    while True:
        resp = client.get(
            f"{base_url}/loops",
            params={"page": page, "pageSize": page_size},
            headers=headers,
        )
        resp.raise_for_status()
        payload = resp.json().get("data", {})
        items = payload.get("items", []) or []
        if not items:
            break
        for item in items:
            lid = str(item["loopId"])
            if lid not in seen:
                seen.add(lid)
                loop_ids.append(lid)
        total = payload.get("total")
        if total is not None and len(loop_ids) >= int(total):
            break
        if len(items) < page_size:
            break
        page += 1
    return loop_ids


def _read_task_loops_done(task: dict) -> int:
    """Read the completed loop counter from the API payload."""
    return int(task.get("loopsDone") or task.get("loops_done") or 0)


# ---------------------------------------------------------------------------
# standard-hourly 模式（Celery 入口 + 投递前冻结 + DB 完成屏障 + 绑定核验）
# ---------------------------------------------------------------------------


def _frozen_window(ts_start_arg: str | None) -> datetime:
    """冻结窗口起点（naive UTC 整点）。"""
    if ts_start_arg:
        raw = ts_start_arg.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        dt = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if dt.minute or dt.second or dt.microsecond:
        raise SystemExit(f"--ts-start 必须为整点小时（收到 {dt.isoformat()}）")
    return dt


def _parse_plan_window(raw: str) -> datetime:
    """计划窗条目解析（复用整点校验口径）。"""
    return _frozen_window(raw)


def _load_manifest(path_str: str) -> dict:
    """加载独立 manifest（generate_capacity_dataset 产物）并做结构校验。"""
    path = Path(path_str)
    if not path.is_file():
        raise SystemExit(
            f"[preflight] manifest 不存在：{path}（standard-hourly 必须提供独立 manifest）"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[preflight] manifest 不是合法 JSON：{path}（{exc}）") from exc
    rows = manifest.get("rows") or []
    required = {"loopTag", "composition", "expectedStatus"}
    for i, row in enumerate(rows):
        missing = required - set(row)
        if missing:
            raise SystemExit(f"[preflight] manifest 第 {i} 行缺字段 {sorted(missing)}（{path}）")
    if not rows:
        raise SystemExit(f"[preflight] manifest rows 为空：{path}")
    return manifest


def _manifest_sha256(path_str: str) -> str:
    return hashlib.sha256(Path(path_str).read_bytes()).hexdigest()


def _expected_snapshot_status(expected_status: str) -> str:
    """manifest expectedStatus → kpi_snapshot_hourly.status 预期。

    独立口径（04 §4.1 场景定义）：EVALUATED*→SUCCESS（完整有效评分）；
    INCONCLUSIVE*→INCONCLUSIVE（数据不足是明确终态而非漏写）。
    """
    if expected_status.startswith("EVALUATED"):
        return "SUCCESS"
    if expected_status.startswith("INCONCLUSIVE"):
        return "INCONCLUSIVE"
    raise SystemExit(f"[preflight] manifest expectedStatus 语义未知：{expected_status!r}")


def _current_algorithm_version() -> str:
    """投递时点的算法版本（只读 import，不改动 kpi_calc）。"""
    from app.tasks.kpi_calc import ALGORITHM_VERSION

    return str(ALGORITHM_VERSION)


async def _preflight_loop_universe_full(prefix: str) -> list[dict]:
    """前缀范围内全部回路（含三条件原始值，供 eligible/excluded 冻结）。

    Args:
        prefix: 回路位号前缀（standard-hourly 必填，构成冻结测试身份）。

    Returns:
        [{"loopId", "loopTag", "isActive", "ledgerStatus", "includeInEvaluation"}]
    """
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger

    async with AsyncSessionLocal() as db:
        stmt = select(
            LoopLedger.id,
            LoopLedger.tag_name,
            LoopLedger.is_active,
            LoopLedger.status,
            LoopLedger.include_in_evaluation,
        ).where(LoopLedger.tag_name.like(f"{prefix}%"))
        rows = (await db.execute(stmt)).all()
    return [
        {
            "loopId": str(r[0]),
            "loopTag": str(r[1]),
            "isActive": bool(r[2]),
            "ledgerStatus": str(r[3]),
            "includeInEvaluation": bool(r[4]),
        }
        for r in rows
    ]


def _partition_universe(universe: list[dict]) -> tuple[list[dict], list[dict]]:
    """按三条件切分 eligible / excluded（含逐回路不可参评原因）。"""
    eligible: list[dict] = []
    excluded: list[dict] = []
    for row in universe:
        reasons = []
        if not row["isActive"]:
            reasons.append("is_active=false")
        if row["ledgerStatus"] != "READY":
            reasons.append(f"status={row['ledgerStatus']}")
        if not row["includeInEvaluation"]:
            reasons.append("include_in_evaluation=false")
        if reasons:
            excluded.append(
                {"loopId": row["loopId"], "loopTag": row["loopTag"], "reasons": reasons}
            )
        else:
            eligible.append(row)
    return eligible, excluded


async def _preflight_latest_conflict(ts_end: datetime, eligible_ids: list[str]) -> list[dict]:
    """R05 预检：冻结集内既有 latest 已晚于被测窗终点的回路（迟到冲突）。

    DEC-09（kpi_calc._save_confidence_latest）：incoming ts_end < 现存
    data_ts_end 时不更新 latest——冲突回路在该窗的 latest 屏障结构性不可满足。
    """
    from sqlalchemy import text

    from app.core.db import AsyncSessionLocal

    if not eligible_ids:
        return []
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT loop_id, data_ts_end FROM loop_confidence_latest "
                    "WHERE loop_id = ANY(:ids) AND data_ts_end > :ts_end"
                ),
                {"ids": eligible_ids, "ts_end": ts_end},
            )
        ).all()
    return [{"loopId": str(r[0]), "latestDataTsEnd": r[1].isoformat()} for r in rows]


async def _latest_ends(eligible_ids: list[str]) -> dict[str, str]:
    """冻结集每回路当前 latest 业务窗终点（ISO；无投影的回路缺省）。

    供 ``--plan-windows`` 编排级预检：effective 现状基线。
    """
    from sqlalchemy import text

    from app.core.db import AsyncSessionLocal

    if not eligible_ids:
        return {}
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT loop_id, data_ts_end FROM loop_confidence_latest "
                    "WHERE loop_id = ANY(:ids)"
                ),
                {"ids": eligible_ids},
            )
        ).all()
    return {str(r[0]): r[1].isoformat() for r in rows}


def _check_plan_windows(
    plan: list[datetime], latest_ends_iso: dict[str, str], *, sample_limit: int = 5
) -> dict:
    """R05 多窗编排预检（纯函数，latest 语义级）。

    规则（与 DEC-09 迟到规则一致，不放宽）：
    1. 计划必须**严格单调升序**（旧窗先跑、新窗后跑——每个新窗的
       incoming ts_end 大于既有 latest 终点，自然可更新 latest）；
    2. 每个被测窗不得与当前 latest 状态冲突（现存 data_ts_end 晚于该窗
       终点的回路 ⇒ 该窗 latest 屏障结构性不可满足）。升序下先跑的窗只会
       把 latest 推进到自己的终点（小于后续窗），因此只需对**现状基线**
       逐窗校验；
    3. 结构性闭合证明：升序 + 首窗无冲突 ⇒ 窗 i 闭合后 latest=窗 i 终点
       < 窗 i+1 终点 ⇒ 窗 i+1 无冲突可闭合（连续两窗即证）。

    Returns:
        {"ok": bool, "problems": [str], "windows": [...], "conflictByWindow": {...}}
    """
    problems: list[str] = []
    conflict_by_window: dict[str, list[dict]] = {}
    if len(plan) < 2:
        problems.append("计划至少需要两个窗口才能构成跨窗闭合证明（收到 <2 个）")
    for i in range(1, len(plan)):
        if plan[i] <= plan[i - 1]:
            problems.append(
                f"计划窗口必须严格单调升序（第 {i} 窗 {plan[i].isoformat()} "
                f"不晚于第 {i - 1} 窗 {plan[i - 1].isoformat()}——先新后旧会使后续"
                "旧窗成为迟到窗，latest 屏障永不满足）"
            )
    for window in plan:
        ts_end = window + timedelta(hours=1)
        conflicts = [
            {"loopId": loop_id, "latestDataTsEnd": end_iso}
            for loop_id, end_iso in latest_ends_iso.items()
            if datetime.fromisoformat(end_iso) > ts_end
        ]
        if conflicts:
            conflict_by_window[window.isoformat()] = conflicts[:sample_limit]
            problems.append(
                f"窗 {window.isoformat()} 存在迟到冲突回路 {len(conflicts)} 个"
                f"（既有 latest 晚于该窗终点，如 {conflicts[0]}）"
            )
    return {
        "ok": not problems,
        "problems": problems,
        "windows": [w.isoformat() for w in plan],
        "conflictByWindow": conflict_by_window,
    }


#: R06 预定节点：结构推导（is_kpi_enabled 祖先 × 子树含冻结集回路）。
#: 不 join 运行中快照——冻结发生在投递前，不能从已有结果反推预期。
_PREDICTED_NODES_STRUCTURAL_SQL = """
WITH RECURSIVE node_tree(root_id, descendant_id) AS (
    SELECT id, id FROM plant_node
    UNION ALL
    SELECT nt.root_id, c.id FROM plant_node c JOIN node_tree nt ON c.parent_id = nt.descendant_id
)
SELECT DISTINCT n.id
FROM plant_node n
JOIN node_tree nt ON nt.root_id = n.id
JOIN loop_ledger l ON l.unit_id = nt.descendant_id
WHERE n.is_kpi_enabled = true AND l.id = ANY(:ids)
"""


async def _predict_nodes_structural(eligible_ids: list[str]) -> list[str]:
    """投递前冻结的预定节点集（结构口径，独立于任何运行结果）。"""
    from sqlalchemy import text

    from app.core.db import AsyncSessionLocal

    if not eligible_ids:
        return []
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(text(_PREDICTED_NODES_STRUCTURAL_SQL), {"ids": eligible_ids})
        ).all()
    return sorted(str(r[0]) for r in rows)


async def _barrier_snapshot(
    ts_start: datetime, eligible_ids: list[str], frozen_node_ids: list[str]
) -> dict[str, object]:
    """一次 DB 回查取全部屏障核验数据（行级明细 + 结果账本绑定）。"""
    from sqlalchemy import text

    from app.core.db import AsyncSessionLocal

    ledger_rows: list[dict] = []
    latest_rows: list[dict] = []
    node_rows: list[dict] = []
    async with AsyncSessionLocal() as db:
        if eligible_ids:
            ledger_rows = [
                {
                    "loopId": str(r[0]),
                    "status": r[1],
                    "score": float(r[2]) if r[2] is not None else None,
                    "algorithmVersion": r[3],
                    "source": r[4],
                    "sourceTaskId": r[5],
                    "resultRecordId": str(r[6]) if r[6] is not None else None,
                    "recordLogicalRunId": str(r[7]) if r[7] is not None else None,
                    "fitnessTags": r[8],
                }
                for r in (
                    await db.execute(
                        text(
                            "SELECT s.loop_id, s.status, s.score, s.algorithm_version, "
                            "s.source, s.source_task_id, s.result_record_id, "
                            "cr.logical_run_id, s.fitness_tags "
                            "FROM kpi_snapshot_hourly s "
                            "LEFT JOIN calculation_result_record cr ON cr.id = s.result_record_id "
                            "WHERE s.ts_start = :ts AND s.loop_id = ANY(:ids)"
                        ),
                        {"ts": ts_start, "ids": eligible_ids},
                    )
                ).all()
            ]
            latest_rows = [
                {
                    "loopId": str(r[0]),
                    "algorithmVersion": r[1],
                    "resultRecordId": str(r[2]) if r[2] is not None else None,
                    "recordLogicalRunId": str(r[3]) if r[3] is not None else None,
                }
                for r in (
                    await db.execute(
                        text(
                            "SELECT l.loop_id, l.algorithm_version, l.result_record_id, "
                            "cr.logical_run_id FROM loop_confidence_latest l "
                            "LEFT JOIN calculation_result_record cr ON cr.id = l.result_record_id "
                            "WHERE l.data_ts_start = :ts AND l.loop_id = ANY(:ids)"
                        ),
                        {"ts": ts_start, "ids": eligible_ids},
                    )
                ).all()
            ]
        if frozen_node_ids:
            node_rows = [
                {"plantNodeId": str(r[0]), "algorithmVersion": r[1]}
                for r in (
                    await db.execute(
                        text(
                            "SELECT plant_node_id, algorithm_version "
                            "FROM kpi_node_snapshot_hourly "
                            "WHERE ts_start = :ts AND plant_node_id = ANY(:nodes)"
                        ),
                        {"ts": ts_start, "nodes": frozen_node_ids},
                    )
                ).all()
            ]
    return {
        "ledger_rows": ledger_rows,
        "latest_rows": latest_rows,
        "node_rows": node_rows,
    }


async def _observe_child_task_ids() -> list[dict]:
    """尽力取证：worker 当前活跃的 calculate_node_kpi_hourly 子任务。"""
    try:
        from app.tasks.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=2.0)
        active = inspect.active() or {}
    except Exception:  # noqa: BLE001 — 取证失败不影响屏障
        return []
    out: list[dict] = []
    for _worker, tasks in active.items():
        for t in tasks or []:
            if t.get("name") == "app.tasks.kpi_calc.calculate_node_kpi_hourly":
                out.append(
                    {
                        "id": t.get("id"),
                        "args": t.get("args"),
                        "kwargs": t.get("kwargs"),
                        "worker": _worker,
                    }
                )
    return out


async def _task_record_status(task_record_id: str | None) -> str | None:
    """TaskRecord 终态（Redis 只读；缺失返回 None）。"""
    if not task_record_id:
        return None
    try:
        from app.services import task_tracker

        return await task_tracker.get_task_status(task_record_id)
    except Exception:  # noqa: BLE001 — Redis 不可读按未知处理，不误判通过
        return None


def _build_freeze(
    *,
    ts_start: datetime,
    manifest: dict,
    manifest_path: str,
    eligible: list[dict],
    excluded: list[dict],
    predicted_nodes: list[str],
    algorithm_version: str,
    prefix: str | None,
) -> dict:
    """投递前冻结件（R06）：eligible/excluded + 预定节点 + 独立 expected。"""
    by_tag = {row["loopTag"]: row for row in eligible}
    frozen_eligible = []
    for mrow in manifest["rows"]:
        tag = mrow["loopTag"]
        loop = by_tag.get(tag)
        expected_status = mrow["expectedStatus"]
        frozen_eligible.append(
            {
                "loopId": loop["loopId"] if loop else None,
                "loopTag": tag,
                "composition": mrow["composition"],
                "expectedStatus": expected_status,
                "expectedSnapshotStatus": _expected_snapshot_status(expected_status),
            }
        )
    return {
        "formatVersion": FREEZE_FORMAT_VERSION,
        "frozenAt": datetime.now(UTC).isoformat(),
        "window": {
            "tsStart": ts_start.isoformat(),
            "tsEnd": (ts_start + timedelta(hours=1)).isoformat(),
        },
        "manifest": {
            "path": manifest_path,
            "sha256": _manifest_sha256(manifest_path),
            "scenario": manifest.get("scenario"),
            "totalLoops": manifest.get("totalLoops"),
        },
        "algorithmVersion": algorithm_version,
        "barrierLoopPrefix": prefix,
        "eligible": frozen_eligible,
        "excluded": excluded,
        "predictedNodes": predicted_nodes,
        "expectedStatusHistogram": _expected_histogram(frozen_eligible),
    }


def _expected_histogram(frozen_eligible: list[dict]) -> dict[str, int]:
    """冻结集 expectedStatus 分布（混合组 800/100/50/50、正常组 1000 的独立预期）。"""
    hist: dict[str, int] = {}
    for row in frozen_eligible:
        hist[row["expectedStatus"]] = hist.get(row["expectedStatus"], 0) + 1
    return hist


def _fitness_tags_of(row: dict) -> list[str]:
    tags = row.get("fitnessTags")
    if isinstance(tags, dict):
        values = tags.get("tags")
        if isinstance(values, list):
            return [str(v) for v in values]
    if isinstance(tags, list):
        return [str(v) for v in tags]
    return []


def _evaluate_run_gate(
    freeze: dict,
    barrier: dict,
    *,
    parent_task_id: str,
    parent_state: str | None,
    task_record_status: str | None,
) -> dict:
    """R06 完成屏障核验（纯函数：冻结件 × DB 回查 × 任务终态）。

    通过条件（全部满足才可判 done；任一违约即不 PASS）：
    - ledger：冻结集每回路都有本窗小时账本行（pending/missing 非 0 即未闭合）；
    - source：全部行 source ∈ {SCHEDULED, MANUAL_STANDARD}；
    - run_binding：每行 result_record_id 非空且其 calculation_result_record.
      logical_run_id == 本批父任务 ID（旧结果污染/其他 run 的同窗行不得通过）；
    - algorithm_version：小时账本行算法版本 == 冻结版本；
    - expected_status：manifest expectedStatus 逐回路核对（EVALUATED→SUCCESS
      且 score 非空；INCONCLUSIVE(DATA_INSUFFICIENT)→INCONCLUSIVE 且
      fitness_tags 含 DATA_INSUFFICIENT）；
    - latest：冻结集每回路 latest 投影行 data_ts_start=本窗 + 版本一致 +
      绑定本批 logicalRunId（DEC-09 迟到窗由 R05 预检在投递前排除）；
    - nodes：冻结预定节点每节点有本窗节点行且版本一致；
    - task_terminal：本批 TaskRecord（快照行 source_task_id 唯一值）=SUCCESS；
    - parent_terminal：Celery 父任务终态=SUCCESS（父任务投递完≠完成）。
    """
    eligible = freeze["eligible"]
    expected_version = freeze["algorithmVersion"]
    ts_start = freeze["window"]["tsStart"]
    ledger_by_loop = {row["loopId"]: row for row in barrier["ledger_rows"]}
    latest_by_loop = {row["loopId"]: row for row in barrier["latest_rows"]}
    node_by_id = {row["plantNodeId"]: row for row in barrier["node_rows"]}

    missing: list[str] = []
    foreign_source: list[dict] = []
    bad_binding: list[dict] = []
    bad_version: list[dict] = []
    status_mismatch: list[dict] = []
    status_counts: dict[str, int] = {}
    for row in eligible:
        loop_id = row["loopId"]
        snap = ledger_by_loop.get(loop_id)
        if snap is None:
            missing.append(row["loopTag"])
            continue
        status_counts[snap["status"]] = status_counts.get(snap["status"], 0) + 1
        if (snap["source"] or "") not in _ALLOWED_SOURCES:
            foreign_source.append({"loopTag": row["loopTag"], "source": snap["source"]})
        if snap["recordLogicalRunId"] != parent_task_id:
            bad_binding.append(
                {
                    "loopTag": row["loopTag"],
                    "recordLogicalRunId": snap["recordLogicalRunId"],
                }
            )
        if snap["algorithmVersion"] != expected_version:
            bad_version.append(
                {"loopTag": row["loopTag"], "algorithmVersion": snap["algorithmVersion"]}
            )
        expected_snapshot = row["expectedSnapshotStatus"]
        ok_status = snap["status"] == expected_snapshot
        if ok_status and expected_snapshot == "SUCCESS" and snap["score"] is None:
            ok_status = False  # EVALUATED 必须有完整有效评分（score 非空）
        if ok_status and "DATA_INSUFFICIENT" in row["expectedStatus"]:
            ok_status = "DATA_INSUFFICIENT" in _fitness_tags_of(snap)
        if not ok_status:
            status_mismatch.append(
                {
                    "loopTag": row["loopTag"],
                    "expected": row["expectedStatus"],
                    "actualStatus": snap["status"],
                    "actualScore": snap["score"],
                    "actualTags": _fitness_tags_of(snap),
                }
            )

    latest_missing: list[str] = []
    latest_bad: list[dict] = []
    for row in eligible:
        loop_id = row["loopId"]
        proj = latest_by_loop.get(loop_id)
        if proj is None:
            latest_missing.append(row["loopTag"])
            continue
        if (
            proj["algorithmVersion"] != expected_version
            or proj["recordLogicalRunId"] != parent_task_id
        ):
            latest_bad.append(
                {
                    "loopTag": row["loopTag"],
                    "algorithmVersion": proj["algorithmVersion"],
                    "recordLogicalRunId": proj["recordLogicalRunId"],
                }
            )

    node_missing = [nid for nid in freeze["predictedNodes"] if nid not in node_by_id]
    node_bad_version = [
        {"plantNodeId": nid, "algorithmVersion": node_by_id[nid]["algorithmVersion"]}
        for nid in freeze["predictedNodes"]
        if nid in node_by_id and node_by_id[nid]["algorithmVersion"] != expected_version
    ]

    # 空结果不得让行级条件"满足"（防伪：零行时 source/run_binding/version/
    # expected_status 的 vacuous True 不计为首达，ledger 完整是它们的前提）
    rows_complete = not missing
    conditions = {
        "ledger": rows_complete,
        "source": rows_complete and not foreign_source,
        "run_binding": rows_complete and not bad_binding,
        "algorithm_version": rows_complete and not bad_version,
        "expected_status": rows_complete and not status_mismatch,
        "latest": not latest_missing and not latest_bad,
        "nodes": not node_missing and not node_bad_version,
        "task_terminal": task_record_status == "SUCCESS",
        "parent_terminal": parent_state == "SUCCESS",
    }
    return {
        "ok": all(conditions.values()),
        "conditions": conditions,
        "denominators": {
            "frozenEligible": len(eligible),
            "excluded": len(freeze.get("excluded", [])),
            "ledgerRows": len(barrier["ledger_rows"]),
            "snapshotStatusCounts": status_counts,
            "expectedHistogram": freeze["expectedStatusHistogram"],
            "success": sum(1 for r in ledger_by_loop.values() if r["status"] == "SUCCESS"),
            "inconclusive": sum(
                1 for r in ledger_by_loop.values() if r["status"] == "INCONCLUSIVE"
            ),
            "partial": sum(1 for r in ledger_by_loop.values() if r["status"] == "PARTIAL"),
            "skipped": len(freeze.get("excluded", [])),
        },
        "violations": {
            "missingLedger": {"count": len(missing), "sample": missing[:_VIOLATION_SAMPLE_LIMIT]},
            "foreignSource": {
                "count": len(foreign_source),
                "sample": foreign_source[:_VIOLATION_SAMPLE_LIMIT],
            },
            "runBinding": {
                "count": len(bad_binding),
                "sample": bad_binding[:_VIOLATION_SAMPLE_LIMIT],
            },
            "algorithmVersion": {
                "count": len(bad_version),
                "sample": bad_version[:_VIOLATION_SAMPLE_LIMIT],
            },
            "expectedStatus": {
                "count": len(status_mismatch),
                "sample": status_mismatch[:_VIOLATION_SAMPLE_LIMIT],
            },
            "latestMissing": {
                "count": len(latest_missing),
                "sample": latest_missing[:_VIOLATION_SAMPLE_LIMIT],
            },
            "latestBad": {"count": len(latest_bad), "sample": latest_bad[:_VIOLATION_SAMPLE_LIMIT]},
            "nodeMissing": {
                "count": len(node_missing),
                "sample": node_missing[:_VIOLATION_SAMPLE_LIMIT],
            },
            "nodeBadVersion": {
                "count": len(node_bad_version),
                "sample": node_bad_version[:_VIOLATION_SAMPLE_LIMIT],
            },
        },
        "window": ts_start,
    }


def _terminal_failure_reason(
    gate: dict, parent_state: str | None, task_record_status: str | None
) -> str | None:
    """只提前终止已确定失败的测量；节点/latest 未到齐仍须等待。

    不停止或撤销 Celery 任务，不把各条件在不同时刻首达拼成 PASS。
    行级违约在父任务和 TaskRecord 均成功、账本齐全后才视为终态。
    """
    if parent_state in {"FAILURE", "REVOKED"}:
        return "PARENT_FAILED"
    conditions = gate["conditions"]
    if conditions["ledger"] and conditions["run_binding"]:
        if task_record_status in {"FAILED", "CANCELLED"}:
            return "TASK_FAILED"
    row_conditions = ("source", "run_binding", "algorithm_version", "expected_status")
    if (
        conditions["ledger"]
        and conditions["parent_terminal"]
        and conditions["task_terminal"]
        and not all(conditions[name] for name in row_conditions)
    ):
        return "TERMINAL_MISMATCH"
    return None


def _write_report_checkpoint(path: str, report: dict) -> None:
    """每次有效观测原子替换报告，进程中断时保留最近证据及 RUNNING 状态。"""
    target = Path(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _print_gate_report(gate: dict, parent_task_id: str, task_record_status: str | None) -> None:
    """屏障报告（完整分母 + 逐项违约明细，失败不缩分母）。"""
    d = gate["denominators"]
    v = gate["violations"]
    print("[barrier] 完成屏障判定（冻结件 × DB 回查 × 任务终态；父任务终态不单独作为屏障）：")
    print(f"  分母: 冻结 eligible={d['frozenEligible']} excluded(跳过)={d['skipped']}")
    print(
        f"  小时账本: {d['ledgerRows']} 行（SUCCESS={d['success']} "
        f"INCONCLUSIVE={d['inconclusive']} PARTIAL={d['partial']}）"
    )
    print(f"  manifest 独立预期: {d['expectedHistogram']}")
    print(f"  各条件: {gate['conditions']}")
    print(f"  parent_task_id(logicalRunId): {parent_task_id}")
    print(f"  TaskRecord 终态: {task_record_status or 'unknown'}")
    for name, detail in v.items():
        if detail["count"]:
            print(f"  违约[{name}]: count={detail['count']} sample={detail['sample']}")


async def run_standard_hourly(args: argparse.Namespace) -> int:
    """standard-hourly 模式主流程（预检 → 冻结 → 投递 → 绑定核验屏障）。"""
    ts_start = _frozen_window(args.ts_start)
    ts_end = ts_start + timedelta(hours=1)
    prefix = args.barrier_loop_prefix
    if not prefix:
        raise SystemExit(
            "[preflight] standard-hourly 必须提供 --barrier-loop-prefix"
            "（混合 dev 环境下不限定前缀无法构成冻结身份）"
        )
    if not args.manifest:
        raise SystemExit(
            "[preflight] standard-hourly 必须提供 --manifest（R06：独立 expected 冻结，"
            "缺 manifest 的行数级核对不得计 SLO/PASS）"
        )
    manifest = _load_manifest(args.manifest)
    if manifest.get("windowStart") != ts_start.isoformat():
        raise SystemExit(
            "[preflight] manifest windowStart 与 --ts-start 不一致"
            f"（manifest={manifest.get('windowStart')} vs ts_start={ts_start.isoformat()}）"
            "——数据身份与被测窗必须同窗"
        )

    print("[preflight] 三条件参评回路数检查（DB 权威口径）")
    print(f"  window: {ts_start.isoformat()} ~ {ts_end.isoformat()} (naive UTC)")
    print(f"  barrier_loop_prefix: {prefix}")
    print(
        f"  manifest: {args.manifest} (scenario={manifest.get('scenario')}, "
        f"totalLoops={manifest.get('totalLoops')})"
    )

    universe = await _preflight_loop_universe_full(prefix)
    eligible, excluded = _partition_universe(universe)
    eligible_ids = [row["loopId"] for row in eligible]
    eligible_tags = {row["loopTag"] for row in eligible}
    manifest_tags = {mrow["loopTag"] for mrow in manifest["rows"]}
    print(f"  eligible(is_active+READY+include_in_evaluation): {len(eligible_ids)}")
    print(f"  excluded(不可参评，单列不缩分母): {len(excluded)}")
    if len(set(eligible_ids)) != len(eligible_ids):
        raise SystemExit("[preflight] 断言失败：eligible loop id 存在重复（不应发生）")
    if len(eligible_ids) != args.expect_eligible:
        raise SystemExit(
            f"[preflight] 断言失败：三条件参评回路数 {len(eligible_ids)} ≠ 预期 "
            f"{args.expect_eligible}（standard-hourly 必须为独立回路集，禁止重放凑数）"
        )
    if manifest_tags != eligible_tags:
        only_manifest = sorted(manifest_tags - eligible_tags)[:5]
        only_eligible = sorted(eligible_tags - manifest_tags)[:5]
        raise SystemExit(
            "[preflight] 测试身份核对失败：manifest 与前缀范围 eligible 集合不一致"
            f"（仅在 manifest: {only_manifest}；仅在前缀 eligible: {only_eligible}）"
        )
    print(f"  独立 loopId 断言: {len(eligible_ids)} unique = expect ✓（与 manifest 身份一致 ✓）")

    # R05 预检：既有 latest 与被测窗冲突（迟到窗屏障结构性不可满足）
    latest_conflicts = await _preflight_latest_conflict(ts_end, eligible_ids)
    if latest_conflicts:
        raise SystemExit(
            "[preflight] R05 拒绝投递：冻结集内 "
            f"{len(latest_conflicts)} 个回路既有 loop_confidence_latest 晚于被测窗终点"
            f"（DEC-09 迟到窗不覆盖 latest ⇒ latest 屏障在本窗结构性不可满足），"
            f"示例: {latest_conflicts[:5]}；请改用不早于既有 latest 终点的窗口"
            "（禁止删除 latest/放宽写入规则来消除冲突）"
        )
    print("  R05 latest 冲突预检: 0 冲突 ✓（本窗非迟到窗，latest 屏障可达）")

    # R05 编排级预检（--plan-windows：单调升序 + 每窗无迟到冲突）
    plan_verdict: dict | None = None
    if args.plan_windows:
        plan = [_parse_plan_window(w) for w in args.plan_windows.split(",") if w.strip()]
        latest_ends_iso = await _latest_ends(eligible_ids)
        plan_verdict = _check_plan_windows(plan, latest_ends_iso)
        if not plan_verdict["ok"]:
            raise SystemExit(
                f"[preflight] R05 编排预检拒绝：{plan_verdict['problems']}；"
                "剧本时序必须单调升序且每窗对现状 latest 无冲突"
            )
        print(
            f"  R05 编排预检: {len(plan)} 窗严格升序 + 现状无冲突 ✓"
            "（升序下每窗闭合后 latest=该窗终点 < 下一窗终点 ⇒ 连续窗可闭合）"
        )

    # R06 投递前冻结：预定节点（结构推导）+ 算法版本 + 独立 expected
    predicted_nodes = await _predict_nodes_structural(eligible_ids)
    algorithm_version = _current_algorithm_version()
    freeze = _build_freeze(
        ts_start=ts_start,
        manifest=manifest,
        manifest_path=args.manifest,
        eligible=eligible,
        excluded=excluded,
        predicted_nodes=predicted_nodes,
        algorithm_version=algorithm_version,
        prefix=prefix,
    )
    print(f"  预定节点(结构推导，独立于运行结果): {len(predicted_nodes)}")
    print(f"  冻结算法版本: {algorithm_version}")
    print(f"  manifest expected 分布: {freeze['expectedStatusHistogram']}")

    barrier0 = await _barrier_snapshot(ts_start, eligible_ids, predicted_nodes)
    if barrier0["ledger_rows"] or barrier0["latest_rows"]:
        raise SystemExit(
            "[preflight] 拒绝投递：冻结集在本窗已存在结果行"
            f"（ledger={len(barrier0['ledger_rows'])} latest={len(barrier0['latest_rows'])}）"
            "——同窗重投的计时与 run 绑定核验均失效（同窗不可作为测量批）"
        )
    print(f"  投递前既有同窗行: ledger=0 latest=0 node={len(barrier0['node_rows'])} ✓")

    if args.dry_run:
        print("[dry-run] 计划（未投递任何任务、未写入任何数据、未落盘冻结件）：")
        print(f"  submit: calculate_hourly_kpi.delay(ts_start='{ts_start.isoformat()}')")
        print(
            f"  冻结: eligible {len(eligible_ids)} + excluded {len(excluded)} + "
            f"预定节点 {len(predicted_nodes)} + manifest sha256 "
            f"{freeze['manifest']['sha256'][:16]}… + 算法版本 {algorithm_version}"
        )
        print(
            "  屏障: 小时账本(行数+source+run 绑定+版本+expectedStatus 逐回路) + "
            "latest(行+版本+run 绑定) + 预定节点(行+版本) + TaskRecord/Celery 父任务终态"
        )
        if plan_verdict:
            print(f"  编排: {plan_verdict['windows']}")
        print(f"  SLO: {args.slo_seconds:.0f}s（计时=投递提交开始 → 全部屏障条件满足）")
        print("[dry-run] preflight 通过，standard-hourly 就绪")
        return 0

    from app.tasks.kpi_calc import calculate_hourly_kpi

    # R06 冻结件投递前落盘（先冻结后投递；dry-run 不落盘）
    freeze_dir = Path(args.freeze_dir)
    freeze_dir.mkdir(parents=True, exist_ok=True)
    freeze_path = freeze_dir / (
        f"freeze_{manifest.get('scenario')}_{ts_start.strftime('%Y%m%d%H')}.json"
    )
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[freeze] 冻结件已落盘（投递前）: {freeze_path}")

    print(
        f"[submit] calculate_hourly_kpi.delay(ts_start='{ts_start.isoformat()}') "
        f"（经既有 Celery 入口，复用唯一 lifespan Worker，不新增开放触发 API）"
    )
    submit_started = time.perf_counter()
    result = calculate_hourly_kpi.delay(ts_start=ts_start.isoformat())
    parent_task_id = result.id
    print(f"[submit] parent_task_id={parent_task_id}")

    child_ids: list[dict] = []
    child_observed_at: str | None = None
    gate: dict = {}
    task_record_status: str | None = None
    first_met: dict[str, float] = {}
    done = False
    completion_reason = "RUNNING"
    report: dict = {}
    while time.perf_counter() - submit_started < args.timeout:
        # 子任务 ID 尽力取证（父任务终态≠屏障；错过即 not-observed）
        if not child_ids:
            child_ids = await _observe_child_task_ids()
            if child_ids:
                child_observed_at = datetime.now(UTC).isoformat()
        barrier = await _barrier_snapshot(ts_start, eligible_ids, predicted_nodes)
        source_task_ids = {
            row["sourceTaskId"] for row in barrier["ledger_rows"] if row["sourceTaskId"]
        }
        task_record_status = await _task_record_status(
            source_task_ids.pop() if len(source_task_ids) == 1 else None
        )
        parent_state: str | None = None
        try:
            parent_state = result.state
        except Exception:  # noqa: BLE001 — 结果后端不可读按未知处理，不误判通过
            parent_state = None
        gate = _evaluate_run_gate(
            freeze,
            barrier,
            parent_task_id=parent_task_id,
            parent_state=parent_state,
            task_record_status=task_record_status,
        )
        for name, ok in gate["conditions"].items():
            if ok and name not in first_met:
                first_met[name] = time.perf_counter() - submit_started
        elapsed = time.perf_counter() - submit_started
        done = gate["ok"]
        completion_reason = (
            "BARRIER_PASSED"
            if done
            else _terminal_failure_reason(gate, parent_state, task_record_status)
        ) or "RUNNING"
        report = {
            "window": freeze["window"],
            "parentTaskId": parent_task_id,
            "parentState": parent_state,
            "taskRecordStatus": task_record_status,
            "freezePath": str(freeze_path),
            "observedAt": datetime.now(UTC).isoformat(),
            "completionReason": completion_reason,
            "totalSeconds": elapsed,
            "sloSeconds": args.slo_seconds,
            "sloPassed": done and elapsed <= args.slo_seconds,
            "gate": gate,
            "firstMetSeconds": first_met,
            "childTaskObservation": child_ids or "not-observed",
        }
        if args.report_out:
            _write_report_checkpoint(args.report_out, report)
        if completion_reason != "RUNNING":
            break
        await asyncio.sleep(args.poll_interval)

    elapsed = time.perf_counter() - submit_started
    if gate:
        _print_gate_report(gate, parent_task_id, task_record_status)
    print(f"  各条件首次满足时刻(s): { {k: round(v, 1) for k, v in sorted(first_met.items())} }")
    print(
        f"  节点子任务取证: {child_ids if child_ids else 'not-observed'}"
        f"{f' @ {child_observed_at}' if child_observed_at else ''}"
        "（kpi_calc 级联节点任务不带父 run 标识，子任务完成以节点结果行核验为准）"
    )
    print(f"  total_seconds: {elapsed:.3f}")
    slo_passed = done and elapsed <= args.slo_seconds
    print(
        f"  SLO <= {args.slo_seconds:.0f}s: {'通过' if slo_passed else '失败'}"
        f"（分母: 冻结 {gate['denominators']['frozenEligible'] if gate else 'n/a'} / "
        f"成功 {gate['denominators']['success'] if gate else 'n/a'} / "
        f"违约 {sum(v_['count'] for v_ in gate['violations'].values()) if gate else 'n/a'} / "
        f"跳过(excluded) {gate['denominators']['skipped'] if gate else 'n/a'}）"
    )
    if completion_reason == "RUNNING":
        completion_reason = "TIMEOUT"
    if args.report_out and gate:
        report.update(
            completionReason=completion_reason, totalSeconds=elapsed, sloPassed=slo_passed
        )
        _write_report_checkpoint(args.report_out, report)
        print(f"[report] 报告已落盘: {args.report_out}")
    if not done:
        print(f"  [{completion_reason}] 屏障未通过（保留违约明细；不缩分母、不改判）")
        return 1
    return 0 if slo_passed else 1


# ---------------------------------------------------------------------------
# custom 模式（loop-evals 回放，独立单列）
# ---------------------------------------------------------------------------


def _replay_windows(requested_loops: int, loop_ids: list[str]) -> list[tuple[int, list[str]]]:
    """Split a loop-eval target into hourly replay batches（仅 custom 模式）。"""
    if not loop_ids:
        return []
    batch_size = len(loop_ids)
    batches = math.ceil(requested_loops / batch_size)
    result: list[tuple[int, list[str]]] = []
    remaining = requested_loops
    for batch_index in range(batches):
        chunk_size = min(batch_size, remaining)
        result.append((batch_index, loop_ids[:chunk_size]))
        remaining -= chunk_size
    return result


def run_custom(args: argparse.Namespace) -> int:
    """custom 模式主流程（loop-evals 回放，不计标准小时 SLO）。"""
    with httpx.Client(timeout=30.0) as client:
        login = client.post(
            f"{args.base_url}/auth/login",
            json={"username": args.username, "password": args.password},
        )
        login.raise_for_status()
        token = login.json()["data"]["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}

        loop_ids = _load_loops(client, args.base_url, headers, args.page_size)
        if not loop_ids:
            raise SystemExit("custom 模式拒绝执行：当前没有可用回路。")
        unique_loops = len(set(loop_ids))
        replay = unique_loops < args.loops
        if replay and not args.allow_replay:
            raise SystemExit(
                f"custom 模式：仅 {unique_loops} 个独立回路 < 目标 {args.loops} loop-evals；"
                "少 ID 多窗口重放需显式 --allow-replay（结果只作吞吐回放基线，"
                "不得视为真实 1000 路径门禁）"
            )

        batches = _replay_windows(args.loops, loop_ids)
        if not batches:
            raise SystemExit("custom 模式拒绝执行：无法生成 replay 批次。")

        base_end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        batch_timings: list[float] = []
        batch_statuses: list[str] = []
        total_loop_evals = 0

        started = time.perf_counter()
        for batch_index, batch_loop_ids in batches:
            ts_end = base_end - timedelta(hours=batch_index + 1)
            ts_start = ts_end - timedelta(hours=1)
            submit_started = time.perf_counter()
            submit = _post_with_retry(
                client,
                f"{args.base_url}/tasks/custom/evaluate",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "loopIds": batch_loop_ids,
                    # P0-03 §3.3：metrics 仅非空校验、不进任务参数（恒为全量
                    # MetricConfig）——报告不得声称"仅 3 项指标"
                    "metrics": ["accuracy_rate", "fast_rate", "steady_rate"],
                    "tsStart": ts_start.isoformat().replace("+00:00", "Z"),
                    "tsEnd": ts_end.isoformat().replace("+00:00", "Z"),
                },
                retry_seconds=args.retry_seconds,
            )
            task_id = submit.json()["data"]["taskId"]

            while True:
                if time.perf_counter() - started > args.timeout:
                    done_evals = total_loop_evals
                    raise SystemExit(
                        f"custom 模式超时：{args.timeout:.0f}s，已完成 {done_evals} loop-evals"
                    )
                task_response = _get_with_retry(
                    client,
                    f"{args.base_url}/tasks/{task_id}",
                    headers=headers,
                    retry_seconds=args.retry_seconds,
                )
                task = task_response.json()["data"]
                status = task["status"]
                if status in {"SUCCESS", "COMPLETED", "FAILED", "CANCELLED"}:
                    break
                time.sleep(args.poll_interval)

            elapsed = time.perf_counter() - submit_started
            loops_done = _read_task_loops_done(task)
            total_loop_evals += loops_done
            batch_timings.append(elapsed)
            batch_statuses.append(status)
            if batch_index < len(batches) - 1:
                time.sleep(args.batch_spacing)

        total_elapsed = time.perf_counter() - started
        all_ok = all(s in {"SUCCESS", "COMPLETED"} for s in batch_statuses)

        print("KPI custom 容量回放结果（loop-evals 口径——不计 standard-hourly 600s SLO）")
        print(f"  mode: custom (replay={replay})")
        print(f"  unique_loops: {unique_loops}")
        print(f"  requested_loop_evals: {args.loops}")
        print(f"  batches: {len(batches)}")
        print(f"  total_loop_evals: {total_loop_evals}")
        print(f"  total_seconds: {total_elapsed:.3f}")
        if batch_timings:
            print(f"  batch_time_p50_seconds: {statistics.median(batch_timings):.3f}")
            print(f"  batch_time_p95_seconds: {_percentile(batch_timings, 0.95):.3f}")
        print(f"  all_batches_successful: {all_ok}")
        if replay:
            print(
                "  ⚠ 重放口径：少量独立回路多窗口重放凑 loop-evals，仅作吞吐参考，"
                "不得表述为标准小时容量或 1000 独立回路容量"
            )
        return 0 if (all_ok and total_loop_evals >= args.loops) else 1


def main() -> int:
    args = _parse_args()
    if args.mode == "standard-hourly":
        return asyncio.run(run_standard_hourly(args))
    return run_custom(args)


if __name__ == "__main__":
    raise SystemExit(main())
