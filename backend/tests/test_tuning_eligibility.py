"""Phase 0 整定/仿真模型来源与可信度放行门禁回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from tests.conftest import TEST_USERS, mock_current_user

_MODEL_PARAMS = {"K": 1.2, "tau": 30.0, "theta": 5.0}


def _record(
    *,
    record_id: str = "record-a",
    loop_id: str = "loop-a",
    confidence_level: str | None = "A",
    confidence_reason: str | None = "拟合与残差检验通过",
    identify_method: str | None = "HISTORICAL_ARX",
    data_source: str | None = "HISTORY",
    model_type: str = "FOPDT",
    model_params: dict | None = None,
):
    return SimpleNamespace(
        id=record_id,
        loop_id=loop_id,
        confidence_level=confidence_level,
        confidence_reason=confidence_reason,
        identify_method=identify_method,
        data_source=data_source,
        model_type=model_type,
        model_params=model_params or dict(_MODEL_PARAMS),
        status="IDENTIFIED",
        task_id="server-task-a",
        # V62-P3-005：遗留记录无版本引用，读路径回退到 model_params
        process_model_version_id=None,
    )


async def _authorize(record=None, **overrides):
    from app.services.tuning import authorize_tuning_model

    db = AsyncMock()
    db.get = AsyncMock(return_value=record)
    kwargs = {
        "db": db,
        "requested_model_type": "FOPDT",
        "requested_model_params": dict(_MODEL_PARAMS),
        "loop_id": "loop-a",
        "source_record_id": getattr(record, "id", None),
        "model_source": "IDENTIFICATION_RECORD",
        "risk_confirmed": False,
    }
    kwargs.update(overrides)
    return await authorize_tuning_model(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", ["A", "B"])
async def test_identification_record_allows_a_and_b(confidence):
    context = await _authorize(_record(confidence_level=confidence))

    assert context.model_source == "IDENTIFICATION_RECORD"
    assert context.model_params == _MODEL_PARAMS
    assert context.loop_id == "loop-a"


@pytest.mark.asyncio
async def test_confidence_c_requires_explicit_confirmation():
    with pytest.raises(BizError, match="C"):
        await _authorize(_record(confidence_level="C"))

    context = await _authorize(
        _record(confidence_level="C"),
        risk_confirmed=True,
    )
    assert context.risk_confirmed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", ["D", "E", "INCONCLUSIVE", None])
async def test_low_or_missing_confidence_is_blocked(confidence):
    with pytest.raises(BizError):
        await _authorize(
            _record(confidence_level=confidence),
            risk_confirmed=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", ["A", "B"])
async def test_clivc_is_released_when_confidence_ok(confidence):
    """P2-009：CLIVC（HISTORICAL_IV）已升级为生产方法，A/B 可信度下可放行.

    早期 IV 实验性原型 pipeline 不再调用，HISTORICAL_IV 现仅代表 CLIVC，
    按正常可信度门禁放行（契约 v2.3 §6.1）。
    """
    context = await _authorize(
        _record(identify_method="HISTORICAL_IV", confidence_level=confidence)
    )
    assert context.identify_method == "HISTORICAL_IV"


@pytest.mark.asyncio
async def test_clivc_c_requires_explicit_confirmation():
    """CLIVC 在 C 级可信度下仍需显式风险确认（可信度门禁不因方法放宽）."""
    with pytest.raises(BizError, match="C"):
        await _authorize(_record(identify_method="HISTORICAL_IV", confidence_level="C"))

    context = await _authorize(
        _record(identify_method="HISTORICAL_IV", confidence_level="C"),
        risk_confirmed=True,
    )
    assert context.risk_confirmed is True


@pytest.mark.asyncio
async def test_heuristic_theta_is_blocked_from_recommendation_chain():
    with pytest.raises(BizError, match="启发"):
        await _authorize(
            _record(confidence_reason="拟合通过;theta_source=HEURISTIC_2TS"),
            risk_confirmed=True,
        )


@pytest.mark.asyncio
async def test_record_model_and_loop_cannot_be_forged():
    with pytest.raises(BizError, match="模型参数"):
        await _authorize(
            _record(),
            requested_model_params={"K": 99.0, "tau": 1.0, "theta": 0.0},
        )

    with pytest.raises(BizError, match="回路"):
        await _authorize(_record(), loop_id="loop-other")


@pytest.mark.asyncio
async def test_manual_source_requires_confirmation_and_keeps_manual_identity():
    with pytest.raises(BizError, match="确认"):
        await _authorize(
            None,
            source_record_id=None,
            model_source="MANUAL",
            risk_confirmed=False,
        )

    context = await _authorize(
        None,
        source_record_id=None,
        model_source="MANUAL",
        risk_confirmed=True,
    )
    assert context.model_source == "MANUAL"
    assert context.source_record_id is None


@pytest.mark.asyncio
async def test_legacy_bare_model_request_is_not_silently_allowed():
    with pytest.raises(BizError, match="模型来源"):
        await _authorize(
            None,
            source_record_id=None,
            model_source=None,
            risk_confirmed=False,
        )


@pytest.mark.asyncio
async def test_external_step_source_needs_persisted_validated_evidence():
    step_record = _record(
        confidence_level=None,
        confidence_reason="step_validation_passed=true",
        identify_method="STEP_TWO_POINT",
        data_source="STEP_EXPERIMENT",
    )
    context = await _authorize(
        step_record,
        model_source="STEP_EXPERIMENT",
    )
    assert context.model_source == "STEP_EXPERIMENT"

    with pytest.raises(BizError, match="阶跃"):
        await _authorize(
            None,
            source_record_id=None,
            model_source="STEP_EXPERIMENT",
        )


@pytest.mark.asyncio
async def test_client_saved_record_cannot_masquerade_as_server_identification():
    untrusted = _record()
    untrusted.task_id = None
    with pytest.raises(BizError, match="服务端辨识链"):
        await _authorize(untrusted)


@pytest.mark.asyncio
async def test_internal_validated_step_path_is_preserved():
    context = await _authorize(
        None,
        source_record_id=None,
        model_source="STEP_EXPERIMENT",
        trusted_step_validation=True,
    )
    assert context.model_source == "STEP_EXPERIMENT"


@pytest.mark.asyncio
async def test_tune_service_rejects_missing_authorization_context():
    from app.services.tuning import tune_pid

    with pytest.raises(BizError, match="来源上下文"):
        await tune_pid(
            model_type="FOPDT",
            model_params=dict(_MODEL_PARAMS),
            algorithm="IMC",
        )


@pytest.mark.asyncio
async def test_sync_step_identification_is_persisted_as_server_evidence():
    from app.services.tuning import persist_step_identification_record

    db = AsyncMock()
    db.add = MagicMock()
    result = {
        "modelType": "FOPDT",
        "params": dict(_MODEL_PARAMS),
        "fittingScore": 92.0,
        "stepValidationPassed": True,
    }

    record_id = await persist_step_identification_record(
        db=db,
        loop_id="loop-a",
        result=result,
        created_by="engineer",
        requested_method="AREA",
    )

    record = db.add.call_args.args[0]
    assert record.id == record_id
    assert record.loop_id == "loop-a"
    assert record.data_source == "STEP_EXPERIMENT"
    assert record.identify_method == "STEP_AREA"
    assert record.status == "IDENTIFIED"
    assert record.model_params == _MODEL_PARAMS
    assert record.task_id.startswith("step-sync:")
    assert record.confidence_reason == "step_validation_passed=true"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_tuning_rejects_legacy_naked_model():
    from app.tasks.tuning import _do_tune_and_simulate

    db = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("app.core.db.AsyncSessionLocal", factory),
        patch("app.services.tuning_progress.init_progress", AsyncMock()),
        pytest.raises(BizError, match="模型来源"),
    ):
        await _do_tune_and_simulate(
            task_id="task-legacy",
            loop_id="loop-a",
            model_type="FOPDT",
            model_params=dict(_MODEL_PARAMS),
            algorithms=["IMC"],
            current_pid=None,
            sim_duration=20.0,
            sim_step=1.0,
            setpoint_step=1.0,
            created_by="engineer",
        )


class TestTuningEligibilityAPI:
    def test_tune_rejects_legacy_bare_request(self, client):
        with mock_current_user(TEST_USERS["admin"]):
            response = client.post(
                "/api/v1/tuning/tune",
                json={
                    "modelType": "FOPDT",
                    "modelParams": _MODEL_PARAMS,
                    "algorithm": "IMC",
                },
            )

        assert response.status_code == 400
        assert response.json()["code"] == "ERR_TUNING_SOURCE_REQUIRED"

    def test_tune_uses_persisted_record_and_audits_source(self, client, mock_db):
        record = _record()
        mock_db.get = AsyncMock(return_value=record)
        tune_result = {
            "algorithm": "IMC",
            "recommendedPid": {"kp": 1.0, "ti": 10.0, "td": 0.0},
            "algorithmVersion": "TUNE_ENGINE_v1.0",
        }

        with (
            patch(
                "app.api.v1.endpoints.tuning.tune_pid",
                AsyncMock(return_value=tune_result),
            ) as mock_tune,
            mock_current_user(TEST_USERS["admin"]),
        ):
            response = client.post(
                "/api/v1/tuning/tune",
                json={
                    "modelType": "FOPDT",
                    "modelParams": _MODEL_PARAMS,
                    "algorithm": "IMC",
                    "loopId": "loop-a",
                    "sourceRecordId": "record-a",
                    "modelSource": "IDENTIFICATION_RECORD",
                },
            )

        assert response.status_code == 200
        assert mock_tune.await_args.kwargs["model_params"] == _MODEL_PARAMS
        audit = mock_db.add.call_args.args[0]
        assert "source=IDENTIFICATION_RECORD" in audit.after_value
        assert "record=record-a" in audit.after_value
        assert "riskConfirmed=false" in audit.after_value

    def test_sync_identify_returns_persisted_step_record_id(self, client):
        identify_result = {
            "modelType": "FOPDT",
            "params": dict(_MODEL_PARAMS),
            "fittingScore": 92.0,
            "stepValidationPassed": True,
            "algorithmVersion": "TUNE_ENGINE_v1.0",
            "dataPoints": 120,
            "fittedCurve": None,
        }
        with (
            patch(
                "app.api.v1.endpoints.tuning.identify_model",
                AsyncMock(return_value=identify_result),
            ),
            patch(
                "app.api.v1.endpoints.tuning.persist_step_identification_record",
                AsyncMock(return_value="step-record-api"),
            ) as mock_persist,
            mock_current_user(TEST_USERS["admin"]),
        ):
            response = client.post(
                "/api/v1/tuning/identify",
                json={
                    "loopId": "loop-a",
                    "startTime": "2026-07-29T00:00:00Z",
                    "endTime": "2026-07-29T01:00:00Z",
                    "modelType": "FOPDT",
                },
            )

        assert response.status_code == 200
        assert response.json()["data"]["recordId"] == "step-record-api"
        assert mock_persist.await_args.kwargs["created_by"] == "admin"

    @pytest.mark.parametrize("path", ["/api/v1/tuning/simulate", "/api/v1/tuning/compare"])
    def test_simulation_chain_blocks_low_confidence_record(self, path, client, mock_db):
        mock_db.get = AsyncMock(return_value=_record(confidence_level="D"))
        payload = {
            "modelType": "FOPDT",
            "modelParams": _MODEL_PARAMS,
            "currentPid": {"kp": 0.5, "ti": 30.0, "td": 0.0},
            "recommendedPid": {"kp": 1.0, "ti": 15.0, "td": 0.0},
            "pidCandidates": [
                {"label": "IMC", "kp": 1.0, "ti": 15.0, "td": 0.0},
                {"label": "SIMC", "kp": 0.8, "ti": 20.0, "td": 0.0},
            ],
            "loopId": "loop-a",
            "sourceRecordId": "record-a",
            "modelSource": "IDENTIFICATION_RECORD",
            "riskConfirmed": True,
            "simDuration": 20.0,
        }

        with mock_current_user(TEST_USERS["admin"]):
            response = client.post(path, json=payload)

        assert response.status_code == 422
        assert response.json()["code"] == "ERR_TUNING_CONFIDENCE_BLOCKED"

    @pytest.mark.parametrize("path", ["/api/v1/tuning/simulate", "/api/v1/tuning/compare"])
    def test_simulation_chain_allows_confirmed_manual_model(self, path, client):
        payload = {
            "modelType": "FOPDT",
            "modelParams": _MODEL_PARAMS,
            "currentPid": {"kp": 0.5, "ti": 30.0, "td": 0.0},
            "recommendedPid": {"kp": 1.0, "ti": 15.0, "td": 0.0},
            "pidCandidates": [
                {"label": "IMC", "kp": 1.0, "ti": 15.0, "td": 0.0},
                {"label": "SIMC", "kp": 0.8, "ti": 20.0, "td": 0.0},
            ],
            "loopId": "loop-a",
            "modelSource": "MANUAL",
            "riskConfirmed": True,
            "simDuration": 20.0,
        }

        with mock_current_user(TEST_USERS["admin"]):
            response = client.post(path, json=payload)

        assert response.status_code == 200
