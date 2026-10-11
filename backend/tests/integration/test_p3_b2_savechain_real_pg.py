"""P3-B2 W4/W5 真实 PG 集成回归：最终选定方案保存链冻结语义（INT-01 尾部）.

与 mock 单测（tests/test_p3_b2_savechain.py）的区别：本文件在真实
PostgreSQL 上验证 ``create_tuning_task`` W4/W5 全链——

1. 360 选定方案保存→真实落库：来源/模型版本/参数/窗口/currentPid 三元组
   服务端权威派生并冻结；
2. **修改当前配置后再读取不漂移**（G2-INC-a 同源冻结语义）：改 PID tag
   实时值 + 产生新辨识记录后，已保存方案的回读值一字不变；
3. currentPid 双表示端到端：PB=50/0.75min → Kp=2/Ti=45s（B1
   pid_conversion 工程事实驱动入口，DcsModel+DcsPidStructure 真实行）。

安全边界：**必须显式提供 ``TEST_DATABASE_URL``**（指向一次性 scratch 库），
否则跳过——避免误写共享 dev 库。

运行方式（实验/验收时）：
    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_p3b2_scratch \
        uv run pytest tests/integration/test_p3_b2_savechain_real_pg.py -v -m integration

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import os
import socket
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.dcs_model import DcsModel
from app.models.dcs_pid_structure import (
    P_TYPE_PROPORTION_BAND,
    PID_REPRESENTATION_KNOWN,
    UNIT_MINUTES,
    UNIT_SECONDS,
    DcsPidStructure,
)
from app.models.dcs_vendor import DcsVendor
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.models.tuning import TuningRecord


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

_TEST_TAG_PREFIX = f"TIC-P3B2-{uuid4().hex[:8]}"

_MODEL_PARAMS = {"K": 1.2, "tau": 30.0, "theta": 5.0}


@pytest.fixture
async def pg_context():
    """真实 PG（scratch 库）：建表 + 测试行上下文（零残留）."""
    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.models.base import Base

    engine = create_async_engine(_database_url())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 与生产 AsyncSessionLocal 同口径（create_tuning_task 在 commit 后读取 loop/record 属性）
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        yield session, engine
    finally:
        await session.close()
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    f"DELETE FROM tuning_record WHERE loop_id IN "
                    f"(SELECT id FROM loop_ledger WHERE tag_name LIKE '{_TEST_TAG_PREFIX}%')"
                )
            )
            await conn.execute(
                text(
                    f"DELETE FROM loop_tag_mapping WHERE loop_id IN "
                    f"(SELECT id FROM loop_ledger WHERE tag_name LIKE '{_TEST_TAG_PREFIX}%')"
                )
            )
            await conn.execute(
                text(f"DELETE FROM loop_ledger WHERE tag_name LIKE '{_TEST_TAG_PREFIX}%'")
            )
            await conn.execute(
                text(f"DELETE FROM tag_registry WHERE tag_name LIKE '{_TEST_TAG_PREFIX}%'")
            )
            await conn.execute(
                text(
                    "DELETE FROM dcs_pid_structure WHERE dcs_model_id IN "
                    "(SELECT id FROM dcs_model WHERE code LIKE 'TEST-P3B2-%')"
                )
            )
            await conn.execute(text("DELETE FROM dcs_model WHERE code LIKE 'TEST-P3B2-%'"))
            await conn.execute(text("DELETE FROM dcs_vendor WHERE code LIKE 'TEST-P3B2-%'"))
            await conn.commit()
        await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[str, str]:
    """播种：DCS(PB/分钟) + 回路 + PID/PV/OP tags + 来源辨识记录.

    Returns:
        (loop_id, source_record_id)
    """
    vendor = DcsVendor(code=f"TEST-P3B2-V-{uuid4().hex[:6]}", name="P3B2 测试厂商")
    session.add(vendor)
    await session.flush()

    model = DcsModel(
        vendor_id=str(vendor.id),
        code=f"TEST-P3B2-M-{uuid4().hex[:6]}",
        name="P3B2 测试型号（PB/分钟）",
    )
    session.add(model)
    await session.flush()

    session.add(
        DcsPidStructure(
            dcs_model_id=str(model.id),
            p_type=P_TYPE_PROPORTION_BAND,
            i_unit=UNIT_MINUTES,
            d_unit=UNIT_SECONDS,
            representation_status=PID_REPRESENTATION_KNOWN,
        )
    )

    loop = LoopLedger(
        tag_name=f"{_TEST_TAG_PREFIX}-LOOP",
        description="P3B2 保存链冻结语义测试回路",
        dcs_model_id=str(model.id),
        is_active=True,
    )
    session.add(loop)
    await session.flush()

    def _tag(role: str, value: float, rng: tuple[float, float] | None) -> TagRegistry:
        return TagRegistry(
            tag_name=f"{_TEST_TAG_PREFIX}-{role}",
            tag_type=role if role in ("PV", "OP") else "OTHER",
            current_value=value,
            last_sync_at=datetime(2026, 10, 11, 0, 0, 0),
            range_min=rng[0] if rng else None,
            range_max=rng[1] if rng else None,
        )

    pv_tag = _tag("PV", 100.0, (0.0, 200.0))
    op_tag = _tag("OP", 50.0, (0.0, 100.0))
    p_tag = _tag("PID_P", 50.0, None)  # PB=50 → Kp=2
    i_tag = _tag("PID_I", 0.75, None)  # 0.75min → Ti=45s
    d_tag = _tag("PID_D", 0.0, None)
    for t in (pv_tag, op_tag, p_tag, i_tag, d_tag):
        session.add(t)
    await session.flush()

    for role, t in (
        ("PV", pv_tag),
        ("OP", op_tag),
        ("PID_P", p_tag),
        ("PID_I", i_tag),
        ("PID_D", d_tag),
    ):
        session.add(
            LoopTagMapping(
                loop_id=str(loop.id),
                tag_id=str(t.id),
                tag_role=role,
                is_required=role in ("PV", "OP"),
            )
        )

    source = TuningRecord(
        id=str(uuid4()),
        loop_id=str(loop.id),
        model_type="FOPDT",
        model_params=dict(_MODEL_PARAMS),
        algorithm="IDENTIFICATION_ONLY",
        status="IDENTIFIED",
        created_by="p3b2-test",
        identify_method="HISTORICAL_ARX",
        data_source="HISTORY",
        confidence_level="A",
        confidence_reason="拟合与残差检验通过",
        fitting_score=Decimal("92.50"),
        task_id="celery-p3b2-source",
        time_window_start=datetime(2026, 10, 1, 0, 0, 0),
        time_window_end=datetime(2026, 10, 1, 2, 0, 0),
    )
    session.add(source)
    await session.commit()
    return str(loop.id), str(source.id)


_SIM_EVIDENCE = {
    "candidateResponses": [
        {"label": "IMC", "response": {"ts": [0.0, 1.0], "pv": [0.0, 0.5]}, "metrics": {}}
    ]
}


class TestSaveChainFreezeRealPg:
    """保存→落库→改当前配置→回读不漂移（INT-01 尾部，真实 PG）."""

    @pytest.mark.asyncio
    async def test_save_readback_no_drift_after_config_change(
        self, pg_context, monkeypatch
    ) -> None:
        import app.services.data_source.realtime_subscriber as rs

        def _raise():
            raise RuntimeError("no redis in integration test")

        monkeypatch.setattr(rs, "get_subscriber", _raise)

        from app.services.tuning import create_tuning_task, get_tuning_task_detail

        session, _ = pg_context
        loop_id, source_id = await _seed(session)

        # ── ① 保存（360 最终选定方案，W4 新协议）──
        saved = await create_tuning_task(
            db=session,
            loop_id=loop_id,
            model_type="FOPDT",
            model_params=dict(_MODEL_PARAMS),
            algorithm="IMC",
            recommended_pid={"kp": 1.5, "ti": 20.0, "td": 0.0},
            model_source="IDENTIFICATION_RECORD",
            source_record_id=source_id,
            risk_confirmed=True,
            simulation_result=_SIM_EVIDENCE,
            status="SIMULATED",
            created_by="p3b2-test",
        )
        task_id = saved["id"]

        # 来源/模型/窗口/元数据：服务端权威派生
        assert saved["sourceRecordId"] == source_id
        assert saved["modelSource"] == "IDENTIFICATION_RECORD"
        assert saved["modelParams"] == _MODEL_PARAMS
        assert saved["identifyMethod"] == "HISTORICAL_ARX"
        assert saved["confidenceLevel"] == "A"
        assert float(saved["fittingScore"]) == 92.5
        assert saved["timeWindowStart"] == "2026-10-01T00:00:00"
        assert saved["timeWindowEnd"] == "2026-10-01T02:00:00"

        # W5：currentPid 三元组（PB=50→Kp=2；0.75min→Ti=45s），标准形式冻结
        assert saved["currentPid"] == {"kp": 2.0, "ti": 45.0, "td": 0.0}
        ctx = saved["currentPidContext"]
        assert ctx["status"] == "KNOWN"
        assert ctx["raw"] == {"p": 50.0, "i": 0.75, "d": 0.0}
        assert ctx["conversion"]["pType"] == "PROPORTION_BAND"
        assert ctx["conversion"]["iUnit"] == "MINUTES"

        # W5：K 单位域冻结（归一化 K=1.2 → 工程域 ×2.0 = 2.4）
        assert saved["unitBasis"]["basis"] == "NORMALIZED_0_100"
        assert saved["unitBasis"]["kConverted"] == pytest.approx(2.4)

        # ── ② 回读（服务端权威，非客户端回显）──
        first = await get_tuning_task_detail(session, task_id)

        # ── ③ 修改当前配置：PID tag 实时值全变 + 新辨识记录（新参数/新窗口）──
        await session.execute(
            text(
                f"UPDATE tag_registry SET current_value = 25.0 "
                f"WHERE tag_name = '{_TEST_TAG_PREFIX}-PID_P'"
            )
        )
        await session.execute(
            text(
                f"UPDATE tag_registry SET current_value = 2.0 "
                f"WHERE tag_name = '{_TEST_TAG_PREFIX}-PID_I'"
            )
        )
        new_source = TuningRecord(
            id=str(uuid4()),
            loop_id=loop_id,
            model_type="FOPDT",
            model_params={"K": 0.6, "tau": 55.0, "theta": 12.0},
            algorithm="IDENTIFICATION_ONLY",
            status="IDENTIFIED",
            created_by="p3b2-test",
            identify_method="HISTORICAL_ARX",
            data_source="HISTORY",
            confidence_level="A",
            confidence_reason="二次辨识",
            fitting_score=Decimal("88.00"),
            task_id="celery-p3b2-source-2",
            time_window_start=datetime(2026, 10, 9, 0, 0, 0),
            time_window_end=datetime(2026, 10, 9, 4, 0, 0),
        )
        session.add(new_source)
        await session.commit()

        # ── ④ 再读取：已保存方案的全部冻结字段不漂移 ──
        second = await get_tuning_task_detail(session, task_id)
        for field in (
            "modelParams",
            "sourceRecordId",
            "modelSource",
            "identifyMethod",
            "confidenceLevel",
            "fittingScore",
            "timeWindowStart",
            "timeWindowEnd",
            "currentPid",
            "rollbackPid",
            "recommendedPid",
            "unitBasis",
            "currentPidContext",
            "dcsTemplateRevision",
        ):
            assert second[field] == first[field], f"冻结字段 {field} 在配置变更后漂移"

        # currentPid 仍为保存时刻的标准形式（PB=50→Kp=2），不随 tag 值变化
        assert second["currentPid"] == {"kp": 2.0, "ti": 45.0, "td": 0.0}

        # ── ⑤ 新来源再存一条：各记录独立冻结（新记录反映新实时值 PB=25→Kp=4）──
        saved2 = await create_tuning_task(
            db=session,
            loop_id=loop_id,
            model_type="FOPDT",
            model_params={"K": 0.6, "tau": 55.0, "theta": 12.0},
            algorithm="SIMC",
            recommended_pid={"kp": 1.0, "ti": 30.0, "td": 0.0},
            model_source="IDENTIFICATION_RECORD",
            source_record_id=str(new_source.id),
            risk_confirmed=True,
            simulation_result=_SIM_EVIDENCE,
            created_by="p3b2-test",
        )
        assert saved2["currentPid"] == {"kp": 4.0, "ti": 120.0, "td": 0.0}
        assert saved2["timeWindowStart"] == "2026-10-09T00:00:00"
        # 旧记录不受影响
        third = await get_tuning_task_detail(session, task_id)
        assert third["currentPid"] == {"kp": 2.0, "ti": 45.0, "td": 0.0}

    @pytest.mark.asyncio
    async def test_cross_loop_and_forged_source_rejected(self, pg_context, monkeypatch) -> None:
        import app.services.data_source.realtime_subscriber as rs

        monkeypatch.setattr(
            rs, "get_subscriber", lambda: (_ for _ in ()).throw(RuntimeError("no redis"))
        )

        from app.core.exceptions import BizError
        from app.services.tuning import create_tuning_task

        session, _ = pg_context
        loop_id, source_id = await _seed(session)
        other_loop = LoopLedger(
            tag_name=f"{_TEST_TAG_PREFIX}-LOOP2", description="跨回路反例", is_active=True
        )
        session.add(other_loop)
        await session.commit()

        # 跨回路 sourceRecordId 引用：拒绝
        with pytest.raises(BizError) as exc_info:
            await create_tuning_task(
                db=session,
                loop_id=str(other_loop.id),
                model_type="FOPDT",
                model_params=dict(_MODEL_PARAMS),
                algorithm="IMC",
                recommended_pid={"kp": 1.0, "ti": 10.0, "td": 0.0},
                model_source="IDENTIFICATION_RECORD",
                source_record_id=source_id,
                risk_confirmed=True,
                simulation_result=_SIM_EVIDENCE,
            )
        assert exc_info.value.code == "ERR_TUNING_LOOP_MISMATCH"

        # 伪造（不存在）来源 ID：拒绝，非静默空值
        with pytest.raises(BizError) as exc_info:
            await create_tuning_task(
                db=session,
                loop_id=loop_id,
                model_type="FOPDT",
                model_params=dict(_MODEL_PARAMS),
                algorithm="IMC",
                recommended_pid={"kp": 1.0, "ti": 10.0, "td": 0.0},
                model_source="IDENTIFICATION_RECORD",
                source_record_id=str(uuid4()),
                risk_confirmed=True,
                simulation_result=_SIM_EVIDENCE,
            )
        assert exc_info.value.code == "ERR_TUNING_SOURCE_NOT_FOUND"

        # 人工模型（无算法来源）：诚实标记（来源元数据全空 + modelSource=MANUAL）
        manual = await create_tuning_task(
            db=session,
            loop_id=loop_id,
            model_type="FOPDT",
            model_params=dict(_MODEL_PARAMS),
            algorithm="MANUAL_TUNING",
            recommended_pid={"kp": 1.0, "ti": 10.0, "td": 0.0},
            model_source="MANUAL",
            source_record_id=None,
            risk_confirmed=True,
            simulation_result=_SIM_EVIDENCE,
            created_by="p3b2-test",
        )
        assert manual["modelSource"] == "MANUAL"
        assert manual["identifyMethod"] is None
        assert manual["dataSource"] is None
        assert manual["confidenceLevel"] is None
        assert manual["fittingScore"] is None
        assert manual["unitBasis"]["basis"] == "UNKNOWN"
