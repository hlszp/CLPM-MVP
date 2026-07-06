"""Phase 3 新增端点测试 (P3-T1~T5).

覆盖：
- GET/POST /configs/weight-templates（权重模板管理 5 端点）
- GET/POST /configs/grading-thresholds（定级阈值 2 端点）
- GET /dashboard/board（装置级 KPI 看板）
- GET /dashboard/auto-rate-rt（实时自控率）
- GET /aas/sync-status（AAS 同步状态）
- GET /aas/sync-logs（AAS 同步日志）
- GET /tasks/{task_id}/results（非标任务结果）

设计依据：code-alignment-plan-v1.0.md Phase 3 验证闭环
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_scalar_mock(value):
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _make_scalar_one_or_none_mock(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalars_all_mock(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_first_mock(value):
    result = MagicMock()
    result.first.return_value = value
    return result


def _make_all_mock(items):
    result = MagicMock()
    result.all.return_value = items
    return result


# ===========================================================================
# P3-T1: 权重模板管理端点
# ===========================================================================


class TestWeightTemplates:
    """权重模板管理端点测试."""

    def test_get_default_weight_templates(self, client, mock_db, fake_redis) -> None:
        """未配置时返回国标默认权重模板（version=0）."""
        # sys_config 中无 weight_template.current
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/weight-templates",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 0
        assert len(data["templates"]) == 4
        # STABLE: a=20, f=30, s=50
        stable = next(t for t in data["templates"] if t["controlType"] == "STABLE")
        assert stable["accuracyRate"] == 20
        assert stable["fastRate"] == 30
        assert stable["steadyRate"] == 50
        # 核心权重和 = 100
        for tpl in data["templates"]:
            assert tpl["accuracyRate"] + tpl["fastRate"] + tpl["steadyRate"] == 100

    def test_save_weight_templates_success(self, client, mock_db, fake_redis) -> None:
        """保存权重模板为新版本成功."""
        # 第一次查询：sys_config 无当前模板（返回 None）
        # 后续查询：upsert 时查询已有记录
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        payload = {
            "templates": [
                {
                    "controlType": "STABLE",
                    "autoModeRate": 0,
                    "steadyRate": 50,
                    "accuracyRate": 20,
                    "fastRate": 30,
                    "oscillationRate": 0,
                    "saturationRate": 0,
                }
            ],
            "remark": "测试保存",
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/weight-templates",
                json=payload,
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 1
        assert len(data["templates"]) == 1
        assert data["updatedBy"] == "admin"

    def test_save_weight_templates_invalid_sum(self, client, mock_db, fake_redis) -> None:
        """核心权重和不为 100 时返回 400."""
        payload = {
            "templates": [
                {
                    "controlType": "STABLE",
                    "autoModeRate": 0,
                    "steadyRate": 40,
                    "accuracyRate": 20,
                    "fastRate": 30,
                    "oscillationRate": 0,
                    "saturationRate": 0,
                }
            ],
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/weight-templates",
                json=payload,
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_WEIGHT_SUM_INVALID"

    def test_get_weight_template_history(self, client, mock_db, fake_redis) -> None:
        """查询版本历史."""
        # 第一次查询 current（返回 None → 默认模板）
        # 第二次查询 history（返回 None → 空列表）
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/weight-templates/history",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["currentVersion"] == 0
        assert len(data["items"]) == 1  # 只有当前版本
        assert data["items"][0]["isCurrent"] is True

    def test_restore_defaults(self, client, mock_db, fake_redis) -> None:
        """恢复国标默认值成功."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/weight-templates/restore-defaults",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 1
        assert len(data["templates"]) == 4


# ===========================================================================
# P3-T2: 定级阈值端点
# ===========================================================================


class TestGradingThresholds:
    """定级阈值端点测试."""

    def test_get_default_thresholds(self, client, mock_db, fake_redis) -> None:
        """未配置时返回国标默认定级阈值."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/grading-thresholds",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["thresholds"]) == 5
        assert data["thresholds"][0]["name"] == "EXCELLENT"
        assert data["thresholds"][0]["minScore"] == 90.0
        assert data["thresholds"][0]["maxScore"] == 100.0
        assert data["thresholds"][4]["name"] == "POOR"
        assert data["thresholds"][4]["minScore"] == 0.0

    def test_save_thresholds_success(self, client, mock_db, fake_redis) -> None:
        """更新定级阈值成功."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        payload = {
            "thresholds": [
                {
                    "level": 1,
                    "name": "EXCELLENT",
                    "minScore": 90,
                    "maxScore": 100,
                    "color": "#52c41a",
                },
                {"level": 2, "name": "GOOD", "minScore": 80, "maxScore": 90, "color": "#1890ff"},
                {"level": 3, "name": "FAIR", "minScore": 60, "maxScore": 80, "color": "#faad14"},
                {"level": 4, "name": "WARNING", "minScore": 40, "maxScore": 60, "color": "#fa8c16"},
                {"level": 5, "name": "POOR", "minScore": 0, "maxScore": 40, "color": "#f5222d"},
            ]
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/grading-thresholds",
                json=payload,
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["thresholds"]) == 5
        assert data["updatedBy"] == "admin"

    def test_save_thresholds_not_contiguous(self, client, mock_db, fake_redis) -> None:
        """等级区间不连续时返回 400.

        校验规则：level N 的 minScore == level N+1 的 maxScore。
        本用例将 Level 1 的 minScore 设为 95，Level 2 的 maxScore 仍为 90，
        95 != 90，触发 ERR_GRADING_NOT_CONTIGUOUS。
        """
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        payload = {
            "thresholds": [
                {"level": 1, "name": "EXCELLENT", "minScore": 95, "maxScore": 100},
                {"level": 2, "name": "GOOD", "minScore": 80, "maxScore": 90},  # 90 != 95 → 不连续
                {"level": 3, "name": "FAIR", "minScore": 60, "maxScore": 80},
                {"level": 4, "name": "WARNING", "minScore": 40, "maxScore": 60},
                {"level": 5, "name": "POOR", "minScore": 0, "maxScore": 40},
            ]
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/grading-thresholds",
                json=payload,
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_GRADING_NOT_CONTIGUOUS"

    def test_save_thresholds_wrong_name(self, client, mock_db, fake_redis) -> None:
        """等级名称不匹配时返回 400."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        payload = {
            "thresholds": [
                {"level": 1, "name": "PERFECT", "minScore": 90, "maxScore": 100},  # 错误名称
                {"level": 2, "name": "GOOD", "minScore": 80, "maxScore": 90},
                {"level": 3, "name": "FAIR", "minScore": 60, "maxScore": 80},
                {"level": 4, "name": "WARNING", "minScore": 40, "maxScore": 60},
                {"level": 5, "name": "POOR", "minScore": 0, "maxScore": 40},
            ]
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/configs/grading-thresholds",
                json=payload,
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_GRADING_NAME_MISMATCH"


# ===========================================================================
# P3-T3: Dashboard board + auto-rate-rt
# ===========================================================================


class TestDashboardBoard:
    """装置级 KPI 看板端点测试."""

    def test_get_board_no_plant_id(self, client, mock_db, fake_redis) -> None:
        """无 plantId 时返回全部装置 KPI（空列表）."""
        # board 查询会执行多次 db.execute
        # 无 plantId 时：子查询 + join 查询，返回空列表
        mock_db.execute = AsyncMock(return_value=_make_all_mock([]))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/dashboard/board",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert isinstance(data["items"], list)

    def test_get_board_with_plant_id_not_found(self, client, mock_db, fake_redis) -> None:
        """指定 plantId 但无数据时返回空列表."""
        mock_db.execute = AsyncMock(return_value=_make_first_mock(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/dashboard/board?plantId=00000000-0000-0000-0000-000000000111",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0


class TestDashboardAutoRateRt:
    """实时自控率端点测试."""

    def test_auto_rate_rt_no_loops(self, client, mock_db, fake_redis) -> None:
        """无活跃回路时返回 rate=null."""
        mock_db.execute = AsyncMock(return_value=_make_all_mock([]))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/dashboard/auto-rate-rt",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["rate"] is None
        assert data["totalCount"] == 0
        assert "无活跃回路" in data["message"]


# ===========================================================================
# P3-T4: AAS sync-status + sync-logs
# ===========================================================================


class TestAasSyncStatus:
    """AAS 同步状态端点测试."""

    def test_get_sync_status(self, client, mock_db, fake_redis) -> None:
        """获取 AAS 同步状态."""
        # 多次查询：get_aas_config (6次 sys_config) + tag 统计 (3次)
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(10)
            if "group_by" in compiled:
                return _make_all_mock([])
            # sys_config 查询
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/sync-status",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "tagStats" in data
        assert data["tagStats"]["total"] == 10
        assert "byQuality" in data["tagStats"]


class TestAasSyncLogs:
    """AAS 同步日志端点测试."""

    def test_get_sync_logs_empty(self, client, mock_db, fake_redis) -> None:
        """无同步日志时返回空列表."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if "count" in str(stmt.compile()).lower():
                return _make_scalar_mock(0)
            return _make_scalars_all_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/sync-logs",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert isinstance(data["items"], list)


# ===========================================================================
# P3-T5: Tasks results
# ===========================================================================


class TestTaskResults:
    """非标任务结果端点测试."""

    def test_get_task_results_not_found(self, client, mock_db, fake_redis) -> None:
        """任务不存在时返回空列表 + taskStatus=NOT_FOUND."""
        # mock_db.execute 被 tasks.py 的 _get_task 调用（通过 redis_client.hgetall）
        # 以及 KpiSnapshotCustom 查询
        # 先 mock redis hgetall 返回空（任务不存在）
        fake_redis._strings.clear()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(0)
            return _make_all_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/non-existent-task-id/results",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["taskStatus"] == "NOT_FOUND"

    def test_get_task_results_with_data(self, client, mock_db, fake_redis) -> None:
        """有结果数据时返回分页列表."""
        # mock task data in fake_redis
        fake_redis._strings.clear()

        # mock KpiSnapshotCustom 数据
        snapshot = MagicMock()
        snapshot.id = "00000000-0000-0000-0000-000000000301"
        snapshot.task_id = "test-task-001"
        snapshot.loop_id = "00000000-0000-0000-0000-000000000201"
        snapshot.ts_start = MagicMock()
        snapshot.ts_start.isoformat.return_value = "2026-07-04T10:00:00"
        snapshot.ts_end = MagicMock()
        snapshot.ts_end.isoformat.return_value = "2026-07-04T11:00:00"
        snapshot.score = 85.5
        snapshot.accuracy_rate = 90.0
        snapshot.fast_rate = 80.0
        snapshot.steady_rate = 85.0
        snapshot.effective_auto_rate = 75.0
        snapshot.good_value_rate = 95.0
        snapshot.oscillation_rate = 10.0
        snapshot.saturation_rate = 5.0
        snapshot.auto_mode_rate = 80.0
        snapshot.stiction_index = None
        snapshot.output_trip_index = None
        snapshot.settling_time = 120.5
        snapshot.ideal_settling_time = 100.0
        snapshot.status = "SUCCESS"
        snapshot.confidence_level = "A"
        snapshot.valid_rate = 0.98
        snapshot.algorithm_version = "KPI_CALC_v1.0"
        snapshot.sampling_freq = "1s"
        snapshot.quality_policy = "TDENGINE"
        snapshot.data_lineage = {"test": True}
        snapshot.created_at = MagicMock()
        snapshot.created_at.isoformat.return_value = "2026-07-04T11:05:00"

        loop_tag_name = "HDS-RX-TIC-101"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(1)
            # 返回 (snapshot, loop_tag_name) 元组列表
            result = MagicMock()
            result.all.return_value = [(snapshot, loop_tag_name)]
            return result

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tasks/test-task-001/results",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["loopTagName"] == "HDS-RX-TIC-101"
        assert item["score"] == 85.5
        assert item["accuracyRate"] == 90.0
        assert item["fastRate"] == 80.0
        assert item["steadyRate"] == 85.0
        assert item["confidenceLevel"] == "A"
        assert item["status"] == "SUCCESS"
