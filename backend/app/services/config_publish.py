"""分层配置发布与跨进程一致服务（P2-02）.

统一作用域链（方案 01 §3.2，低→高，"<" 表示被右侧遮盖）::

    DEFAULT  <  TEMPLATE  <  NODE  <  LOOP  <  TASK
    （算法默认 + algorithm_parameter + metric_config.threshold 兼容全局覆盖）
      （响应/控制特征模板） （装置/单元） （回路） （本次任务临时覆盖，内存）

职责：
- **有效值解析**：``resolve_effective_params()`` 返回逐参数的
  ``EffectiveParameter``（key/value/unit/source/sourceId/sourceRevision）与
  被遮盖关系（shadowed），支撑"有效值/来源/遮盖关系可查询"。
- **发布（ADMIN，DEC-03）**：``publish_override()`` / ``reset_override()`` /
  ``rollback_publication()`` 均以 expectedRevision 乐观锁发布——冲突 409 并
  返回最新版本；每次发布写入 ``config_publication``（全局单调 revision +
  before/after/原因/操作者/影响范围/回退版本）与 SysAuditLog 审计。
- **跨进程一致（CFG-03）**：
  - 加速路径：发布后 PUBLISH 到 Redis ``CONFIG_REVISION_CHANNEL``，各进程
    （API lifespan + Celery worker 子进程）的后台订阅线程收到后按版本去重
    重载运行时缓存（复用 confidence_evaluator THRESHOLD_CHANNEL 样板）。
  - 一致性保证：**任务边界读持久 revision**——``pin_config_snapshot()``
    每次从 PostgreSQL 读当前 revision（Redis 只是加速，不作版本真相源）；
    与进程内已同步版本不一致（如漏收广播）则当场从 DB 重载。运行中任务
    使用固定快照，不切参。DB 读取失败时异常显式上抛，禁止用过期值静默计算。
- **重置解释（P2-01 遗留）**：``reset_override()`` 返回 resetExplanation——
  重置低层后哪些键仍被更高层覆盖、来自哪一层（"重置低层后高层仍生效且可解释"）。

不做的事：
- 不以 pub/sub 替代持久版本检查（广播只加速，落库 revision 才是真相）。
- 不使用模块级 asyncio 同步原语（本模块仅用 threading 原语，兼容 Celery
  每任务新事件循环）。
- kpi_calc 等既有任务管线接入固定快照属 P2-03 CalculationContext；本模块
  提供 ``pin_config_snapshot()`` 边界固定能力与 ``app.tasks.config_probe``
  演示/诊断任务。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.algorithm_parameter import AlgorithmParameter
from app.models.audit import SysAuditLog
from app.models.config_publish import ConfigOverride, ConfigPublication
from app.models.engineering_template import EngineeringTemplate
from app.models.loop import LoopLedger
from app.models.metric import MetricConfig
from app.services import algorithm_config as ac

logger = logging.getLogger(__name__)

#: 配置 revision 广播频道（复用 THRESHOLD_CHANNEL pub/sub 模式；仅加速通知）
CONFIG_REVISION_CHANNEL = "config:revision:updated"

#: 层常量（TASK 层不落库，仅内存）
LAYER_TEMPLATE = "TEMPLATE"
LAYER_NODE = "NODE"
LAYER_LOOP = "LOOP"
LAYER_TASK = "TASK"
SOURCE_DEFAULT = "DEFAULT"

_VALID_LAYERS = (LAYER_TEMPLATE, LAYER_NODE, LAYER_LOOP)
_SOURCE_RANK = {
    SOURCE_DEFAULT: 0,
    LAYER_TEMPLATE: 1,
    LAYER_NODE: 2,
    LAYER_LOOP: 3,
    LAYER_TASK: 4,
}

# ---------------------------------------------------------------------------
# 进程内同步状态（threading 原语——禁用模块级 asyncio 原语红线）
# ---------------------------------------------------------------------------

#: 本进程运行时缓存已同步到的 config revision（-1 = 从未同步）
_local_synced_revision = -1
_state_lock = threading.Lock()

#: 订阅线程幂等启动标记
_subscriber_started = False
_subscriber_lock = threading.Lock()


def _get_synced_revision() -> int:
    with _state_lock:
        return _local_synced_revision


def _set_synced_revision(revision: int) -> None:
    global _local_synced_revision
    with _state_lock:
        _local_synced_revision = revision


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# 持久 revision 读取（DB 为唯一版本真相源）
# ---------------------------------------------------------------------------


async def get_current_revision(db: AsyncSession) -> int:
    """读取当前全局配置 revision（PostgreSQL 持久值；无发布历史时为 0）.

    Redis 不参与本函数——版本号持久化，Redis 仅承载加速通知。
    """
    result = await db.execute(select(func.max(ConfigPublication.revision)))
    current = result.scalar_one_or_none()
    return int(current) if current is not None else 0


async def get_last_publication(db: AsyncSession) -> ConfigPublication | None:
    """最近一次发布记录（GET /configs/publish/revision 用）."""
    result = await db.execute(
        select(ConfigPublication).order_by(ConfigPublication.revision.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def sync_runtime_cache(db: AsyncSession) -> int:
    """从 DB 重载运行时缓存并记录已同步 revision（返回该 revision）.

    供 lifespan / worker_process_init 预载、广播消息重载、任务边界
    ``pin_config_snapshot`` 失配重载三处复用。
    """
    table_overrides = await ac.load_stored_config(db)
    metric_thresholds = await ac.load_metric_thresholds(db)
    ac.apply_runtime(table_overrides, metric_thresholds)
    revision = await get_current_revision(db)
    _set_synced_revision(revision)
    logger.info("[config-publish pid=%s] 运行时缓存已同步至 revision=%s", os.getpid(), revision)
    return revision


async def pin_config_snapshot(db: AsyncSession, *, metric_codes: list[str] | None = None) -> dict:
    """任务边界固定配置快照（CalculationContext 的配置部分）.

    语义（方案 §3.2"任务开始固定，任务中不切参"）：
    1. 从 DB 读当前持久 revision（**不读 Redis**——Redis 断连不影响本函数）；
    2. 若进程内运行时缓存 revision 与 DB 不一致（漏收广播/Redis 故障期间有
       发布），当场从 DB 重载后再快照；
    3. 返回不可变快照（dict 拷贝），任务全程使用本快照，不随后续发布切换。

    DB 读取失败时异常原样上抛（显式失败），绝不静默返回已知过期值。

    Args:
        db: 异步数据库会话
        metric_codes: 仅固定指定指标（None = 全部注册指标）

    Returns:
        ``{"schemaVersion": "1", "configRevision": int, "pinnedAt": iso,
           "params": {"metric|controlType": {...}}（DEFAULT 层合并值）,
           "overrides": {"TEMPLATE|scope|metric": {...}, ...}（覆盖层）,
           "hostname", "pid"}``

    覆盖层（TEMPLATE/NODE/LOOP）一并入快照：任务内经
    ``resolve_from_snapshot()`` 按五层链解析，全程不再查库——覆盖层的
    "任务中不切参"同样成立（TASK 层值由调用方以 task_overrides 传入）。
    """
    revision = await get_current_revision(db)
    if _get_synced_revision() != revision:
        # 漏收广播（Redis 故障/新进程首跑）：以 DB 为准重载，不用过期缓存
        revision = await sync_runtime_cache(db)
    snapshot: dict[str, dict[str, Any]] = {}
    for (metric_code, ct), params in ac._merged_cache.items():
        if metric_codes is not None and metric_code not in metric_codes:
            continue
        snapshot[f"{metric_code}|{ct}"] = dict(params)
    overrides: dict[str, dict[str, Any]] = {}
    override_rows = (
        (
            await db.execute(
                select(ConfigOverride).where(
                    ConfigOverride.is_enabled.is_(True),
                    *(
                        (ConfigOverride.metric_code.in_(metric_codes),)
                        if metric_codes is not None
                        else ()
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in override_rows:
        overrides[f"{row.layer}|{row.scope_id}|{row.metric_code}"] = dict(row.params or {})
    return {
        "schemaVersion": "1",
        "configRevision": revision,
        "pinnedAt": _now_iso(),
        "params": snapshot,
        "overrides": overrides,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
    }


def resolve_from_snapshot(
    snapshot: dict[str, Any],
    metric_code: str,
    control_type: str | None,
    *,
    template_key: str | None = None,
    node_id: str | None = None,
    loop_id: str | None = None,
    task_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在固定快照上按五层链解析有效参数（任务内零 DB 访问）.

    层序：DEFAULT（快照 params 的合并值）< TEMPLATE < NODE < LOOP < TASK
    （task_overrides）。与 ``resolve_effective_params`` 的 DB 解析语义一致，
    区别是数据源为 ``pin_config_snapshot`` 固定的快照——任务运行中不受后续
    发布影响（方案 §3.2"任务开始固定，任务中不切参"）。

    Returns:
        ``{"metricCode", "controlType", "configRevision", "params", "sources"}``，
        sources 为逐参数来源层（DEFAULT/TEMPLATE/NODE/LOOP/TASK）。
    """
    ct = control_type if control_type in ac._CONTROL_TYPES else "STABLE"
    merged: dict[str, Any] = dict(snapshot.get("params", {}).get(f"{metric_code}|{ct}", {}))
    sources: dict[str, str] = dict.fromkeys(merged, SOURCE_DEFAULT)
    overrides = snapshot.get("overrides", {})
    for layer, scope in (
        (LAYER_TEMPLATE, template_key),
        (LAYER_NODE, node_id),
        (LAYER_LOOP, loop_id),
    ):
        if not scope:
            continue
        layer_params = overrides.get(f"{layer}|{scope}|{metric_code}")
        if not layer_params:
            continue
        for key, value in layer_params.items():
            sources[key] = layer
            merged[key] = value
    if task_overrides:
        for key, value in task_overrides.items():
            sources[key] = LAYER_TASK
            merged[key] = value
    return {
        "metricCode": metric_code,
        "controlType": ct,
        "configRevision": snapshot.get("configRevision"),
        "params": merged,
        "sources": sources,
    }


def resolve_loop_params_map(
    snapshot: dict[str, Any],
    *,
    template_key: str | None = None,
    node_id: str | None = None,
    loop_id: str | None = None,
) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    """在固定快照上解析一个回路的全部五层有效参数（G2-INC-a）.

    对注册表全部 (metric_code, control_type) 组合循环调用既有
    ``resolve_from_snapshot``（零 DB 访问），返回::

        {"params": {(metric, ct): {param: 五层展开有效值}},
         "sources": {(metric, ct): {param: 来源层}}}

    供 KPI 任务把 per-loop 解析结果注入计算器（``with_pinned_params``），
    任务运行期不再读当前缓存——"任务开始固定，任务中不切参"在参数值
    层面成立（此前仅 pin revision 号，算子仍读 ``_merged_cache``）。
    sources 供输入包 manifest.effectiveParams 记录逐参数来源层。
    """
    params_map: dict[tuple[str, str], dict[str, Any]] = {}
    sources_map: dict[tuple[str, str], dict[str, str]] = {}
    for metric_code in ac._DEFAULTS:
        for ct in ac._CONTROL_TYPES:
            resolved = resolve_from_snapshot(
                snapshot,
                metric_code,
                ct,
                template_key=template_key,
                node_id=node_id,
                loop_id=loop_id,
            )
            params_map[(metric_code, ct)] = dict(resolved["params"])
            sources_map[(metric_code, ct)] = dict(resolved["sources"])
    return {"params": params_map, "sources": sources_map}


# ---------------------------------------------------------------------------
# 有效值解析（DEFAULT < TEMPLATE < NODE < LOOP < TASK）
# ---------------------------------------------------------------------------


async def _load_default_layer_values(
    db: AsyncSession, metric_code: str, control_type: str
) -> list[tuple[str, str | None, dict[str, Any], str]]:
    """加载 DEFAULT 层的三段值（算法默认 / algorithm_parameter / metric.threshold）.

    三段内部次序保持旧三层链兼容（算法默认 < algorithm_parameter <
    metric_config.threshold），不静默改变旧优先级；TEMPLATE/NODE/LOOP/TASK
    为其上的新增层。
    """
    values: list[tuple[str, str | None, dict[str, Any], str]] = [
        (SOURCE_DEFAULT, None, ac.get_default_params(metric_code, control_type), "builtin")
    ]

    ap_result = await db.execute(
        select(AlgorithmParameter).where(
            AlgorithmParameter.metric_code == metric_code,
            AlgorithmParameter.control_type == control_type,
            AlgorithmParameter.is_enabled.is_(True),
        )
    )
    ap_row = ap_result.scalar_one_or_none()
    if ap_row and ap_row.params:
        values.append(
            (SOURCE_DEFAULT, f"algorithm_parameter:{control_type}", dict(ap_row.params), "legacy")
        )

    mc_result = await db.execute(
        select(MetricConfig.threshold).where(
            MetricConfig.metric_code == metric_code,
            MetricConfig.threshold.is_not(None),
        )
    )
    threshold = mc_result.scalar_one_or_none()
    if threshold and isinstance(threshold, dict) and threshold:
        values.append((SOURCE_DEFAULT, "metric_config.threshold", dict(threshold), "legacy"))

    return values


async def _load_override_layer_value(
    db: AsyncSession,
    layer: str,
    scope_id: str,
    metric_code: str,
) -> tuple[dict[str, Any], int] | None:
    """加载某覆盖层在指定作用域的参数（无启用行/空参数返回 None）."""
    result = await db.execute(
        select(ConfigOverride).where(
            ConfigOverride.layer == layer,
            ConfigOverride.scope_id == scope_id,
            ConfigOverride.metric_code == metric_code,
            ConfigOverride.is_enabled.is_(True),
        )
    )
    row = result.scalar_one_or_none()
    if row is None or not row.params:
        return None
    return dict(row.params), int(row.published_revision)


def _merge_layers(
    metric_code: str,
    layers: list[tuple[str, str | None, dict[str, Any], str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """按层序归并：返回 (merged参数, effective逐参数来源, shadowed遮盖关系).

    Args:
        layers: [(source, sourceId, params, sourceRevision), ...] 已按低→高排序
    """
    meta = ac.PARAM_META.get(metric_code, {})
    merged: dict[str, Any] = {}
    effective: dict[str, dict[str, Any]] = {}
    shadowed: list[dict[str, Any]] = []
    for source, source_id, params, source_revision in layers:
        for key, value in params.items():
            entry = {
                "key": key,
                "value": value,
                "unit": meta.get(key, {}).get("unit", ""),
                "source": source,
                "sourceId": source_id,
                "sourceRevision": source_revision,
            }
            if key in effective:
                shadowed.append(
                    {
                        "key": key,
                        "value": effective[key]["value"],
                        "source": effective[key]["source"],
                        "sourceId": effective[key]["sourceId"],
                        "shadowedBy": source,
                        "shadowedBySourceId": source_id,
                        "shadowedByValue": value,
                    }
                )
            effective[key] = entry
            merged[key] = value
    return merged, list(effective.values()), shadowed


async def resolve_effective_params(
    db: AsyncSession,
    metric_code: str,
    control_type: str | None,
    *,
    template_key: str | None = None,
    node_id: str | None = None,
    loop_id: str | None = None,
    task_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析统一作用域链的有效参数（有效值/来源/遮盖关系可查询）.

    Args:
        db: 异步数据库会话
        metric_code: 指标代码（须在注册表 ``_DEFAULTS`` 内）
        control_type: 控制类型（DEFAULT 层按其取值；非法值回退 STABLE）
        template_key: TEMPLATE 层作用域（响应/控制特征模板键；None=该层不参与）
        node_id: NODE 层作用域（plant_node.id）
        loop_id: LOOP 层作用域（loop_ledger.id）
        task_overrides: TASK 层本次任务临时覆盖（内存值，不落库）

    Returns:
        ``{"metricCode", "controlType", "revision", "params", "effective",
           "shadowed"}``（effective = EffectiveParameter 列表）
    """
    if metric_code not in ac._DEFAULTS:
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"未知指标代码: {metric_code}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    ct = control_type if control_type in ac._CONTROL_TYPES else "STABLE"
    revision = await get_current_revision(db)

    layers: list[tuple[str, str | None, dict[str, Any], str]] = await _load_default_layer_values(
        db, metric_code, ct
    )
    for layer, scope in (
        (LAYER_TEMPLATE, template_key),
        (LAYER_NODE, node_id),
        (LAYER_LOOP, loop_id),
    ):
        if not scope:
            continue
        loaded = await _load_override_layer_value(db, layer, str(scope), metric_code)
        if loaded is not None:
            params, published_revision = loaded
            layers.append((layer, str(scope), params, str(published_revision)))
    if task_overrides:
        layers.append((LAYER_TASK, "task", dict(task_overrides), str(revision)))

    merged, effective, shadowed = _merge_layers(metric_code, layers)
    return {
        "metricCode": metric_code,
        "controlType": ct,
        "revision": revision,
        "params": merged,
        "effective": effective,
        "shadowed": shadowed,
    }


async def _count_affected_loops(
    db: AsyncSession, layer: str | None, scope_id: str | None
) -> tuple[int | None, str | None]:
    """计算发布影响范围（受影响回路数）；无法精确计数返回 (None, 说明).

    - LOOP：经 loop_ledger 存在性精确核验（存在=1，不存在=0+说明——发布
      本身不被拦截，P2-02 口径保留）；
    - NODE：scope_id 须为 plant_node.id（UUID 形态）——非 UUID 时**诚实降级**
      （影响数 None + 说明），不虚报也不让计数查询以绑定错误 500；
    - TEMPLATE（P2-04 收口）：scope_id = 模板键经 ``engineering_template``
      实体解析，按 ``loop_ledger.engineering_template_id`` 精确计数；模板键
      未注册为工程模板实体时保持诚实降级（None + 说明）。
    """
    if layer == LAYER_LOOP:
        result = await db.execute(
            select(func.count()).select_from(LoopLedger).where(LoopLedger.id == str(scope_id))
        )
        count = int(result.scalar_one())
        if count == 0:
            return 0, "LOOP 层 scope_id 未解析到回路（覆盖将不作用于任何回路）"
        return 1, None
    if layer == LAYER_NODE:
        from uuid import UUID

        try:
            UUID(str(scope_id))
        except (TypeError, ValueError):
            return None, "NODE 层 scope_id 非 plant_node.id UUID 形态，影响回路数未统计"
        result = await db.execute(
            select(func.count()).select_from(LoopLedger).where(LoopLedger.unit_id == scope_id)
        )
        return int(result.scalar_one()), None
    if layer == LAYER_TEMPLATE:
        template = (
            await db.execute(
                select(EngineeringTemplate).where(EngineeringTemplate.template_key == scope_id)
            )
        ).scalar_one_or_none()
        if template is None:
            return None, (
                f"模板键 {scope_id} 未注册为工程模板实体（engineering_template），"
                "影响回路数未统计；注册模板并关联回路后可精确计数"
            )
        result = await db.execute(
            select(func.count())
            .select_from(LoopLedger)
            .where(LoopLedger.engineering_template_id == template.id)
        )
        return int(result.scalar_one()), None
    result = await db.execute(
        select(func.count()).select_from(LoopLedger).where(LoopLedger.is_active.is_(True))
    )
    return int(result.scalar_one()), "全局层：影响全部启用回路（按控制类型细分待后续批次）"


async def _loop_lower_chain(
    db: AsyncSession, loop_id: str
) -> tuple[list[tuple[str, str]], str | None]:
    """解析 LOOP 发布的"其下有效覆盖链"（loop→node/template 映射）.

    Returns:
        ([(layer, scope_id), ...] 低→高, 降级说明或 None)。scope 未解析到
        回路时返回空链 + 说明（退回 DEFAULT 层合并视图，不拦截发布）。
    """
    loop = (
        await db.execute(select(LoopLedger).where(LoopLedger.id == str(loop_id)))
    ).scalar_one_or_none()
    if loop is None:
        return [], (
            "LOOP scope_id 未解析到回路，覆盖层合并视图退回 DEFAULT 层（scope 真实性不拦截发布）"
        )
    chain: list[tuple[str, str]] = []
    template_id = getattr(loop, "engineering_template_id", None)
    if template_id:
        template = (
            await db.execute(
                select(EngineeringTemplate).where(EngineeringTemplate.id == str(template_id))
            )
        ).scalar_one_or_none()
        if template is not None:
            chain.append((LAYER_TEMPLATE, template.template_key))
    if loop.unit_id:
        chain.append((LAYER_NODE, str(loop.unit_id)))
    return chain, None


async def _node_lower_chains(db: AsyncSession, unit_id: str) -> list[list[tuple[str, str]]]:
    """解析 NODE 发布的"其下有效覆盖链"集合（单位内各回路的模板）.

    单位内不同回路可关联不同模板——每个模板一条链分别求值；另含空链
    （DEFAULT 层，覆盖无模板回路的组合）。
    """
    loops = (
        (await db.execute(select(LoopLedger).where(LoopLedger.unit_id == str(unit_id))))
        .scalars()
        .all()
    )
    template_ids = sorted(
        {
            str(row.engineering_template_id)
            for row in loops
            if getattr(row, "engineering_template_id", None)
        }
    )
    chains: list[list[tuple[str, str]]] = []
    for tid in template_ids:
        template = (
            await db.execute(select(EngineeringTemplate).where(EngineeringTemplate.id == tid))
        ).scalar_one_or_none()
        if template is not None:
            chains.append([(LAYER_TEMPLATE, template.template_key)])
    chains.append([])
    return chains


async def _validate_layer_params(
    db: AsyncSession,
    layer: str,
    scope_id: str,
    metric_code: str,
    params: dict[str, Any],
    control_type: str | None,
) -> None:
    """发布前校验：键/值域/组合约束在"该层以下全部层的合并视图"上求值.

    P2-04 收口（P2-02 验收报告 I 项）：覆盖层合并视图下界从"仅 DEFAULT 层"
    扩为——LOOP 发布合并其下 NODE/TEMPLATE 有效覆盖（loop→node/template
    映射就位后可算）、NODE 发布合并其下 TEMPLATE 覆盖（单位内各回路模板
    逐链求值）。拦截"单层合法、跨覆盖层组合颠倒"的漏拦窗口。

    查询次序（mock 测试按此编排）：
    1. 链解析（LOOP=回路行[+模板行]；NODE=单位回路集[+模板行]；TEMPLATE=无）；
    2. 链上覆盖加载（每链元素一次 ConfigOverride 查询）；
    3. 逐控制类型 DEFAULT 层三段加载（algorithm_parameter + metric.threshold）。

    覆盖层不区分控制类型，全部 4 个控制类型都必须通过。
    """
    degradation: str | None = None
    if layer == LAYER_LOOP:
        chains, degradation = await _loop_lower_chain(db, str(scope_id))
        chain_list = [chains]
    elif layer == LAYER_NODE:
        chain_list = await _node_lower_chains(db, str(scope_id))
    else:
        # TEMPLATE 层之下仅 DEFAULT；TASK 层不入库（发布校验不涉及）
        chain_list = [[]]
    if degradation:
        logger.info("[config-publish] %s", degradation)

    chain_overrides: list[dict[str, Any]] = []
    for chain in chain_list:
        merged: dict[str, Any] = {}
        for clayer, cscope in chain:
            loaded = await _load_override_layer_value(db, clayer, cscope, metric_code)
            if loaded is not None:
                merged.update(loaded[0])
        chain_overrides.append(merged)

    cts = [control_type] if control_type else list(ac._CONTROL_TYPES)
    errors: list[str] = []
    for ct in cts:
        below_layers = await _load_default_layer_values(db, metric_code, ct)
        default_base: dict[str, Any] = {}
        for _source, _sid, below_params, _rev in below_layers:
            default_base.update(below_params)
        for override_base in chain_overrides:
            errors.extend(
                ac.validate_metric_params(
                    metric_code, params, base={**default_base, **override_base}
                )
            )
    if errors:
        raise BizError(
            code="ERR_PARAM_INVALID",
            message="；".join(dict.fromkeys(errors)),
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def _audit_envelope(
    *,
    reason: str,
    scope: dict[str, Any],
    rollback_revision: int | None,
    affected_loops: int | None,
    note: str | None,
    value: Any,
) -> str:
    """审计 before/after 载荷封包（原因/作用范围/回退版本/影响范围随审计入册）."""
    return json.dumps(
        {
            "reason": reason,
            "scope": scope,
            "rollbackRevision": rollback_revision,
            "affectedLoops": affected_loops,
            "note": note,
            "value": value,
        },
        ensure_ascii=False,
        default=str,
    )


# ---------------------------------------------------------------------------
# 发布预览（dry-run：只读，不推进 revision 不落任何行）
# ---------------------------------------------------------------------------


async def _affected_loop_sample(
    db: AsyncSession, layer: str, scope_id: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    """受影响回路清单样本（发布预览用；不虚报，解析不到返回空列表）."""
    stmt = select(LoopLedger.id, LoopLedger.tag_name, LoopLedger.is_active)
    if layer == LAYER_LOOP:
        stmt = stmt.where(LoopLedger.id == str(scope_id))
    elif layer == LAYER_NODE:
        from uuid import UUID

        try:
            UUID(str(scope_id))
        except (TypeError, ValueError):
            return []
        stmt = stmt.where(LoopLedger.unit_id == str(scope_id))
    elif layer == LAYER_TEMPLATE:
        template = (
            await db.execute(
                select(EngineeringTemplate).where(EngineeringTemplate.template_key == scope_id)
            )
        ).scalar_one_or_none()
        if template is None:
            return []
        stmt = stmt.where(LoopLedger.engineering_template_id == template.id)
    else:
        return []
    rows = (await db.execute(stmt.order_by(LoopLedger.tag_name.asc()).limit(limit))).all()
    return [
        {"loopId": str(r.id), "tagName": r.tag_name, "isActive": bool(r.is_active)} for r in rows
    ]


async def preview_publish_override(
    db: AsyncSession,
    *,
    layer: str,
    scope_id: str,
    metric_code: str,
    params: dict[str, Any],
    control_type: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """发布预览（dry-run）：校验结果 / before-after 差异 / 受影响回路，零写入.

    P2-04（03 §5 任务卡"批量配置有 dry-run、差异、部分失败状态；读取与发布
    能力分开"）——预览为只读能力（IC/PE 可调用），发布仍 ADMIN（DEC-03）。

    Returns:
        ``{"layer", "scopeId", "metricCode", "currentRevision", "wouldConflict",
           "validation": {"ok", "errors"}, "before", "after",
           "affectedLoops", "note", "affectedLoopSample",
           "mergePreview": [{"controlType", "base", "result"}]}``

        - ``wouldConflict``：expected_revision 提供且与当前 revision 不一致时
          True（预览不抛 409——调用方据此提示）；
        - ``mergePreview``：该层以下合并视图（base）与叠加本次参数后的结果
          （result），逐控制类型（control_type 提供时仅该类型）——跨覆盖层
          组合颠倒在 errors 中可见。
    """
    if layer not in _VALID_LAYERS:
        raise BizError(
            code="ERR_PARAM_INVALID",
            message=f"layer 必须为 {_VALID_LAYERS} 之一，收到 {layer}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if metric_code not in ac._DEFAULTS:
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"未知指标代码: {metric_code}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    revision = await get_current_revision(db)
    would_conflict = expected_revision is not None and expected_revision != revision

    # 覆盖层合并视图（与 _validate_layer_params 同源，只收集不抛）
    if layer == LAYER_LOOP:
        chains, degradation = await _loop_lower_chain(db, str(scope_id))
        chain_list = [chains]
    elif layer == LAYER_NODE:
        chain_list = await _node_lower_chains(db, str(scope_id))
        degradation = None
    else:
        chain_list = [[]]
        degradation = None
    chain_overrides: list[dict[str, Any]] = []
    for chain in chain_list:
        merged: dict[str, Any] = {}
        for clayer, cscope in chain:
            loaded = await _load_override_layer_value(db, clayer, cscope, metric_code)
            if loaded is not None:
                merged.update(loaded[0])
        chain_overrides.append(merged)

    if control_type:
        cts = [control_type if control_type in ac._CONTROL_TYPES else "STABLE"]
    else:
        cts = list(ac._CONTROL_TYPES)
    errors: list[str] = []
    merge_preview: list[dict[str, Any]] = []
    for ct in cts:
        below_layers = await _load_default_layer_values(db, metric_code, ct)
        default_base: dict[str, Any] = {}
        for _source, _sid, below_params, _rev in below_layers:
            default_base.update(below_params)
        for override_base in chain_overrides:
            base = {**default_base, **override_base}
            errors.extend(ac.validate_metric_params(metric_code, params, base=base))
            merge_preview.append(
                {
                    "controlType": ct,
                    "base": base,
                    "result": {**base, **params},
                }
            )

    existing_result = await db.execute(
        select(ConfigOverride).where(
            ConfigOverride.layer == layer,
            ConfigOverride.scope_id == str(scope_id),
            ConfigOverride.metric_code == metric_code,
        )
    )
    existing = existing_result.scalar_one_or_none()
    before_params = dict(existing.params) if existing and existing.params else None

    affected_loops, note = await _count_affected_loops(db, layer, str(scope_id))
    sample = await _affected_loop_sample(db, layer, str(scope_id))
    return {
        "layer": layer,
        "scopeId": str(scope_id),
        "metricCode": metric_code,
        "currentRevision": revision,
        "wouldConflict": would_conflict,
        "validation": {"ok": not errors, "errors": list(dict.fromkeys(errors))},
        "before": before_params,
        "after": dict(params),
        "affectedLoops": affected_loops,
        "note": note or degradation,
        "affectedLoopSample": sample,
        "mergePreview": merge_preview,
    }


def _layer_rank(layer: str | None) -> int:
    return _SOURCE_RANK.get(layer or SOURCE_DEFAULT, 0)


async def _require_revision_or_409(db: AsyncSession, expected_revision: int) -> int:
    """expectedRevision 乐观锁前置校验；失配即 409 并携带最新版本."""
    current = await get_current_revision(db)
    if expected_revision != current:
        raise BizError(
            code="ERR_CONFIG_REVISION_CONFLICT",
            message=(
                f"配置版本冲突：expectedRevision={expected_revision}，"
                f"当前最新 revision={current}（并发发布已被他人提交，请以最新版本重试）"
            ),
            status_code=status.HTTP_409_CONFLICT,
            data={"currentRevision": current},
        )
    return current


def _conflict_409(current: int) -> BizError:
    return BizError(
        code="ERR_CONFIG_REVISION_CONFLICT",
        message=(
            f"配置版本冲突：并发发布抢先提交了同一 revision（最新 revision={current}），"
            "请以最新版本重试"
        ),
        status_code=status.HTTP_409_CONFLICT,
        data={"currentRevision": current},
    )


async def broadcast_config_revision(revision: int, source: str = "api") -> None:
    """发布后广播加速通知（Redis 故障仅告警不阻断——DB 已是真相源）."""
    try:
        from app.core.redis import redis_client

        message = json.dumps(
            {"revision": revision, "source": source, "updated_at": _now_iso()},
            ensure_ascii=False,
        )
        n = await redis_client.publish(CONFIG_REVISION_CHANNEL, message)
        logger.info(
            "[config-publish pid=%s] 配置 revision 已广播: revision=%s, source=%s, 投递订阅者数=%s",
            os.getpid(),
            revision,
            source,
            n,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[config-publish pid=%s] 配置 revision 广播失败（不影响持久版本，"
            "任务边界将按持久 revision 校验补齐）: %s",
            os.getpid(),
            exc,
        )


# ---------------------------------------------------------------------------
# 发布 / 重置 / 回退（ADMIN 动作，DEC-03）
# ---------------------------------------------------------------------------


async def publish_override(
    db: AsyncSession,
    *,
    layer: str,
    scope_id: str,
    metric_code: str,
    params: dict[str, Any],
    expected_revision: int,
    reason: str,
    operator: str,
    control_type: str | None = None,
) -> dict[str, Any]:
    """发布一层覆盖（整组替换语义；expectedRevision 乐观锁，冲突 409）.

    流程：前置 409 校验 → 注册表校验（含组合约束，base=该层以下合并视图）→
    upsert ``config_override`` → 追加 ``config_publication``（revision=
    expected+1，唯一约束兜底并发）→ SysAuditLog 审计 → 单事务 commit →
    广播 + 本进程缓存同步。

    Raises:
        BizError: ERR_PARAM_INVALID(400) / ERR_CONFIG_REVISION_CONFLICT(409)
    """
    if layer not in _VALID_LAYERS:
        raise BizError(
            code="ERR_PARAM_INVALID",
            message=f"layer 必须为 {_VALID_LAYERS} 之一，收到 {layer}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if metric_code not in ac._DEFAULTS:
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"未知指标代码: {metric_code}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not str(scope_id).strip():
        raise BizError(
            code="ERR_PARAM_INVALID",
            message=f"{layer} 层 scope_id 不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    current = await _require_revision_or_409(db, expected_revision)
    await _validate_layer_params(db, layer, str(scope_id), metric_code, params, control_type)

    existing_result = await db.execute(
        select(ConfigOverride).where(
            ConfigOverride.layer == layer,
            ConfigOverride.scope_id == str(scope_id),
            ConfigOverride.metric_code == metric_code,
        )
    )
    existing = existing_result.scalar_one_or_none()
    before_params = dict(existing.params) if existing and existing.params else None

    new_revision = current + 1
    if existing:
        existing.params = dict(params)
        existing.is_enabled = True
        existing.published_revision = new_revision
        existing.updated_by = operator
        existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
        existing.version += 1
    else:
        db.add(
            ConfigOverride(
                layer=layer,
                scope_id=str(scope_id),
                metric_code=metric_code,
                params=dict(params),
                is_enabled=True,
                published_revision=new_revision,
                updated_by=operator,
                updated_at=datetime.now(UTC).replace(tzinfo=None),
                version=1,
            )
        )

    scope = {
        "layer": layer,
        "scopeId": str(scope_id),
        "metricCode": metric_code,
        "channel": "config-publish",
    }
    affected_loops, note = await _count_affected_loops(db, layer, str(scope_id))
    publication = ConfigPublication(
        revision=new_revision,
        operation="PUBLISH",
        layer=layer,
        scope=scope,
        reason=reason,
        operator=operator,
        before_value=json.dumps(before_params, ensure_ascii=False, default=str)
        if before_params is not None
        else None,
        after_value=json.dumps(dict(params), ensure_ascii=False, default=str),
        affected_loops=affected_loops,
        rollback_revision=current,
    )
    db.add(publication)
    db.add(
        SysAuditLog(
            operator=operator,
            operation_type="CONFIG_PUBLISH",
            target_type="config_publication",
            target_id=str(new_revision),
            before_value=_audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=note,
                value=before_params,
            ),
            after_value=_audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=note,
                value=dict(params),
            ),
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )

    try:
        await db.commit()
    except IntegrityError:
        # revision 唯一约束：并发发布同持一个 expectedRevision，仅一方成功
        await db.rollback()
        latest = await get_current_revision(db)
        raise _conflict_409(latest) from None

    await sync_runtime_cache(db)
    await broadcast_config_revision(new_revision, source="api")
    return {
        "revision": new_revision,
        "rollbackRevision": current,
        "affectedLoops": affected_loops,
        "note": note,
        "before": before_params,
        "after": dict(params),
        "layer": layer,
        "scopeId": str(scope_id),
        "metricCode": metric_code,
    }


async def reset_override(
    db: AsyncSession,
    *,
    override_id: str,
    expected_revision: int,
    reason: str,
    operator: str,
) -> dict[str, Any]:
    """重置一层覆盖并解释"重置低层后高层仍生效"（P2-01 遗留收口）.

    删除 ``config_override`` 行后，该层此前遮盖的键回落到更低层；若更高层
    仍然覆盖其中某些键，返回 resetExplanation 逐键说明（键/仍生效值/来源层/
    来源作用域）——重置结果可解释，不产生"重置后值出乎意料"的暗变更。

    Raises:
        BizError: ERR_NOT_FOUND(404) / ERR_CONFIG_REVISION_CONFLICT(409)
    """
    current = await _require_revision_or_409(db, expected_revision)
    existing_result = await db.execute(
        select(ConfigOverride).where(ConfigOverride.id == override_id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing is None:
        await db.rollback()
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"配置覆盖不存在: {override_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    layer = existing.layer
    scope_id = existing.scope_id
    metric_code = existing.metric_code
    before_params = dict(existing.params) if existing.params else {}

    new_revision = current + 1
    await db.delete(existing)
    scope = {
        "layer": layer,
        "scopeId": scope_id,
        "metricCode": metric_code,
        "channel": "config-publish",
        # str()：裸 SQL/asyncpg 路径传入的 id 可能是原生 UUID 对象，
        # JSONB 序列化前统一字符串形态
        "overrideId": str(override_id),
    }
    affected_loops, note = await _count_affected_loops(db, layer, scope_id)
    publication = ConfigPublication(
        revision=new_revision,
        operation="RESET",
        layer=layer,
        scope=scope,
        reason=reason,
        operator=operator,
        before_value=json.dumps(before_params, ensure_ascii=False, default=str),
        after_value=None,
        affected_loops=affected_loops,
        rollback_revision=current,
    )
    db.add(publication)
    db.add(
        SysAuditLog(
            operator=operator,
            operation_type="CONFIG_RESET",
            target_type="config_publication",
            target_id=str(new_revision),
            before_value=_audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=note,
                value=before_params,
            ),
            after_value=_audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=note,
                value=None,
            ),
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        latest = await get_current_revision(db)
        raise _conflict_409(latest) from None

    await sync_runtime_cache(db)
    await broadcast_config_revision(new_revision, source="api")
    return {
        "revision": new_revision,
        "rollbackRevision": current,
        "affectedLoops": affected_loops,
        "note": note,
        "before": before_params,
        "after": None,
        "layer": layer,
        "scopeId": scope_id,
        "metricCode": metric_code,
    }


async def explain_reset(
    db: AsyncSession,
    *,
    layer: str,
    scope_id: str,
    metric_code: str,
    control_type: str | None,
    keys: list[str],
    template_key: str | None = None,
    node_id: str | None = None,
    loop_id: str | None = None,
) -> list[dict[str, Any]]:
    """解释重置某层后，给定键在更高层的剩余覆盖（重置结果可解释）.

    Args:
        keys: 被重置层此前覆盖的参数键
        template_key/node_id/loop_id: 更高层作用域（查询侧给定）
    """
    higher_scopes = {
        LAYER_TEMPLATE: template_key,
        LAYER_NODE: node_id,
        LAYER_LOOP: loop_id,
    }
    explanations: list[dict[str, Any]] = []
    for higher_layer in (LAYER_TEMPLATE, LAYER_NODE, LAYER_LOOP):
        if _layer_rank(higher_layer) <= _layer_rank(layer):
            continue
        higher_scope = higher_scopes.get(higher_layer)
        if not higher_scope:
            continue
        loaded = await _load_override_layer_value(db, higher_layer, str(higher_scope), metric_code)
        if loaded is None:
            continue
        higher_params, published_revision = loaded
        for key in keys:
            if key in higher_params:
                explanations.append(
                    {
                        "key": key,
                        "value": higher_params[key],
                        "source": higher_layer,
                        "sourceId": str(higher_scope),
                        "sourceRevision": str(published_revision),
                    }
                )
    return explanations


async def rollback_publication(
    db: AsyncSession,
    *,
    revision: int,
    expected_revision: int,
    reason: str,
    operator: str,
) -> dict[str, Any]:
    """回退指定发布：将其 before 状态重新应用为**新 revision**（不删历史）.

    回退是逆操作重放而非时间旅行——rollback(R) 把 R 号发布作用范围内的状态
    恢复为 R.before；R 之后其他作用域的发布不受影响。

    Raises:
        BizError: ERR_NOT_FOUND(404) / ERR_CONFIG_REVISION_CONFLICT(409)
    """
    current = await _require_revision_or_409(db, expected_revision)
    target_result = await db.execute(
        select(ConfigPublication).where(ConfigPublication.revision == revision)
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        await db.rollback()
        raise BizError(
            code="ERR_NOT_FOUND",
            message=f"发布记录不存在: revision={revision}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if revision > current:
        await db.rollback()
        raise BizError(
            code="ERR_PARAM_INVALID",
            message=f"回退目标 revision={revision} 大于当前 revision={current}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    scope = dict(target.scope or {})
    before_state = json.loads(target.before_value) if target.before_value else None
    new_revision = current + 1
    after_desc: str

    if target.layer in _VALID_LAYERS:
        layer = target.layer
        scope_id = str(scope.get("scopeId", ""))
        existing_result = await db.execute(
            select(ConfigOverride).where(
                ConfigOverride.layer == layer,
                ConfigOverride.scope_id == scope_id,
                ConfigOverride.metric_code == str(scope.get("metricCode", "")),
            )
        )
        existing = existing_result.scalar_one_or_none()
        if before_state is None:
            if existing is not None:
                await db.delete(existing)
            after_desc = f"{layer} 层 {scope_id} 覆盖已删除（恢复至无覆盖）"
        else:
            if existing:
                existing.params = dict(before_state)
                existing.published_revision = new_revision
                existing.updated_by = operator
                existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
                existing.version += 1
            else:
                db.add(
                    ConfigOverride(
                        layer=layer,
                        scope_id=scope_id,
                        metric_code=str(scope.get("metricCode", "")),
                        params=dict(before_state),
                        is_enabled=True,
                        published_revision=new_revision,
                        updated_by=operator,
                        updated_at=datetime.now(UTC).replace(tzinfo=None),
                        version=1,
                    )
                )
            after_desc = f"{layer} 层 {scope_id} 覆盖已恢复为 revision={revision} 的 before 状态"
    else:
        # LEGACY_SYNC（algorithm_parameter 全局通道）：按 before 的 {ct: params} 恢复
        metric_code = str(scope.get("metricCode", ""))
        restored: dict[str, Any] = {}
        if isinstance(before_state, dict):
            for ct, ct_params in before_state.items():
                row_result = await db.execute(
                    select(AlgorithmParameter).where(
                        AlgorithmParameter.metric_code == metric_code,
                        AlgorithmParameter.control_type == ct,
                    )
                )
                row = row_result.scalar_one_or_none()
                if ct_params is None:
                    if row is not None:
                        await db.delete(row)
                    restored[ct] = None
                else:
                    if row:
                        row.params = dict(ct_params)
                        row.updated_by = operator
                        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
                        row.version += 1
                    else:
                        db.add(
                            AlgorithmParameter(
                                metric_code=metric_code,
                                control_type=ct,
                                params=dict(ct_params),
                                description=f"{metric_code} 算法参数（回退恢复）",
                                is_enabled=True,
                                updated_by=operator,
                                updated_at=datetime.now(UTC).replace(tzinfo=None),
                                version=1,
                            )
                        )
                    restored[ct] = dict(ct_params)
        after_desc = f"algorithm_parameter 全局通道已恢复为 revision={revision} 的 before 状态"

    rollback_scope = dict(scope)
    rollback_scope["channel"] = "config-publish-rollback"
    rollback_scope["rollbackOf"] = revision
    publication = ConfigPublication(
        revision=new_revision,
        operation="ROLLBACK",
        layer=target.layer,
        scope=rollback_scope,
        reason=reason,
        operator=operator,
        before_value=target.after_value,
        after_value=json.dumps(before_state, ensure_ascii=False, default=str)
        if before_state is not None
        else None,
        affected_loops=target.affected_loops,
        rollback_revision=current,
    )
    db.add(publication)
    db.add(
        SysAuditLog(
            operator=operator,
            operation_type="CONFIG_ROLLBACK",
            target_type="config_publication",
            target_id=str(new_revision),
            before_value=_audit_envelope(
                reason=reason,
                scope=rollback_scope,
                rollback_revision=current,
                affected_loops=target.affected_loops,
                note=after_desc,
                value=target.after_value,
            ),
            after_value=_audit_envelope(
                reason=reason,
                scope=rollback_scope,
                rollback_revision=current,
                affected_loops=target.affected_loops,
                note=after_desc,
                value=before_state,
            ),
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        latest = await get_current_revision(db)
        raise _conflict_409(latest) from None

    await sync_runtime_cache(db)
    await broadcast_config_revision(new_revision, source="rollback")
    return {
        "revision": new_revision,
        "rollbackRevision": current,
        "rolledBackRevision": revision,
        "description": after_desc,
    }


# ---------------------------------------------------------------------------
# 兼容通道（algorithm-params PUT 等）发布登记
# ---------------------------------------------------------------------------


async def note_legacy_sync(
    db: AsyncSession,
    *,
    metric_code: str,
    before: dict[str | None, dict[str, Any] | None],
    after: dict[str, dict[str, Any]],
    reason: str,
    operator: str,
    expected_revision: int | None = None,
    control_types: list[str] | None = None,
) -> int:
    """兼容写入通道的发布登记（LEGACY_SYNC）——推进统一 revision + 入账 + 审计.

    供既有 PUT /configs/algorithm-params/{metricCode} 在**同一事务**内调用：
    - expected_revision 提供时做 409 前置校验（旧前端不传则保持原行为）；
    - 生成 LEGACY_SYNC 发布记录（before/after 按 {controlType: params}）；
    - 调用方 commit 后应调 ``broadcast_after_commit()`` 广播加速通知。

    Returns:
        新 revision（= 原 revision + 1）
    """
    if expected_revision is not None:
        current = await _require_revision_or_409(db, expected_revision)
    else:
        current = await get_current_revision(db)
    new_revision = current + 1
    scope = {
        "layer": None,
        "channel": "algorithm-params",
        "metricCode": metric_code,
        "controlTypes": control_types or sorted(after.keys()),
    }
    affected_loops, note = await _count_affected_loops(db, None, None)
    before_value = json.dumps(before, ensure_ascii=False, default=str, sort_keys=True)
    after_value = json.dumps(after, ensure_ascii=False, default=str, sort_keys=True)
    db.add(
        ConfigPublication(
            revision=new_revision,
            operation="LEGACY_SYNC",
            layer=None,
            scope=scope,
            reason=reason,
            operator=operator,
            before_value=before_value,
            after_value=after_value,
            affected_loops=affected_loops,
            rollback_revision=current,
        )
    )
    db.add(
        SysAuditLog(
            operator=operator,
            operation_type="CONFIG_LEGACY_SYNC",
            target_type="config_publication",
            target_id=str(new_revision),
            before_value=_audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=note,
                value=before,
            ),
            after_value=_audit_envelope(
                reason=reason,
                scope=scope,
                rollback_revision=current,
                affected_loops=affected_loops,
                note=note,
                value=after,
            ),
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    return new_revision


async def broadcast_after_commit(revision: int, source: str = "api") -> None:
    """兼容通道 commit 后的广播 + 本进程缓存同步（供端点调用）."""
    await broadcast_config_revision(revision, source=source)


# ---------------------------------------------------------------------------
# 跨进程通知（CONFIG_REVISION_CHANNEL pub/sub，THRESHOLD_CHANNEL 样板）
# ---------------------------------------------------------------------------


def _get_sync_redis():  # noqa: ANN202 - 与 confidence_evaluator 同款签名风格
    """获取同步 Redis 客户端（后台订阅线程用，不依赖 event loop）."""
    import redis as sync_redis

    from app.core.config import settings

    return sync_redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
    )


def _reload_runtime_cache_sync() -> bool:
    """在新事件循环中从 DB 重载运行时缓存（供订阅线程/测试调用）.

    失败返回 False 并记日志——一致性不依赖本函数：任务边界
    ``pin_config_snapshot`` 会按持久 revision 再校验补齐。
    """
    import asyncio

    from app.core.db import AsyncSessionLocal

    async def _reload() -> None:
        async with AsyncSessionLocal() as db:
            await sync_runtime_cache(db)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_reload())
        return True
    except Exception:  # noqa: BLE001
        logger.exception(
            "[config-publish pid=%s] 订阅线程重载运行时缓存失败（任务边界将按持久 revision 补齐）",
            os.getpid(),
        )
        return False
    finally:
        loop.close()


def _handle_config_revision_message(message_data: str) -> bool:
    """处理一条 revision 广播（版本去重后重载运行时缓存；供测试直接调用）.

    Returns:
        True 表示已按新版本重载；False 表示版本过期/解析失败/重载失败（跳过）
    """
    try:
        data = json.loads(message_data)
        msg_revision = int(data.get("revision", -1))
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.exception(
            "[config-publish pid=%s] 解析配置 revision 广播消息失败，已跳过", os.getpid()
        )
        return False
    if msg_revision <= _get_synced_revision():
        return False
    logger.info(
        "[config-publish pid=%s] 收到配置 revision 广播: revision=%s, source=%s",
        os.getpid(),
        msg_revision,
        data.get("source", "unknown"),
    )
    return _reload_runtime_cache_sync()


def _config_revision_subscriber_loop() -> None:
    """后台守护线程：订阅 CONFIG_REVISION_CHANNEL，按版本去重重载缓存.

    连接断开自动重连（listen() 抛异常后 5 秒重试）。
    """
    import time

    while True:
        try:
            client = _get_sync_redis()
            pubsub = client.pubsub()
            pubsub.subscribe(CONFIG_REVISION_CHANNEL)
            logger.info(
                "[config-publish pid=%s] 配置 revision 订阅线程已连接 Redis，频道: %s",
                os.getpid(),
                CONFIG_REVISION_CHANNEL,
            )
            for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                _handle_config_revision_message(message["data"])
        except Exception:  # noqa: BLE001
            logger.exception(
                "[config-publish pid=%s] 配置 revision 订阅线程异常，5 秒后重连",
                os.getpid(),
            )
            time.sleep(5)


def start_config_revision_subscriber() -> None:
    """启动配置 revision 订阅后台守护线程（进程级幂等）.

    在 uvicorn lifespan（API 进程）与 Celery worker_process_init（每个
    prefork 子进程）调用。
    """
    global _subscriber_started
    with _subscriber_lock:
        if _subscriber_started:
            return
        thread = threading.Thread(
            target=_config_revision_subscriber_loop,
            name="config-revision-subscriber",
            daemon=True,
        )
        thread.start()
        _subscriber_started = True
        logger.info(
            "[config-publish pid=%s] 配置 revision 订阅守护线程已启动 (thread=%s)",
            os.getpid(),
            thread.name,
        )


async def preload_config_revision(db: AsyncSession) -> None:
    """lifespan / worker_process_init 预载：同步缓存至当前持久 revision + 启订阅.

    预载失败不阻塞启动（调用方兜底），但任务边界的 revision 校验会显式失败
    而非用过期值静默计算。
    """
    await sync_runtime_cache(db)
    start_config_revision_subscriber()


__all__ = [
    "CONFIG_REVISION_CHANNEL",
    "LAYER_LOOP",
    "LAYER_NODE",
    "LAYER_TASK",
    "LAYER_TEMPLATE",
    "SOURCE_DEFAULT",
    "_handle_config_revision_message",
    "broadcast_after_commit",
    "broadcast_config_revision",
    "explain_reset",
    "get_current_revision",
    "get_last_publication",
    "note_legacy_sync",
    "pin_config_snapshot",
    "preload_config_revision",
    "preview_publish_override",
    "publish_override",
    "reset_override",
    "resolve_effective_params",
    "resolve_from_snapshot",
    "resolve_loop_params_map",
    "rollback_publication",
    "start_config_revision_subscriber",
    "sync_runtime_cache",
]
