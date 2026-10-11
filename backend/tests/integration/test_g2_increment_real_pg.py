"""G2 增量批真实 PG 集成测试：发布 v1→任务 A→发布 v2→任务 B→旧包重放（C12-EA1/G2-INC-a 真实链）.

与 mock 单测（tests/test_g2_increment.py）的区别：本文件在**真实 PostgreSQL
scratch 库**上走完整链路——

1. 真实 ``publish_override``（LOOP 层 stability_rate decay_ratio=0.07，乐观锁
   + 审计 + revision 推进）→ 真实 ``_pin_task_config`` 固定完整五层快照 →
   真实 ``_calculate_loop_kpi``（真实 DataPlanner→共享 TDengine 只读取数，
   写入 scratch）落包；
2. 断言包 manifest ``source.effectiveParams`` 为五层完整展开值（v1 值 +
   sources=LOOP + configRevision）；
3. 发布 v2（decay_ratio=0.15）→ 任务 B 同窗重算取 v2；
4. v2 时代重放 v1 旧包：按包内 v1 参数固定计算，identical=True，
   paramsSource=package（当前缓存/DB 已是 v2，不污染旧包重放）。

安全边界：**必须显式提供 ``TEST_DATABASE_URL``**（一次性 scratch 库，
01_schema.sql 新装）；dev 库（DATABASE_URL）全程只读（仅复制回路/tag 配置行
与选取有 TDengine 数据的真实窗口）。TDengine 只读共享（计算类历史数据查询
恒走本地 TDengine 的既定口径）。

运行方式（验收时）::

    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_g2inc_scratch \
        uv run pytest tests/integration/test_g2_increment_real_pg.py -v -m integration

CI 跳过：pyproject addopts ``-m "not integration"``。
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.exceptions import BizError
from app.services import calc_snapshot as cs
from app.services import config_publish as cp

# ---------------------------------------------------------------------------
# 环境门（scratch PG 必须显式指定；dev PG 从 .env 读，只读）
# ---------------------------------------------------------------------------


def _test_database_url() -> str:
    return os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _test_database_url() or not _pg_reachable(17102),
        reason="未显式提供 TEST_DATABASE_URL（scratch PG）",
    ),
    pytest.mark.timeout(600),
]

_LOOP_ID = "8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5"


def _dev_database_url() -> str:
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return ""
    m = re.search(r"POSTGRES_PASSWORD=(.*)", env.read_text(encoding="utf-8"))
    if not m:
        return ""
    pw = m.group(1).strip()
    return f"postgresql+asyncpg://clpm:{pw}@localhost:17102/clpm"


async def _copy_rows(src_conn, dst: AsyncSession, table: str, where: str, params: dict) -> int:
    """dev→scratch 动态行复制（select * → insert 同名列；on conflict id 跳过）."""
    rows = (
        (await src_conn.execute(text(f"select * from {table} where {where}"), params))
        .mappings()
        .all()
    )
    import json as _json

    for row in rows:
        cols = list(row.keys())
        # plant_node.parent_id 自引用 FK：置空避免父节点级联复制
        if table == "plant_node":
            row = dict(row, parent_id=None)
        values = {
            k: _json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v
            for k, v in row.items()
        }
        await dst.execute(
            text(
                f"insert into {table} ({', '.join(cols)}) "
                f"values ({', '.join(f':{c}' for c in cols)}) on conflict (id) do nothing"
            ),
            values,
        )
    return len(rows)


async def _seed_loop_from_dev(scratch: AsyncSession) -> tuple[str, str, str]:
    """从 dev 库只读复制回路/node/tag 配置行进 scratch；返回 (窗口起, 窗口止, loop_id).

    选取该回路最近一次 SUCCESS 小时窗（证明共享 TDengine 在该窗有真实数据）。
    """
    dev_url = _dev_database_url()
    assert dev_url, "backend/.env 缺 POSTGRES_PASSWORD（dev 库只读选择窗口需要）"
    dev = create_async_engine(dev_url)
    try:
        async with dev.connect() as c:
            win = (
                await c.execute(
                    text(
                        "select ts_start, ts_end from kpi_snapshot_hourly "
                        "where loop_id=:l and status='SUCCESS' "
                        "order by ts_end desc limit 1"
                    ),
                    {"l": _LOOP_ID},
                )
            ).first()
            assert win is not None, "dev 库无该回路 SUCCESS 窗口（选真实窗口失败）"
            unit_id = (
                await c.execute(
                    text("select unit_id from loop_ledger where id=:l"), {"l": _LOOP_ID}
                )
            ).scalar_one_or_none()
            if unit_id:
                await _copy_rows(c, scratch, "plant_node", "id=:u", {"u": unit_id})
            # metric_config + clpm_metric_data_requirement：指标配置与
            # DataPlanner 数据需求契约来源（全量复制，小字典表）
            await _copy_rows(c, scratch, "metric_config", "true", {})
            await _copy_rows(c, scratch, "clpm_metric_data_requirement", "true", {})
            await _copy_rows(
                c,
                scratch,
                "tag_registry",
                "id in (select tag_id from loop_tag_mapping where loop_id=:l)",
                {"l": _LOOP_ID},
            )
            await _copy_rows(c, scratch, "loop_ledger", "id=:l", {"l": _LOOP_ID})
            await _copy_rows(
                c,
                scratch,
                "loop_tag_mapping",
                "loop_id=:l",
                {"l": _LOOP_ID},
            )
    finally:
        await dev.dispose()
    await scratch.commit()
    return win[0], win[1], _LOOP_ID


async def _run_kpi_task_with_pin(scratch: AsyncSession, loop_id: str, ts_start, ts_end) -> dict:
    """真实 KPI 任务链：pin 完整快照 → 真实取数计算 → 落包 → 落账本."""
    from sqlalchemy import select

    from app.models.loop import LoopLedger
    from app.tasks.kpi_calc import _build_data_planner, _calculate_loop_kpi, _pin_task_config

    loop = (await scratch.execute(select(LoopLedger).where(LoopLedger.id == loop_id))).scalar_one()
    pinned_config = await _pin_task_config(scratch)
    assert pinned_config is not None, "真实链任务边界必须固定成功（DB 可用）"
    data_planner = _build_data_planner(scratch)
    result = await _calculate_loop_kpi(
        db=scratch,
        loop=loop,
        metric_configs={},
        ts_start=ts_start,
        ts_end=ts_end,
        data_planner=data_planner,
        source="G2INC_REAL",
        logical_run_id=str(uuid4()),  # 结果账本 logical_run_id 为 UUID 列
        save_input_snapshot=True,
        pinned_config=pinned_config,
    )
    await scratch.commit()
    return result


async def _record_by_snapshot(scratch: AsyncSession, snapshot_id: str):
    from sqlalchemy import select

    from app.models.calculation_result import CalculationResultRecord

    return (
        await scratch.execute(
            select(CalculationResultRecord).where(
                CalculationResultRecord.dataset_snapshot_id == snapshot_id
            )
        )
    ).scalar_one()


class TestG2IncRealPublishTaskReplay:
    async def test_publish_v1_task_a_publish_v2_task_b_replay_v1(self, tmp_path, monkeypatch):
        """C12-EA1/G2-INC-a 真实链：v1 包五层完整旧参数固定；v2 后旧包重放不污染."""
        monkeypatch.setattr(
            "app.core.config.settings.CLPM_CALC_SNAPSHOT_DIR", str(tmp_path / "g2-real-snaps")
        )
        engine = create_async_engine(_test_database_url())
        try:
            async with AsyncSession(engine) as scratch:
                ts_start, ts_end, loop_id = await _seed_loop_from_dev(scratch)

                # --- v1 发布（真实 publish_override：审计+乐观锁+revision 推进）---
                rev0 = await cp.get_current_revision(scratch)
                await cp.publish_override(
                    scratch,
                    layer="LOOP",
                    scope_id=loop_id,
                    metric_code="stability_rate",
                    params={"decay_ratio": 0.07},
                    expected_revision=rev0,
                    reason="G2增量批真实链 v1",
                    operator="g2inc-tester",
                )
                # --- 任务 A：pin 完整快照 → 真实取数计算 → 落包 ---
                # 真实数据窗（共享 TDengine 只读）：composite 状态取决于该窗
                # 数据质量（gap 守卫可能使 fast_rate/settling_time INCONCLUSIVE），
                # 本链断言的是参数固定与包/账本完整落盘，不限定 composite 状态
                await _run_kpi_task_with_pin(scratch, loop_id, ts_start, ts_end)
                snap_a = (
                    await scratch.execute(
                        text(
                            "select id, input_hash from calculation_dataset_snapshot "
                            "order by created_at desc limit 1"
                        )
                    )
                ).first()
                assert snap_a is not None, "任务 A 应保存输入包"
                manifest_a, _arrays = _read_package(tmp_path / "g2-real-snaps", str(snap_a[0]))
                eff = manifest_a["source"]["effectiveParams"]
                assert eff["status"] == "PINNED"
                assert eff["configRevision"] == rev0 + 1
                assert eff["scope"]["loopId"] == loop_id
                ct = manifest_a["replayContext"]["controlType"]
                # 五层展开值：LOOP 层 0.07 遮盖 DEFAULT 0.05（覆盖所有响应类别）
                for key, params in eff["params"].items():
                    if key.startswith("stability_rate|"):
                        assert params["decay_ratio"] == 0.07, key
                        assert eff["sources"][key]["decay_ratio"] == "LOOP"
                assert ct  # controlType 记录在包内（重放上下文完整）

                # --- v2 发布（同层改值）→ 任务 B 取 v2 ---
                await cp.publish_override(
                    scratch,
                    layer="LOOP",
                    scope_id=loop_id,
                    metric_code="stability_rate",
                    params={"decay_ratio": 0.15},
                    expected_revision=rev0 + 1,
                    reason="G2增量批真实链 v2",
                    operator="g2inc-tester",
                )
                await _run_kpi_task_with_pin(scratch, loop_id, ts_start, ts_end)
                snap_b = (
                    await scratch.execute(
                        text(
                            "select id, input_hash from calculation_dataset_snapshot "
                            "order by created_at desc limit 1"
                        )
                    )
                ).first()
                manifest_b, _ = _read_package(tmp_path / "g2-real-snaps", str(snap_b[0]))
                eff_b = manifest_b["source"]["effectiveParams"]
                assert eff_b["configRevision"] == rev0 + 2
                assert eff_b["params"]["stability_rate|STABLE"]["decay_ratio"] == 0.15
                # 参数属输入身份：v1/v2 包 inputHash 不同
                assert snap_a[1] != snap_b[1]

                # --- v2 时代重放 v1 旧包：包内 v1 参数固定，identical ---
                record_a = await _record_by_snapshot(scratch, str(snap_a[0]))
                assert record_a is not None and record_a.dataset_snapshot_id
                report = await cs.replay_result_record(scratch, str(record_a.id))
                assert report["paramsSource"] == "package"
                assert report["executionMode"] == "in-process"
                assert report["identical"] is True, f"重放差异: {report['differences']}"
                # A/B 稳定率各自有效且不同（0.07 vs 0.15 真实生效）
                detail_a = (record_a.payload or {}).get("metricsDetail") or {}
                va = (detail_a.get("steady_rate") or {}).get("value")
                record_b = await _record_by_snapshot(scratch, str(snap_b[0]))
                detail_b = (record_b.payload or {}).get("metricsDetail") or {}
                vb = (detail_b.get("steady_rate") or {}).get("value")
                assert va is not None, f"任务 A 稳定率应为有效值（数据窗 {ts_start}）"
                if vb is not None:
                    assert va != vb, f"A(v1 decay=0.07)={va} 应不同于 B(v2 decay=0.15)={vb}"

                # --- 拒绝分支真实链：删除包文件 → 重放显式拒绝（非静默）---
                paths = cs.snapshot_file_paths(str(snap_a[0]))
                for p in paths:
                    p.unlink(missing_ok=True)
                with pytest.raises(BizError) as ei:
                    await cs.replay_result_record(scratch, str(record_a.id))
                assert ei.value.code == cs.ERR_SNAPSHOT_FILES_MISSING
        finally:
            await engine.dispose()


def _read_package(root: Path, snapshot_id: str):
    paths = cs.snapshot_file_paths(snapshot_id)
    npz = next(p for p in paths if p.suffix == ".npz")
    man = next(p for p in paths if p.name.endswith(".manifest.json"))
    return cs._read_files(npz, man)
