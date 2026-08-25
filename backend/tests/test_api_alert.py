"""智能预警规则引擎 API 端点测试.

覆盖：
- 规则 CRUD（list/create/get/update/delete/toggle）+ 权限校验
- 订阅 CRUD（list/create/delete）+ 权限校验
- 事件查询与处置（list/get/acknowledge/resolve/false-positive/archive）
- 手动抑制（list/create/delete）
- 审计日志查询
- 全局开关
- 徽章计数

设计：通过 ``patch`` alert_service 层函数，隔离 DB 依赖，专注测试
API 层的路由匹配、权限校验、请求/响应序列化。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_rule_dict(
    rule_id: str = "rule-001",
    rule_code: str = "R001",
    rule_type: str = "THRESHOLD",
    is_enabled: bool = True,
    version: int = 1,
) -> dict:
    return {
        "ruleId": rule_id,
        "ruleCode": rule_code,
        "ruleName": f"规则-{rule_code}",
        "ruleType": rule_type,
        "dsl": {
            "ruleType": rule_type,
            "scope": {"loopSelector": {"type": "ALL"}},
            "condition": {"metric": "PV", "operator": ">", "value": 100},
            "severity": "WARN",
            "actions": [{"type": "CREATE_EVENT"}],
        },
        "description": "测试规则",
        "priority": 100,
        "isEnabled": is_enabled,
        "version": version,
        "createdBy": "admin",
        "createdAt": "2026-08-01T00:00:00",
        "updatedBy": None,
        "updatedAt": None,
    }


def _make_event_dict(
    event_id: str = "evt-001",
    status: str = "ACTIVE",
    severity: str = "WARN",
) -> dict:
    return {
        "eventId": event_id,
        "ruleId": "rule-001",
        "ruleCode": "R001",
        "ruleVersion": 1,
        "loopId": "loop-001",
        "severity": severity,
        "status": status,
        "triggerConditionSnapshot": {"metric": "PV", "actualValue": 150.0},
        "dataWindow": None,
        "triggeredValue": 150.0,
        "confidenceLevel": "B",
        "ruleDslSnapshot": {"ruleType": "THRESHOLD"},
        "trackerId": None,
        "isFalsePositive": False,
        "triggerCount": 1,
        "triggeredAt": "2026-08-01T10:00:00",
        "acknowledgedBy": None,
        "acknowledgedAt": None,
        "resolvedBy": None,
        "resolvedAt": None,
        "resolutionNote": None,
        "loopName": "TAG-001",
    }


def _make_subscription_dict(sub_id: str = "sub-001") -> dict:
    return {
        "subscriptionId": sub_id,
        "ruleId": "rule-001",
        "loopId": "loop-001",
        "scopeType": "LOOP",
        "scopeValue": None,
        "isActive": True,
        "createdBy": "admin",
        "createdAt": "2026-08-01T00:00:00",
    }


def _make_suppression_dict(sup_id: str = "sup-001") -> dict:
    return {
        "suppressionId": sup_id,
        "ruleId": "rule-001",
        "loopId": "loop-001",
        "reason": "维护中",
        "suppressedBy": "admin",
        "startAt": "2026-08-01T00:00:00",
        "endAt": "2026-08-01T08:00:00",
        "isActive": True,
        "createdAt": "2026-08-01T00:00:00",
    }


_VALID_RULE_PAYLOAD = {
    "ruleCode": "R_NEW",
    "ruleName": "新规则",
    "ruleType": "THRESHOLD",
    "dsl": {
        "ruleType": "THRESHOLD",
        "scope": {"loopSelector": {"type": "ALL"}},
        "condition": {"metric": "PV", "operator": ">", "value": 100},
        "severity": "WARN",
        "actions": [{"type": "CREATE_EVENT"}],
    },
    "description": "新规则描述",
    "priority": 50,
    "isEnabled": True,
}


# ===========================================================================
# 规则列表 GET /alert/rules
# ===========================================================================


class TestListRules:
    """GET /alert/rules 规则列表。"""

    def test_list_rules_success(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_rules",
                new_callable=AsyncMock,
                return_value={"total": 1, "items": [_make_rule_dict()]},
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/rules",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["ruleCode"] == "R001"

    def test_list_rules_with_filters(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_rules",
                new_callable=AsyncMock,
                return_value={"total": 0, "items": []},
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/rules?ruleType=THRESHOLD&isEnabled=true&limit=10&offset=0",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        # 验证筛选参数传递
        call_kwargs = m.call_args
        assert call_kwargs.kwargs["rule_type"] == "THRESHOLD"
        assert call_kwargs.kwargs["is_enabled"] is True
        assert call_kwargs.kwargs["limit"] == 10

    def test_list_rules_ic_engineer_can_view(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 有 alert:view 权限，可查看规则列表。"""
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_rules",
                new_callable=AsyncMock,
                return_value={"total": 0, "items": []},
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.get(
                "/api/v1/alert/rules",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200


# ===========================================================================
# 创建规则 POST /alert/rules
# ===========================================================================


class TestCreateRule:
    """POST /alert/rules 创建规则（仅 ADMIN）。"""

    def test_create_rule_disabled_preset_mode(self, client, fake_redis) -> None:
        """预制规则模式：不允许新增规则（403，2026-08-24）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/alert/rules",
                json=_VALID_RULE_PAYLOAD,
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 403

    def test_create_rule_rejects_ic_engineer(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 无权创建规则（仅 ADMIN）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/alert/rules",
                json=_VALID_RULE_PAYLOAD,
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 403

    def test_create_rule_invalid_payload_returns_422(self, client, mock_db, fake_redis) -> None:
        """请求体缺少必填字段返回 422。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/alert/rules",
                json={"ruleCode": "R"},  # 缺 ruleName/ruleType/dsl
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 422


# ===========================================================================
# 规则详情/更新/删除/启停
# ===========================================================================


class TestRuleOperations:
    """规则详情/更新/删除/启停。"""

    def test_get_rule_success(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.get_rule",
                new_callable=AsyncMock,
                return_value=_make_rule_dict(),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/rules/rule-001",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["ruleId"] == "rule-001"

    def test_update_rule_success(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.update_rule",
                new_callable=AsyncMock,
                return_value=_make_rule_dict(version=2),
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/alert/rules/rule-001",
                json={"ruleName": "更新名称", "priority": 10},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == 2
        mock_db.commit.assert_awaited_once()
        # 验证 exclude_unset 透传（仅传修改字段）
        call_kwargs = m.call_args
        assert call_kwargs.args[0] is mock_db
        assert call_kwargs.args[1] == "rule-001"

    def test_update_rule_rejects_ic_engineer(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/alert/rules/rule-001",
                json={"ruleName": "x"},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 403

    def test_delete_rule_success(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.delete_rule",
                new_callable=AsyncMock,
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.delete(
                "/api/v1/alert/rules/rule-001",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["message"] == "规则已删除"
        m.assert_awaited_once()
        mock_db.commit.assert_awaited_once()

    def test_toggle_rule_enable(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.toggle_rule",
                new_callable=AsyncMock,
                return_value=_make_rule_dict(is_enabled=True),
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/alert/rules/rule-001/toggle?enabled=true",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        # 验证 enabled 参数传递
        assert m.call_args.args[2] is True

    def test_toggle_rule_disable(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.toggle_rule",
                new_callable=AsyncMock,
                return_value=_make_rule_dict(is_enabled=False),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/alert/rules/rule-001/toggle?enabled=false",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["isEnabled"] is False


# ===========================================================================
# 订阅 CRUD
# ===========================================================================


class TestSubscriptions:
    """订阅关系 CRUD。"""

    def test_list_rule_subscriptions(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_subscriptions",
                new_callable=AsyncMock,
                return_value=[_make_subscription_dict()],
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/rules/rule-001/subscriptions",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["ruleId"] == "rule-001"

    def test_create_subscription_success(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.create_subscription",
                new_callable=AsyncMock,
                return_value=_make_subscription_dict(),
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/alert/rules/rule-001/subscriptions",
                json={"loopId": "loop-001", "scopeType": "LOOP"},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()

    def test_create_subscription_rejects_pe_engineer(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 无 alert 配置权限。"""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.post(
                "/api/v1/alert/rules/rule-001/subscriptions",
                json={"loopId": "loop-001", "scopeType": "LOOP"},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 403

    def test_list_subscriptions_by_loop(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_subscriptions",
                new_callable=AsyncMock,
                return_value=[_make_subscription_dict()],
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/subscriptions?loopId=loop-001",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert m.call_args.kwargs["loop_id"] == "loop-001"

    def test_delete_subscription(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.delete_subscription",
                new_callable=AsyncMock,
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.delete(
                "/api/v1/alert/subscriptions/sub-001",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()


# ===========================================================================
# 事件查询与处置
# ===========================================================================


class TestEventQuery:
    """事件查询与处置。"""

    def test_list_events_success(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_events",
                new_callable=AsyncMock,
                return_value={"total": 1, "items": [_make_event_dict()]},
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/events?severity=WARN&status=ACTIVE&limit=20",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 1
        # 验证筛选参数
        kwargs = m.call_args.kwargs
        assert kwargs["severity"] == "WARN"
        assert kwargs["status_filter"] == "ACTIVE"
        assert kwargs["limit"] == 20

    def test_get_event_detail(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.get_event",
                new_callable=AsyncMock,
                return_value=_make_event_dict(),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/events/evt-001",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["eventId"] == "evt-001"

    def test_acknowledge_event(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.acknowledge_event",
                new_callable=AsyncMock,
                return_value=_make_event_dict(status="ACKNOWLEDGED"),
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/alert/events/evt-001/acknowledge",
                json={"note": "已查看"},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACKNOWLEDGED"
        mock_db.commit.assert_awaited_once()

    def test_resolve_event(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.resolve_event",
                new_callable=AsyncMock,
                return_value=_make_event_dict(status="RESOLVED"),
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/alert/events/evt-001/resolve",
                json={"resolutionNote": "已处理"},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "RESOLVED"

    def test_mark_false_positive(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.mark_false_positive",
                new_callable=AsyncMock,
                return_value=_make_event_dict(),
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/alert/events/evt-001/false-positive",
                json={"isFalsePositive": True},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert m.call_args.args[2] is True

    def test_archive_event_admin_only(self, client, mock_db, fake_redis) -> None:
        """归档事件仅 ADMIN。"""
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.archive_event",
                new_callable=AsyncMock,
                return_value=_make_event_dict(status="ARCHIVED"),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/alert/events/evt-001/archive",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ARCHIVED"

    def test_archive_event_rejects_ic_engineer(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 无权归档事件。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/alert/events/evt-001/archive",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 403


# ===========================================================================
# 手动抑制
# ===========================================================================


class TestSuppressions:
    """手动抑制 CRUD。"""

    def test_list_suppressions(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_suppressions",
                new_callable=AsyncMock,
                return_value={"total": 1, "items": [_make_suppression_dict()]},
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/suppressions?isActive=true",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 1

    def test_create_suppression(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.create_suppression",
                new_callable=AsyncMock,
                return_value=_make_suppression_dict(),
            ),
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/alert/suppressions",
                json={
                    "ruleId": "rule-001",
                    "loopId": "loop-001",
                    "reason": "维护中",
                    "durationMinutes": 480,
                },
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()

    def test_delete_suppression(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.delete_suppression",
                new_callable=AsyncMock,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.delete(
                "/api/v1/alert/suppressions/sup-001",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200


# ===========================================================================
# 审计日志 / 全局开关 / 徽章
# ===========================================================================


class TestAuditAndGlobal:
    """审计日志、全局开关、徽章。"""

    def test_list_audit_logs(self, client, mock_db, fake_redis) -> None:
        audit_item = {
            "logId": "log-001",
            "ruleId": "rule-001",
            "ruleCode": "R001",
            "operationType": "CREATE",
            "beforeValue": None,
            "afterValue": '{"ruleName": "测试"}',
            "operator": "admin",
            "operatedAt": "2026-08-01T00:00:00",
        }
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.list_audit_logs",
                new_callable=AsyncMock,
                return_value={"total": 1, "items": [audit_item]},
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/audit-logs?operator=admin",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["items"][0]["operationType"] == "CREATE"

    def test_get_global_switch_default(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.get_global_switch",
                new_callable=AsyncMock,
                return_value=True,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/global-switch",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["enabled"] is True

    def test_set_global_switch_admin_only(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.set_global_switch",
                new_callable=AsyncMock,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                "/api/v1/alert/global-switch",
                json={"enabled": False},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["enabled"] is False
        mock_db.commit.assert_awaited_once()

    def test_set_global_switch_rejects_ic_engineer(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/alert/global-switch",
                json={"enabled": False},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 403

    def test_get_badge_count(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.get_badge_count",
                new_callable=AsyncMock,
                return_value=5,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(
                "/api/v1/alert/badge",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 5

    def test_reset_badge_count(self, client, mock_db, fake_redis) -> None:
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.reset_badge",
                new_callable=AsyncMock,
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/alert/badge/reset",
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 0


# ===========================================================================
# Dry-Run 试运行 POST /alert/rules/dry-run
# ===========================================================================


class TestDryRun:
    """POST /alert/rules/dry-run 规则试运行。"""

    def test_dry_run_with_custom_dsl_triggered(self, client, mock_db, fake_redis) -> None:
        """传入自定义 DSL 试运行，规则命中时返回 triggered=True。"""
        dry_run_result = {
            "triggered": True,
            "triggeredValue": 150.0,
            "conditionSnapshot": {
                "metric": "PV",
                "operator": ">",
                "threshold": 100,
                "actualValue": 150.0,
            },
            "severity": "WARN",
            "confidenceLevel": "B",
            "dedupKey": "loop-1+DRY_RUN",
            "currentValues": {"PV": 150.0, "OP": 55.0},
        }
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.dry_run",
                new_callable=AsyncMock,
                return_value=dry_run_result,
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/alert/rules/dry-run",
                json={
                    "loopId": "loop-001",
                    "dsl": {
                        "ruleType": "THRESHOLD",
                        "scope": {"loopSelector": {"type": "ALL"}},
                        "condition": {"metric": "PV", "operator": ">", "value": 100},
                        "severity": "WARN",
                        "actions": [{"type": "CREATE_EVENT"}],
                    },
                },
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["triggered"] is True
        assert data["triggeredValue"] == 150.0
        assert data["severity"] == "WARN"
        assert data["currentValues"]["PV"] == 150.0
        m.assert_awaited_once()

    def test_dry_run_with_rule_id(self, client, mock_db, fake_redis) -> None:
        """传入已有规则 ID 试运行。"""
        dry_run_result = {
            "triggered": False,
            "triggeredValue": None,
            "conditionSnapshot": {"metric": "PV", "reason": "no_data"},
            "severity": "WARN",
            "confidenceLevel": None,
            "dedupKey": "loop-1+rule-001",
            "currentValues": {},
        }
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.dry_run",
                new_callable=AsyncMock,
                return_value=dry_run_result,
            ) as m,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            resp = client.post(
                "/api/v1/alert/rules/dry-run",
                json={"loopId": "loop-001", "ruleId": "rule-001"},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["triggered"] is False
        m.assert_awaited_once()
        # 验证 rule_id 被正确传递
        call_kwargs = m.call_args.kwargs
        assert call_kwargs["rule_id"] == "rule-001"
        assert call_kwargs["dsl"] is None

    def test_dry_run_rejects_sponsor(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 角色无 alert:manage 权限，不能试运行。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/alert/rules/dry-run",
                json={"loopId": "loop-001", "ruleId": "rule-001"},
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 403

    def test_dry_run_with_confidence_level(self, client, mock_db, fake_redis) -> None:
        """试运行可传入模拟可信度等级。"""
        dry_run_result = {
            "triggered": False,
            "triggeredValue": None,
            "conditionSnapshot": {"maxLevel": "B", "actualLevel": "D"},
            "severity": "WARN",
            "confidenceLevel": "D",
            "dedupKey": "loop-1+DRY_RUN",
            "currentValues": {},
        }
        with (
            patch(
                "app.api.v1.endpoints.alert.alert_service.dry_run",
                new_callable=AsyncMock,
                return_value=dry_run_result,
            ) as m,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.post(
                "/api/v1/alert/rules/dry-run",
                json={
                    "loopId": "loop-001",
                    "dsl": {
                        "ruleType": "CONFIDENCE",
                        "scope": {"loopSelector": {"type": "ALL"}},
                        "condition": {"maxLevel": "B"},
                        "severity": "WARN",
                        "actions": [{"type": "CREATE_EVENT"}],
                    },
                    "confidenceLevel": "D",
                },
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["confidenceLevel"] == "D"
        call_kwargs = m.call_args.kwargs
        assert call_kwargs["confidence_level"] == "D"
