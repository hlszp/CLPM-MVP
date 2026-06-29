"""安全 P2 修复测试（S4-C1~C3）。

覆盖：
- S4-C1: ValidationError 脱敏（非 DEBUG 模式不暴露 loc/type/ctx）
- S4-C2: Refresh Token 设备绑定（IP 不一致返回 ERR_TOKEN_DEVICE_MISMATCH）
- S4-C3: schemas 枚举校验（拒绝非法值、接受合法值）
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.exceptions import BizError
from app.core.security import (
    create_refresh_token,
    decode_token,
    verify_refresh_token,
)
from app.schemas.diagnosis import TrackerStatusUpdate
from app.schemas.tuning import CreateTuningTaskRequest, ModelIdentifyRequest, TuneRequest
from tests.conftest import TEST_PASSWORD, TEST_USERS, make_db_execute_return, mock_current_user


@pytest.fixture
def non_debug_mode():
    """临时关闭 DEBUG 模式，测试非 DEBUG 下的脱敏行为。"""
    original = settings.DEBUG
    settings.DEBUG = False
    try:
        yield
    finally:
        settings.DEBUG = original


# ===========================================================================
# S4-C1: ValidationError 脱敏
# ===========================================================================


class TestValidationErrorSanitization:
    """校验错误脱敏测试（非 DEBUG 模式）。"""

    def test_validation_error_returns_err_validation_code(self, client, non_debug_mode) -> None:
        """校验失败返回 ERR_VALIDATION 错误码。"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "ERR_VALIDATION"

    def test_validation_error_no_loc_in_response(self, client, non_debug_mode) -> None:
        """脱敏后响应不包含 loc（字段路径）。"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": TEST_PASSWORD},
        )
        body = resp.json()
        body_str = json.dumps(body, ensure_ascii=False)
        # 不应包含字段路径
        assert "loc" not in body_str
        # 不应包含具体字段名（如 username/password）
        data = body.get("data")
        if data and isinstance(data, list):
            for item in data:
                assert isinstance(item, str)
                assert "username" not in item.lower()
                assert "password" not in item.lower()

    def test_validation_error_no_ctx_in_response(self, client, non_debug_mode) -> None:
        """脱敏后响应不包含 ctx（上下文）。"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": TEST_PASSWORD},
        )
        body_str = json.dumps(resp.json(), ensure_ascii=False)
        assert "ctx" not in body_str

    def test_validation_error_no_internal_type_in_response(self, client, non_debug_mode) -> None:
        """脱敏后响应不包含内部 type 字段（如 string_too_short）。"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": TEST_PASSWORD},
        )
        body_str = json.dumps(resp.json(), ensure_ascii=False)
        # 不应包含 Pydantic 内部错误类型
        assert "string_too_short" not in body_str

    def test_validation_error_generic_message(self, client, non_debug_mode) -> None:
        """脱敏后 message 为通用提示。"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": TEST_PASSWORD},
        )
        body = resp.json()
        assert body["message"] == "输入校验失败"

    def test_validation_error_data_is_list_of_strings(self, client, non_debug_mode) -> None:
        """脱敏后 data 为字符串列表（通用提示）。"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": TEST_PASSWORD},
        )
        body = resp.json()
        data = body.get("data")
        assert isinstance(data, list)
        assert len(data) > 0
        for item in data:
            assert isinstance(item, str)

    def test_missing_field_returns_generic_message(self, client, non_debug_mode) -> None:
        """缺少必填字段返回通用提示。"""
        # 不传 password 字段
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin"},
        )
        assert resp.status_code == 422
        body = resp.json()
        data = body.get("data")
        assert isinstance(data, list)
        # 应包含"缺少必填字段"类提示
        assert any("缺少" in item or "格式" in item for item in data)


# ===========================================================================
# S4-C2: Refresh Token 设备绑定
# ===========================================================================


class TestRefreshTokenDeviceBinding:
    """Refresh Token 设备绑定测试。"""

    def test_verify_refresh_token_ip_mismatch_raises_biz_error(self) -> None:
        """IP 不一致时抛出 ERR_TOKEN_DEVICE_MISMATCH。"""
        # 创建绑定 IP 的 refresh token
        token, _, _ = create_refresh_token(
            subject="test-user-id", remember_me=False, device_ip="1.2.3.4"
        )
        # 用不同 IP 验证 → 应抛出 BizError
        with pytest.raises(BizError) as exc_info:
            verify_refresh_token(token, device_ip="5.6.7.8")
        assert exc_info.value.code == "ERR_TOKEN_DEVICE_MISMATCH"
        assert exc_info.value.status_code == 401

    def test_verify_refresh_token_ip_match_succeeds(self) -> None:
        """IP 一致时验证通过。"""
        token, _, _ = create_refresh_token(
            subject="test-user-id", remember_me=False, device_ip="1.2.3.4"
        )
        payload = verify_refresh_token(token, device_ip="1.2.3.4")
        assert payload["sub"] == "test-user-id"
        assert payload["type"] == "refresh"
        assert payload["device"] == "1.2.3.4"

    def test_verify_refresh_token_no_device_ip_skips_check(self) -> None:
        """请求未提供 IP 时跳过设备绑定检查（向后兼容）。"""
        token, _, _ = create_refresh_token(
            subject="test-user-id", remember_me=False, device_ip="1.2.3.4"
        )
        # 不传 device_ip → 跳过检查
        payload = verify_refresh_token(token, device_ip=None)
        assert payload["sub"] == "test-user-id"

    def test_verify_refresh_token_token_without_device_skips_check(self) -> None:
        """Token 未绑定设备时跳过检查（向后兼容旧 token）。"""
        # 创建不绑定 IP 的 refresh token
        token, _, _ = create_refresh_token(
            subject="test-user-id", remember_me=False, device_ip=None
        )
        # 验证 payload 中没有 device 字段
        payload = decode_token(token)
        assert "device" not in payload
        # 用任意 IP 验证 → 应通过
        result = verify_refresh_token(token, device_ip="9.9.9.9")
        assert result["sub"] == "test-user-id"

    def test_refresh_token_stores_device_in_payload(self) -> None:
        """Refresh Token payload 中包含 device 字段。"""
        token, _, _ = create_refresh_token(subject="test-user-id", device_ip="192.168.1.100")
        payload = decode_token(token)
        assert payload["device"] == "192.168.1.100"

    def test_refresh_api_ip_mismatch_returns_401(self, client, mock_db, fake_redis) -> None:
        """API 层面：登录 IP 与刷新 IP 不一致返回 401。"""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))

        # 用 IP 1.2.3.4 登录
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert login_resp.status_code == 200
        refresh_token = login_resp.json()["data"]["refreshToken"]

        # 用不同 IP 5.6.7.8 刷新 → 应返回 401
        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=make_db_execute_return(TEST_USERS["admin"])
            )
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.post(
                "/api/v1/auth/refresh",
                json={"refreshToken": refresh_token},
                headers={"X-Forwarded-For": "5.6.7.8"},
            )
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_DEVICE_MISMATCH"

    def test_refresh_api_same_ip_succeeds(self, client, mock_db, fake_redis) -> None:
        """API 层面：登录 IP 与刷新 IP 一致时刷新成功。"""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))

        # 用 IP 1.2.3.4 登录
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert login_resp.status_code == 200
        refresh_token = login_resp.json()["data"]["refreshToken"]

        # 用相同 IP 1.2.3.4 刷新 → 应成功
        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=make_db_execute_return(TEST_USERS["admin"])
            )
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.post(
                "/api/v1/auth/refresh",
                json={"refreshToken": refresh_token},
                headers={"X-Forwarded-For": "1.2.3.4"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "accessToken" in data
        assert "refreshToken" in data


# ===========================================================================
# S4-C3: schemas 枚举校验
# ===========================================================================


class TestEnumValidation:
    """枚举校验测试。"""

    # ---- modelType 枚举 ----

    def test_invalid_model_type_rejected(self) -> None:
        """非法 modelType 被拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelIdentifyRequest(
                loopId="test-loop",
                startTime="2026-01-01T00:00:00",
                endTime="2026-01-01T01:00:00",
                modelType="INVALID_MODEL",
            )

    def test_valid_model_types_accepted(self) -> None:
        """合法 modelType 被接受。"""
        for model_type in ("FOPDT", "SOPDT", "IPDT"):
            req = ModelIdentifyRequest(
                loopId="test-loop",
                startTime="2026-01-01T00:00:00",
                endTime="2026-01-01T01:00:00",
                modelType=model_type,
            )
            assert req.modelType == model_type

    def test_model_type_default_is_fopdt(self) -> None:
        """modelType 默认值为 FOPDT。"""
        req = ModelIdentifyRequest(
            loopId="test-loop",
            startTime="2026-01-01T00:00:00",
            endTime="2026-01-01T01:00:00",
        )
        assert req.modelType == "FOPDT"

    # ---- algorithm 枚举 ----

    def test_invalid_algorithm_rejected(self) -> None:
        """非法 algorithm 被拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TuneRequest(
                modelType="FOPDT",
                modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
                algorithm="INVALID_ALGO",
            )

    def test_valid_algorithms_accepted(self) -> None:
        """合法 algorithm 被接受。"""
        for algo in ("IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"):
            req = TuneRequest(
                modelType="FOPDT",
                modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
                algorithm=algo,
            )
            assert req.algorithm == algo

    # ---- taskStatus 枚举 ----

    def test_invalid_task_status_rejected(self) -> None:
        """非法 taskStatus 被拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CreateTuningTaskRequest(
                loopId="test-loop",
                modelType="FOPDT",
                modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
                algorithm="IMC",
                recommendedPid={"kp": 1.0, "ti": 10.0, "td": 0.0},
                status="INVALID_STATUS",
            )

    def test_valid_task_statuses_accepted(self) -> None:
        """合法 taskStatus 被接受。"""
        for status in ("PENDING", "IDENTIFIED", "SIMULATED", "APPLIED", "VERIFIED"):
            req = CreateTuningTaskRequest(
                loopId="test-loop",
                modelType="FOPDT",
                modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
                algorithm="IMC",
                recommendedPid={"kp": 1.0, "ti": 10.0, "td": 0.0},
                status=status,
            )
            assert req.status == status

    def test_task_status_default_is_simulated(self) -> None:
        """taskStatus 默认值为 SIMULATED。"""
        req = CreateTuningTaskRequest(
            loopId="test-loop",
            modelType="FOPDT",
            modelParams={"K": 1.0, "tau": 30.0, "theta": 5.0},
            algorithm="IMC",
            recommendedPid={"kp": 1.0, "ti": 10.0, "td": 0.0},
        )
        assert req.status == "SIMULATED"

    # ---- actionStatus 枚举 ----

    def test_invalid_action_status_rejected(self) -> None:
        """非法 actionStatus 被拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TrackerStatusUpdate(status="INVALID_STATUS")

    def test_valid_action_statuses_accepted(self) -> None:
        """合法 actionStatus 被接受。"""
        for status in ("PENDING", "IN_PROGRESS", "IMPLEMENTED", "IGNORED"):
            req = TrackerStatusUpdate(status=status)
            assert req.status == status

    # ---- API 层面枚举校验 ----

    def test_api_invalid_model_type_returns_422(self, client) -> None:
        """API 层面：非法 modelType 返回 422。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "INVALID_MODEL",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "algorithm": "IMC",
                },
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_api_invalid_algorithm_returns_422(self, client) -> None:
        """API 层面：非法 algorithm 返回 422。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "algorithm": "invalid_algo",
                },
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_api_valid_enum_values_accepted(self, client) -> None:
        """API 层面：合法枚举值通过校验。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
                    "algorithm": "IMC",
                    "algorithmParams": {"lambdaRatio": 1.0},
                },
            )
        assert resp.status_code == 200
        assert resp.json()["code"] == "0"
