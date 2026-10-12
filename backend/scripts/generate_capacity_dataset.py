"""P5 批 α：1000 独立回路容量数据集生成器（DEC-08a 混合通道）.

通道（P0-03 v2 §1.3 冻结口径）：
1. 元数据 API 组态——测试装置树（FACTORY/AREA/UNIT，PERF- 前缀隔离）经
   POST /plant-nodes；7000 测点（1000 回路 × 7 OPC 角色）经 POST /tags/import
   （含量程/单位/类型，Excel 真实业务链路）；1000 回路经 POST /loops/import
   （4 必填角色绑定 + 参评=是 + READY 状态推导，Excel 真实业务链路）。
2. 时序直写本地 TDengine——st_point_data_v1 点子表（p_<tag_id hex>），
   1h@1s 网格，复用 data_import._write_points_bulk 同款批量直拼 SQL
   （source_kind=4，payload_hash 同公式）；PV 携带质量三态。
3. 合成声明入库——sys_config 键 ``capacity.perf_dataset.declaration``（JSON：
   synthetic=true、构成、窗口、seed、manifest 路径）+ 每回路 remark 标注；
   数据 manifest（P0-03 §1.2 字段）落 backend/capacity_dataset/。

场景构成（04§4.1 / DEC-08a 裁决"50 手动=eligible 内终态"）：
- scenario b（本批执行）：800 正常 + 100 数据不足(PV 大比例 BAD→INCONCLUSIVE)
  + 50 手动(MODE=0，仍参评、eligible 内终态) + 50 含缺口(前 15min 有数据)；
  台账 1000 全部三条件参评（is_active+READY+include_in_evaluation）。
- scenario a（备选）：1000 全部正常输入（另一冻结窗口，可后补）。

生成器红线（P0-03 §1.1）：创建后**禁止任何停用/关参评操作**（停用联动置
不参评且复用不恢复，会静默缩分母）；隔离前缀 PERF-T，不污染现有真实回路；
所有产物显式标注合成（synthetic），不得称"1000 现场真实回路"。

用法（工作目录 backend/；写入类动作须先经主协调确认）::

    uv run python scripts/generate_capacity_dataset.py --plan                # 零写入
    uv run python scripts/generate_capacity_dataset.py --execute --scenario b
    uv run python scripts/generate_capacity_dataset.py --self-check --scenario b
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 隔离前缀（tag/回路/节点名共用；与现有真实回路命名空间隔离）
PREFIX = "PERF-T"
#: 台账规模（冻结：1000 独立回路，禁止少 ID 重放凑数）
TOTAL_LOOPS = 1000
#: 装置树结构：1 工厂 × 5 装置(AREA) × 4 单元(UNIT) = 20 单元 × 50 回路
AREAS = 5
UNITS_PER_AREA = 4
LOOPS_PER_UNIT = TOTAL_LOOPS // (AREAS * UNITS_PER_AREA)
#: 场景 B 构成（冻结：800+100+50+50；scenario a 为 1000 全正常）
SCENARIO_B = {"normal": 800, "insufficient": 100, "manual": 50, "gap": 50}
SCENARIO_A = {"normal": TOTAL_LOOPS, "insufficient": 0, "manual": 0, "gap": 0}
#: 7 OPC 角色
ROLES = ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")
#: 回路类型轮转（LOOP_TYPE 字典 seed 值）
LOOP_TYPES = ("TEMPERATURE", "PRESSURE", "LEVEL", "FLOW")
#: 控制类型轮转（影响默认稳态时间等）
CONTROL_TYPES = ("STABLE", "SLOW", "FAST", "STABLE")
#: 量程模板（按回路类型轮转；PV/SP 共用，OP 恒 0-100）
RANGES = {
    "TEMPERATURE": (0.0, 400.0, "degC"),
    "PRESSURE": (0.0, 2.5, "MPa"),
    "LEVEL": (0.0, 100.0, "%"),
    "FLOW": (0.0, 120.0, "t/h"),
}
#: 时序窗口：1h @ 1s
POINTS = 3600
#: TD 批量 INSERT 行数/条（与 data_import._write_points_bulk 一致）
TD_BATCH = 5000
#: 合成声明 sys_config 键
DECLARATION_KEY = "capacity.perf_dataset.declaration"
#: 合成标记（manifest/声明/remark 共用）
SYNTHETIC_MARK = "P5容量合成数据集(synthetic)——生成器产出，非现场真实回路"

MANIFEST_DIR = Path(__file__).resolve().parent.parent / "capacity_dataset"
DEFAULT_BASE_URL = "http://localhost:17101/api/v1"  # MVP 隔离端口


def _loop_tag(i: int) -> str:
    """回路位号（1-based，4 位补零）：PERF-T0001..PERF-T1000."""
    return f"{PREFIX}{i:04d}"


def _unit_name(area_idx: int, unit_idx: int) -> str:
    return f"{PREFIX}-AREA{area_idx + 1}-U{unit_idx + 1}"


def _loop_composition(scenario: str, i: int) -> str:
    """回路 i（1-based）的场景归属（确定性分配，manifest 与时序同源）."""
    comp = SCENARIO_B if scenario == "b" else SCENARIO_A
    if i <= comp["normal"]:
        return "normal"
    if i <= comp["normal"] + comp["insufficient"]:
        return "insufficient"
    if i <= comp["normal"] + comp["insufficient"] + comp["manual"]:
        return "manual"
    return "gap"


def _response_category(i: int) -> str:
    """响应特征类别（确定性轮转；配比登记 manifest——正式配比待 P0-02 profile）."""
    r = i % 10
    if r < 7:
        return "steady"
    if r == 7:
        return "oscillation"
    if r == 8:
        return "drift"
    return "noisy"


# ---------------------------------------------------------------------------
# 时序合成（纯函数，seed 确定）
# ---------------------------------------------------------------------------


def synth_series(
    i: int, composition: str, category: str, span: float
) -> dict[str, list[tuple[int, float, int, int]]]:
    """合成单回路 1h@1s 序列（seed=loop 序号，可再生）.

    Returns:
        {role: [(t_offset_s, value, quality_class, quality_raw), ...]}
        PV/OP 密集（3600 点），SP COV 稀疏，MODE 单点，PID 各单点。
    """
    rng = np.random.default_rng(seed=i)
    lo = 0.0
    sp0 = 0.55 * span

    # SP：基线 + 两次阶跃（COV 稀疏落点）
    sp = np.full(POINTS, sp0)
    sp[900:2400] += 0.05 * span
    sp[2400:] += 0.10 * span
    sp_series = [
        (0, round(float(sp[0]), 4), 1, 1),
        (900, round(float(sp[900]), 4), 1, 1),
        (2400, round(float(sp[2400]), 4), 1, 1),
    ]

    if composition == "manual":
        # 手动：PV 自由漂移、OP 人工阶跃；MODE=0(MANUAL) 全窗
        pv = sp0 + np.cumsum(rng.normal(0, 0.0008 * span, POINTS))
        pv = np.clip(pv, lo, span)
        op = np.full(POINTS, 45.0)
        for cut in (600, 1500, 2700):
            op[cut:] += 8.0
        pv_q = np.ones(POINTS, dtype=int)
        mode_val = 0
    else:
        # 自动：PV 跟随 SP（一阶惯性 + 噪声；类别变体）
        noise_scale = 0.01 * span
        if category == "oscillation":
            noise_scale = 0.006 * span
        elif category == "noisy":
            noise_scale = 0.03 * span
        wn = rng.normal(0, noise_scale, POINTS)
        pv = np.empty(POINTS)
        pv[0] = sp0
        alpha = 0.08 if category != "drift" else 0.02
        for t in range(1, POINTS):
            pv[t] = pv[t - 1] + alpha * (sp[t] - pv[t - 1]) + wn[t]
        if category == "oscillation":
            pv = pv + 0.04 * span * np.sin(2 * np.pi * np.arange(POINTS) / 240.0)
        if category == "drift":
            pv = pv + np.linspace(0, 0.06 * span, POINTS)
        pv = np.clip(pv, lo, span)
        op = 50.0 - (pv - sp) * (2.5 if category != "noisy" else 1.5)
        op = np.clip(op + rng.normal(0, 0.5, POINTS), 0.0, 100.0)
        pv_q = np.ones(POINTS, dtype=int)
        if composition == "insufficient":
            # 数据不足：70% 点 PV 质量 BAD（值照写、质量类 0）→ expected INCONCLUSIVE
            pv_q[rng.random(POINTS) < 0.70] = 0
        mode_val = 1

    if composition == "gap":
        # 含缺口：仅前 15min 落数据（后 45min 长缺口 → held/覆盖语义）
        n_valid = 900
        sp_series = sp_series[:1]
    else:
        n_valid = POINTS

    pv_series = [(t, round(float(pv[t]), 4), int(pv_q[t]), int(pv_q[t])) for t in range(n_valid)]
    op_series = [(t, round(float(op[t]), 4), 1, 1) for t in range(n_valid)]
    return {
        "PV": pv_series,
        "SP": sp_series,
        "OP": op_series,
        "MODE": [(0, float(mode_val), 1, 1)],
        "PID_P": [(0, 1.2, 1, 1)],
        "PID_I": [(0, 60.0, 1, 1)],
        "PID_D": [(0, 0.0, 1, 1)],
    }


# ---------------------------------------------------------------------------
# API 组态（元数据真实业务链路）
# ---------------------------------------------------------------------------


class ApiClient:
    """最小 API 客户端（登录 + 组态导入）。"""

    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=120.0)
        resp = self.client.post(
            f"{self.base}/auth/login", json={"username": username, "password": password}
        )
        resp.raise_for_status()
        self.headers = {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}

    def get(self, path: str, **kw):
        return self.client.get(f"{self.base}{path}", headers=self.headers, **kw)

    def post(self, path: str, **kw):
        return self.client.post(
            f"{self.base}{path}", headers={**self.headers, "Content-Type": "application/json"}, **kw
        )

    def post_file(self, path: str, field: str, filename: str, data: bytes):
        return self.client.post(
            f"{self.base}{path}",
            headers=self.headers,
            files={
                field: (
                    filename,
                    data,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )


def _xlsx(headers: list[str], rows: list[list[object]]) -> bytes:
    """构造导入用 Excel（openpyxl 内存工作簿）。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# 列头与 services 侧导入契约一致（tag 10 列 / loop 19 列）
TAG_HEADERS = [
    "位号",
    "名称",
    "测点类型",
    "量程下限",
    "量程上限",
    "单位",
    "参数类型",
    "所属单元",
    "原始ID",
    "是否启用",
]
LOOP_HEADERS = [
    "自控回路编号",
    "回路描述",
    "设定值位号",
    "测量值位号",
    "输出值位号",
    "控制方式位号",
    "所属单元名称",
    "是否启用",
    "比例带",
    "积分时间",
    "微分时间",
    "回路类型",
    "控制类型",
    "等级",
    "参评状态",
    "OP输出下限位",
    "OP输出上限位",
    "DCS型号",
    "备注",
]


def build_tag_rows() -> list[list[object]]:
    """7000 tag 行：PERF-T0001_PV ... PID_D（PV/SP 带量程，OP 0-100）。"""
    rows: list[list[object]] = []
    for i in range(1, TOTAL_LOOPS + 1):
        lt = LOOP_TYPES[(i - 1) % len(LOOP_TYPES)]
        lo, hi, unit = RANGES[lt]
        for role in ROLES:
            name = f"{_loop_tag(i)}_{role}"
            if role in ("PV", "SP"):
                rmin, rmax, uom = lo, hi, unit
            elif role == "OP":
                rmin, rmax, uom = 0.0, 100.0, "%"
            else:
                rmin = rmax = uom = None
            measure = lt if role in ("PV", "SP") else "OTHER"
            rows.append(
                [name, f"{SYNTHETIC_MARK} {role}", measure, rmin, rmax, uom, role, "", "", ""]
            )
    return rows


def build_loop_rows() -> list[list[object]]:
    """1000 loop 行：4 必填 + 3 PID 角色绑定、单元归属、启用、参评=是。"""
    rows: list[list[object]] = []
    for i in range(1, TOTAL_LOOPS + 1):
        area_idx = (i - 1) // (LOOPS_PER_UNIT * UNITS_PER_AREA)
        unit_idx = ((i - 1) // LOOPS_PER_UNIT) % UNITS_PER_AREA
        unit_name = _unit_name(area_idx, unit_idx)
        tag = _loop_tag(i)
        lt = LOOP_TYPES[(i - 1) % len(LOOP_TYPES)]
        ct = CONTROL_TYPES[(i - 1) % len(CONTROL_TYPES)]
        rows.append(
            [
                tag,  # 自控回路编号
                SYNTHETIC_MARK,  # 回路描述
                f"{tag}_SP",
                f"{tag}_PV",
                f"{tag}_OP",
                f"{tag}_MODE",
                unit_name,
                "是",
                f"{tag}_PID_P",
                f"{tag}_PID_I",
                f"{tag}_PID_D",
                lt,
                ct,
                2,
                "是",
                None,
                None,
                "",
                f"{SYNTHETIC_MARK}; 红线:创建后禁停用/禁关参评(P5容量分母)",
            ]
        )
    return rows


def ensure_tree(api: ApiClient) -> dict[str, str]:
    """创建测试装置树（已存在同名节点则复用）。Returns: {unit_name: node_id}.

    GET /plant-nodes 返回三层树（节点键为 ``id``，子级在 ``children``）；
    POST /plant-nodes 返回 ``{"data": {"id": ...}}``。
    """
    resp = api.get("/plant-nodes")
    resp.raise_for_status()

    existing: dict[str, str] = {}

    def _collect(nodes: list[dict]) -> None:
        for n in nodes or []:
            existing[n["name"]] = n["id"]
            _collect(n.get("children") or [])

    _collect(resp.json().get("data") or [])

    def _ensure(name: str, ntype: str, parent_id: str | None) -> str:
        if name in existing:
            return existing[name]
        r = api.post("/plant-nodes", json={"name": name, "type": ntype, "parentId": parent_id})
        r.raise_for_status()
        node_id = r.json()["data"]["id"]
        existing[name] = node_id
        return node_id

    factory = _ensure(f"{PREFIX}-FACTORY", "FACTORY", None)
    units: dict[str, str] = {}
    for a in range(AREAS):
        area = _ensure(f"{PREFIX}-AREA{a + 1}", "AREA", factory)
        for u in range(UNITS_PER_AREA):
            units[_unit_name(a, u)] = _ensure(_unit_name(a, u), "UNIT", area)
    return units


# ---------------------------------------------------------------------------
# 时序直写 TDengine（st_point_data_v1 点子表；镜像 data_import._write_points_bulk）
# ---------------------------------------------------------------------------


async def load_role_points(prefix: str) -> dict[str, dict[str, tuple[str, str]]]:
    """PG 查生成回路的 role→(tag_name, point_id=tag_registry.id)。"""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.tag import TagRegistry

    async with AsyncSessionLocal() as db:
        loops = (
            (await db.execute(select(LoopLedger).where(LoopLedger.tag_name.like(f"{prefix}%"))))
            .scalars()
            .all()
        )
        loop_ids = [str(lp.id) for lp in loops]
        maps = (
            (await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids))))
            .scalars()
            .all()
        )
        tag_ids = list({str(m.tag_id) for m in maps})
        tags = (
            (await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids))))
            .scalars()
            .all()
        )
        name_map = {str(t.id): t.tag_name for t in tags}
        out: dict[str, dict[str, tuple[str, str]]] = {}
        for m in maps:
            lid = str(m.loop_id)
            out.setdefault(lid, {})[m.tag_role] = (name_map.get(str(m.tag_id)), str(m.tag_id))
    return out


def _num(v: float | int | None) -> str:
    """SQL 数值字面量（NaN/Inf→NULL，与导入侧拒写语义一致）。"""
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return "NULL"
    return repr(f)


def _td_rows(
    point_id: str, series: list[tuple[int, float, int, int]], ts_start: datetime
) -> list[str]:
    """序列 → TD VALUES 行（列序/公式与 data_import._write_points_bulk 一致）。"""
    from app.services.data_source.point_history_repository import (
        compute_payload_hash,
        format_ts_utc,
    )

    recv = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    rows = []
    for t_off, value, q_class, q_raw in series:
        ts = ts_start + timedelta(seconds=t_off)
        ts_ms = int(ts.replace(tzinfo=UTC).timestamp() * 1000)
        ph = compute_payload_hash(
            point_id=point_id, ts_ms=ts_ms, value=value, quality_class=q_class, quality_raw=q_raw
        )
        rows.append(
            f"('{format_ts_utc(ts)}', {_num(value)}, {_num(q_raw)}, {_num(q_class)}, 1, "
            f"'{recv}', 4, '{ph}')"
        )
    return rows


async def write_timeseries(
    role_points: dict[str, dict[str, tuple[str, str]]],
    loop_meta: dict[str, tuple[str, str, str]],
    ts_start: datetime,
) -> dict[str, int]:
    """全部回路时序直写（密集角色 PV/OP 全量、稀疏角色 COV 点）。"""
    from app.core.config import settings
    from app.core.tdengine_native import execute_native_effective
    from app.services.data_source.point_history_repository import POINT_STABLE, point_subtable

    db = settings.TDENGINE_DB
    written: dict[str, int] = {}
    total_rows = 0
    t0 = time.perf_counter()
    loop_items = sorted(loop_meta.items(), key=lambda kv: kv[1][0])  # 按位号排序
    for idx, (loop_id, (tag_name, composition, category)) in enumerate(loop_items, 1):
        i = int(tag_name.replace(PREFIX, ""))
        span = RANGES[LOOP_TYPES[(i - 1) % len(LOOP_TYPES)]][1]
        series = synth_series(i, composition, category, span)
        roles = role_points.get(loop_id, {})
        for role in ROLES:
            entry = roles.get(role)
            if not entry:
                continue
            _tname, point_id = entry
            rows = _td_rows(point_id, series[role], ts_start)
            if not rows:
                continue
            sub = point_subtable(point_id)
            src = f"synthetic:{PREFIX.lower()}"
            head = (
                f"INSERT INTO {db}.{sub} USING {POINT_STABLE} TAGS ('{point_id}', '{src}') VALUES"
            )
            for k in range(0, len(rows), TD_BATCH):
                chunk = rows[k : k + TD_BATCH]
                await execute_native_effective(" ".join([head] + chunk))
            written[role] = written.get(role, 0) + len(rows)
            total_rows += len(rows)
        if idx % 100 == 0:
            elapsed = time.perf_counter() - t0
            print(f"  [td] {idx}/{len(loop_items)} 回路完成，累计 {total_rows} 行，{elapsed:.0f}s")
    written["__total__"] = total_rows
    return written


# ---------------------------------------------------------------------------
# manifest + 合成声明
# ---------------------------------------------------------------------------


def build_manifest_rows(units: dict[str, str], scenario: str, ts_start: datetime) -> list[dict]:
    """P0-03 §1.2 manifest 行（每回路一条，字段全量）。"""
    rows = []
    expected_map = {
        "normal": "EVALUATED",
        "insufficient": "INCONCLUSIVE(DATA_INSUFFICIENT)",
        "manual": "EVALUATED(ELIGIBLE内终态; MODE=MANUAL, auto表现照常评估)",
        "gap": "INCONCLUSIVE(窗口覆盖不足——缺口段按 held/coverage 语义)",
    }
    for i in range(1, TOTAL_LOOPS + 1):
        tag = _loop_tag(i)
        area_idx = (i - 1) // (LOOPS_PER_UNIT * UNITS_PER_AREA)
        unit_idx = ((i - 1) // LOOPS_PER_UNIT) % UNITS_PER_AREA
        unit_name = _unit_name(area_idx, unit_idx)
        lt = LOOP_TYPES[(i - 1) % len(LOOP_TYPES)]
        composition = _loop_composition(scenario, i)
        rows.append(
            {
                "loopTag": tag,
                "unitName": unit_name,
                "unitNodeId": units.get(unit_name),
                "range": f"0-{RANGES[lt][1]} {RANGES[lt][2]}",
                "responseCategory": _response_category(i),
                "composition": composition,
                "loopType": lt,
                "controlType": CONTROL_TYPES[(i - 1) % len(CONTROL_TYPES)],
                "windowStart": ts_start.isoformat(),
                "windowEnd": (ts_start + timedelta(hours=1)).isoformat(),
                "pointsPerTag": {"PV": POINTS, "OP": POINTS, "SP": "COV_sparse", "MODE": 1},
                "scenario": scenario,
                "includeInEvaluation": True,
                "expectedStatus": expected_map[composition],
                "isActive": True,
                "ledgerStatus": "READY",
                "synthetic": True,
            }
        )
    return rows


def save_manifest(scenario: str, ts_start: datetime, rows: list[dict], extras: dict) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / f"manifest_{scenario}_{ts_start.strftime('%Y%m%d%H')}.json"
    payload = {
        "synthetic": True,
        "mark": SYNTHETIC_MARK,
        "scenario": scenario,
        "totalLoops": TOTAL_LOOPS,
        "tree": f"{PREFIX}-FACTORY/{AREAS}AREA/{AREAS * UNITS_PER_AREA}UNIT",
        "seed": "numpy default_rng(seed=loop_index)",
        "windowStart": ts_start.isoformat(),
        "channel": {
            "metadata": "API组态(plant-nodes + tags/import + loops/import)",
            "timeseries": "TDengine直写(st_point_data_v1, source_kind=4)",
        },
        "rows": rows,
        **extras,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    return path


async def write_declaration(scenario: str, ts_start: datetime, manifest_path: Path) -> None:
    """合成声明入库（sys_config upsert——声明性元数据，无既有通用 API，直写并自审计于值内）。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.db import AsyncSessionLocal
    from app.models.sys_config import SysConfig

    value = json.dumps(
        {
            "synthetic": True,
            "mark": SYNTHETIC_MARK,
            "prefix": PREFIX,
            "totalLoops": TOTAL_LOOPS,
            "scenario": scenario,
            "composition": SCENARIO_B if scenario == "b" else SCENARIO_A,
            "windowStart": ts_start.isoformat(),
            "seed": "numpy default_rng(seed=loop_index)",
            "manifestPath": str(manifest_path),
            "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "generatedAt": datetime.now(UTC).isoformat(),
            "redline": "创建后禁止停用/关参评（会静默缩容量分母）",
            "generatedBy": "backend/scripts/generate_capacity_dataset.py",
        },
        ensure_ascii=False,
    )
    async with AsyncSessionLocal() as db:
        stmt = (
            pg_insert(SysConfig)
            .values(key=DECLARATION_KEY, value=value, description=f"{SYNTHETIC_MARK} 声明")
            .on_conflict_do_update(index_elements=["key"], set_={"value": value})
        )
        await db.execute(stmt)
        await db.commit()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="P5 容量 1000 独立回路合成数据集生成器（DEC-08a 混合通道）"
    )
    p.add_argument("--plan", action="store_true", help="只打印计划，零写入")
    p.add_argument(
        "--execute", action="store_true", help="执行生成（PG/TD 大量写入——须先经主协调确认）"
    )
    p.add_argument("--self-check", action="store_true", help="准备就绪自检（只读）")
    p.add_argument("--scenario", choices=("a", "b"), default="b")
    p.add_argument(
        "--ts-start",
        default=None,
        help="冻结窗口起点（ISO 8601 naive UTC 整点）；缺省=当前时间往前第 2 个完整小时",
    )
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="admin123")
    return p.parse_args()


def _window(ts_start_arg: str | None) -> datetime:
    if ts_start_arg:
        dt = datetime.fromisoformat(ts_start_arg.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        dt = (now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    if dt.minute or dt.second or dt.microsecond:
        raise SystemExit(f"--ts-start 必须为整点小时（收到 {dt.isoformat()}）")
    return dt


async def _load_loop_meta(scenario: str) -> dict[str, tuple[str, str, str]]:
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger

    async with AsyncSessionLocal() as db:
        rows = (
            (await db.execute(select(LoopLedger).where(LoopLedger.tag_name.like(f"{PREFIX}%"))))
            .scalars()
            .all()
        )
    meta = {}
    for lp in rows:
        i = int(lp.tag_name.replace(PREFIX, ""))
        meta[str(lp.id)] = (lp.tag_name, _loop_composition(scenario, i), _response_category(i))
    return meta


async def run_execute(args: argparse.Namespace) -> int:
    ts_start = _window(args.ts_start)
    comp = SCENARIO_B if args.scenario == "b" else SCENARIO_A
    print(
        f"[execute] 场景 {args.scenario}（构成 {comp}），冻结窗口 {ts_start.isoformat()} naive UTC"
    )

    print("[meta-1] 装置树（API 组态，幂等复用）")
    api = ApiClient(args.base_url, args.username, args.password)
    units = ensure_tree(api)
    print(f"  units={len(units)}（预期 {AREAS * UNITS_PER_AREA}）")

    print("[meta-2] 测点导入（POST /tags/import，7000 行，含量程；≤5000 行/文件分批）")
    tag_rows = build_tag_rows()
    tag_chunk = 3500  # API upload_guard MAX_IMPORT_ROWS=5000，留余量
    for ci in range(0, len(tag_rows), tag_chunk):
        part = tag_rows[ci : ci + tag_chunk]
        tag_x = _xlsx(TAG_HEADERS, part)
        r = api.post_file("/tags/import", "file", f"perf_tags_{ci // tag_chunk + 1}.xlsx", tag_x)
        r.raise_for_status()
        print(f"  批 {ci // tag_chunk + 1}（{len(part)} 行）: {r.json()['data']}")

    print("[meta-3] 回路导入（POST /loops/import，1000 行，参评=是）")
    loop_x = _xlsx(LOOP_HEADERS, build_loop_rows())
    r = api.post_file("/loops/import", "file", "perf_loops.xlsx", loop_x)
    r.raise_for_status()
    print(f"  {r.json()['data']}")

    print("[ts] 加载 role→point 映射并直写 TDengine（st_point_data_v1）")
    role_points = await load_role_points(PREFIX)
    loop_meta = await _load_loop_meta(args.scenario)
    if len(loop_meta) != TOTAL_LOOPS:
        raise SystemExit(f"[ts] 断言失败：生成回路台账 {len(loop_meta)} ≠ {TOTAL_LOOPS}")
    written = await write_timeseries(role_points, loop_meta, ts_start)
    print(f"  [td] 写入行数分布: {written}")

    print("[manifest] 数据 manifest + 合成声明入库")
    mrows = build_manifest_rows(units, args.scenario, ts_start)
    path = save_manifest(args.scenario, ts_start, mrows, {"tdWritten": dict(written)})
    await write_declaration(args.scenario, ts_start, path)
    print(f"  manifest={path}")
    print(f"  declaration=sys_config:{DECLARATION_KEY}")
    print("[execute] 完成（生成器红线：后续禁止对这些回路做停用/关参评操作）")
    return 0


async def run_self_check(args: argparse.Namespace) -> int:
    """准备就绪自检（全部只读）：台账/绑定/三条件/TS 抽查/50 手动/声明在案。"""
    from sqlalchemy import func, select

    from app.core.db import AsyncSessionLocal
    from app.core.tdengine_native import execute_native
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.sys_config import SysConfig
    from app.models.tag import TagRegistry
    from app.services.data_source.point_history_repository import point_subtable

    ts_start = _window(args.ts_start)
    failures: list[str] = []

    async with AsyncSessionLocal() as db:
        loops = (
            (await db.execute(select(LoopLedger).where(LoopLedger.tag_name.like(f"{PREFIX}%"))))
            .scalars()
            .all()
        )
        by_tag = {lp.tag_name: lp for lp in loops}
        three_cond = [
            lp for lp in loops if lp.is_active and lp.status == "READY" and lp.include_in_evaluation
        ]
        loop_ids = [str(lp.id) for lp in loops]
        mappings = (
            (await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids))))
            .scalars()
            .all()
        )
        tag_count = await db.scalar(
            select(func.count())
            .select_from(TagRegistry)
            .where(TagRegistry.tag_name.like(f"{PREFIX}%"))
        )
        decl = (
            await db.execute(select(SysConfig).where(SysConfig.key == DECLARATION_KEY))
        ).scalar_one_or_none()

    print(f"[self-check] 台账回路数: {len(loops)}（预期 {TOTAL_LOOPS}）")
    if len(loops) != TOTAL_LOOPS:
        failures.append(f"台账 {len(loops)} ≠ {TOTAL_LOOPS}")
    print(f"[self-check] 三条件参评(is_active+READY+include_in_evaluation): {len(three_cond)}")
    if len(three_cond) != TOTAL_LOOPS:
        failures.append(f"三条件参评 {len(three_cond)} ≠ {TOTAL_LOOPS}（分母被静默缩小）")
    print(f"[self-check] 生成前缀测点数: {tag_count}（预期 {TOTAL_LOOPS * 7}）")
    if int(tag_count or 0) != TOTAL_LOOPS * 7:
        failures.append(f"测点 {tag_count} ≠ {TOTAL_LOOPS * 7}")
    roles_per_loop: dict[str, set[str]] = {}
    for m in mappings:
        roles_per_loop.setdefault(str(m.loop_id), set()).add(m.tag_role)
    full_bound = sum(1 for lid in loop_ids if len(roles_per_loop.get(lid, ())) >= 4)
    print(f"[self-check] ≥4 必填角色绑定的回路数: {full_bound}")
    if full_bound != TOTAL_LOOPS:
        failures.append(f"角色绑定完整回路 {full_bound} ≠ {TOTAL_LOOPS}")
    if decl is None:
        failures.append("合成声明不在案（sys_config 缺失）")
    else:
        print(f"[self-check] 合成声明在案: sys_config:{DECLARATION_KEY} ✓")

    # TD 抽查：每构成类别各抽 ≥2 回路（合计 ≥5 回路 TS 流量）
    role_map = await load_role_points(PREFIX)
    manual_loops = [
        i for i in range(1, TOTAL_LOOPS + 1) if _loop_composition(args.scenario, i) == "manual"
    ]
    checks = [1, 2, 801, 802, 901, 951, 952, 999, 1000] + manual_loops[:2]
    seen_tags: set[str] = set()
    for i in checks:
        tag = _loop_tag(i)
        loop = by_tag.get(tag)
        if loop is None or str(loop.id) not in role_map:
            continue
        seen_tags.add(tag)
        entry = role_map[str(loop.id)].get("PV")
        if not entry:
            failures.append(f"{tag} 无 PV 点映射")
            continue
        _tname, point_id = entry
        sql = (
            f"SELECT count(*) AS cnt FROM clpm_ts.{point_subtable(point_id)} "
            f"WHERE ts >= '{ts_start.isoformat()}'"
        )
        rows = await execute_native(sql)
        row = rows[0] if rows else {}
        cnt = row.get("cnt") or row.get("count(*)")
        composition = _loop_composition(args.scenario, i)
        expected_min = 900 if composition == "gap" else POINTS
        print(f"  [ts-check] {tag}({composition}) PV 子表行数={cnt}（预期 ≥{expected_min}）")
        try:
            if int(cnt or 0) < expected_min:
                failures.append(f"{tag} PV 行数 {cnt} < 预期 {expected_min}")
        except (TypeError, ValueError):
            failures.append(f"{tag} PV 行数不可读: {row}")
    if len(seen_tags) < 5:
        failures.append(f"TS 抽查回路数 {len(seen_tags)} < 5")

    # 50 手动终态构造核验：MODE 点值=0 且三条件成立（eligible 内终态）
    manual_ok = 0
    for i in manual_loops[:5]:
        tag = _loop_tag(i)
        loop = by_tag.get(tag)
        if loop is None:
            continue
        if not (loop.is_active and loop.status == "READY" and loop.include_in_evaluation):
            failures.append(f"{tag} 手动回路不满足三条件（eligible 内终态被破坏）")
            continue
        entry = role_map.get(str(loop.id), {}).get("MODE")
        if not entry:
            failures.append(f"{tag} 无 MODE 点映射")
            continue
        _tname, point_id = entry
        rows = await execute_native(
            f"SELECT LAST(`value`) AS v FROM clpm_ts.{point_subtable(point_id)} "
            f"WHERE ts >= '{ts_start.isoformat()}'"
        )
        row = rows[0] if rows else {}
        mode_first = row.get("v")
        if mode_first is None:
            mode_first = row.get("first(value)")
        if mode_first is not None and float(mode_first) == 0.0:
            manual_ok += 1
        else:
            failures.append(f"{tag} MODE 首值 {mode_first} ≠ 0 (MANUAL)")
    expected_manual = SCENARIO_B["manual"] if args.scenario == "b" else 0
    print(f"[self-check] 50 手动构造抽样: {manual_ok}/5 MODE=0 且 eligible 内")
    print(f"[self-check] 手动回路总数: {len(manual_loops)}（预期 {expected_manual}）")
    if len(manual_loops) != expected_manual:
        failures.append(f"手动回路 {len(manual_loops)} ≠ {expected_manual}")

    if failures:
        print("[self-check] 失败项：")
        for f_ in failures:
            print(f"  x {f_}")
        return 1
    print("[self-check] 准备就绪 ✓（正式五批测量归后续批，本批不执行）")
    return 0


def main() -> int:
    args = _args()
    if args.plan:
        ts_start = _window(args.ts_start)
        comp = SCENARIO_B if args.scenario == "b" else SCENARIO_A
        est_rows = TOTAL_LOOPS * (POINTS * 2 + 10)
        print("[plan] P5 容量数据集生成计划（本命令零写入）：")
        print(
            f"  前缀: {PREFIX}（测试装置树 {PREFIX}-FACTORY/AREA×{AREAS}"
            f"/UNIT×{AREAS * UNITS_PER_AREA}）"
        )
        print(f"  场景: {args.scenario} 构成 {comp}；窗口 {ts_start.isoformat()} naive UTC")
        print("  PG 写入（经 API 组态）: 装置树 26 节点 + 7000 测点 + 1000 回路 + 7000 绑定")
        print(
            f"  TD 写入（直写 st_point_data_v1）: ~{est_rows} 行"
            "（PV/OP 密集 3600×2/回路 + 稀疏角色）"
        )
        print(f"  合成声明: sys_config:{DECLARATION_KEY} + manifest → {MANIFEST_DIR}")
        print("  红线: 生成后禁停用/关参评；全部产物标注 synthetic")
        return 0
    if args.execute:
        return asyncio.run(run_execute(args))
    if args.self_check:
        return asyncio.run(run_self_check(args))
    raise SystemExit("须指定 --plan / --execute / --self-check 之一")


if __name__ == "__main__":
    raise SystemExit(main())
