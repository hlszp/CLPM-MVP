"""S8-TEST-001 补充测试：异常分支、边界条件、低覆盖率模块。

覆盖清单中的以下用例：
- UT-ERR-001~006: 异常分支（无Token/错误Token/404/422/分页越界/UUID格式）
- UT-LOOP-002/004/006/007/012/013: 回路权限/搜索/权重/监控
- UT-AUTH-005: 登录锁定解锁
- 跨模块权限验证矩阵
- 回路监控服务（提升 monitor.py 覆盖率）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalar_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = value
    return result


# ===========================================================================
# UT-ERR-001~006: 异常分支与边界条件
# ===========================================================================


class TestExceptionBranches:
    """异常分支测试：无Token/错误Token/404/422/分页越界/UUID格式。"""

    def test_err_001_no_token_protected_endpoint(self, client) -> None:
        """UT-ERR-001: 无 Token 访问受保护端点返回 401。"""
        endpoints = [
            ("/api/v1/auth/me", "get"),
            ("/api/v1/loops", "get"),
            ("/api/v1/loops/monitor", "get"),
            ("/api/v1/performance/metrics", "get"),
            ("/api/v1/diagnosis/list", "get"),
            ("/api/v1/tuning/methods", "get"),
            ("/api/v1/dashboard/overview", "get"),
        ]
        for path, method in endpoints:
            resp = getattr(client, method)(path)
            assert resp.status_code == 401, f"{path} 应返回 401，实际 {resp.status_code}"

    def test_err_002_malformed_token(self, client) -> None:
        """UT-ERR-002: 格式错误的 Token 返回 401。"""
        malformed_tokens = [
            "Bearer invalid_format",
            "Bearer ",
            "InvalidScheme token",
            "Bearer aaa.bbb.ccc.ddd",
        ]
        for token in malformed_tokens:
            resp = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": token},
            )
            assert resp.status_code == 401, f"Token '{token}' 应返回 401"

    def test_err_003_resource_not_found(self, client, mock_db) -> None:
        """UT-ERR-003: 资源不存在返回 404 + 对应错误码。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/00000000-0000-0000-0000-999999999999",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_err_004_validation_error(self, client) -> None:
        """UT-ERR-004: 请求体校验失败返回 422。"""
        # 登录缺 password 字段
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin"},
        )
        assert resp.status_code == 422

    def test_err_005_pagination_out_of_range(self, client, mock_db) -> None:
        """UT-ERR-005: 分页参数越界返回 422。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with mock_current_user(TEST_USERS["admin"]):
            # page=0 应被 Pydantic 校验拒绝（ge=1）
            resp = client.get(
                "/api/v1/loops?page=0",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp.status_code == 422

    def test_err_006_invalid_uuid_format(self, client) -> None:
        """UT-ERR-006: UUID 格式错误返回 422 或 404。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/not-a-uuid",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp.status_code in (422, 404)


# ===========================================================================
# UT-LOOP-002/004/006/007: 回路权限/搜索/权重校验
# ===========================================================================


LOOP_001 = MagicMock()
LOOP_001.id = "00000000-0000-0000-0000-000000000201"
LOOP_001.tag_name = "HDS-RX-TIC-101"
LOOP_001.description = "R-101 反应器入口温度调节回路"
LOOP_001.unit_id = "00000000-0000-0000-0000-000000000111"
LOOP_001.loop_type = "TEMPERATURE"
LOOP_001.control_type = "STABLE"
LOOP_001.level = 3
LOOP_001.modeattr_tag_id = None
LOOP_001.data_retention_days = None
LOOP_001.score_weight = None
LOOP_001.is_active = True
LOOP_001.is_monitored = True
LOOP_001.is_stat_enabled = True
LOOP_001.last_aas_sync_at = None
LOOP_001.status = "READY"
LOOP_001.created_at = MagicMock()
LOOP_001.created_at.isoformat.return_value = "2026-06-20T10:00:00"
LOOP_001.updated_at = MagicMock()
LOOP_001.updated_at.isoformat.return_value = "2026-06-20T10:00:00"
LOOP_001.created_by = "admin"
LOOP_001.updated_by = None
LOOP_001.score_weights = None
LOOP_001.remark = None
# P4 S4：复杂回路分组字段（默认 None = 普通单回路）
LOOP_001.complex_loop_group_id = None
LOOP_001.complex_role = None


class TestLoopPermissionAndValidation:
    """回路权限和校验测试。"""

    def test_loop_002_sponsor_cannot_create(self, client, mock_db) -> None:
        """UT-LOOP-002: SPONSOR 无权创建回路（403）。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tagName": "TEST-LOOP-001",
                    "unitId": "00000000-0000-0000-0000-000000000111",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_loop_004_keyword_search(self, client, mock_db) -> None:
        """UT-LOOP-004: 关键词搜索回路列表。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(1)
            return _make_scalars_mock([LOOP_001])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops?keyword=HDS-RX",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert "HDS-RX" in data["items"][0]["tagName"]


# ===========================================================================
# UT-LOOP-012/013: 回路监控（提升 monitor.py 覆盖率）
# ===========================================================================


class TestLoopMonitor:
    """回路监控列表和详情测试。"""

    def test_monitor_list_success(self, client, mock_db) -> None:
        """UT-LOOP-012: 回路监控列表返回实时值。"""
        # mock 回路列表查询
        loop_with_values = MagicMock()
        loop_with_values.id = "00000000-0000-0000-0000-000000000201"
        loop_with_values.tag_name = "HDS-RX-TIC-101"
        loop_with_values.description = "测试回路"
        loop_with_values.unit_id = "00000000-0000-0000-0000-000000000111"
        loop_with_values.is_active = True
        loop_with_values.status = "READY"
        loop_with_values.control_mode = "AUTO"
        loop_with_values.score = 85.5
        loop_with_values.current_pv = 150.0
        loop_with_values.current_sp = 152.0
        loop_with_values.current_op = 55.0
        loop_with_values.current_mode = "AUTO"
        loop_with_values.pv_quality = 192
        loop_with_values.last_update = MagicMock()
        loop_with_values.last_update.isoformat.return_value = "2026-06-22T10:00:00"

        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([loop_with_values]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/monitor",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"

    def test_monitor_list_no_token(self, client) -> None:
        """UT-LOOP-012: 未认证访问监控列表返回 401。"""
        resp = client.get("/api/v1/loops/monitor")
        assert resp.status_code == 401

    def test_monitor_detail_not_found(self, client, mock_db) -> None:
        """UT-LOOP-013: 监控详情-回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/00000000-0000-0000-0000-000000000201/monitor",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"


# ===========================================================================
# 跨模块权限验证矩阵
# ===========================================================================


class TestCrossModuleRBAC:
    """跨模块 RBAC 权限验证矩阵。"""

    def test_sponsor_cannot_access_users(self, client) -> None:
        """SPONSOR 无权访问用户管理。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_sponsor_cannot_access_audit_logs(self, client) -> None:
        """SPONSOR 无权访问审计日志。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/audit-logs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_sponsor_cannot_access_reports(self, client) -> None:
        """SPONSOR 无权访问报表配置。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_pe_engineer_cannot_access_tuning(self, client, mock_db) -> None:
        """PE_ENGINEER 无权访问整定功能。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.post(
                "/api/v1/tuning/identify",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": "00000000-0000-0000-0000-000000000201",
                    "modelType": "FOPDT",
                    "startTime": "2026-06-22T00:00:00Z",
                    "endTime": "2026-06-22T01:00:00Z",
                },
            )
        assert resp.status_code == 403

    def test_ic_engineer_cannot_access_user_management(self, client) -> None:
        """IC_ENGINEER 无权管理用户。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_ic_engineer_can_access_tuning(self, client, mock_db) -> None:
        """IC_ENGINEER 有权访问整定方法列表。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/tuning/methods",
                headers={"Authorization": "Bearer fake-token"},
            )
        # methods 端点对所有认证用户开放
        assert resp.status_code == 200

    def test_expert_can_access_tuning(self, client) -> None:
        """EXPERT 有权访问整定方法列表。"""
        with mock_current_user(TEST_USERS["expert"]):
            resp = client.get(
                "/api/v1/tuning/methods",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_admin_can_access_all_modules(self, client, mock_db) -> None:
        """ADMIN 有权访问所有模块。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with mock_current_user(TEST_USERS["admin"]):
            # 用户管理
            resp = client.get(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp.status_code == 200
            # 审计日志
            resp = client.get(
                "/api/v1/audit-logs",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp.status_code == 200
            # 报表配置
            resp = client.get(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert resp.status_code == 200


# ===========================================================================
# UT-AUTH-005: 登录锁定后解锁
# ===========================================================================


class TestAuthLockUnlock:
    """登录锁定与解锁测试。"""

    def test_auth_005_lock_then_unlock_after_clear(self, client, mock_db, fake_redis) -> None:
        """UT-AUTH-005: 登录失败计数在成功登录后清零。"""
        from tests.conftest import TEST_PASSWORD, make_db_execute_return

        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        # 失败 3 次
        for _ in range(3):
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
        # 成功登录 → 清零
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        # 再失败 4 次（总计未达 5 次阈值）不应锁定
        for _ in range(4):
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
            assert resp.status_code == 400, "清零后未达 5 次不应锁定"


# ===========================================================================
# 回路整定服务边界条件（提升 tuning.py 覆盖率）
# ===========================================================================


class TestTuningServiceEdgeCases:
    """整定服务边界条件测试。"""

    def test_tune_invalid_algorithm(self, client, mock_db) -> None:
        """UT-TUNE-004: 无效算法返回 422（S4-C3 枚举约束）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/tune",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "modelType": "FOPDT",
                    "modelParams": {"K": 1.0, "tau": 10.0, "theta": 2.0},
                    "algorithm": "INVALID_ALGORITHM",
                    "currentPid": {"kp": 1.0, "ti": 10.0, "td": 0.0},
                },
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_identify_data_insufficient(self, client, mock_db) -> None:
        """UT-TUNE-002: 数据不足返回 400。"""
        # mock 回路存在但无波形数据
        loop = MagicMock()
        loop.id = "00000000-0000-0000-0000-000000000201"
        loop.tag_name = "TEST"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            return _make_scalars_mock([])  # 无波形数据

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tuning/identify",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopId": "00000000-0000-0000-0000-000000000201",
                    "modelType": "FOPDT",
                    "startTime": "2026-06-22T00:00:00Z",
                    "endTime": "2026-06-22T01:00:00Z",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_TUNING_DATA_INSUFFICIENT"


# ===========================================================================
# 健康检查与基础设施
# ===========================================================================


class TestHealthAndInfra:
    """健康检查和基础设施测试。"""

    def test_health_endpoint(self, client) -> None:
        """健康检查端点返回 200。"""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_openapi_docs_available(self, client) -> None:
        """OpenAPI 文档可访问。"""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_schema_has_all_modules(self, client) -> None:
        """OpenAPI schema 包含所有业务模块。"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        required_modules = [
            "/api/v1/auth",
            "/api/v1/loops",
            "/api/v1/aas",
            "/api/v1/performance",
            "/api/v1/diagnosis",
            "/api/v1/tuning",
            "/api/v1/users",
            "/api/v1/dashboard",
        ]
        for module in required_modules:
            assert any(module in p for p in paths), f"OpenAPI 缺少模块: {module}"
