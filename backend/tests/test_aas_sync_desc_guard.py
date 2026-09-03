"""AAS 同步描述防回冲测试（WS-C 7-11）。

自 tests/test_aas.py 迁出——该文件因早期模块禁用标记整文件 skip，
本组测试对应的 sync_tags_from_aas 逻辑在 MVP 中实际存活（回路导入
自动创建 tag 的占位描述依赖此规则被 AAS 真实描述覆盖），需保持可运行。

口径：仅当现有 description 为空、与 AAS 本次值一致（覆盖为无操作）、
或为 Excel 导入占位描述（机器写入非人工维护）时才写入；非空且与 AAS
不一致的描述视为手工编辑，予以保留。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    pass


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


class TestAasSyncDescriptionGuard:
    """WS-C 7-11：AAS 同步不回冲手工编辑的描述。"""

    @staticmethod
    def _make_existing_tag(
        tag_name: str,
        description: str | None,
        current_value: float = 1.0,
        quality: str = "GOOD",
    ) -> MagicMock:
        tag = MagicMock()
        tag.tag_name = tag_name
        tag.tag_description = description
        tag.current_value = current_value
        tag.quality = quality
        tag.last_sync_at = None
        return tag

    @staticmethod
    def _aas_tag(
        tag_name: str,
        description: str,
        current_value: float = 1.0,
    ) -> dict:
        return {
            "tag_name": tag_name,
            "tag_description": description,
            "tag_type": "PV",
            "current_value": current_value,
            "quality": "GOOD",
        }

    async def _run_sync(self, mock_db: AsyncMock, existing, aas_tags: list) -> dict:
        with (
            patch(
                "app.services.aas_config.set_last_sync_status",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.aas_sync._retry_async",
                new_callable=AsyncMock,
                return_value=aas_tags,
            ),
            patch("app.services.aas_sync.get_aas_provider") as mock_provider_fn,
        ):
            mock_provider_fn.return_value = MagicMock()
            mock_db.execute = AsyncMock(
                side_effect=[
                    _make_scalars_mock([existing]),  # select(TagRegistry)
                    MagicMock(),  # update(LoopLedger)
                ]
            )

            from app.services.aas_sync import sync_tags_from_aas

            return await sync_tags_from_aas(mock_db)

    async def test_manual_description_not_overwritten(self, mock_db: AsyncMock) -> None:
        """手工编辑过的描述（与 AAS 不一致）不被回冲，值/质量码仍正常更新。"""
        existing = self._make_existing_tag("T-001", "手工修改的描述", current_value=1.0)

        stats = await self._run_sync(
            mock_db, existing, [self._aas_tag("T-001", "AAS 侧描述", current_value=2.0)]
        )

        assert stats["updated"] == 1
        assert existing.tag_description == "手工修改的描述"
        assert existing.current_value == 2.0
        assert existing.last_sync_at is not None

    async def test_empty_description_filled_from_aas(self, mock_db: AsyncMock) -> None:
        """现有描述为空（None）时允许 AAS 填充。"""
        existing = self._make_existing_tag("T-002", None, current_value=1.0)

        stats = await self._run_sync(mock_db, existing, [self._aas_tag("T-002", "AAS 侧描述")])

        assert stats["updated"] == 1
        assert existing.tag_description == "AAS 侧描述"

    async def test_matching_description_counts_unchanged(self, mock_db: AsyncMock) -> None:
        """描述与 AAS 一致且值/质量码无变化时计入 unchanged，last_sync_at 仍刷新。"""
        existing = self._make_existing_tag("T-003", "AAS 侧描述", current_value=1.0)

        stats = await self._run_sync(mock_db, existing, [self._aas_tag("T-003", "AAS 侧描述")])

        assert stats["unchanged"] == 1
        assert stats["updated"] == 0
        assert existing.tag_description == "AAS 侧描述"
        assert existing.last_sync_at is not None

    async def test_import_placeholder_description_overwritten(self, mock_db: AsyncMock) -> None:
        """Excel 导入占位描述（机器写入，非人工维护）允许被 AAS 真实描述覆盖。"""
        from app.models.tag import TagRegistry

        existing = self._make_existing_tag(
            "T-004", TagRegistry.IMPORT_PLACEHOLDER_DESC, current_value=1.0
        )

        stats = await self._run_sync(mock_db, existing, [self._aas_tag("T-004", "AAS 侧真实描述")])

        assert stats["updated"] == 1
        assert existing.tag_description == "AAS 侧真实描述"
