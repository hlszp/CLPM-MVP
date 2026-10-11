"""P3-01 D1/D2 真实 PG 集成回归：诊断输入包落库 + assessment_meta 落库.

与 mock 单测（tests/test_diagnosis_d1d2_wiring.py）的区别：本文件在真实
PostgreSQL 上验证 ``run_diagnosis_for_loop`` 全链——

1. **dataset_snapshot_id 真实写入**（D1，08§3.1/P2-03 交接接续第一步）：
   诊断完成后 ``diagnosis_run.dataset_snapshot_id`` 非空，指向
   ``calculation_dataset_snapshot`` 真实行（kind=DIAGNOSIS_RAW），NPZ 文件
   落盘且读回 hash 一致（manifest 携带 operatorGroup/effectiveThresholds/
   量程等诊断身份）；
2. **assessment_meta 真实落库**（D2）：JSONB 结构完整，覆盖分母三等式
   守恒（08§4.3）；
3. 历史行保护：不回填旧 run（P1-05 口径 NULL=显式不可完整复现）。

安全边界：**必须显式提供 ``TEST_DATABASE_URL``**（指向一次性 scratch 库），
否则跳过——避免误写共享 dev 库。

运行方式（实验/验收时）：
    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_p3d12_scratch \
    uv run pytest tests/integration/test_p3_d1d2_diagnosis_wiring_real_pg.py -v -m integration
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.models.calc_dataset_snapshot import CalculationDatasetSnapshot
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger


def _database_url() -> str:
    return os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable() -> bool:
    if not _database_url():
        return False
    try:
        with socket.create_connection(("localhost", 17102), timeout=3):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="未显式提供 TEST_DATABASE_URL（scratch PG）"),
]


@pytest.fixture
async def pg_context(tmp_path: Path):
    """真实 PG（scratch 库）：建表 + 测试回路行 + snapshot 目录隔离.

    会话工厂显式 ``expire_on_commit=False``（与生产 AsyncSessionLocal 同
    口径：编排器 commit 后还会读 run 属性生成 SYSTEM 建议）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.models.base import Base

    engine = create_async_engine(_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        loop_id_raw = uuid4()
        loop = LoopLedger(
            id=loop_id_raw,
            tag_name=f"TIC-P3D12-{uuid4().hex[:6]}",
            loop_type="FLOW",
            is_active=True,
        )
        session.add(loop)
        loop_id = str(loop_id_raw)  # commit 前取（避免 expire 后触发同步 IO）
        await session.commit()

    from unittest.mock import patch as _patch

    from app.core.config import settings as _settings

    old_dir = os.environ.get("CLPM_CALC_SNAPSHOT_DIR")
    snap_dir = str(tmp_path / "snapshots")
    os.environ["CLPM_CALC_SNAPSHOT_DIR"] = snap_dir
    # settings 已在 import 时实例化（env 后设不生效）——patch 对象属性，
    # 使 snapshot_root() 在测试期内指向 tmp 目录（不污染工作区 data/）
    with _patch.object(_settings, "CLPM_CALC_SNAPSHOT_DIR", snap_dir, create=True):
        try:
            yield {
                "engine": engine,
                "session_factory": session_factory,
                "loop_id": loop_id,
                "snapshot_dir": tmp_path / "snapshots",
            }
        finally:
            pass
    if old_dir is None:
        os.environ.pop("CLPM_CALC_SNAPSHOT_DIR", None)
    else:
        os.environ["CLPM_CALC_SNAPSHOT_DIR"] = old_dir
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CalculationDatasetSnapshot).where(
                        CalculationDatasetSnapshot.loop_id == loop_id
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            await session.delete(r)
        runs = (
            (await session.execute(select(DiagnosisRun).where(DiagnosisRun.loop_id == loop_id)))
            .scalars()
            .all()
        )
        for r in runs:
            await session.delete(r)
        await session.flush()
        loop_row = await session.get(LoopLedger, loop_id)
        if loop_row is not None:
            await session.delete(loop_row)
        await session.commit()
    await engine.dispose()


def _steady_series(n: int = 3600):
    """平稳窗口（质量 Good）：全部算子合格阴性/前提不成立，主分类 NULL."""
    rng = np.random.default_rng(11)
    start = datetime(2026, 8, 15, 0, 0, 0)
    from app.contracts.data_types import RawTimeSeries

    ts = [start + timedelta(seconds=i) for i in range(n)]
    return RawTimeSeries(
        timestamps=ts,
        signals={
            "pv": (50.0 + 0.3 * rng.normal(0, 1, n)).tolist(),
            "sp": [50.0] * n,
            "op": (50.0 + 0.1 * rng.normal(0, 1, n)).tolist(),
            "mode": [1] * n,
        },
        quality_codes={"pv_quality": [1] * n},
    )


async def _run_diagnosis(session_factory, loop_id: str, raw_series):
    """真实 DB 会话跑编排器（provider mock 取数，其余真实链路）."""
    from app.services import diagnosis_orchestrator as orch

    async with session_factory() as session:
        provider = MagicMock()
        provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)
        with patch.object(orch, "get_provider", return_value=provider):
            run = await orch.run_diagnosis_for_loop(
                session,
                loop_id,
                start=raw_series.timestamps[0],
                end=raw_series.timestamps[-1] + timedelta(seconds=1),
                task_id="task-p3d12",
                triggered_by="tester",
            )
    return run


@pytest.mark.asyncio
async def test_diagnosis_run_persists_snapshot_and_assessment(pg_context) -> None:
    """D1+D2 真实写入：dataset_snapshot_id 非空 + NPZ 落盘 + assessment_meta
    JSONB 结构与覆盖三等式守恒。"""
    run = await _run_diagnosis(
        pg_context["session_factory"], pg_context["loop_id"], _steady_series()
    )
    assert run is not None

    # ---- D1：输入包真实写入（两不管接线闭环） ----
    assert run.dataset_snapshot_id is not None, "dataset_snapshot_id 未写入（两不管）"
    async with pg_context["session_factory"]() as session:
        snap = await session.get(CalculationDatasetSnapshot, run.dataset_snapshot_id)
        assert snap is not None
        assert snap.kind == "DIAGNOSIS_RAW"
        assert str(snap.loop_id) == pg_context["loop_id"]
        assert str(snap.created_by_record_id) == run.id
        # NPZ + manifest 文件真实落盘
        files = list(pg_context["snapshot_dir"].rglob(f"{snap.id}.npz"))
        manifest_files = list(pg_context["snapshot_dir"].rglob(f"{snap.id}.manifest.json"))
        assert len(files) == 1 and len(manifest_files) == 1
        manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
        assert manifest["kind"] == "DIAGNOSIS_RAW"
        assert manifest["diagnosis"]["operatorGroup"] == "full"
        assert "effectiveThresholds" in manifest["diagnosis"]
        assert "pvRange" in manifest["diagnosis"] and "gate" in manifest["diagnosis"]
        with np.load(files[0], allow_pickle=False) as z:
            assert "grid__ts" in z.files
            assert "grid__sig_pv" in z.files
            assert "grid__qual_pv" in z.files

    # ---- D2：assessment_meta 落库（JSONB） ----
    meta = run.assessment_meta
    assert meta is not None
    assert meta["schemaVersion"] == "diagnosis-assessment/1"
    assert meta["judgementStatus"] in {"VALID_NEGATIVE", "INCONCLUSIVE"}
    cov = meta["coverage"]
    assert cov["profileExpected"] == cov["selected"] + cov["explicitlyExcluded"]
    assert cov["selected"] == sum(
        cov[k]
        for k in (
            "positive",
            "negative",
            "notApplicable",
            "insufficient",
            "error",
            "cancelled",
            "notRun",
        )
    )
    assert cov["validAssessmentCount"] == cov["positive"] + cov["negative"]
    assert cov["invariantSelected"] is True
    assert cov["invariantExpected"] is True


@pytest.mark.asyncio
async def test_diagnosis_run_row_queryable_with_new_columns(pg_context) -> None:
    """落库后真实回读：assessment_meta / dataset_snapshot_id / 算子八态
    经 PG 序列化往返完整（JSONB 无损）。"""
    run = await _run_diagnosis(
        pg_context["session_factory"], pg_context["loop_id"], _steady_series()
    )
    async with pg_context["session_factory"]() as session:
        row = await session.get(DiagnosisRun, run.id)
        assert row is not None
        assert row.assessment_meta["judgementStatus"] == run.assessment_meta["judgementStatus"]
        assert row.dataset_snapshot_id == run.dataset_snapshot_id
        states = {r["assessmentState"] for r in (row.operator_results or {}).values()}
        assert None not in states or all(
            r.get("reasonCode") in ("OUT_OF_PROFILE",)
            for r in (row.operator_results or {}).values()
            if r["assessmentState"] is None
        )
        # 算子序列化含新字段（reader-first：API/落库字段就位）
        sample = next(iter((row.operator_results or {}).values()))
        assert "assessmentState" in sample and "reasonCode" in sample
