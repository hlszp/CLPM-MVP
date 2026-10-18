"""诊断触发条件配置服务（sys_config 存储 + 运行时缓存）.

存储方式参照 ``outlier_params``：sys_config 表 JSON 序列化，
key = ``diagnosis_trigger.current``，结构::

    {
      "scoreThreshold": 60,        // 评分阈值：跌破此值触发诊断
      "concurrency": 5,            // 并发 worker 数
      "minDataPoints": 32,         // 数据最少点数
      "checkupEnabled": true,      // 体检轨是否启用
      "updatedAt": "...", "updatedBy": "..."
    }

生效机制：
- 保存配置后调用 ``apply_runtime()`` 刷新进程内缓存，
  热路径（diagnosis_engine）经 ``get_trigger_config()`` 读取，不查库；
- FastAPI lifespan 与 Celery ``worker_process_init`` 调用
  ``preload_diagnosis_trigger()`` 从 DB 预载，保证 worker 子进程同样生效。

设计依据：整改计划 C6 — 触发条件可配
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.schemas.config import DiagnosisTriggerSaveRequest, DiagnosisTriggerSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# sys_config 键常量
# ---------------------------------------------------------------------------

SYS_CONFIG_KEY = "diagnosis_trigger.current"
SYS_CONFIG_DESC = "诊断触发条件配置（JSON）"

# 默认值（与原硬编码常量一致：SCORE_THRESHOLD=60, CONCURRENCY=5, MIN_DATA_POINTS=32）
_DEFAULTS = DiagnosisTriggerSchema(
    score_threshold=60.0,
    concurrency=5,
    min_data_points=32,
    checkup_enabled=True,
)

# 进程内缓存（预载/保存后刷新，热路径读取）
_cache: DiagnosisTriggerSchema = _DEFAULTS


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# 存储解析 / 运行时应用
# ---------------------------------------------------------------------------


def parse_stored(raw: str | None) -> dict[str, Any] | None:
    """解析 sys_config 存储的 JSON，缺失或损坏时返回 None（回落默认）."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("诊断触发条件配置解析失败，回退默认: %s", exc)
        return None
    if not isinstance(data, dict):
        logger.warning("诊断触发条件配置结构非法（非对象），回退默认")
        return None
    return data


def apply_runtime(stored: dict[str, Any] | None) -> None:
    """将存储配置应用到进程内缓存（保存后/预载时调用）.

    Args:
        stored: ``parse_stored()`` 的结果（None 表示无配置，使用默认值）
    """
    global _cache
    stored = stored or {}
    try:
        _cache = DiagnosisTriggerSchema(
            score_threshold=float(stored.get("scoreThreshold", _DEFAULTS.score_threshold)),
            concurrency=int(stored.get("concurrency", _DEFAULTS.concurrency)),
            min_data_points=int(stored.get("minDataPoints", _DEFAULTS.min_data_points)),
            checkup_enabled=bool(stored.get("checkupEnabled", _DEFAULTS.checkup_enabled)),
            updated_at=stored.get("updatedAt"),
            updated_by=stored.get("updatedBy"),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("诊断触发条件配置应用失败，回退默认: %s", exc)
        _cache = _DEFAULTS


def get_trigger_config() -> DiagnosisTriggerSchema:
    """获取当前触发条件配置（进程内缓存，热路径调用，不查库）."""
    return _cache


def build_stored_payload(
    req: DiagnosisTriggerSaveRequest,
    operator: str,
) -> dict[str, Any]:
    """由保存请求构建存储 JSON（camelCase 键）."""
    return {
        "scoreThreshold": req.score_threshold,
        "concurrency": req.concurrency,
        "minDataPoints": req.min_data_points,
        "checkupEnabled": req.checkup_enabled,
        "updatedAt": _now_iso(),
        "updatedBy": operator,
    }


# ---------------------------------------------------------------------------
# sys_config 读写 + 审计
# ---------------------------------------------------------------------------


async def get_config_value(db: AsyncSession, key: str) -> str | None:
    """读取 sys_config 表中某个 key 的值."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    return cfg.value if cfg else None


async def set_config_value(
    db: AsyncSession,
    key: str,
    value: str,
    description: str | None,
    operator: str,
) -> None:
    """写入 sys_config 表（upsert，不提交）."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    now = _now_naive()
    if cfg is None:
        cfg = SysConfig(
            key=key,
            value=value,
            description=description,
            updated_by=operator,
            updated_at=now,
        )
        db.add(cfg)
    else:
        cfg.value = value
        cfg.description = description or cfg.description
        cfg.updated_by = operator
        cfg.updated_at = now


async def write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志."""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=_now_naive(),
    )
    db.add(log)


async def load_stored_config(db: AsyncSession) -> dict[str, Any] | None:
    """加载 sys_config 中存储的诊断触发条件配置（未配置返回 None）."""
    return parse_stored(await get_config_value(db, SYS_CONFIG_KEY))


async def preload_diagnosis_trigger(db: AsyncSession) -> None:
    """从 sys_config 预载诊断触发条件到进程内缓存.

    供 FastAPI lifespan 与 Celery ``worker_process_init`` 调用，
    预载失败由调用方兜底（回落默认值）。
    """
    stored = await load_stored_config(db)
    apply_runtime(stored)
    logger.info(
        "诊断触发条件已预载: %s",
        "使用 sys_config 配置" if stored else "无配置，使用默认",
    )


__all__ = [
    "SYS_CONFIG_DESC",
    "SYS_CONFIG_KEY",
    "apply_runtime",
    "build_stored_payload",
    "get_config_value",
    "get_trigger_config",
    "load_stored_config",
    "parse_stored",
    "preload_diagnosis_trigger",
    "set_config_value",
    "write_audit",
]
