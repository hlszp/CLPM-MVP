"""Loop ledger API tests (S2-LOOP-004).

Covers:
- GET /api/v1/loops (list)
- POST /api/v1/loops (create, ERR_LOOP_DUPLICATE check)
- GET /api/v1/loops/{id} (detail)
- PUT /api/v1/loops/{id} (update)
- DELETE /api/v1/loops/{id} (delete, ERR_LOOP_HAS_TAGS check)
- Status derivation: READY/PARTIAL/INACTIVE
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# 测试用的回路数据
LOOP_001 = MagicMock()
LOOP_001.id = "00000000-0000-0000-0000-000000000201"
LOOP_001.tag_name = "HDS-RX-TIC-101"
LOOP_001.description = "R-101 反应器入口温度调节回路"
LOOP_001.unit_id = "00000000-0000-0000-0000-000000000111"
LOOP_001.score_weight = None
LOOP_001.is_active = True
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
LOOP_001.loop_type = "TEMPERATURE"
LOOP_001.control_type = "STABLE"
LOOP_001.level = 3
LOOP_001.modeattr_tag_id = None
LOOP_001.data_retention_days = None


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


class TestLoopList:
    """GET /api/v1/loops tests."""

    def test_list_loops_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取回路列表。"""
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
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["tagName"] == "HDS-RX-TIC-101"

    def test_list_loops_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/loops")
        assert resp.status_code == 401


class TestLoopCreate:
    """POST /api/v1/loops tests."""

    def test_create_loop_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN/IC_ENGINEER 可以创建回路。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tagName": "NEW-LOOP-001",
                    "description": "测试回路",
                    "isActive": True,
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["tagName"] == "NEW-LOOP-001"
        assert body["data"]["status"] == "PARTIAL"

    def test_create_loop_duplicate(self, client, mock_db, fake_redis) -> None:
        """tag_name 重复返回 ERR_LOOP_DUPLICATE。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_001))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={"tagName": "HDS-RX-TIC-101", "isActive": True},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_LOOP_DUPLICATE"

    def test_create_loop_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能创建回路（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={"tagName": "NEW-LOOP", "isActive": True},
            )
        assert resp.status_code == 403

    def test_create_loop_valid_weight_sum(self, client, mock_db, fake_redis) -> None:
        """评分权重总和为 100 应该成功。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tagName": "NEW-LOOP-002",
                    "isActive": True,
                    "scoreWeights": {
                        "auto_mode_rate": 50,
                        "steady_rate": 0,
                        "accuracy_rate": 0,
                        "fast_rate": 50,
                        "oscillation_rate": 0,
                        "saturation_rate": 0,
                    },
                },
            )
        assert resp.status_code == 201


class TestLoopDetail:
    """GET /api/v1/loops/{id} tests."""

    def test_get_loop_detail_success(self, client, mock_db, fake_redis) -> None:
        """获取回路详情成功。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_001))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/loops/{LOOP_001.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["basicInfo"]["tagName"] == "HDS-RX-TIC-101"
        assert "tagMapping" in data
        assert "runtimeParams" in data
        assert "aasSyncStatus" in data

    def test_get_loop_detail_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"


class TestLoopDelete:
    """DELETE /api/v1/loops/{id} tests."""

    def test_delete_loop_with_tags_fails(self, client, mock_db, fake_redis) -> None:
        """有关联 Tag 的回路不能删除（ERR_LOOP_HAS_TAGS）。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(LOOP_001)
            return _make_scalar_mock(7)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                f"/api/v1/loops/{LOOP_001.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_LOOP_HAS_TAGS"

    def test_delete_loop_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能删除回路（403，仅 ADMIN）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                f"/api/v1/loops/{LOOP_001.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403


class TestStatusDerivation:
    """状态推导逻辑单元测试。"""

    async def test_status_inactive_when_not_active(self) -> None:
        """is_active=False → INACTIVE。"""
        from app.services.loop import derive_loop_status

        loop = MagicMock()
        loop.is_active = False
        db = AsyncMock()
        status = await derive_loop_status(db, loop, mappings={})
        assert status == "INACTIVE"

    async def test_status_partial_when_missing_required(self) -> None:
        """is_active=True 但缺必填 Tag → PARTIAL。"""
        from app.services.loop import derive_loop_status

        loop = MagicMock()
        loop.is_active = True
        db = AsyncMock()
        mappings = {"PV": MagicMock()}
        status = await derive_loop_status(db, loop, mappings=mappings)
        assert status == "PARTIAL"

    async def test_status_ready_when_all_required(self) -> None:
        """is_active=True 且 4 个必填 Tag 齐全 → READY。"""
        from app.services.loop import derive_loop_status

        loop = MagicMock()
        loop.is_active = True
        db = AsyncMock()
        mappings = {role: MagicMock() for role in ("PV", "SP", "OP", "MODE")}
        status = await derive_loop_status(db, loop, mappings=mappings)
        assert status == "READY"


class TestControlModeFilter:
    """controlMode SQL 过滤反向映射单元测试（P2 #23 B2）。

    验证 _control_mode_to_values 与 _mode_value_to_label 保持一致，
    确保 SQL 层 EXISTS 子查询的过滤口径与 Python 层标签生成一致。
    """

    def test_auto_label_maps_to_one(self) -> None:
        from app.services.loop import _control_mode_to_values

        assert _control_mode_to_values("Auto") == [1]

    def test_manual_label_maps_to_zero(self) -> None:
        from app.services.loop import _control_mode_to_values

        assert _control_mode_to_values("Manual") == [0]

    def test_cascade_label_maps_to_two_and_three(self) -> None:
        """Cascade 对应 MODE=2 和 MODE=3（与 _mode_value_to_label 一致）。"""
        from app.services.loop import _control_mode_to_values

        assert _control_mode_to_values("Cascade") == [2, 3]

    def test_case_insensitive(self) -> None:
        from app.services.loop import _control_mode_to_values

        assert _control_mode_to_values("AUTO") == [1]
        assert _control_mode_to_values("auto") == [1]
        assert _control_mode_to_values("Cascade") == [2, 3]

    def test_unknown_label_returns_empty(self) -> None:
        """未识别的标签返回空列表，调用方据此返回空结果。"""
        from app.services.loop import _control_mode_to_values

        assert _control_mode_to_values("Unknown") == []
        assert _control_mode_to_values("Telepathic") == []

    def test_empty_input_returns_empty(self) -> None:
        from app.services.loop import _control_mode_to_values

        assert _control_mode_to_values("") == []
        assert _control_mode_to_values(None) == []

    def test_reverse_mapping_covers_all_known_labels(self) -> None:
        """_mode_value_to_label 的所有已知输出都应有反向映射。"""
        from app.services.loop import _control_mode_to_values, _mode_value_to_label

        known_values = [0, 1, 2, 3]
        labels = {_mode_value_to_label(float(v)) for v in known_values}
        # 移除 None / Unknown
        labels -= {None, "Unknown"}
        for label in labels:
            assert _control_mode_to_values(label), f"标签 {label} 缺失反向映射"

    def test_unknown_mode_value_maps_to_unknown_label(self) -> None:
        """MODE 值不在 {0,1,2,3} 时返回 Unknown 标签（无法 SQL 过滤）。"""
        from app.services.loop import _mode_value_to_label

        assert _mode_value_to_label(99.0) == "Unknown"
        assert _mode_value_to_label(-1.0) == "Unknown"


class TestLoopCreateNewFields:
    """P2 #24/#25: create_loop 接收 controlType/level/modeattrTagId/dataRetentionDays。

    通过验证 service 签名包含这些参数 + LoopCreate schema 字段，确保前端
    声明的字段不再被静默忽略。
    """

    def test_loop_create_schema_has_control_type(self) -> None:
        from app.schemas.loop import LoopCreate

        fields = set(LoopCreate.model_fields.keys())
        assert "controlType" in fields
        assert "level" in fields
        assert "modeattrTagId" in fields
        assert "dataRetentionDays" in fields

    def test_loop_update_schema_has_control_type(self) -> None:
        from app.schemas.loop import LoopUpdate

        fields = set(LoopUpdate.model_fields.keys())
        assert "controlType" in fields
        assert "level" in fields
        assert "modeattrTagId" in fields
        assert "dataRetentionDays" in fields

    def test_loop_create_service_accepts_new_params(self) -> None:
        """create_loop service 签名应包含 control_type 等参数。"""
        import inspect

        from app.services.loop import create_loop

        sig = inspect.signature(create_loop)
        params = set(sig.parameters.keys())
        assert "control_type" in params
        assert "level" in params
        assert "modeattr_tag_id" in params
        assert "data_retention_days" in params

    def test_loop_update_service_accepts_new_params(self) -> None:
        """update_loop service 签名应包含 control_type 等参数。"""
        import inspect

        from app.services.loop import update_loop

        sig = inspect.signature(update_loop)
        params = set(sig.parameters.keys())
        assert "control_type" in params
        assert "level" in params
        assert "modeattr_tag_id" in params
        assert "data_retention_days" in params

    def test_list_loops_service_accepts_control_type(self) -> None:
        """list_loops service 签名应包含 control_type 参数。"""
        import inspect

        from app.services.loop import list_loops

        sig = inspect.signature(list_loops)
        assert "control_type" in sig.parameters


class TestLoopListControlTypeFilter:
    """P2 #24: list 端点 controlType 查询参数过滤。"""

    def test_list_endpoint_accepts_control_type_query(
        self, client, mock_db, fake_redis
    ) -> None:
        """list 端点应接受 controlType 查询参数，不返回 422。"""

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops?controlType=STABLE",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"


class TestLoopListIsActiveMonitorStatusMutex:
    """P3 #42: isActive 与 monitorStatus 语义冲突防护。

    两个参数都映射到 LoopLedger.is_active 字段，同时传不同值会生成
    is_active=X AND is_active=Y → 永远返回空结果。
    """

    def test_conflict_returns_400(self, client, mock_db, fake_redis) -> None:
        """isActive=True 与 monitorStatus=false 同时传入应返回 400 错误。"""

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops?isActive=true&monitorStatus=false",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200  # HTTP 200，但业务 code=400
        body = resp.json()
        assert body["code"] == "400"
        assert "isActive" in body["message"] or "monitorStatus" in body["message"]

    def test_consistent_values_no_error(
        self, client, mock_db, fake_redis
    ) -> None:
        """isActive=True 与 monitorStatus=true 同时传入（值一致）应正常返回。"""

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops?isActive=true&monitorStatus=true",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"

    def test_only_is_active_no_error(
        self, client, mock_db, fake_redis
    ) -> None:
        """仅传 isActive 应正常返回（无 monitorStatus 冲突）。"""

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops?isActive=true",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"

    def test_only_monitor_status_no_error(
        self, client, mock_db, fake_redis
    ) -> None:
        """仅传 monitorStatus 应正常返回（无 isActive 冲突）。"""

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops?monitorStatus=true",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"


class TestImportLoopsTagAutoCreate:
    """P3 #47: TagRegistry 在导入时静默创建防护测试。

    验证 Excel 导入回路时，若 Tag 不存在于 TagRegistry：
    - 不再静默创建（绕过 AAS 同步）
    - 改为显式 logger.warning + 标记 tag_description
    - 提示运维人员后续通过 AAS 同步补全元数据
    """

    async def test_auto_created_tag_has_warning_description(self, mock_db) -> None:
        """自动创建的 Tag 应有清晰的 tag_description 标记。"""
        from app.services.loop import _import_one_row

        # 模拟：PlantNode 不存在 → 新建；LoopLedger 不存在 → 新建；
        # TagRegistry 不存在 → 触发 P3 #47 自动创建路径
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
            # PlantNode 查询 → 返回 None
            if "plant_node" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            # LoopLedger 查询 → 返回 None（新建回路）
            if "loop_ledger" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            # TagRegistry 查询 → 返回 None（触发自动创建）
            if "tag_registry" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        # 记录 db.add 的所有对象
        added_objects: list = []
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.flush = AsyncMock()

        with patch("app.services.loop.logger") as mock_logger:
            await _import_one_row(
                db=mock_db,
                tag_name="T-TEST-001",
                description="测试回路",
                unit_name="单元A",
                is_active=True,
                role_tag_values={"PV": "T-TEST-001-PV"},
                operator="admin",
                plant_node_cache={},
                tag_cache={},
            )

        # 验证 logger.warning 被调用（不再静默创建）
        assert mock_logger.warning.called
        # logger.warning 使用 %s 懒格式化，args[0] 是格式串，args[1:] 是参数
        warning_call = mock_logger.warning.call_args
        warning_fmt = warning_call.args[0]
        warning_params = warning_call.args[1:]
        assert "Excel 导入自动创建 Tag" in warning_fmt
        assert "T-TEST-001-PV" in warning_params

        # 验证自动创建的 TagRegistry 有清晰的 tag_description 标记
        tag_objs = [o for o in added_objects if hasattr(o, "tag_description")]
        assert len(tag_objs) >= 1
        tag = tag_objs[0]
        assert tag.tag_description == "[Excel 导入自动创建，未通过 AAS 同步，元数据待补全]"
        assert tag.is_linked is True

    async def test_existing_tag_no_warning_logged(self, mock_db) -> None:
        """Tag 已存在时不应触发自动创建警告。"""
        from app.services.loop import _import_one_row

        existing_tag = MagicMock()
        existing_tag.id = "existing-tag-id"
        existing_tag.is_linked = False

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
            if "plant_node" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            if "loop_ledger" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            if "tag_registry" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(existing_tag)
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        with patch("app.services.loop.logger") as mock_logger:
            await _import_one_row(
                db=mock_db,
                tag_name="T-TEST-002",
                description="测试回路",
                unit_name="单元B",
                is_active=True,
                role_tag_values={"PV": "T-TEST-002-PV"},
                operator="admin",
                plant_node_cache={},
                tag_cache={},
            )

        # Tag 已存在 → 不应触发自动创建警告
        auto_create_warnings = [
            call for call in mock_logger.warning.call_args_list
            if "Excel 导入自动创建 Tag" in (call.args[0] if call.args else "")
        ]
        assert len(auto_create_warnings) == 0
        # 已存在 Tag 的 is_linked 应被置为 True
        assert existing_tag.is_linked is True

    async def test_tag_cache_prevents_duplicate_creation(self, mock_db) -> None:
        """同一 Tag 在多行导入中只创建一次（通过 tag_cache 缓存）。"""
        from app.services.loop import _import_one_row

        tag_cache: dict[str, str] = {}

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
            if "plant_node" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            if "loop_ledger" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            # TagRegistry 查询 — 第 2 次调用时 tag_cache 已有缓存，不会执行到这里
            if "tag_registry" in compiled and "select" in compiled:
                return _make_scalar_one_or_none_mock(None)
            return _make_scalar_one_or_none_mock(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        added_objects: list = []
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.flush = AsyncMock()

        with patch("app.services.loop.logger") as mock_logger:
            # 第 1 行：Tag 不存在 → 自动创建 + 警告
            await _import_one_row(
                db=mock_db,
                tag_name="T-TEST-003",
                description="测试回路 1",
                unit_name="单元C",
                is_active=True,
                role_tag_values={"PV": "T-TEST-003-PV"},
                operator="admin",
                plant_node_cache={},
                tag_cache=tag_cache,
            )
            first_warning_count = mock_logger.warning.call_count

            # 第 2 行：相同 Tag → 通过 tag_cache 命中，不再查询 DB、不再创建
            await _import_one_row(
                db=mock_db,
                tag_name="T-TEST-004",
                description="测试回路 2",
                unit_name="单元C",
                is_active=True,
                role_tag_values={"PV": "T-TEST-003-PV"},  # 同一个 Tag
                operator="admin",
                plant_node_cache={},
                tag_cache=tag_cache,
            )

        # 第 2 次不应触发新的警告
        assert mock_logger.warning.call_count == first_warning_count
        # tag_cache 应缓存了该 Tag
        assert "T-TEST-003-PV" in tag_cache
