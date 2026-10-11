"""P3-B2 W4/W5 验收反例：最终选定方案保存链全链修复 + currentPid 标准化接线.

覆盖（任务书 §2 验收反例）：
- 360 选定方案保存→真实落库→改当前配置→再读取：来源/模型/参数/窗口不漂移
  （本文件为 mock 级冻结语义断言；真实 PG 端到端见
  ``tests/integration/test_p3_b2_savechain_real_pg.py``）
- 跨回路 sourceRecordId 引用：拒绝（ERR_TUNING_LOOP_MISMATCH）
- 伪造/不存在的来源 ID：拒绝（ERR_TUNING_SOURCE_NOT_FOUND / SOURCE_UNVERIFIED）
- 篡改模型参数冒充辨识输出：拒绝（ERR_TUNING_MODEL_MISMATCH）
- 人工模型（无算法来源）：诚实标记人工，不冒充算法输出
- currentPid 双表示（PB↔Kp、分钟↔秒）+ 未知 DCS 拒绝默认标准 kp/ti/td
- K 单位域冻结（tuning_units 换算层：kConverted 或诚实未知）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import BizError

# ---------------------------------------------------------------------------
# fixtures 辅助
# ---------------------------------------------------------------------------

_LOOP_ID = "00000000-0000-0000-0000-0000000000a1"
_OTHER_LOOP_ID = "00000000-0000-0000-0000-0000000000b2"
_SOURCE_ID = "00000000-0000-0000-0000-0000000000c3"
_MODEL_PARAMS = {"K": 1.2, "tau": 30.0, "theta": 5.0}

_SIM_EVIDENCE = {
    "candidateResponses": [{"label": "IMC", "response": {"ts": [0.0], "pv": [0.0]}, "metrics": {}}]
}

_PID_REPR_KNOWN = {
    "status": "KNOWN",
    "modelId": "00000000-0000-0000-0000-0000000000d4",
    "modelCode": "TEST-DCS-PB-MIN",
    "pType": "PROPORTION_BAND",
    "iUnit": "MINUTES",
    "dUnit": "SECONDS",
    "dFilterEnabled": False,
    "dcsTemplateRevision": "dcs_model:00000000-0000-0000-0000-0000000000d4",
}

_FACTS_KNOWN = {
    "pidRepresentation": dict(_PID_REPR_KNOWN),
    "pvRange": {"lower": 0.0, "upper": 200.0, "unit": "kPa"},
    "opTagRange": {"lower": 0.0, "upper": 100.0, "unit": "%"},
}


def _loop(loop_id: str = _LOOP_ID) -> SimpleNamespace:
    return SimpleNamespace(id=loop_id, tag_name="TIC-101", is_active=True, dcs_model_id=None)


def _source_record(
    *,
    loop_id: str = _LOOP_ID,
    status: str = "IDENTIFIED",
    confidence_level: str | None = "A",
    identify_method: str | None = "HISTORICAL_ARX",
    model_params: dict | None = None,
    process_model_version_id: str | None = None,
    with_window: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=_SOURCE_ID,
        loop_id=loop_id,
        status=status,
        task_id="celery-task-1",
        model_type="FOPDT",
        model_params=model_params or dict(_MODEL_PARAMS),
        process_model_version_id=process_model_version_id,
        confidence_level=confidence_level,
        confidence_reason="拟合与残差检验通过",
        identify_method=identify_method,
        data_source="HISTORY",
        fitting_score=Decimal("92.50"),
        excitation_score=Decimal("0.72"),
        residual_test_passed=True,
        time_window_start=datetime(2026, 10, 1, 0, 0, 0) if with_window else None,
        time_window_end=datetime(2026, 10, 1, 2, 0, 0) if with_window else None,
        dataset_snapshot_id=None,
    )


def _make_db(
    *,
    loop=None,
    source=None,
    version_params: dict | None = None,
) -> MagicMock:
    """构造 create_tuning_task 路径的 db mock（execute/get 按序返回）.

    execute 序列：① _get_loop（scalar_one_or_none）
    ② get_effective_model_params 的版本查询（仅 source 带版本 FK 时）。
    get：TuningRecord 来源记录。
    """
    db = MagicMock()
    results = []

    loop_result = MagicMock()
    loop_result.scalar_one_or_none = MagicMock(return_value=loop)
    results.append(loop_result)

    if source is not None and getattr(source, "process_model_version_id", None):
        version_result = MagicMock()
        version_result.scalar_one_or_none = MagicMock(return_value=version_params)
        results.append(version_result)

    db.execute = AsyncMock(side_effect=results)
    db.get = AsyncMock(return_value=source)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


_PID_SNAPSHOT_KNOWN = {
    "status": "KNOWN",
    "standard": {"kp": 2.0, "ti": 45.0, "td": 0.0},
    "raw": {"p": 50.0, "i": 0.75, "d": 0.0},
    "representation": dict(_PID_REPR_KNOWN),
    "conversion": {"engine": "PID_CONVERSION_FACTS_V1", "pType": "PROPORTION_BAND"},
    "readAt": None,
    "reason": None,
}


async def _create(**overrides):
    """直调 create_tuning_task（真实 authorize 门禁；facts/currentPid 打桩）."""
    from app.services.tuning import create_tuning_task

    kwargs = {
        "db": _make_db(loop=_loop(), source=_source_record()),
        "loop_id": _LOOP_ID,
        "model_type": "FOPDT",
        "model_params": dict(_MODEL_PARAMS),
        "algorithm": "IMC",
        "recommended_pid": {"kp": 1.5, "ti": 20.0, "td": 0.0},
        "model_source": "IDENTIFICATION_RECORD",
        "source_record_id": _SOURCE_ID,
        "risk_confirmed": True,
        "simulation_result": _SIM_EVIDENCE,
        "status": "SIMULATED",
        "created_by": "tester",
    }
    kwargs.update(overrides)
    with (
        patch(
            "app.services.engineering_facts.resolve_loop_engineering_facts",
            new=AsyncMock(return_value=_FACTS_KNOWN),
        ),
        patch(
            "app.services.tuning._current_pid_snapshot",
            new=AsyncMock(return_value=dict(_PID_SNAPSHOT_KNOWN)),
        ),
    ):
        return await create_tuning_task(**kwargs), kwargs["db"]


# ---------------------------------------------------------------------------
# W4：服务端校验冻结
# ---------------------------------------------------------------------------


class TestCreateTuningTaskFrozenIdentity:
    """保存链冻结语义：来源/模型/参数/窗口服务端权威派生并冻结落库."""

    @pytest.mark.asyncio
    async def test_save_freezes_source_identity_and_window(self) -> None:
        result, db = await _create()
        record: object = db.add.call_args[0][0]

        assert str(record.source_record_id) == _SOURCE_ID
        # 模型参数 = 服务端持久化值（冻结，非客户端回显）
        assert record.model_params == _MODEL_PARAMS
        # 来源元数据从来源记录服务端派生
        assert record.identify_method == "HISTORICAL_ARX"
        assert record.data_source == "HISTORY"
        assert record.confidence_level == "A"
        assert record.fitting_score == Decimal("92.50")
        assert record.residual_test_passed is True
        # 辨识时间窗随来源记录冻结
        assert record.time_window_start == datetime(2026, 10, 1, 0, 0, 0)
        assert record.time_window_end == datetime(2026, 10, 1, 2, 0, 0)
        # calc_context（与异步 TUNING_PROPOSAL 链同字段口径）
        ctx = record.calc_context
        assert ctx["kind"] == "TUNING_PROPOSAL"
        assert ctx["savedVia"] == "WORKBENCH_FINAL_SELECTION"
        assert ctx["sourceRecordId"] == _SOURCE_ID
        assert ctx["modelSource"] == "IDENTIFICATION_RECORD"
        assert ctx["frozenModel"] == {"modelType": "FOPDT", "modelParams": _MODEL_PARAMS}
        assert ctx["sourceWindow"] == {
            "tsStart": "2026-10-01T00:00:00",
            "tsEnd": "2026-10-01T02:00:00",
        }
        assert ctx["dcsTemplateRevision"].startswith("dcs_model:")
        # currentPid/rollbackPid = 服务端标准形式快照（W5）
        assert record.current_pid == {"kp": 2.0, "ti": 45.0, "td": 0.0}
        assert record.rollback_pid == {"kp": 2.0, "ti": 45.0, "td": 0.0}

    @pytest.mark.asyncio
    async def test_response_dict_carries_additive_identity_keys(self) -> None:
        result, _ = await _create()
        assert result["sourceRecordId"] == _SOURCE_ID
        assert result["modelSource"] == "IDENTIFICATION_RECORD"
        assert result["currentPid"] == {"kp": 2.0, "ti": 45.0, "td": 0.0}
        assert result["currentPidContext"]["status"] == "KNOWN"
        assert result["currentPidContext"]["standard"] == {"kp": 2.0, "ti": 45.0, "td": 0.0}
        assert result["unitBasis"]["basis"] == "NORMALIZED_0_100"
        assert result["timeWindowStart"] == "2026-10-01T00:00:00"

    @pytest.mark.asyncio
    async def test_frozen_after_current_config_change(self) -> None:
        """改当前配置（新 currentPid 快照 + 新来源记录）后，已存记录回读不漂移."""
        result_first, _ = await _create()
        # 第二次保存：同回路同来源，但实时 PID 已变（换 PB/换积分时间）
        changed_snapshot = {
            **_PID_SNAPSHOT_KNOWN,
            "standard": {"kp": 4.0, "ti": 120.0, "td": 0.0},
            "raw": {"p": 25.0, "i": 2.0, "d": 0.0},
        }
        from app.services.tuning import create_tuning_task

        db2 = _make_db(loop=_loop(), source=_source_record())
        with (
            patch(
                "app.services.engineering_facts.resolve_loop_engineering_facts",
                new=AsyncMock(return_value=_FACTS_KNOWN),
            ),
            patch(
                "app.services.tuning._current_pid_snapshot",
                new=AsyncMock(return_value=changed_snapshot),
            ),
        ):
            await create_tuning_task(
                db=db2,
                loop_id=_LOOP_ID,
                model_type="FOPDT",
                model_params=dict(_MODEL_PARAMS),
                algorithm="IMC",
                recommended_pid={"kp": 1.5, "ti": 20.0, "td": 0.0},
                model_source="IDENTIFICATION_RECORD",
                source_record_id=_SOURCE_ID,
                risk_confirmed=True,
                simulation_result=_SIM_EVIDENCE,
            )
        record_second: object = db2.add.call_args[0][0]

        # 第一次保存的冻结值不随第二次变化（各记录独立冻结）
        assert record_second.current_pid == {"kp": 4.0, "ti": 120.0, "td": 0.0}
        assert result_first["currentPid"] == {"kp": 2.0, "ti": 45.0, "td": 0.0}
        assert result_first["modelParams"] == _MODEL_PARAMS
        assert result_first["sourceRecordId"] == _SOURCE_ID


class TestCreateTuningTaskRejections:
    """验收反例：跨回路/伪造/过期/篡改来源拒绝（真实 authorize 门禁）."""

    @pytest.mark.asyncio
    async def test_cross_loop_source_rejected(self) -> None:
        db = _make_db(loop=_loop(), source=_source_record(loop_id=_OTHER_LOOP_ID))
        with pytest.raises(BizError) as exc_info:
            await _create(db=db)
        assert exc_info.value.code == "ERR_TUNING_LOOP_MISMATCH"

    @pytest.mark.asyncio
    async def test_forged_source_id_rejected(self) -> None:
        db = _make_db(loop=_loop(), source=None)
        with pytest.raises(BizError) as exc_info:
            await _create(db=db)
        assert exc_info.value.code == "ERR_TUNING_SOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_expired_source_status_rejected(self) -> None:
        db = _make_db(loop=_loop(), source=_source_record(status="INCONCLUSIVE"))
        with pytest.raises(BizError) as exc_info:
            await _create(db=db)
        assert exc_info.value.code == "ERR_TUNING_SOURCE_UNVERIFIED"

    @pytest.mark.asyncio
    async def test_tampered_model_params_rejected(self) -> None:
        """客户端申报参数与来源记录不一致（伪造辨识输出）→ 拒绝."""
        db = _make_db(loop=_loop(), source=_source_record())
        with pytest.raises(BizError) as exc_info:
            await _create(db=db, model_params={"K": 99.0, "tau": 30.0, "theta": 5.0})
        assert exc_info.value.code == "ERR_TUNING_MODEL_MISMATCH"

    @pytest.mark.asyncio
    async def test_simulated_status_requires_simulation_evidence(self) -> None:
        with pytest.raises(BizError) as exc_info:
            await _create(simulation_result={"foo": "bar"})
        assert exc_info.value.code == "ERR_TUNING_SIMULATION_EVIDENCE_REQUIRED"

    @pytest.mark.asyncio
    async def test_legacy_naked_model_rejected(self) -> None:
        with pytest.raises(BizError) as exc_info:
            await _create(model_source=None)
        assert exc_info.value.code == "ERR_TUNING_SOURCE_REQUIRED"


class TestManualModelHonestMarking:
    """人工模型诚实标记：无算法来源不冒充算法输出."""

    @pytest.mark.asyncio
    async def test_manual_requires_risk_confirmation(self) -> None:
        db = _make_db(loop=_loop(), source=None)
        with pytest.raises(BizError) as exc_info:
            await _create(db=db, model_source="MANUAL", source_record_id=None, risk_confirmed=False)
        assert exc_info.value.code == "ERR_TUNING_RISK_CONFIRMATION_REQUIRED"

    @pytest.mark.asyncio
    async def test_manual_cannot_bind_fake_source_record(self) -> None:
        db = _make_db(loop=_loop(), source=_source_record())
        with pytest.raises(BizError) as exc_info:
            await _create(db=db, model_source="MANUAL", source_record_id=_SOURCE_ID)
        assert exc_info.value.code == "ERR_TUNING_SOURCE_INVALID"

    @pytest.mark.asyncio
    async def test_manual_provenance_all_none(self) -> None:
        db = _make_db(loop=_loop(), source=None)
        with (
            patch(
                "app.services.engineering_facts.resolve_loop_engineering_facts",
                new=AsyncMock(return_value=_FACTS_KNOWN),
            ),
            patch(
                "app.services.tuning._current_pid_snapshot",
                new=AsyncMock(return_value=dict(_PID_SNAPSHOT_KNOWN)),
            ),
        ):
            from app.services.tuning import create_tuning_task

            await create_tuning_task(
                db=db,
                loop_id=_LOOP_ID,
                model_type="FOPDT",
                model_params=dict(_MODEL_PARAMS),
                algorithm="MANUAL_TUNING",
                recommended_pid={"kp": 1.5, "ti": 20.0, "td": 0.0},
                model_source="MANUAL",
                source_record_id=None,
                risk_confirmed=True,
                simulation_result=_SIM_EVIDENCE,
            )
        record: object = db.add.call_args[0][0]
        # 诚实标记：来源元数据全部置空
        assert record.identify_method is None
        assert record.data_source is None
        assert record.confidence_level is None
        assert record.fitting_score is None
        assert record.source_record_id is None
        assert record.calc_context["modelSource"] == "MANUAL"
        # 人工模型单位域未声明（诚实 UNKNOWN，不冒充辨识链）
        assert record.calc_context["unitBasis"]["basis"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# W5：currentPid 三元组（标准/原始/转换依据）
# ---------------------------------------------------------------------------


def _pid_tags_db(p: float | None = 50.0, i: float | None = 0.75, d: float | None = 0.0):
    """构造 _current_pid_snapshot 的 db mock（角色映射 join 单查询）."""
    rows = []
    for role, value in (("PID_P", p), ("PID_I", i), ("PID_D", d)):
        if value is None and role != "PID_D":
            continue
        tag = SimpleNamespace(tag_name=f"TAG_{role}", current_value=value)
        mapping = SimpleNamespace(tag_role=role, tag_id=str(uuid4()))
        rows.append((mapping, tag))
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _no_redis():
    """禁用 Redis 实时缓存（打桩 get_subscriber 抛错 → 回退 current_value）."""
    import app.services.data_source.realtime_subscriber as rs

    def _raise():
        raise RuntimeError("no redis in unit test")

    return patch.object(rs, "get_subscriber", side_effect=_raise)


class TestCurrentPidSnapshot:
    """currentPid 双表示转换（B1 pid_conversion 工程事实驱动入口）."""

    @pytest.mark.asyncio
    async def test_known_representation_pb_minutes_to_standard(self) -> None:
        from app.services.tuning import _current_pid_snapshot

        db = _pid_tags_db(p=50.0, i=0.75, d=0.0)
        with _no_redis():
            snap = await _current_pid_snapshot(db, _loop(), _FACTS_KNOWN)
        assert snap["status"] == "KNOWN"
        # PB=50 → Kp=2；0.75min → Ti=45s；Td 秒直通
        assert snap["standard"] == pytest.approx({"kp": 2.0, "ti": 45.0, "td": 0.0})
        assert snap["raw"] == {"p": 50.0, "i": 0.75, "d": 0.0}
        assert snap["conversion"]["engine"] == "PID_CONVERSION_FACTS_V1"
        assert snap["conversion"]["pType"] == "PROPORTION_BAND"
        assert snap["conversion"]["iUnit"] == "MINUTES"

    @pytest.mark.asyncio
    async def test_roundtrip_standard_back_to_dcs(self) -> None:
        """双表示往返：标准 → DCS 私有 → 标准，数值复原（1e-9 级）."""
        from app.services.pid_conversion import (
            DcsPid,
            dcs_pid_from_standard_facts,
            standard_pid_from_facts,
        )

        standard = standard_pid_from_facts(DcsPid(p=50.0, i=0.75, d=0.0), _PID_REPR_KNOWN)
        dcs_back = dcs_pid_from_standard_facts(standard, _PID_REPR_KNOWN)
        assert dcs_back.p == pytest.approx(50.0, abs=1e-9)
        assert dcs_back.i == pytest.approx(0.75, abs=1e-9)
        assert dcs_back.d == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.asyncio
    async def test_unknown_representation_no_default_standard(self) -> None:
        """未知 DCS 表示：不默认标准 kp/ti/td（显式 REPRESENTATION_UNKNOWN）."""
        from app.services.tuning import _current_pid_snapshot

        facts_unknown = {**_FACTS_KNOWN, "pidRepresentation": {"status": "UNKNOWN"}}
        db = _pid_tags_db()
        with _no_redis():
            snap = await _current_pid_snapshot(db, _loop(), facts_unknown)
        assert snap["status"] == "REPRESENTATION_UNKNOWN"
        assert snap["standard"] is None
        assert snap["raw"] == {"p": 50.0, "i": 0.75, "d": 0.0}  # 原始表示保留
        assert "不得默认标准" in snap["reason"]

    @pytest.mark.asyncio
    async def test_missing_runtime_values_unavailable(self) -> None:
        from app.services.tuning import _current_pid_snapshot

        db = _pid_tags_db(p=None, i=None, d=None)
        with _no_redis():
            snap = await _current_pid_snapshot(db, _loop(), _FACTS_KNOWN)
        assert snap["status"] == "UNAVAILABLE"
        assert snap["standard"] is None
        assert snap["raw"] is None

    @pytest.mark.asyncio
    async def test_zero_pb_conversion_error(self) -> None:
        from app.services.tuning import _current_pid_snapshot

        db = _pid_tags_db(p=0.0, i=1.0, d=0.0)
        with _no_redis():
            snap = await _current_pid_snapshot(db, _loop(), _FACTS_KNOWN)
        assert snap["status"] == "CONVERSION_ERROR"
        assert snap["standard"] is None


class TestFrozenUnitBasis:
    """K 单位域冻结（tuning_units 换算层接入）."""

    def test_history_chain_converts_to_engineering(self) -> None:
        from app.services.tuning import _frozen_unit_basis

        block = _frozen_unit_basis(_FACTS_KNOWN, 1.2, "HISTORICAL_ARX")
        assert block["basis"] == "NORMALIZED_0_100"
        # K=1.2 %PV/%OP → 工程域：×(pv_span/100)×(100/op_span) = 1.2×2.0
        assert block["kConverted"] == pytest.approx(2.4)
        assert block["convertedBasis"] == "ENGINEERING"

    def test_step_chain_converts_to_normalized(self) -> None:
        from app.services.tuning import _frozen_unit_basis

        block = _frozen_unit_basis(_FACTS_KNOWN, 2.4, "STEP_TWO_POINT")
        assert block["basis"] == "TAG_STORED"
        assert block["kConverted"] == pytest.approx(1.2)
        assert block["convertedBasis"] == "NORMALIZED_0_100"

    def test_missing_spans_honest_unknown(self) -> None:
        from app.services.tuning import _frozen_unit_basis

        facts_default = {
            "pidRepresentation": dict(_PID_REPR_KNOWN),
            "pvRange": {"lower": None, "upper": None},
            "opTagRange": {"lower": None, "upper": None},
        }
        block = _frozen_unit_basis(facts_default, 1.2, "HISTORICAL_ARX")
        assert block["basis"] == "NORMALIZED_0_100"
        assert "kConverted" not in block
        assert "量程未全部核实" in block["kConvertedUnknownReason"]

    def test_manual_model_unknown_basis(self) -> None:
        from app.services.tuning import _frozen_unit_basis

        block = _frozen_unit_basis(_FACTS_KNOWN, 1.2, None)
        assert block["basis"] == "UNKNOWN"
        assert "人工模型" in block["note"]
        assert "kConverted" not in block
