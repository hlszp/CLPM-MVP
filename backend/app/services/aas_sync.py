"""AAS integration service — OPC UA sync + Mock provider (IDS v3.2 §2.2.5~2.2.6).

设计要点：
- 开发环境无真实 AAS 服务器，通过 ``AAS_MOCK_MODE=true`` 切换至 MockAasProvider
- 同步逻辑：读取 AAS 所有 Tag → 与 tag_registry 对比 → 批量 upsert
- **严禁任何 Write 操作到 AAS**（绝对只读边界）
- 失败重试 3 次，指数退避
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BizError
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# Tag 类型枚举
TAG_TYPES = ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D", "OTHER")

# 同步重试参数
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # 指数退避基数（秒）


# ---------------------------------------------------------------------------
# Mock AAS Provider
# ---------------------------------------------------------------------------


class MockAasProvider:
    """Mock AAS 数据源（开发环境无真实 AAS 时使用）。

    生成约 50 条示例 Tag 数据，覆盖 PV/SP/OP/MODE/PID_P/PID_I/PID_D 类型。
    """

    def __init__(self) -> None:
        # 7 个示例回路前缀，每个回路生成 7 个 Tag = 49 条，加 1 条 OTHER = 50 条
        self._loop_prefixes = [
            ("HDS-RX-TIC-101", "R-101 反应器入口温度"),
            ("HDS-FR-FIC-201", "E-201 分馏塔进料流量"),
            ("HDC-RX-TIC-301", "R-301 反应器入口温度"),
            ("HDC-FR-FIC-401", "E-401 分馏塔进料流量"),
            ("SZB-AD-PIC-501", "SZB-AD 吸附系统压力"),
            ("HDS-RX-LIC-102", "R-101 反应器液位"),
            ("HDC-RX-LIC-302", "R-301 反应器液位"),
        ]

    async def read_all_tags(self) -> list[dict[str, Any]]:
        """读取所有 Tag（模拟）。返回 Tag 字典列表。"""
        # 模拟网络延迟
        await asyncio.sleep(0.05)

        tags: list[dict[str, Any]] = []
        # 每个回路生成 7 个 Tag
        for prefix, desc in self._loop_prefixes:
            for tag_type in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"):
                tag_name = f"T-{prefix.split('-')[-2]}-{prefix.split('-')[-1]}-{tag_type}"
                # 简化命名，避免与种子数据冲突
                tag_name = f"{prefix}.{tag_type}"
                tags.append(
                    {
                        "tag_name": tag_name,
                        "tag_description": f"{desc} {tag_type}",
                        "tag_type": tag_type,
                        "current_value": self._mock_value(tag_type),
                        "quality": self._mock_quality(tag_type),
                    }
                )
        # 1 条 OTHER 类型 Tag
        tags.append(
            {
                "tag_name": "T-MOCK-OTHER-001",
                "tag_description": "Mock 其他类型 Tag",
                "tag_type": "OTHER",
                "current_value": 0.0,
                "quality": "GOOD",
            }
        )
        return tags

    @staticmethod
    def _mock_value(tag_type: str) -> float:
        """根据 Tag 类型生成模拟值。"""
        if tag_type == "PV":
            return round(random.uniform(50.0, 400.0), 2)
        if tag_type == "SP":
            return round(random.uniform(50.0, 400.0), 2)
        if tag_type == "OP":
            return round(random.uniform(0.0, 100.0), 2)
        if tag_type == "MODE":
            return float(random.choice([0, 1, 2, 3]))
        if tag_type == "PID_P":
            return round(random.uniform(0.5, 5.0), 3)
        if tag_type == "PID_I":
            return round(random.uniform(0.1, 2.0), 3)
        if tag_type == "PID_D":
            return round(random.uniform(0.0, 1.0), 3)
        return 0.0

    @staticmethod
    def _mock_quality(tag_type: str) -> str:
        """PV 类型携带质量码（90% GOOD, 5% BAD, 5% UNCERTAIN）。"""
        if tag_type != "PV":
            return "GOOD"
        r = random.random()
        if r < 0.9:
            return "GOOD"
        if r < 0.95:
            return "BAD"
        return "UNCERTAIN"


# ---------------------------------------------------------------------------
# Real AAS Provider (OPC UA via asyncua)
# ---------------------------------------------------------------------------


class RealAasProvider:
    """真实 AAS OPC UA 客户端（生产环境使用）。

    **绝对只读**：仅调用 read 操作，禁止任何 write/subscribe 副作用。
    """

    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = endpoint or settings.AAS_ENDPOINT

    async def read_all_tags(self) -> list[dict[str, Any]]:
        """通过 OPC UA 读取所有 Tag。失败抛 BizError(ERR_AAS_CONNECTION_FAILED)。"""
        try:
            # 延迟导入，避免开发环境未安装 asyncua 时报错
            from asyncua import Client
        except ImportError as exc:  # pragma: no cover
            raise BizError(
                code="ERR_AAS_CONNECTION_FAILED",
                message="asyncua 库未安装，无法连接 AAS",
                status_code=500,
            ) from exc

        tags: list[dict[str, Any]] = []
        try:
            async with Client(self.endpoint) as client:
                # 读取 AAS 命名空间下的所有 Tag 节点
                # 这里假设 AAS 服务器在 ns=2 下暴露 Tag 节点
                # 实际实现需根据 AAS 服务器具体地址空间调整
                root = client.nodes.objects
                children = await root.get_children()
                for child in children:
                    try:
                        browse_name = await child.read_browse_name()
                        display_name = await child.read_display_name()
                        value = await child.read_value()
                        # 简化：根据 browse_name 推断 tag_type
                        tag_type = self._infer_tag_type(browse_name)
                        tags.append(
                            {
                                "tag_name": browse_name,
                                "tag_description": display_name.Text,
                                "tag_type": tag_type,
                                "current_value": float(value) if value is not None else None,
                                "quality": "GOOD",
                            }
                        )
                    except Exception:  # pragma: no cover  noqa: BLE001
                        # 跳过无法读取的节点
                        continue
        except Exception as exc:
            raise BizError(
                code="ERR_AAS_CONNECTION_FAILED",
                message=f"AAS 连接失败: {exc}",
                status_code=502,
            ) from exc
        return tags

    @staticmethod
    def _infer_tag_type(browse_name: str) -> str:
        """根据 browse_name 推断 Tag 类型。"""
        name_upper = browse_name.upper()
        for tag_type in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"):
            if tag_type in name_upper:
                return tag_type
        return "OTHER"


# ---------------------------------------------------------------------------
# Sync service
# ---------------------------------------------------------------------------


def get_aas_provider() -> MockAasProvider | RealAasProvider:
    """根据配置返回 AAS Provider 实例。"""
    if settings.AAS_MOCK_MODE:
        return MockAasProvider()
    return RealAasProvider()


async def _retry_async(
    func: Any,
    args: tuple = (),
    kwargs: dict | None = None,
    max_retries: int = MAX_RETRIES,
) -> Any:
    """带指数退避的重试包装器。"""
    kwargs = kwargs or {}
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except BizError:
            # 业务错误（如 ERR_AAS_CONNECTION_FAILED）直接重试
            last_exc = exc = None
            import sys

            exc = sys.exc_info()[1]
            last_exc = exc
            if attempt == max_retries:
                raise
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            logger.warning("AAS 同步第 %d 次尝试失败，%ds 后重试: %s", attempt, wait, exc)
            await asyncio.sleep(wait)
        except Exception as exc:
            last_exc = exc
            if attempt == max_retries:
                raise BizError(
                    code="ERR_AAS_CONNECTION_FAILED",
                    message=f"AAS 同步失败（重试 {max_retries} 次后仍失败）: {exc}",
                    status_code=502,
                ) from exc
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            logger.warning("AAS 同步第 %d 次尝试异常，%ds 后重试: %s", attempt, wait, exc)
            await asyncio.sleep(wait)
    # 不会执行到这里
    raise last_exc  # type: ignore[misc]


async def sync_tags_from_aas(db: AsyncSession) -> dict[str, Any]:
    """从 AAS 同步 Tag 到 tag_registry。

    同步完成后更新 sys_config 中的 aas.last_sync_status/aas.last_sync_at，
    供前端轮询 GET /aas/config 判断同步进度。

    Returns:
        统计信息 dict：{total, inserted, updated, unchanged, duration_ms}
    """
    from app.services.aas_config import (
        SYNC_STATUS_FAILED,
        SYNC_STATUS_PROCESSING,
        SYNC_STATUS_SUCCESS,
        set_last_sync_status,
    )

    start_time = datetime.now(UTC).replace(tzinfo=None)
    provider = get_aas_provider()

    # 防御性：若 trigger 端点未设置 PROCESSING（如定时任务入口），此处补设
    try:
        await set_last_sync_status(db, SYNC_STATUS_PROCESSING)
    except Exception:  # pragma: no cover  noqa: BLE001
        logger.warning("设置 PROCESSING 状态失败（继续同步）", exc_info=True)

    try:
        # 带重试地读取 AAS
        aas_tags = await _retry_async(provider.read_all_tags)

        # 读取现有 tag_registry（按 tag_name 索引）
        result = await db.execute(select(TagRegistry))
        existing_tags = {t.tag_name: t for t in result.scalars().all()}

        now = datetime.now(UTC).replace(tzinfo=None)
        inserted = 0
        updated = 0
        unchanged = 0

        for aas_tag in aas_tags:
            tag_name = aas_tag["tag_name"]
            existing = existing_tags.get(tag_name)
            if existing is None:
                # 新增
                new_tag = TagRegistry(
                    id=str(uuid4()),
                    tag_name=tag_name,
                    tag_description=aas_tag.get("tag_description"),
                    tag_type=aas_tag.get("tag_type", "OTHER"),
                    current_value=aas_tag.get("current_value"),
                    quality=aas_tag.get("quality"),
                    last_sync_at=now,
                    is_linked=False,
                )
                db.add(new_tag)
                inserted += 1
            else:
                # 更新（值或质量码变化时）
                changed = (
                    existing.current_value != aas_tag.get("current_value")
                    or existing.quality != aas_tag.get("quality")
                    or existing.tag_description != aas_tag.get("tag_description")
                )
                if changed:
                    existing.current_value = aas_tag.get("current_value")
                    existing.quality = aas_tag.get("quality")
                    existing.tag_description = aas_tag.get("tag_description")
                    existing.last_sync_at = now
                    updated += 1
                else:
                    existing.last_sync_at = now
                    unchanged += 1

        await db.commit()

        # 同步成功：更新状态为 SUCCESS（带完成时间）
        try:
            await set_last_sync_status(db, SYNC_STATUS_SUCCESS, sync_at=now)
        except Exception:  # pragma: no cover  noqa: BLE001
            logger.warning("设置 SUCCESS 状态失败（同步已完成）", exc_info=True)

        duration_ms = int((datetime.now(UTC).replace(tzinfo=None) - start_time).total_seconds() * 1000)
        stats = {
            "total": len(aas_tags),
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "duration_ms": duration_ms,
        }
        logger.info("AAS 同步完成: %s", stats)
        return stats
    except Exception as exc:
        # 同步失败：更新状态为 FAILED（不掩盖异常，仍向上抛出）
        try:
            await set_last_sync_status(db, SYNC_STATUS_FAILED)
        except Exception:  # pragma: no cover  noqa: BLE001
            logger.warning("设置 FAILED 状态失败", exc_info=True)
        raise


async def test_aas_connection(endpoint: str | None = None) -> dict[str, Any]:
    """测试 AAS 连接（不写入数据库）。

    Returns:
        {success, latencyMs, message}
    """
    start = datetime.now(UTC).replace(tzinfo=None)
    try:
        if settings.AAS_MOCK_MODE:
            # Mock 模式：直接返回成功
            await asyncio.sleep(0.05)
            latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
            return {
                "success": True,
                "latencyMs": latency,
                "message": "Mock 模式连接成功",
            }
        # 真实模式：尝试连接 OPC UA
        provider = RealAasProvider(endpoint=endpoint)
        await _retry_async(provider.read_all_tags, max_retries=1)
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
        return {
            "success": True,
            "latencyMs": latency,
            "message": "连接成功",
        }
    except BizError as exc:
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
        return {
            "success": False,
            "latencyMs": latency,
            "message": exc.message,
        }
    except Exception as exc:  # pragma: no cover  noqa: BLE001
        latency = int((datetime.now(UTC).replace(tzinfo=None) - start).total_seconds() * 1000)
        return {
            "success": False,
            "latencyMs": latency,
            "message": f"连接失败: {exc}",
        }


__all__ = [
    "MockAasProvider",
    "RealAasProvider",
    "get_aas_provider",
    "sync_tags_from_aas",
    "test_aas_connection",
]
