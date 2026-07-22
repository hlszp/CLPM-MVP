"""专家规则引擎服务（C2 规则引擎化，FDS §5.4.6）。

职责：
- 规则 CRUD（含审计日志、Redis 缓存失效）
- 运行时规则求值：simpleeval 安全沙箱 + 5 种动作执行器
- 规则缓存（内存 + Redis 双层，避免每次诊断查 DB）

设计依据：FDS §5.4.6 / 整改计划 C2
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from simpleeval import EvalWithCompoundTypes, NameNotDefined
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.redis import redis_client
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisRule

logger = logging.getLogger(__name__)

# Redis 缓存键
RULE_CACHE_KEY = "clpm:diagnosis_rules"
RULE_CACHE_TTL = 300  # 5 分钟

# 内存缓存（进程级，worker_process_init 时预热）
_memory_cache: list[DiagnosisRule] | None = None


# ---------------------------------------------------------------------------
# 规则缓存
# ---------------------------------------------------------------------------


async def _load_rules_from_db(db: AsyncSession) -> list[DiagnosisRule]:
    """从 DB 加载启用的规则（按 priority 升序）。"""
    result = await db.execute(
        select(DiagnosisRule)
        .where(DiagnosisRule.is_enabled.is_(True))
        .order_by(DiagnosisRule.priority.asc())
    )
    return list(result.scalars().all())


async def get_active_rules(db: AsyncSession) -> list[DiagnosisRule]:
    """获取启用的规则列表（内存 → Redis → DB 三级缓存）。

    DB 查询失败时返回空列表（触发 _diagnose_loop 回退到硬编码规则）。
    """
    global _memory_cache
    if _memory_cache is not None:
        return _memory_cache

    # Redis 层
    try:
        cached = await redis_client.get(RULE_CACHE_KEY)
        if cached:
            _memory_cache = _deserialize_rules(json.loads(cached))
            return _memory_cache
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取规则 Redis 缓存失败: %s", exc)

    # DB 层（失败时返回空列表，触发调用方回退到硬编码规则）
    try:
        _memory_cache = await _load_rules_from_db(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("从 DB 加载规则失败，回退到空列表: %s", exc)
        _memory_cache = []

    try:
        await redis_client.setex(
            RULE_CACHE_KEY,
            RULE_CACHE_TTL,
            json.dumps(_serialize_rules(_memory_cache)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入规则 Redis 缓存失败: %s", exc)

    return _memory_cache


def preload_rules_to_memory(rules: list[DiagnosisRule]) -> None:
    """预热内存缓存（worker_process_init / lifespan 调用）。"""
    global _memory_cache
    _memory_cache = rules


async def preload_rules(db: AsyncSession) -> None:
    """预热规则缓存（lifespan / worker_process_init 调用）。

    从 DB 加载启用的规则到内存缓存，并同步写入 Redis。
    失败不抛异常，回退到空列表（触发 _diagnose_loop 硬编码规则兜底）。
    """
    global _memory_cache
    try:
        _memory_cache = await _load_rules_from_db(db)
        logger.info("已预载 %d 条诊断专家规则", len(_memory_cache))
    except Exception as exc:  # noqa: BLE001
        logger.warning("预载诊断专家规则失败（将回退到硬编码规则）: %s", exc)
        _memory_cache = []


async def invalidate_rule_cache() -> None:
    """失效规则缓存（CRUD 后调用）。"""
    global _memory_cache
    _memory_cache = None
    try:
        await redis_client.delete(RULE_CACHE_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("失效规则缓存失败: %s", exc)


def _serialize_rules(rules: list[DiagnosisRule]) -> list[dict]:
    """序列化规则列表为 JSON 可存储格式。"""
    return [
        {
            "id": str(r.id),
            "rule_code": r.rule_code,
            "rule_name": r.rule_name,
            "priority": r.priority,
            "condition_expr": r.condition_expr,
            "action_type": r.action_type,
            "action_params": r.action_params or {},
            "is_enabled": r.is_enabled,
            "version": r.version,
        }
        for r in rules
    ]


def _deserialize_rules(data: list[dict]) -> list[DiagnosisRule]:
    """从 JSON 数据反序列化规则列表为 DiagnosisRule 对象。"""
    rules = []
    for d in data:
        r = DiagnosisRule.__new__(DiagnosisRule)
        r.id = d["id"]
        r.rule_code = d["rule_code"]
        r.rule_name = d["rule_name"]
        r.priority = d["priority"]
        r.condition_expr = d["condition_expr"]
        r.action_type = d["action_type"]
        r.action_params = d["action_params"]
        r.is_enabled = d["is_enabled"]
        r.version = d["version"]
        rules.append(r)
    return rules


# ---------------------------------------------------------------------------
# 规则求值引擎
# ---------------------------------------------------------------------------


def _build_eval_namespace(
    algorithm_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """构建 simpleeval 安全命名空间。

    提供以下函数供条件表达式使用：
    - has(label): 检查标签是否存在于结果列表中
    - confidence(label): 获取指定标签的置信度（不存在返回 0.0）
    - feature(key): 获取主标签的特征值（不存在返回 0.0）
    - count(): 当前结果列表中的标签数量
    - max_confidence(): 当前结果列表中的最大置信度
    """
    label_map: dict[str, dict[str, Any]] = {
        r["label"]: r for r in algorithm_results if r.get("label")
    }

    def _has(label: str) -> bool:
        return label in label_map

    def _confidence(label: str) -> float:
        r = label_map.get(label)
        return float(r["confidence"]) if r else 0.0

    def _feature(key: str) -> float:
        # 从所有结果中查找指定特征值（取第一个有值的）
        for r in algorithm_results:
            val = r.get("feature_values", {}).get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _count() -> int:
        return len(algorithm_results)

    def _max_confidence() -> float:
        if not algorithm_results:
            return 0.0
        return max(r.get("confidence", 0.0) for r in algorithm_results)

    return {
        "has": _has,
        "confidence": _confidence,
        "feature": _feature,
        "count": _count,
        "max_confidence": _max_confidence,
    }


def _execute_action(
    action_type: str,
    action_params: dict[str, Any],
    algorithm_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """执行规则动作，返回处理后的结果列表。"""
    if action_type == "REMOVE_LABEL":
        label = action_params.get("label", "")
        return [r for r in algorithm_results if r.get("label") != label]

    if action_type == "ADD_LABEL":
        label = action_params.get("label", "")
        conf = action_params.get("confidence", 0.5)
        algorithm_results.append(
            {
                "label": label,
                "confidence": conf,
                "feature_values": {},
                "evidence": {
                    "reasoning": f"规则触发：{label}（置信度 {conf}）",
                },
            }
        )
        return algorithm_results

    if action_type == "KEEP_HIGHEST":
        labels = action_params.get("labels", [])
        if len(labels) < 2:
            return algorithm_results
        # 找到指定标签中置信度最高的
        candidates = [r for r in algorithm_results if r.get("label") in labels]
        if len(candidates) < 2:
            return algorithm_results
        best = max(candidates, key=lambda r: r.get("confidence", 0.0))
        best_label = best["label"]
        # 移除非最佳的标签
        return [
            r
            for r in algorithm_results
            if r.get("label") not in labels or r.get("label") == best_label
        ]

    if action_type == "FILTER_ONLY":
        keep_label = action_params.get("keep", "")
        return [r for r in algorithm_results if r.get("label") == keep_label]

    if action_type == "SORT_PRIORITY":
        priority_map = action_params.get("priority_map", {})
        algorithm_results.sort(key=lambda r: priority_map.get(r.get("label", ""), 100))
        return algorithm_results

    logger.warning("未知动作类型: %s", action_type)
    return algorithm_results


def apply_rules(
    algorithm_results: list[dict[str, Any]],
    rules: list[DiagnosisRule],
) -> list[dict[str, Any]]:
    """应用专家规则矩阵。

    按 priority 升序逐条执行：对每条规则求值条件表达式，
    条件为 True 时执行对应动作，修改 algorithm_results。

    Args:
        algorithm_results: 算法结果列表
        rules: 启用的规则列表（已按 priority 排序）

    Returns:
        处理后的算法结果列表
    """
    if not algorithm_results or not rules:
        return algorithm_results

    for rule in rules:
        try:
            namespace = _build_eval_namespace(algorithm_results)
            evaluator = EvalWithCompoundTypes(
                names={},
                functions=namespace,
            )
            condition_met = bool(evaluator.eval(rule.condition_expr))
            if condition_met:
                algorithm_results = _execute_action(
                    rule.action_type,
                    rule.action_params or {},
                    algorithm_results,
                )
        except NameNotDefined as exc:
            logger.warning(
                "规则 %s 条件表达式引用了未定义名称: %s",
                rule.rule_code,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "规则 %s 求值失败: %s (expr=%s)",
                rule.rule_code,
                exc,
                rule.condition_expr,
            )

    return algorithm_results


# ---------------------------------------------------------------------------
# 规则 CRUD
# ---------------------------------------------------------------------------


async def list_rules(db: AsyncSession) -> list[dict]:
    """获取规则列表（含停用的）。"""
    result = await db.execute(select(DiagnosisRule).order_by(DiagnosisRule.priority.asc()))
    rules = result.scalars().all()
    return [_rule_to_dict(r) for r in rules]


async def update_rule(
    db: AsyncSession,
    rule_id: str,
    operator: str,
    *,
    rule_name: str | None = None,
    condition_expr: str | None = None,
    action_type: str | None = None,
    action_params: dict | None = None,
    priority: int | None = None,
    is_enabled: bool | None = None,
) -> dict:
    """更新规则配置。

    Raises:
        BizError: ERR_RULE_NOT_FOUND
    """
    result = await db.execute(select(DiagnosisRule).where(DiagnosisRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise BizError(
            code="ERR_RULE_NOT_FOUND",
            message="规则不存在",
            status_code=404,
        )

    before = _rule_to_dict(rule)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if rule_name is not None:
        rule.rule_name = rule_name
    if condition_expr is not None:
        rule.condition_expr = condition_expr
    if action_type is not None:
        rule.action_type = action_type
    if action_params is not None:
        rule.action_params = action_params
    if priority is not None:
        rule.priority = priority
    if is_enabled is not None:
        rule.is_enabled = is_enabled

    rule.updated_by = operator
    rule.updated_at = datetime.now(UTC).replace(tzinfo=None)
    rule.version = (rule.version or 1) + 1

    after = _rule_to_dict(rule)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    # 审计日志
    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="DIAG_RULE_UPDATE",
        target_type="diagnosis_rule",
        target_id=str(rule.id),
        before_value=before_json,
        after_value=after_json,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(audit_log)
    await db.commit()

    # 失效缓存
    await invalidate_rule_cache()

    return after


def _rule_to_dict(rule: DiagnosisRule) -> dict:
    """规则对象转字典。"""
    return {
        "ruleId": str(rule.id),
        "ruleCode": rule.rule_code,
        "ruleName": rule.rule_name,
        "priority": rule.priority,
        "conditionExpr": rule.condition_expr,
        "actionType": rule.action_type,
        "actionParams": rule.action_params or {},
        "isEnabled": rule.is_enabled,
        "version": rule.version,
        "updatedBy": rule.updated_by,
        "updatedAt": rule.updated_at.isoformat() if rule.updated_at else None,
    }
