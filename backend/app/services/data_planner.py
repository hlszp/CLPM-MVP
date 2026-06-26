"""DataPlanner 数据编排器 — v4.0 架构中枢.

DataPlanner 负责指标驱动的数据获取与编排：
    1. 读取指标数据需求契约（clpm_metric_data_requirement）
    2. 按 tagGroup 合并查询计划（流量回路复用 BASE）
    3. 查询 L1 DataBlock 缓存（zstd 压缩）
    4. 未命中 → 查询 TDengine + 8 步预处理 → 写入缓存
    5. 组装 MetricDataBundle（含 8 字段数据血缘）

核心优化：
    - tagGroup 复用：流量回路（FC）BASE 已是 1s，OP_HF/PVOP_HF/MODE_HF/QUALITY_HF
      直接从 BASE DataBlock 派生，仅需 1 次 TDengine 查询（算法说明 §3.5.2）
    - 缓存复用：多指标共享同一 tagGroup 时仅查询/预处理一次
    - Pipeline 批量写入：减少 Redis 网络往返

设计依据：ADS §2/§8/§10.1/§10.7, FDS §4/§5.3.9, PRD §8.1-8.3, 数据流程图 §7
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.contracts.data_types import (
    ControlType,
    DataBlock,
    LoopPreprocessConfig,
    MetricDataBundle,
    RawTimeSeries,
    TagGroup,
    TimeWindow,
)
from app.services.cache.l1_datablock import L1DataBlockCache
from app.services.cache.l2_bundle import L2BundleCache
from app.services.metric_data_bundle import MetricDataBundleAssembler
from app.services.preprocessing.pipeline import PREPROCESS_VERSION, PreprocessingPipeline
from app.services.preprocessing.thresholds import get_threshold

logger = logging.getLogger(__name__)

# TDengine 查询函数签名：按 tag 角色列表查询原始时序数据
# 生产环境由适配器将现有 query_trend_data 包装为此签名；测试时注入 mock
TDengineQueryFn = Callable[
    [str, list[str], datetime, datetime, int],
    Awaitable[RawTimeSeries],
]

# 回路预处理配置加载器签名：返回 LoopPreprocessConfig（含 range_min/range_max/config_version）
ConfigLoader = Callable[[str, ControlType], Awaitable[LoopPreprocessConfig]]


# ---------------------------------------------------------------------------
# 查询计划数据结构
# ---------------------------------------------------------------------------


@dataclass
class QueryTask:
    """单个 tagGroup 的查询任务（合并后）.

    Attributes:
        tag_group: 目标 tagGroup
        metrics: 依赖此 tagGroup 的指标列表
        tag_roles: 需要查询的 tag 角色并集（如 ["pv", "sp"]）
        interval_s: 采样间隔（秒）
        reused_from: 若复用 BASE，则为 BASE；否则 None
    """

    tag_group: TagGroup
    metrics: list[str]
    tag_roles: list[str]
    interval_s: int
    reused_from: TagGroup | None = None


# ---------------------------------------------------------------------------
# DataPlanner
# ---------------------------------------------------------------------------


class DataPlanner:
    """数据编排器：指标驱动取数 + 查询计划合并 + DataBlock 缓存管理.

    使用方式::

        planner = DataPlanner(
            cache=L1DataBlockCache(redis_client),
            tdengine_query_fn=my_query_fn,
            assembler=MetricDataBundleAssembler(),
            db=session,
            config_loader=my_config_loader,
            bundle_cache=L2BundleCache(redis_client),  # 可选，启用 L2 Bundle 缓存
        )
        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate", "stability_rate"],
            time_window=TimeWindow(start, end),
            control_type=ControlType.TEMPERATURE,
        )

    设计依据：ADS §2, 数据流程图 §7.1
    """

    def __init__(
        self,
        cache: L1DataBlockCache,
        tdengine_query_fn: TDengineQueryFn,
        assembler: MetricDataBundleAssembler,
        db: Any | None = None,
        config_loader: ConfigLoader | None = None,
        bundle_cache: L2BundleCache | None = None,
    ) -> None:
        """初始化 DataPlanner.

        Args:
            cache: L1 DataBlock 缓存
            tdengine_query_fn: TDengine 查询函数（按 tag 角色列表查询）
            assembler: MetricDataBundle 组装器
            db: 异步数据库会话（查询契约表；config_loader 为 None 时也用于加载回路配置）
            config_loader: 回路预处理配置加载器（注入便于测试，None 时用默认 db 查询）
            bundle_cache: L2 MetricDataBundle 缓存（可选，``None`` 时禁用 L2 缓存）。
                启用后 ``request_bundles`` 会优先查询 L2，命中则跳过查询计划与组装。
        """
        self._cache = cache
        self._query_fn = tdengine_query_fn
        self._assembler = assembler
        self._db = db
        self._config_loader = config_loader or self._default_config_loader
        self._bundle_cache = bundle_cache
        # 待写入 L2 缓存的 Key（request_bundles 中设置，_maybe_write_l2_cache 消费）
        self._pending_l2_key: str | None = None

    # ------------------------------------------------------------------
    # 核心入口
    # ------------------------------------------------------------------

    async def request_bundles(
        self,
        loop_id: str,
        metrics: list[str],
        time_window: TimeWindow,
        control_type: ControlType,
    ) -> list[MetricDataBundle]:
        """提交数据需求，返回 MetricDataBundle 列表.

        流程（数据流程图 §7.1）：
            Phase 1: L2 Bundle 缓存查询（若启用，命中则直接返回，跳过组装）
            Phase 2: 读取指标数据需求契约
            Phase 3: 合并相同 tagGroup 的查询计划
            Phase 4: 查询 DataBlock 缓存
            Phase 5: 未命中 → 查询 TDengine
            Phase 6: 8 步预处理
            Phase 7: 写入缓存（Pipeline 批量）
            Phase 8: 组装 MetricDataBundle + 写入 L2 缓存

        Args:
            loop_id: 回路 ID
            metrics: 指标代码列表，如 ``["accuracy_rate", "stability_rate"]``
            time_window: 评估时间窗口
            control_type: 回路控制类型（决定采样率与阈值）

        Returns:
            MetricDataBundle 列表（每个指标一个 Bundle）

        设计依据：ADS §2, §10.7.1, 数据流程图 §7.1
        """
        logger.debug(
            "DataPlanner.request_bundles: loop=%s, metrics=%s, window=%s~%s, control=%s",
            loop_id,
            metrics,
            time_window.start.isoformat(),
            time_window.end.isoformat(),
            control_type.value,
        )

        # Phase 1: L2 Bundle 缓存查询（若启用，命中则直接返回，跳过查询与组装）
        if self._bundle_cache is not None and metrics:
            l2_key = L2BundleCache.build_key(
                loop_id=loop_id,
                metrics=metrics,
                time_window_start=time_window.start,
                time_window_end=time_window.end,
                control_type=control_type.value,
            )
            cached_bundles = await self._bundle_cache.get(l2_key)
            if cached_bundles is not None:
                logger.info(
                    "DataPlanner L2 命中，跳过查询+组装: loop=%s, bundles=%d",
                    loop_id,
                    len(cached_bundles),
                )
                return cached_bundles
            # 未命中，记录 Key 供 Phase 8 写入使用
            self._pending_l2_key = l2_key
        else:
            self._pending_l2_key = None

        # Phase 2: 读取指标数据需求契约
        requirements = await self._load_requirements(metrics)
        if not requirements:
            logger.warning("DataPlanner: 未找到任何指标契约: metrics=%s", metrics)
            return []

        # 加载回路预处理配置（含 range_min/range_max/config_version）
        preprocess_config = await self._config_loader(loop_id, control_type)

        # Phase 3: 合并查询计划（按 tagGroup 分组）
        query_plan = self._build_query_plan(requirements, control_type)
        logger.info(
            "DataPlanner 查询计划: loop=%s, metrics=%d→tagGroups=%d, tasks=%s",
            loop_id,
            len(metrics),
            len(query_plan),
            [
                f"{t.tag_group.value}(metrics={t.metrics},tags={t.tag_roles},"
                f"interval={t.interval_s}s,reuse={t.reused_from})"
                for t in query_plan
            ],
        )

        # Phase 4-7: 执行查询计划（查缓存 → 未命中查 TDengine + 预处理 → 写缓存）
        data_blocks = await self._execute_query_plan(
            query_plan, loop_id, time_window, control_type, preprocess_config
        )

        # Phase 8: 按指标组装 MetricDataBundle
        bundles = self._assemble_bundles(requirements, data_blocks)

        # Phase 8 (续): 写入 L2 缓存（若启用且本次未命中）
        await self._maybe_write_l2_cache(bundles)

        logger.info(
            "DataPlanner 完成: loop=%s, bundles=%d, cached_blocks=%d",
            loop_id,
            len(bundles),
            len(data_blocks),
        )
        return bundles

    # ------------------------------------------------------------------
    # Phase 2: 读取指标数据需求契约
    # ------------------------------------------------------------------

    async def _load_requirements(
        self, metrics: list[str]
    ) -> dict[str, Any]:
        """从 clpm_metric_data_requirement 表读取指标契约.

        Args:
            metrics: 指标代码列表

        Returns:
            ``{metric_code: ClpmMetricDataRequirement}`` 字典

        设计依据：DDS §2.15, ADS §2, 算法说明 §3.6
        """
        if self._db is None:
            logger.debug("DataPlanner: db session 未注入，返回空契约")
            return {}

        from sqlalchemy import select

        from app.models.metric_data_requirement import ClpmMetricDataRequirement

        result = await self._db.execute(
            select(ClpmMetricDataRequirement).where(
                ClpmMetricDataRequirement.metric_code.in_(metrics)
            )
        )
        rows = result.scalars().all()
        return {row.metric_code: row for row in rows}

    # ------------------------------------------------------------------
    # Phase 3: 构建合并查询计划
    # ------------------------------------------------------------------

    def _build_query_plan(
        self,
        requirements: dict[str, Any],
        control_type: ControlType,
    ) -> list[QueryTask]:
        """按 tagGroup 分组指标并合并查询计划.

        合并规则（算法说明 §3.5.2）：
            - 相同 tagGroup 的指标合并为一次查询（tags 取并集）
            - 流量回路（FC）BASE=1s，OP_HF/PVOP_HF/MODE_HF/QUALITY_HF 复用 BASE
            - CONFIG tagGroup 跳过（无时序数据，如 ideal_settling_time）

        Args:
            requirements: ``{metric_code: requirement}`` 字典
            control_type: 控制类型

        Returns:
            QueryTask 列表（每个对应一次 TDengine 查询或一次 BASE 派生）

        设计依据：算法说明 §3.5.2, PRD §8.3
        """
        base_threshold = get_threshold(control_type)
        base_interval = base_threshold.base_sampling_freq
        # 流量回路 BASE=1s，高频 tagGroup 可复用 BASE
        reuse_base = base_interval == 1

        # 按 tagGroup 分组指标，并合并 tags
        grouped: dict[TagGroup, dict[str, Any]] = {}
        for metric_code, req in requirements.items():
            tag_group = self._parse_tag_group(req.tag_group)
            # CONFIG tagGroup 无时序数据，跳过
            if tag_group == TagGroup.CONFIG:
                logger.debug("跳过 CONFIG 指标（无时序数据）: %s", metric_code)
                continue

            if tag_group not in grouped:
                grouped[tag_group] = {"metrics": [], "tag_roles": set(), "req": req}
            grouped[tag_group]["metrics"].append(metric_code)
            # 合并 tags（并集）
            tags = req.tags if isinstance(req.tags, list) else list(req.tags or [])
            for t in tags:
                grouped[tag_group]["tag_roles"].add(t)

        # 构建 QueryTask 列表
        tasks: list[QueryTask] = []
        base_tags: set[str] = set()

        # 若复用 BASE，先收集 BASE 需要的额外 tags（来自所有 HF tagGroup）
        if reuse_base and TagGroup.BASE in grouped:
            base_tags = grouped[TagGroup.BASE]["tag_roles"].copy()
            for tg, info in grouped.items():
                if tg != TagGroup.BASE:
                    base_tags.update(info["tag_roles"])

        for tag_group, info in grouped.items():
            tag_roles = sorted(info["tag_roles"])

            # 复用 BASE：HF tagGroup 标记为 reused_from=BASE
            if reuse_base and tag_group != TagGroup.BASE:
                task = QueryTask(
                    tag_group=tag_group,
                    metrics=info["metrics"],
                    tag_roles=tag_roles,
                    interval_s=base_interval,  # 复用 BASE 的采样率
                    reused_from=TagGroup.BASE,
                )
            elif tag_group == TagGroup.BASE and reuse_base:
                # BASE 需包含所有 HF tagGroup 的 tags（用于派生）
                task = QueryTask(
                    tag_group=tag_group,
                    metrics=info["metrics"],
                    tag_roles=sorted(base_tags),
                    interval_s=base_interval,
                    reused_from=None,
                )
            else:
                # 非复用：BASE 按控制类型采样，HF 固定 1s
                interval = (
                    base_interval if tag_group == TagGroup.BASE else 1
                )
                task = QueryTask(
                    tag_group=tag_group,
                    metrics=info["metrics"],
                    tag_roles=tag_roles,
                    interval_s=interval,
                    reused_from=None,
                )
            tasks.append(task)

        # 排序：BASE 优先（派生依赖 BASE）
        tasks.sort(key=lambda t: (t.reused_from is not None, t.tag_group.value))
        return tasks

    @staticmethod
    def _parse_tag_group(value: str | TagGroup) -> TagGroup:
        """将字符串解析为 TagGroup 枚举."""
        if isinstance(value, TagGroup):
            return value
        try:
            return TagGroup(value)
        except ValueError:
            logger.warning("未知 tagGroup 值，回退为 BASE: %s", value)
            return TagGroup.BASE

    # ------------------------------------------------------------------
    # Phase 4-7: 执行查询计划
    # ------------------------------------------------------------------

    async def _execute_query_plan(
        self,
        query_plan: list[QueryTask],
        loop_id: str,
        time_window: TimeWindow,
        control_type: ControlType,
        preprocess_config: LoopPreprocessConfig,
    ) -> dict[TagGroup, DataBlock]:
        """执行查询计划：查缓存 → 未命中查 TDengine + 预处理 → 写缓存.

        对于复用 BASE 的 tagGroup，从 BASE DataBlock 派生子集，不单独查询。
        未命中的 DataBlock 通过 Pipeline 批量写入缓存（减少 RTT）。

        Args:
            query_plan: 合并后的查询计划
            loop_id: 回路 ID
            time_window: 时间窗口
            control_type: 控制类型
            preprocess_config: 预处理配置

        Returns:
            ``{TagGroup: DataBlock}`` 字典

        设计依据：数据流程图 §7.1 Phase 4-7
        """
        data_blocks: dict[TagGroup, DataBlock] = {}
        # 待批量写入缓存的 (cache_key, DataBlock) 对
        pending_writes: list[tuple[str, DataBlock]] = []

        for task in query_plan:
            # 复用 BASE：从已查询的 BASE DataBlock 派生
            if task.reused_from is not None:
                base_block = data_blocks.get(task.reused_from)
                if base_block is None:
                    logger.warning(
                        "无法派生 %s：BASE DataBlock 未就绪，跳过",
                        task.tag_group.value,
                    )
                    continue
                derived = self._derive_from_base(
                    base_block, task.tag_group, task.tag_roles, loop_id
                )
                data_blocks[task.tag_group] = derived
                logger.debug(
                    "从 BASE 派生 %s: tags=%s, points=%d",
                    task.tag_group.value,
                    task.tag_roles,
                    derived.point_count,
                )
                continue

            # 构建缓存 Key
            # pre_version: 预处理版本（PreprocessingPipeline 升级时递增）
            # cfg_version: 回路配置版本（量程/控制类型变更时递增）
            cache_key = L1DataBlockCache.build_key(
                loop_id=loop_id,
                tag_group=task.tag_group.value,
                time_window_start=time_window.start,
                time_window_end=time_window.end,
                sampling_freq=f"{task.interval_s}s",
                quality_policy=self._quality_policy_for(task.tag_group),
                pre_version=PREPROCESS_VERSION,
                cfg_version=preprocess_config.config_version,
            )

            # Phase 4: 查询缓存
            cached = await self._cache.get(cache_key)
            if cached is not None:
                data_blocks[task.tag_group] = cached
                continue

            # Phase 5-6: 未命中 → 查询 TDengine + 8 步预处理
            data_block = await self._query_and_preprocess(
                loop_id=loop_id,
                task=task,
                time_window=time_window,
                preprocess_config=preprocess_config,
            )
            data_blocks[task.tag_group] = data_block
            pending_writes.append((cache_key, data_block))

        # Phase 7: Pipeline 批量写入未命中的 DataBlock
        if pending_writes:
            keys = [k for k, _ in pending_writes]
            blocks = [b for _, b in pending_writes]
            written = await self._cache.set_many(blocks, keys=keys)
            logger.info(
                "DataPlanner 缓存写入: loop=%s, blocks=%d, written=%d",
                loop_id,
                len(pending_writes),
                written,
            )

        return data_blocks

    async def _query_and_preprocess(
        self,
        loop_id: str,
        task: QueryTask,
        time_window: TimeWindow,
        preprocess_config: LoopPreprocessConfig,
    ) -> DataBlock:
        """查询 TDengine + 8 步预处理，生成 DataBlock.

        Phase 5: 查询 TDengine
        Phase 6: 8 步预处理

        设计依据：数据流程图 §7.1 Phase 5-6
        """
        logger.info(
            "DataPlanner 回源查询: loop=%s, tagGroup=%s, tags=%s, interval=%ds",
            loop_id,
            task.tag_group.value,
            task.tag_roles,
            task.interval_s,
        )

        # Phase 5: 查询 TDengine
        raw = await self._query_fn(
            loop_id,
            task.tag_roles,
            time_window.start,
            time_window.end,
            task.interval_s,
        )

        if not raw.timestamps:
            logger.warning(
                "TDengine 返回空数据: loop=%s, tagGroup=%s",
                loop_id,
                task.tag_group.value,
            )
            # 返回空 DataBlock（避免后续 KeyError）
            return self._empty_data_block(loop_id, task.tag_group, task.interval_s)

        # Phase 6: 8 步预处理
        pipeline = PreprocessingPipeline(preprocess_config)
        data_block = pipeline.process(raw, task.tag_group)

        logger.debug(
            "预处理完成: loop=%s, tagGroup=%s, points=%d, valid_rate=%.4f",
            loop_id,
            task.tag_group.value,
            data_block.point_count,
            data_block.quality_summary.valid_rate,
        )
        return data_block

    # ------------------------------------------------------------------
    # tagGroup 复用派生
    # ------------------------------------------------------------------

    def _derive_from_base(
        self,
        base_block: DataBlock,
        tag_group: TagGroup,
        tag_roles: list[str],
        loop_id: str,
    ) -> DataBlock:
        """从 BASE DataBlock 派生子 tagGroup DataBlock（流量回路复用）.

        提取指定 tag 角色的信号/有效性/异常原因码，构造新的 DataBlock。
        时间戳、连续段、配置版本等与 BASE 一致。

        设计依据：算法说明 §3.5.2, 数据流程图 §7.3
        """
        signals: dict[str, list[Any]] = {}
        validity: dict[str, list[bool]] = {}
        outlier_reasons: dict[str, list[list[str]]] = {}

        for role in tag_roles:
            if role in base_block.signals:
                signals[role] = base_block.signals[role]
            valid_key = f"{role}_valid"
            if valid_key in base_block.validity:
                validity[valid_key] = base_block.validity[valid_key]
            if role in base_block.outlier_reasons:
                outlier_reasons[role] = base_block.outlier_reasons[role]

        data_block_id = f"db_{loop_id}_{tag_group.value}_{base_block.sampling_freq}"
        return DataBlock(
            data_block_id=data_block_id,
            loop_id=loop_id,
            tag_group=tag_group.value,
            sampling_freq=base_block.sampling_freq,
            timestamps=list(base_block.timestamps),
            signals=signals,
            validity=validity,
            outlier_reasons=outlier_reasons,
            quality_summary=base_block.quality_summary,
            consecutive_segments=list(base_block.consecutive_segments),
            config_version=base_block.config_version,
            preprocess_version=base_block.preprocess_version,
            point_count=base_block.point_count,
        )

    # ------------------------------------------------------------------
    # Phase 8: 组装 MetricDataBundle
    # ------------------------------------------------------------------

    def _assemble_bundles(
        self,
        requirements: dict[str, Any],
        data_blocks: dict[TagGroup, DataBlock],
    ) -> list[MetricDataBundle]:
        """按指标组装 MetricDataBundle.

        每个指标根据其契约的 tag_group 和 mask_expression，从对应 DataBlock
        组装 Bundle（应用 Metric Validity Mask + 生成数据血缘）。

        设计依据：数据流程图 §7.1 Phase 8, 算法说明 §3.6-3.7
        """
        bundles: list[MetricDataBundle] = []
        for metric_code, req in requirements.items():
            tag_group = self._parse_tag_group(req.tag_group)
            if tag_group == TagGroup.CONFIG:
                # CONFIG 指标（如 ideal_settling_time）无时序数据，不生成 Bundle
                continue

            data_block = data_blocks.get(tag_group)
            if data_block is None:
                logger.warning(
                    "DataBlock 缺失，跳过指标组装: metric=%s, tagGroup=%s",
                    metric_code,
                    tag_group.value,
                )
                continue

            bundle = self._assembler.assemble(
                metric_code=metric_code,
                data_block=data_block,
                mask_expression=req.mask_expression,
                requirement=req,
            )
            bundles.append(bundle)
        return bundles

    # ------------------------------------------------------------------
    # Phase 8 (续): 写入 L2 缓存
    # ------------------------------------------------------------------

    async def _maybe_write_l2_cache(self, bundles: list[MetricDataBundle]) -> None:
        """将组装好的 Bundle 列表写入 L2 缓存（若启用且本次 L2 未命中）.

        仅在本次 ``request_bundles`` 触发了 L2 查询且未命中时写入，
        避免重复写入已命中的 Key。空 Bundle 列表不写入。

        设计依据：ADS §10.7.1, 数据流程图 §7.1 Phase 8
        """
        l2_key = self._pending_l2_key
        # 消费后立即清空，避免跨请求泄漏
        self._pending_l2_key = None
        if l2_key is None or self._bundle_cache is None or not bundles:
            return
        try:
            await self._bundle_cache.set(l2_key, bundles)
            logger.debug(
                "DataPlanner L2 写入: key=%s, bundles=%d", l2_key, len(bundles)
            )
        except Exception:  # noqa: BLE001
            # L2 写入失败不应影响主流程（缓存只是优化）
            logger.warning(
                "DataPlanner L2 写入失败，忽略: key=%s", l2_key, exc_info=True
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _quality_policy_for(tag_group: TagGroup) -> str:
        """根据 tagGroup 推断质量策略标签.

        QUALITY_HF 使用 KEEP_ALL（好值率不删除行），
        其余默认 KEEP_ALL_WITH_VALIDITY。

        设计依据：算法说明 §3.4.1, §3.6.2
        """
        if tag_group == TagGroup.QUALITY_HF:
            return "KEEP_ALL"
        return "KEEP_ALL_WITH_VALIDITY"

    @staticmethod
    def _empty_data_block(
        loop_id: str, tag_group: TagGroup, interval_s: int
    ) -> DataBlock:
        """构造空 DataBlock（TDengine 返回空数据时使用）."""
        return DataBlock(
            data_block_id=f"db_{loop_id}_{tag_group.value}_{interval_s}s",
            loop_id=loop_id,
            tag_group=tag_group.value,
            sampling_freq=f"{interval_s}s",
            timestamps=[],
            signals={},
            validity={},
            outlier_reasons={},
            point_count=0,
        )

    async def _default_config_loader(
        self, loop_id: str, control_type: ControlType
    ) -> LoopPreprocessConfig:
        """默认回路配置加载器：从数据库查询量程与配置版本.

        查询 LoopTagMapping + TagRegistry 获取 PV tag 的 range_min/range_max。
        config_version 基于 LoopLedger.updated_at 生成。

        设计依据：DDS §3/§4/§5
        """
        if self._db is None:
            # 无 db session 时返回默认配置（便于单元测试）
            return LoopPreprocessConfig(
                loop_id=loop_id,
                control_type=control_type,
                range_min=0.0,
                range_max=100.0,
                config_version="v1",
            )

        from sqlalchemy import select

        from app.models.loop import LoopLedger, LoopTagMapping
        from app.models.tag import TagRegistry

        # 查询回路
        loop_result = await self._db.execute(
            select(LoopLedger).where(LoopLedger.id == loop_id)
        )
        loop = loop_result.scalar_one_or_none()

        # 查询 PV tag 的量程
        range_min = 0.0
        range_max = 100.0
        mapping_result = await self._db.execute(
            select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
        )
        mappings = {m.tag_role: m for m in mapping_result.scalars().all()}
        pv_mapping = mappings.get("PV")
        if pv_mapping:
            tag_result = await self._db.execute(
                select(TagRegistry).where(TagRegistry.id == str(pv_mapping.tag_id))
            )
            pv_tag = tag_result.scalar_one_or_none()
            if pv_tag:
                if pv_tag.range_min is not None:
                    range_min = float(pv_tag.range_min)
                if pv_tag.range_max is not None:
                    range_max = float(pv_tag.range_max)

        # config_version 基于 loop.updated_at（配置变更时自动递增）
        if loop and loop.updated_at:
            config_version = f"cfg_{int(loop.updated_at.timestamp())}"
        else:
            config_version = "v1"

        return LoopPreprocessConfig(
            loop_id=loop_id,
            control_type=control_type,
            range_min=range_min,
            range_max=range_max,
            config_version=config_version,
        )


__all__ = ["DataPlanner", "QueryTask"]
