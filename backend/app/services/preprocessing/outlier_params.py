"""8 类异常值检测参数配置服务（sys_config 存储 + 运行时缓存应用）.

存储方式参照 confidence-thresholds：sys_config 表 JSON 序列化，
key = ``outlier_params.current``，结构::

    {
      "thresholds": {"FC": {"baseSamplingFreq": 2, ...}, ...},   // 部分覆盖
      "switches": {"nan": true, "frozen": false, ...},           // 部分覆盖
      "updatedAt": "...", "updatedBy": "..."
    }

生效机制：
- 保存配置后调用 ``apply_runtime()`` 刷新 thresholds 模块的进程内缓存
  （覆盖合并 + 检测开关），热路径（Pipeline/DataPlanner/诊断引擎）不查库；
- FastAPI lifespan 与 Celery ``worker_process_init`` 调用
  ``preload_outlier_params()`` 从 DB 预载，保证 worker 子进程同样生效。

设计依据：算法说明 §3.4.3-3.4.4, PRD §5.5.2-5.5.3
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.data_types import ControlType
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.schemas.config import (
    OutlierParamsSchema,
    OutlierThresholdParams,
    OutlierThresholdViewItem,
)
from app.services.preprocessing.thresholds import (
    DETECTOR_KEYS,
    PARAM_FIELDS,
    get_default_threshold,
    set_detector_switches,
    set_threshold_overrides,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# sys_config 键常量
# ---------------------------------------------------------------------------

SYS_CONFIG_KEY = "outlier_params.current"
SYS_CONFIG_DESC = "8 类异常值检测参数与启停开关配置（JSON）"

#: snake_case（ControlTypeThreshold 字段）→ camelCase（存储/前端）参数名映射
_SNAKE_TO_CAMEL: dict[str, str] = {
    "base_sampling_freq": "baseSamplingFreq",
    "frozen_window_points": "frozenWindowPoints",
    "frozen_std_pct": "frozenStdPct",
    "jump_threshold_pct": "jumpThresholdPct",
    "spike_threshold_pct": "spikeThresholdPct",
    "noise_cutoff_hz": "noiseCutoffHz",
    "min_consecutive_points": "minConsecutivePoints",
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串."""
    return datetime.now(UTC).isoformat()


def _validate_params_to_snake(params: dict[str, Any], control_type: str) -> dict[str, Any]:
    """校验存储的 camelCase 参数覆盖并转为 snake_case（剔除 None/未知键）.

    存储数据经 pydantic 二次校验，损坏时跳过该控制类型并告警。
    """
    try:
        validated = OutlierThresholdParams.model_validate(params)
    except ValueError as exc:
        logger.warning("异常值检测参数覆盖校验失败，跳过控制类型 %s: %s", control_type, exc)
        return {}
    return validated.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# 存储解析 / 合并视图 / 运行时应用
# ---------------------------------------------------------------------------


def parse_stored(raw: str | None) -> dict[str, Any] | None:
    """解析 sys_config 存储的 JSON，缺失或损坏时返回 None（回落默认）."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("异常值检测参数配置解析失败，回退算法默认: %s", exc)
        return None
    if not isinstance(data, dict):
        logger.warning("异常值检测参数配置结构非法（非对象），回退算法默认")
        return None
    return data


def build_merged_view(stored: dict[str, Any] | None) -> OutlierParamsSchema:
    """构建合并视图：默认阈值叠加存储的覆盖项，含每项是否被覆盖标记.

    Args:
        stored: ``parse_stored()`` 的结果（None 表示无配置）

    Returns:
        完整合并视图（5 控制类型 × 7 参数 + 8 检测开关生效值）
    """
    stored = stored or {}
    stored_thresholds = stored.get("thresholds") or {}
    stored_switches = stored.get("switches") or {}

    items: list[OutlierThresholdViewItem] = []
    for ct in ControlType:
        base = get_default_threshold(ct)
        override_snake = _validate_params_to_snake(stored_thresholds.get(ct.value) or {}, ct.value)
        effective = {
            field: override_snake.get(field, getattr(base, field)) for field in PARAM_FIELDS
        }
        overridden = {_SNAKE_TO_CAMEL[field]: field in override_snake for field in PARAM_FIELDS}
        items.append(
            OutlierThresholdViewItem(
                control_type=ct.value,
                params=OutlierThresholdParams(**effective),
                overridden=overridden,
            )
        )

    switches = {key: bool(stored_switches.get(key, True)) for key in DETECTOR_KEYS}

    return OutlierParamsSchema(
        thresholds=items,
        switches=switches,
        updated_at=stored.get("updatedAt"),
        updated_by=stored.get("updatedBy"),
    )


def apply_runtime(stored: dict[str, Any] | None) -> None:
    """将存储配置应用到 thresholds 模块的进程内缓存（保存后/预载时调用）.

    Args:
        stored: ``parse_stored()`` 的结果（None 表示重置为纯默认）
    """
    stored = stored or {}
    overrides: dict[str, dict[str, Any]] = {}
    for ct_value, params in (stored.get("thresholds") or {}).items():
        if not isinstance(params, dict):
            continue
        snake = _validate_params_to_snake(params, str(ct_value))
        if snake:
            overrides[str(ct_value)] = snake
    set_threshold_overrides(overrides)

    raw_switches = stored.get("switches") or {}
    set_detector_switches(
        {str(k): bool(v) for k, v in raw_switches.items()} if raw_switches else None
    )


def build_stored_payload(
    thresholds: dict[str, OutlierThresholdParams],
    switches: dict[str, bool],
    operator: str,
) -> dict[str, Any]:
    """由保存请求构建存储 JSON（camelCase 参数，剔除空覆盖）."""
    return {
        "thresholds": {
            ct: dumped
            for ct, params in thresholds.items()
            if (dumped := params.model_dump(exclude_none=True, by_alias=True))
        },
        "switches": dict(switches),
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
    """加载 sys_config 中存储的异常值检测参数配置（未配置返回 None）."""
    return parse_stored(await get_config_value(db, SYS_CONFIG_KEY))


async def preload_outlier_params(db: AsyncSession) -> None:
    """从 sys_config 预载异常值检测参数到进程内缓存.

    供 FastAPI lifespan 与 Celery ``worker_process_init`` 调用，
    预载失败由调用方兜底（回落算法默认值）。
    """
    stored = await load_stored_config(db)
    apply_runtime(stored)
    logger.info(
        "异常值检测参数已预载: %s",
        "使用 sys_config 配置" if stored else "无配置，使用算法默认",
    )


__all__ = [
    "SYS_CONFIG_DESC",
    "SYS_CONFIG_KEY",
    "apply_runtime",
    "build_merged_view",
    "build_stored_payload",
    "get_config_value",
    "load_stored_config",
    "parse_stored",
    "preload_outlier_params",
    "set_config_value",
    "write_audit",
]
