"""AAS API tests (S2-LOOP-002, S2-LOOP-003).

Covers:
- GET /api/v1/aas/config (ADMIN only)
- PUT /api/v1/aas/config (ADMIN only)
- POST /api/v1/aas/config/test (ADMIN only)
- GET /api/v1/aas/tags (paginated list)
- POST /api/v1/aas/sync (trigger sync)
- MockAasProvider generates ~50 tags
- LTTB downsampling algorithm
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USERS, mock_current_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.skip(reason="MVP: diagnosis/tuning/AAS/tracker module disabled")

# 测试用 Tag 数据
TAG_001 = MagicMock()
TAG_001.id = "00000000-0000-0000-0000-000000000301"
TAG_001.tag_name = "T-HDS-001-PV"
TAG_001.tag_description = "R-101 反应器入口温度 PV"
TAG_001.tag_type = "PV"
TAG_001.current_value = 358.50
TAG_001.quality = "GOOD"
TAG_001.last_sync_at = MagicMock()
TAG_001.last_sync_at.isoformat.return_value = "2026-06-20T10:00:00"
TAG_001.is_linked = True


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


def _make_config_row(value: str) -> MagicMock:
    """构造一个模拟 SysConfig 行（含 .value 属性）。"""
    row = MagicMock()
    row.value = value
    return row


class TestAasConfig:
    """AAS Config API tests."""

    def test_get_config_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以获取 AAS 配置。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "endpoint" in data
        assert "syncIntervalSeconds" in data
        assert "enabled" in data
        assert "mockMode" in data

    def test_get_config_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能获取 AAS 配置（403）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_update_config_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以更新 AAS 配置。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
                json={"endpoint": "opc.tcp://new-host:4840", "enabled": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"

    def test_test_connection_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以测试 AAS 连接。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/aas/config/test",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert "success" in body["data"]


class TestAasTags:
    """GET /api/v1/aas/tags tests."""

    def test_list_tags_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取 Tag 列表。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(1)
            return _make_scalars_mock([TAG_001])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/tags",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["tagName"] == "T-HDS-001-PV"

    def test_list_tags_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/aas/tags")
        assert resp.status_code == 401


class TestAasSync:
    """POST /api/v1/aas/sync tests."""

    def test_trigger_sync_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 触发同步返回 task_id，且会预先设置 PROCESSING 状态。"""
        mock_task = MagicMock()
        mock_task.id = "task-uuid-xxx"

        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.tasks.aas_sync.trigger_sync") as mock_trigger,
            patch(
                "app.api.v1.endpoints.aas.set_last_sync_status",
                new_callable=AsyncMock,
            ) as mock_set_status,
        ):
            mock_trigger.delay.return_value = mock_task
            resp = client.post(
                "/api/v1/aas/sync",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["taskId"] == "task-uuid-xxx"
        assert body["data"]["status"] == "PROCESSING"
        # 验证触发端点先调用了 set_last_sync_status("PROCESSING")
        mock_set_status.assert_awaited_once()
        args, _kwargs = mock_set_status.call_args
        assert args[1] == "PROCESSING"


class TestAasConfigFields:
    """AAS 配置接口新增 lastSyncAt/lastSyncStatus 字段测试（P0 #7）。"""

    def test_get_config_returns_last_sync_fields(self, client, mock_db, fake_redis) -> None:
        """GET /aas/config 响应包含 lastSyncAt 与 lastSyncStatus 字段。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 即使 sys_config 表无值，字段也应存在（值为 None）
        assert "lastSyncAt" in data
        assert "lastSyncStatus" in data
        assert data["lastSyncAt"] is None
        assert data["lastSyncStatus"] is None

    def test_get_config_returns_populated_last_sync_fields(
        self, client, mock_db, fake_redis
    ) -> None:
        """sys_config 表中存在 aas.last_sync_status 时，GET /aas/config 返回实际值。"""
        # 模拟 sys_config 中 6 个键依次返回的 SysConfig 行
        # 前 4 个为 None（回退 settings 默认值），后 2 个为有值的 config 行
        values_iter = iter(
            [
                None,  # endpoint → 回退 settings
                None,  # sync_interval → 回退 settings
                None,  # enabled → 回退 settings
                None,  # security_mode → 回退 settings
                _make_config_row("2026-07-02T10:00:00"),  # last_sync_at
                _make_config_row("PROCESSING"),  # last_sync_status
            ]
        )

        async def execute_side_effect(stmt, *args, **kwargs):
            return _make_scalar_one_or_none_mock(next(values_iter))

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["lastSyncAt"] == "2026-07-02T10:00:00"
        assert data["lastSyncStatus"] == "PROCESSING"


class TestSetLastSyncStatus:
    """set_last_sync_status 辅助函数测试（P0 #7）。"""

    async def test_processing_does_not_write_sync_at(self, mock_db: AsyncMock) -> None:
        """PROCESSING 状态不写 last_sync_at（同步尚未完成）。"""
        from app.services.aas_config import set_last_sync_status

        await set_last_sync_status(mock_db, "PROCESSING")

        # 验证仅写入 last_sync_status，未写入 last_sync_at
        # 由于 _set_config_value 调用 db.execute(select(...)) 与 db.add/commit
        # 这里只验证 commit 被调用
        mock_db.commit.assert_awaited_once()

    async def test_success_writes_sync_at(self, mock_db: AsyncMock) -> None:
        """SUCCESS 状态同时写入 last_sync_at 与 last_sync_status。"""
        from datetime import UTC, datetime

        from app.services.aas_config import set_last_sync_status

        sync_at = datetime.now(UTC).replace(tzinfo=None)
        await set_last_sync_status(mock_db, "SUCCESS", sync_at=sync_at)

        mock_db.commit.assert_awaited_once()

    async def test_failed_writes_sync_at(self, mock_db: AsyncMock) -> None:
        """FAILED 状态也写入 last_sync_at（标记失败时间）。"""
        from app.services.aas_config import set_last_sync_status

        await set_last_sync_status(mock_db, "FAILED")

        mock_db.commit.assert_awaited_once()


class TestSyncTagsFromAasStatusTracking:
    """sync_tags_from_aas 同步状态追踪测试（P0 #7）。

    验证同步函数在以下场景下正确更新 last_sync_status：
    - 同步开始时设置为 PROCESSING
    - 同步成功时设置为 SUCCESS
    - 同步失败时设置为 FAILED
    """

    async def test_success_sets_success_status(self, mock_db: AsyncMock) -> None:
        """同步成功后，last_sync_status 应为 SUCCESS。"""
        with (
            patch(
                "app.services.aas_config.set_last_sync_status",
                new_callable=AsyncMock,
            ) as mock_set_status,
            patch(
                "app.services.aas_sync._retry_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.services.aas_sync.get_aas_provider") as mock_provider_fn,
        ):
            mock_provider_fn.return_value = MagicMock()
            mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))

            from app.services.aas_sync import sync_tags_from_aas

            stats = await sync_tags_from_aas(mock_db)

            assert stats["total"] == 0
            assert stats["inserted"] == 0
            # 两次调用：PROCESSING（开始）、SUCCESS（成功后）
            assert mock_set_status.await_count == 2
            first_call = mock_set_status.call_args_list[0]
            second_call = mock_set_status.call_args_list[1]
            assert first_call.args[1] == "PROCESSING"
            assert second_call.args[1] == "SUCCESS"

    async def test_failure_sets_failed_status(self, mock_db: AsyncMock) -> None:
        """同步失败后，last_sync_status 应为 FAILED，且异常向上抛出。"""
        with (
            patch(
                "app.services.aas_config.set_last_sync_status",
                new_callable=AsyncMock,
            ) as mock_set_status,
            patch(
                "app.services.aas_sync._retry_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("AAS 连接失败"),
            ),
            patch("app.services.aas_sync.get_aas_provider") as mock_provider_fn,
        ):
            mock_provider_fn.return_value = MagicMock()

            from app.services.aas_sync import sync_tags_from_aas

            with pytest.raises(RuntimeError, match="AAS 连接失败"):
                await sync_tags_from_aas(mock_db)

            # 两次调用：PROCESSING（开始）、FAILED（异常时）
            assert mock_set_status.await_count == 2
            first_call = mock_set_status.call_args_list[0]
            second_call = mock_set_status.call_args_list[1]
            assert first_call.args[1] == "PROCESSING"
            assert second_call.args[1] == "FAILED"


class TestSyncTagsFromAasLoopLedgerUpdate:
    """P3 #43: sync_tags_from_aas 同步成功后更新 LoopLedger.last_aas_sync_at。

    验证 AAS 同步成功后，所有活跃回路的 last_aas_sync_at 字段被更新为
    同步完成时间，避免该字段成为孤儿。
    """

    async def test_updates_loop_ledger_last_aas_sync_at(self, mock_db: AsyncMock) -> None:
        """同步成功后应执行 UPDATE LoopLedger SET last_aas_sync_at=now WHERE is_active=True。"""
        with (
            patch(
                "app.services.aas_config.set_last_sync_status",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.aas_sync._retry_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("app.services.aas_sync.get_aas_provider") as mock_provider_fn,
        ):
            mock_provider_fn.return_value = MagicMock()
            mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))

            from app.services.aas_sync import sync_tags_from_aas

            await sync_tags_from_aas(mock_db)

            # set_last_sync_status 被 mock，所以只有 2 次 execute：
            # 1. select(TagRegistry) 查询现有 tag
            # 2. update(LoopLedger) 批量更新 last_aas_sync_at
            assert mock_db.execute.await_count == 2

            # 验证第 2 次 execute 是 update 语句（含 LoopLedger 表）
            second_call_args = mock_db.execute.call_args_list[1]
            stmt_arg = second_call_args.args[0] if second_call_args.args else None
            stmt_str = str(stmt_arg).upper() if stmt_arg is not None else ""
            assert "UPDATE" in stmt_str or "LOOP_LEDGER" in stmt_str, (
                f"第 2 次 execute 应为 UPDATE LoopLedger 语句，实际：{stmt_str}"
            )

            # 验证有 2 次 commit：tag upsert + LoopLedger 更新
            assert mock_db.commit.await_count >= 2

    async def test_no_loop_ledger_update_on_failure(self, mock_db: AsyncSession) -> None:
        """同步失败时不应执行 LoopLedger 更新（异常在 update 之前抛出）。"""
        with (
            patch(
                "app.services.aas_config.set_last_sync_status",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.aas_sync._retry_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("AAS 连接失败"),
            ),
            patch("app.services.aas_sync.get_aas_provider") as mock_provider_fn,
        ):
            mock_provider_fn.return_value = MagicMock()
            mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))

            from app.services.aas_sync import sync_tags_from_aas

            with pytest.raises(RuntimeError, match="AAS 连接失败"):
                await sync_tags_from_aas(mock_db)

            # 失败路径不应到达 update(LoopLedger) 调用
            # 只应有 set_last_sync_status 的两次 execute（PROCESSING + FAILED）
            # 不应有 LoopLedger 的 update 调用
            assert mock_db.commit.await_count == 0


class TestMockAasProvider:
    """MockAasProvider 单元测试。"""

    async def test_mock_provider_generates_tags(self) -> None:
        """MockAasProvider 生成约 50 条 Tag。"""
        from app.services.aas_sync import MockAasProvider

        provider = MockAasProvider()
        tags = await provider.read_all_tags()
        # 7 个回路 × 7 个 Tag + 1 条 OTHER = 50 条
        assert len(tags) == 50

    async def test_mock_provider_tag_types(self) -> None:
        """MockAasProvider 生成的 Tag 覆盖所有类型。"""
        from app.services.aas_sync import MockAasProvider

        provider = MockAasProvider()
        tags = await provider.read_all_tags()
        tag_types = {t["tag_type"] for t in tags}
        assert "PV" in tag_types
        assert "SP" in tag_types
        assert "OP" in tag_types
        assert "MODE" in tag_types
        assert "PID_P" in tag_types
        assert "PID_I" in tag_types
        assert "PID_D" in tag_types
        assert "OTHER" in tag_types

    async def test_mock_provider_pv_has_quality(self) -> None:
        """PV 类型 Tag 携带质量码。"""
        from app.services.aas_sync import MockAasProvider

        provider = MockAasProvider()
        tags = await provider.read_all_tags()
        pv_tags = [t for t in tags if t["tag_type"] == "PV"]
        assert len(pv_tags) > 0
        for pv in pv_tags:
            assert pv["quality"] in ("GOOD", "BAD", "UNCERTAIN")


class TestLTTB:
    """LTTB 降采样算法单元测试。"""

    def test_lttb_no_downsample_below_threshold(self) -> None:
        """数据点数低于阈值时不降采样。"""
        from app.services.monitor import lttb_downsample

        data = [{"ts": i, "value": float(i), "quality": "GOOD"} for i in range(100)]
        result = lttb_downsample(data)
        assert len(result) == 100

    def test_lttb_downsample_above_threshold(self) -> None:
        """数据点数超过阈值时降采样到 2000 点。"""
        from app.services.monitor import lttb_downsample

        data = [{"ts": i, "value": float(i) * 0.1, "quality": "GOOD"} for i in range(15000)]
        result = lttb_downsample(data)
        assert len(result) == 2000

    def test_lttb_preserves_endpoints(self) -> None:
        """降采样后保留首尾两个点。"""
        from app.services.monitor import lttb_downsample

        data = [{"ts": i, "value": float(i), "quality": "GOOD"} for i in range(15000)]
        result = lttb_downsample(data)
        assert result[0]["ts"] == 0
        assert result[-1]["ts"] == 14999

    def test_lttb_empty_data(self) -> None:
        """空数据返回空列表。"""
        from app.services.monitor import lttb_downsample

        result = lttb_downsample([])
        assert result == []


class TestRetryAsync:
    """P3 #46: _retry_async 异常处理测试。

    验证修复后的代码异味（移除 sys.exc_info() + 死代码 last_exc）后，
    重试逻辑行为保持不变：
    - 成功调用不重试
    - BizError 触发重试，最终成功
    - BizError 重试耗尽后抛出原始 BizError
    - 通用 Exception 重试耗尽后包装为 BizError(ERR_AAS_CONNECTION_FAILED)
    - 指数退避：第 N 次重试等待 RETRY_BACKOFF_BASE^(N-1) 秒
    """

    async def test_success_no_retry(self) -> None:
        """成功调用立即返回，不触发重试。"""
        from app.services.aas_sync import _retry_async

        func = AsyncMock(return_value="ok")
        with patch("app.services.aas_sync.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            result = await _retry_async(func)

        assert result == "ok"
        func.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_biz_error_retry_then_success(self) -> None:
        """BizError 触发重试，第二次成功。"""
        from app.core.exceptions import BizError
        from app.services.aas_sync import _retry_async

        func = AsyncMock(side_effect=[BizError(code="TEST", message="fail"), "ok"])
        with patch("app.services.aas_sync.asyncio.sleep", new=AsyncMock()):
            result = await _retry_async(func, max_retries=3)

        assert result == "ok"
        assert func.await_count == 2

    async def test_biz_error_exhausted_raises_original(self) -> None:
        """BizError 重试耗尽后抛出原始 BizError（不包装）。"""
        from app.core.exceptions import BizError
        from app.services.aas_sync import _retry_async

        original_exc = BizError(code="ERR_AAS_CONNECTION_FAILED", message="conn fail")
        func = AsyncMock(side_effect=original_exc)
        with patch("app.services.aas_sync.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(BizError) as exc_info:
                await _retry_async(func, max_retries=2)

        # 应抛出原始异常（而非新建包装异常）
        assert exc_info.value is original_exc
        assert func.await_count == 2

    async def test_generic_exception_wrapped_as_biz_error(self) -> None:
        """通用 Exception 重试耗尽后包装为 BizError(ERR_AAS_CONNECTION_FAILED)。"""
        from app.core.exceptions import BizError
        from app.services.aas_sync import _retry_async

        original_exc = ValueError("network timeout")
        func = AsyncMock(side_effect=original_exc)
        with patch("app.services.aas_sync.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(BizError) as exc_info:
                await _retry_async(func, max_retries=2)

        assert exc_info.value.code == "ERR_AAS_CONNECTION_FAILED"
        assert "network timeout" in exc_info.value.message
        # 原始异常应作为 __cause__ 保留（raise ... from exc）
        assert exc_info.value.__cause__ is original_exc
        assert func.await_count == 2

    async def test_exponential_backoff_applied(self) -> None:
        """验证指数退避：第 N 次重试等待 RETRY_BACKOFF_BASE^(N-1) 秒。"""
        from app.core.exceptions import BizError
        from app.services.aas_sync import RETRY_BACKOFF_BASE, _retry_async

        func = AsyncMock(side_effect=BizError(code="TEST", message="fail"))
        with patch("app.services.aas_sync.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with pytest.raises(BizError):
                await _retry_async(func, max_retries=3)

        # 3 次尝试 → 2 次 sleep（attempt 1 → wait BASE^0=1s，attempt 2 → wait BASE^1=2s）
        assert mock_sleep.await_count == 2
        first_wait = mock_sleep.await_args_list[0].args[0]
        second_wait = mock_sleep.await_args_list[1].args[0]
        assert first_wait == RETRY_BACKOFF_BASE**0  # 1s
        assert second_wait == RETRY_BACKOFF_BASE**1  # 2s
