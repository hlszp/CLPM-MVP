"""指标可信度评估器与综合评分计算（Phase 3 任务 3.3-3.4）.

提供：
1. 基于有效数据率（valid_rate）判定指标可信度等级 A/B/C/D/E（算法说明 §3.7.2）。
2. 构建数据血缘 DataLineage（算法说明 §3.7.1，8 字段）。
3. 综合评分 v2 计算：P = (A·a + F·f + S·s)/(a+f+s) × R（算法说明 §4.10）。

设计依据：算法说明 §3.7.1, §3.7.2, §4.10；GB/T 44693.2-2024 附录 B.6

可信度统一 Phase 3（P3-2 / D4）：阈值多进程同步。
- POST /configs/confidence-thresholds 保存后通过 Redis pub/sub 广播
- 各 Celery worker / uvicorn 进程后台守护线程订阅，收到消息后调 set_thresholds()
- worker_process_init / lifespan 启动时从 DB 全量加载兜底
- 消息含版本号去重，避免旧消息覆盖新阈值
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

from app.contracts.data_types import (
    ConfidenceLevel,
    DataLineage,
    MetricDataBundle,
    MetricResult,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

#: 算法版本号（与 kpi_calc / node_aggregation 对齐）
ALGORITHM_VERSION = "KPI_CALC_v2.0"

#: 质量策略标识（与预处理 Pipeline 对齐）
QUALITY_POLICY = "KEEP_ALL_WITH_VALIDITY"

#: 参与综合评分的核心指标代码
CORE_METRIC_CODES: tuple[str, ...] = (
    "accuracy_rate",
    "fast_rate",
    "stability_rate",
)

#: 折扣因子指标代码
DISCOUNT_METRIC_CODE = "effective_auto_rate"

#: 默认权重（无配置时回退，对齐国标附录 C 稳定型 STABLE 0.2/0.3/0.5）
#: v2.1 修正（对齐 FDS v5.1 §5.3.7.1 / 算法 v2.1 §4.10.3）：
#: 原 v2.0 权重 0.25/0.20/0.55 与国标默认值不一致，已修正为国标稳定型默认值。
#: 四套控制类型权重模板：
#:   - STABLE（稳定型）: a=0.2, f=0.3, s=0.5  —— 温度、压力控制
#:   - SLOW（慢速型）:   a=0.3, f=0.1, s=0.6  —— 缓慢调节回路
#:   - FAST（快速型）:   a=0.2, f=0.5, s=0.3  —— 副回路、流量控制
#:   - LOGIC（逻辑型）:  a=0.0, f=0.4, s=0.6  —— 防回流、防超温
#: effective_auto_rate 作为折扣因子 R 不参与权重分配。
DEFAULT_WEIGHTS: dict[str, float] = {
    "accuracy_rate": 0.2,
    "fast_rate": 0.3,
    "stability_rate": 0.5,
}

# ---------------------------------------------------------------------------
# 可信度阈值（可配置，通过 set_thresholds() 动态更新）
# ---------------------------------------------------------------------------

#: 算法默认可信度阈值（对齐算法说明 §3.7.2）
DEFAULT_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "A": 0.95,
    "B": 0.80,
    "C": 0.60,
    "D": 0.20,
}

#: 运行时可信度阈值缓存（通过 set_thresholds() 更新）
_threshold_cache: dict[str, float] = dict(DEFAULT_CONFIDENCE_THRESHOLDS)

#: 当前已应用的阈值版本号（用于 pub/sub 消息去重，仅单调递增）
_threshold_version: int = 0

#: Redis pub/sub 频道：阈值更新广播
THRESHOLD_CHANNEL = "confidence:thresholds:updated"

#: Redis key：阈值版本号计数器（INCR 生成递增版本号）
THRESHOLD_VERSION_KEY = "confidence:thresholds:version"

#: 订阅线程启动标记（进程级幂等，避免重复启动）
_subscriber_started: bool = False
_subscriber_lock = threading.Lock()


class ConfidenceEvaluator:
    """指标可信度评估器.

    提供可信度等级判定、数据血缘构建、综合评分计算三类能力。
    所有指标计算器与编排层共享本类的静态方法，保证可信度判定一致性。

    可信度阈值默认值可通过 ``set_thresholds()`` 动态更新（由配置接口触发）。

    设计依据：算法说明 §3.7.1, §3.7.2, §4.10
    """

    @staticmethod
    def set_thresholds(
        thresholds: dict[str, float] | None,
        version: int | None = None,
    ) -> None:
        """更新运行时可信度阈值缓存.

        由配置管理接口（confidence_config）在保存配置后调用，
        或由 pub/sub 订阅线程收到广播后调用。
        传入 None 或空字典时重置为算法默认值。

        Args:
            thresholds: ``{"A": 0.95, "B": 0.80, "C": 0.60, "D": 0.20}``
            version: 阈值版本号（pub/sub 去重用）。None 表示不更新版本号
                （如启动预载）。仅当 version > 当前版本号时才更新版本号。
        """
        global _threshold_cache, _threshold_version
        if not thresholds:
            _threshold_cache = dict(DEFAULT_CONFIDENCE_THRESHOLDS)
            logger.info("可信度阈值已重置为算法默认: %s", _threshold_cache)
        else:
            _threshold_cache = {
                "A": float(thresholds.get("A", DEFAULT_CONFIDENCE_THRESHOLDS["A"])),
                "B": float(thresholds.get("B", DEFAULT_CONFIDENCE_THRESHOLDS["B"])),
                "C": float(thresholds.get("C", DEFAULT_CONFIDENCE_THRESHOLDS["C"])),
                "D": float(thresholds.get("D", DEFAULT_CONFIDENCE_THRESHOLDS["D"])),
            }
            logger.info("可信度阈值已更新: %s", _threshold_cache)
        if version is not None and version > _threshold_version:
            _threshold_version = version

    @staticmethod
    def get_thresholds() -> dict[str, float]:
        """获取当前运行时可信度阈值（副本）."""
        return dict(_threshold_cache)

    @staticmethod
    def get_threshold_version() -> int:
        """获取当前已应用的阈值版本号（pub/sub 去重用）."""
        return _threshold_version

    @staticmethod
    def evaluate(valid_rate: float) -> ConfidenceLevel:
        """根据有效数据率判定可信度等级（算法说明 §3.7.2）.

        使用运行时阈值缓存（可通过 ``set_thresholds()`` 动态配置）。

        Args:
            valid_rate: 有效数据率 0~1

        Returns:
            可信度等级枚举 A/B/C/D/E

        等级阈值（默认值，可通过配置修改）：
            - A: valid_rate >= 0.95
            - B: 0.80 <= valid_rate < 0.95
            - C: 0.60 <= valid_rate < 0.80
            - D: 0.20 <= valid_rate < 0.60
            - E: valid_rate < 0.20 → INCONCLUSIVE

        可信度统一 Phase 3（P3-3 / D5）：valid_rate 落入 [D, D+0.10) 时
        记 WARN 告警（濒临 INCONCLUSIVE，数据质量接近不可计算边界）。
        """
        t = _threshold_cache

        # D5 监控：valid_rate 濒临 INCONCLUSIVE 时告警
        d_threshold = t["D"]
        if d_threshold <= valid_rate < d_threshold + 0.10:
            logger.warning(
                "valid_rate=%.4f 濒临 INCONCLUSIVE（D 阈值=%.2f），"
                "数据质量接近不可计算边界，建议检查数据源完整性",
                valid_rate,
                d_threshold,
            )

        if valid_rate >= t["A"]:
            return ConfidenceLevel.A
        if valid_rate >= t["B"]:
            return ConfidenceLevel.B
        if valid_rate >= t["C"]:
            return ConfidenceLevel.C
        if valid_rate >= t["D"]:
            return ConfidenceLevel.D
        return ConfidenceLevel.E

    @staticmethod
    def build_lineage(
        bundle: MetricDataBundle,
        valid_rate: float,
        algorithm_version: str = ALGORITHM_VERSION,
    ) -> DataLineage:
        """构建数据血缘（算法说明 §3.7.1，8 字段）.

        Args:
            bundle: 指标数据包
            valid_rate: 有效数据率 0~1
            algorithm_version: 算法版本号

        Returns:
            DataLineage 对象，含采样频率/聚合策略/质量策略/tagGroup/
            data_block_ids/valid_rate/预处理版本/算法版本
        """
        block = bundle.data_block
        return DataLineage(
            sampling_freq=block.sampling_freq,
            aggregation_policy="LAST",
            quality_policy=QUALITY_POLICY,
            tag_group=block.tag_group,
            data_block_ids=[block.data_block_id],
            valid_rate=round(valid_rate, 4),
            data_policy_version=block.preprocess_version,
            algorithm_version=algorithm_version,
        )

    @staticmethod
    def compute_composite_score(
        metric_results: dict[str, MetricResult],
        weights: dict[str, float] | None = None,
    ) -> MetricResult:
        """计算综合评分 P = (A·a + F·f + S·s)/(a+f+s) × R（算法说明 §4.10）.

        Args:
            metric_results: 各指标结果字典 ``{metric_code: MetricResult}``，
                至少含 accuracy_rate/fast_rate/stability_rate/effective_auto_rate
            weights: 权重配置 ``{accuracy_rate: a, fast_rate: f, stability_rate: s}``；
                None 时使用默认权重

        Returns:
            综合评分 MetricResult：
                - 有效自控率为 E 级（INCONCLUSIVE）→ value=None
                - 参与评分的核心指标缺失或 E 级（INCONCLUSIVE）→ value=None
                - 所有权重为 0 → value=0.0
                - 正常 → value=round2(基础评分 × R/100)

        设计依据：算法说明 §4.10.2, GB/T 44693.2-2024 附录 B.6

        缺失指标处理（评审决策口径）：
            参与评分（权重 > 0）的核心指标 accuracy_rate/fast_rate/stability_rate
            任一缺失（结果不存在或 value=None）或可信度 E 级（视同缺失）
            → 综合评分整体 INCONCLUSIVE（value=None, confidence=E），
            不做"分子计 0、分母留权重"的隐性扣分。
            权重为 0 的核心指标不参与评分公式（如逻辑型 a=0），不要求其存在。
            核心指标 D 级时保留评分，details 增加 low_confidence_inputs 标注。
        """
        w = weights if weights else DEFAULT_WEIGHTS
        a = float(w.get("accuracy_rate", 0.0))
        f = float(w.get("fast_rate", 0.0))
        s = float(w.get("stability_rate", 0.0))

        # 折扣因子 R
        r_result = metric_results.get(DISCOUNT_METRIC_CODE)
        r_value = r_result.value if r_result else None

        logger.info(
            "[综合评分] 输入: A=%s, F=%s, S=%s, R=%s, weights(a=%.3f, f=%.3f, s=%.3f)",
            metric_results.get("accuracy_rate").value
            if metric_results.get("accuracy_rate")
            else None,
            metric_results.get("fast_rate").value if metric_results.get("fast_rate") else None,
            metric_results.get("stability_rate").value
            if metric_results.get("stability_rate")
            else None,
            r_value,
            a,
            f,
            s,
        )

        # R 缺失或可信度 E 级 → 评分留空（INCONCLUSIVE）
        # P1 #18: 移除原"R 缺失降级 60%"无依据逻辑，统一视为 INCONCLUSIVE
        if (
            r_result is None
            or r_result.value is None
            or r_result.confidence_level == ConfidenceLevel.E.value
        ):
            logger.info(
                "[综合评分] R 缺失或可信度 E 级，评分留空（INCONCLUSIVE）: r_result=%s",
                "None"
                if r_result is None
                else f"value={r_result.value}, conf={r_result.confidence_level}",
            )
            if r_result and r_result.lineage:
                lineage = r_result.lineage
            else:
                # R 缺失时回退到 accuracy_rate 的血缘
                acc_result = metric_results.get("accuracy_rate")
                lineage = (
                    acc_result.lineage
                    if acc_result and acc_result.lineage
                    else DataLineage(algorithm_version=ALGORITHM_VERSION)
                )
            return MetricResult(
                metric_code="composite_score",
                value=None,
                confidence_level=ConfidenceLevel.E.value,
                lineage=lineage,
                details={"reason": "effective_auto_rate INCONCLUSIVE"},
            )

        # 核心指标缺失或可信度 E 级（视同缺失）→ 评分整体 INCONCLUSIVE
        # 消除原"分子计 0、分母留权重"的隐性惩罚（评审决策口径）
        inconclusive_core: list[str] = []
        for code, weight in (("accuracy_rate", a), ("fast_rate", f), ("stability_rate", s)):
            if weight <= 0:
                continue
            result = metric_results.get(code)
            if (
                result is None
                or result.value is None
                or result.confidence_level == ConfidenceLevel.E.value
            ):
                inconclusive_core.append(code)

        if inconclusive_core:
            logger.info(
                "[综合评分] 核心指标缺失或 E 级，评分留空（INCONCLUSIVE）: %s",
                inconclusive_core,
            )
            first_missing = metric_results.get(inconclusive_core[0])
            if first_missing and first_missing.lineage:
                lineage = first_missing.lineage
            else:
                acc_result = metric_results.get("accuracy_rate")
                lineage = (
                    acc_result.lineage
                    if acc_result and acc_result.lineage
                    else DataLineage(algorithm_version=ALGORITHM_VERSION)
                )
            return MetricResult(
                metric_code="composite_score",
                value=None,
                confidence_level=ConfidenceLevel.E.value,
                lineage=lineage,
                details={
                    "reason": "core metric INCONCLUSIVE",
                    "inconclusive_inputs": inconclusive_core,
                },
            )

        # 加权分子：(A*a + F*f + S*s)
        weighted_sum = 0.0
        for code, weight in (("accuracy_rate", a), ("fast_rate", f), ("stability_rate", s)):
            result = metric_results.get(code)
            val = result.value if result else None
            if val is not None and weight > 0:
                # 归一化到 [0,1] 再加权
                eta = max(0.0, min(1.0, val / 100.0))
                weighted_sum += weight * eta
                logger.info(
                    "[综合评分] 加权项 %s: value=%.4f eta=%.4f weight=%.3f 贡献=%.4f",
                    code,
                    float(val),
                    eta,
                    weight,
                    weight * eta,
                )
            else:
                logger.info(
                    "[综合评分] 加权项 %s: value=%s weight=%.3f 跳过（权重为0）",
                    code,
                    val,
                    weight,
                )

        total_weight = a + f + s
        logger.info("[综合评分] weighted_sum=%.4f, total_weight=%.3f", weighted_sum, total_weight)
        if total_weight <= 0:
            logger.warning("[综合评分] 所有权重总和为 0，返回 0")
            return MetricResult(
                metric_code="composite_score",
                value=0.0,
                confidence_level=ConfidenceLevel.A.value,
                lineage=DataLineage(algorithm_version=ALGORITHM_VERSION),
                details={"reason": "zero total weight"},
            )

        # 基础评分 = (A*a + F*f + S*s) / (a+f+s) * 100
        base_score = weighted_sum / total_weight * 100.0

        # R 作为乘数：P = base_score * R/100
        # R 缺失或 INCONCLUSIVE 已在前面提前返回，此处 r_value 必为有效值
        r_norm = max(0.0, min(1.0, r_value / 100.0))
        score = base_score * r_norm
        logger.info(
            "[综合评分] R 折扣: R=%.4f r_norm=%.4f, base_score=%.4f → score=%.4f",
            float(r_value),
            r_norm,
            base_score,
            score,
        )

        score = max(0.0, min(100.0, score))
        score = round(score, 2)

        # 可信度统一 Phase 2（P2-3）：综合评分可信度 = 回路级可信度。
        # 各指标可信度已统一为回路级（P2-2），不再需要 _min_confidence 取最低。
        # accuracy_rate 必存在（前面已校验核心指标非 INCONCLUSIVE），直接读取。
        confidence = metric_results["accuracy_rate"].confidence_level

        # D 级输入保留评分，但标注低可信度输入（评审决策口径）
        low_confidence_inputs = [
            code
            for code in (*CORE_METRIC_CODES, DISCOUNT_METRIC_CODE)
            if (result := metric_results.get(code)) is not None
            and result.confidence_level == ConfidenceLevel.D.value
        ]

        # 血缘取准确率的血缘（若存在）
        lineage_ref = metric_results.get("accuracy_rate")
        lineage = (
            lineage_ref.lineage
            if lineage_ref and lineage_ref.lineage
            else DataLineage(algorithm_version=ALGORITHM_VERSION)
        )

        logger.info(
            "[综合评分] 最终结果: base_score=%.4f, R=%s, score=%.2f, confidence=%s",
            base_score,
            r_value,
            score,
            confidence,
        )

        details: dict = {
            "base_score": round(base_score, 2),
            "effective_auto_rate": r_value,
            "weights": {"a": a, "f": f, "s": s},
        }
        if low_confidence_inputs:
            details["low_confidence_inputs"] = low_confidence_inputs

        return MetricResult(
            metric_code="composite_score",
            value=score,
            confidence_level=confidence,
            lineage=lineage,
            details=details,
        )


def _min_confidence(metric_results: dict[str, MetricResult]) -> str:
    """取核心指标 + R 中最低的可信度等级（A 最高，E 最低）.

    .. deprecated:: v6.2 可信度统一 Phase 2（P2-3）
        各指标可信度已统一为回路级，综合评分直接读取
        ``accuracy_rate.confidence_level``，不再需要取最低。
        保留此函数仅供向后兼容引用，不再被 ``compute_composite_score`` 调用。

    仅在综合评分可计算（非 INCONCLUSIVE）的路径调用——
    评分 INCONCLUSIVE 时各提前返回路径已显式返回 E 级，
    保证评分语义与可信度一致（INCONCLUSIVE 评分不会出现 A/B 可信度）。

    Args:
        metric_results: 指标结果字典

    Returns:
        最低可信度等级字符串
    """
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
    codes = (*CORE_METRIC_CODES, DISCOUNT_METRIC_CODE)
    worst = "A"
    worst_rank = 0
    for code in codes:
        result = metric_results.get(code)
        if result is None:
            continue
        rank = order.get(result.confidence_level, 4)
        if rank > worst_rank:
            worst_rank = rank
            worst = result.confidence_level
    return worst


# ---------------------------------------------------------------------------
# 可信度统一 Phase 3（P3-2 / D4）：阈值多进程同步 — Redis pub/sub
# ---------------------------------------------------------------------------


def _get_sync_redis():
    """获取同步 Redis 客户端（用于后台订阅线程，不依赖 event loop）."""
    import redis as sync_redis

    return sync_redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=True,
    )


async def broadcast_thresholds(thresholds: dict[str, float], source: str = "api") -> int:
    """通过 Redis pub/sub 广播阈值更新（POST 接口调用）.

    流程：
    1. INCR 版本号计数器 ``THRESHOLD_VERSION_KEY``
    2. PUBLISH 消息到 ``THRESHOLD_CHANNEL``（含 version/thresholds/source）

    Args:
        thresholds: 阈值字典 ``{"A": 0.95, "B": 0.80, ...}``
        source: 更新来源标记（如 "api" / "bootstrap"）

    Returns:
        新版本号
    """
    from app.core.redis import redis_client

    version = await redis_client.incr(THRESHOLD_VERSION_KEY)
    message = json.dumps(
        {
            "version": version,
            "thresholds": thresholds,
            "updated_at": datetime.now(UTC).isoformat(),
            "source": source,
        },
        ensure_ascii=False,
    )
    await redis_client.publish(THRESHOLD_CHANNEL, message)
    logger.info("阈值更新已广播: version=%s, source=%s", version, source)
    return version


def _handle_threshold_message(message_data: str) -> bool:
    """处理一条阈值更新广播消息（供订阅线程和测试调用）.

    解析消息 JSON，按版本号去重后调 ``set_thresholds()``。

    Args:
        message_data: Redis pub/sub message 的 data 字段（JSON 字符串）

    Returns:
        True 表示阈值已更新，False 表示因版本号过期或解析失败而跳过
    """
    global _threshold_version
    try:
        data = json.loads(message_data)
        msg_version = int(data.get("version", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.exception("解析阈值更新广播消息失败，已跳过")
        return False

    if msg_version <= _threshold_version:
        logger.debug(
            "阈值更新广播版本号过期，已跳过: msg=%s, current=%s",
            msg_version,
            _threshold_version,
        )
        return False

    thresholds = data.get("thresholds", {})
    ConfidenceEvaluator.set_thresholds(thresholds, version=msg_version)
    logger.info(
        "收到阈值更新广播并已应用: version=%s, source=%s",
        msg_version,
        data.get("source", "unknown"),
    )
    return True


def _threshold_subscriber_loop() -> None:
    """后台守护线程：订阅阈值更新频道，收到消息后调 set_thresholds().

    消息去重：仅当消息中的 version > 当前 _threshold_version 时才更新。
    连接断开时自动重连（Redis pub/sub 的 listen() 在连接断开时会抛异常，
    捕获后重试）。
    """
    while True:
        try:
            client = _get_sync_redis()
            pubsub = client.pubsub()
            pubsub.subscribe(THRESHOLD_CHANNEL)
            logger.info("可信度阈值订阅线程已连接 Redis，监听频道: %s", THRESHOLD_CHANNEL)
            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                _handle_threshold_message(message["data"])
        except Exception:  # noqa: BLE001
            logger.exception("阈值订阅线程异常，5 秒后重连")
            import time

            time.sleep(5)


def start_threshold_subscriber() -> None:
    """启动阈值更新订阅后台守护线程（进程级幂等）.

    在以下时机调用：
    - uvicorn lifespan 启动（API 进程）
    - Celery worker_process_init（每个 worker 子进程）

    幂等：同一进程内重复调用不会启动多个线程。
    """
    global _subscriber_started
    with _subscriber_lock:
        if _subscriber_started:
            return
        thread = threading.Thread(
            target=_threshold_subscriber_loop,
            name="confidence-threshold-subscriber",
            daemon=True,
        )
        thread.start()
        _subscriber_started = True
        logger.info("可信度阈值订阅线程已启动")


async def load_thresholds_from_db(db) -> None:  # noqa: ANN001
    """从 DB 加载当前可信度阈值到进程内缓存（启动预载兜底）.

    在 worker_process_init / lifespan 启动时调用，确保新进程启动时
    即使用未收到 pub/sub 广播也能读取到最新阈值。

    直接查询 ``sys_config`` 表（避免从 confidence_config 反向导入导致循环依赖），
    解析 ``confidence_thresholds.current`` JSON，提取各等级 minRate。

    Args:
        db: AsyncSession
    """
    from sqlalchemy import select

    from app.models.sys_config import SysConfig

    key = "confidence_thresholds.current"
    result = await db.execute(select(SysConfig).where(SysConfig.key == key))
    cfg = result.scalar_one_or_none()
    if not cfg or not cfg.value:
        # 未配置过，使用算法默认值（不写入数据库）
        ConfidenceEvaluator.set_thresholds(None)
        logger.info("可信度阈值预载：sys_config 无配置，使用算法默认值")
        return
    try:
        data = json.loads(cfg.value)
        # schema 结构：{"thresholds": [{"name": "A", "minRate": 0.95}, ...], ...}
        items = data.get("thresholds", [])
        threshold_map = {item["name"]: float(item["minRate"]) for item in items}
        if not threshold_map:
            ConfidenceEvaluator.set_thresholds(None)
            logger.warning("可信度阈值预载：thresholds 为空，使用算法默认值")
            return
        ConfidenceEvaluator.set_thresholds(threshold_map)
        logger.info("可信度阈值预载：已从 sys_config 加载: %s", threshold_map)
    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        logger.warning("可信度阈值预载解析失败，回退算法默认: %s", exc)
        ConfidenceEvaluator.set_thresholds(None)


__all__ = [
    "ALGORITHM_VERSION",
    "ConfidenceEvaluator",
    "CORE_METRIC_CODES",
    "DEFAULT_CONFIDENCE_THRESHOLDS",
    "DEFAULT_WEIGHTS",
    "DISCOUNT_METRIC_CODE",
    "QUALITY_POLICY",
    "THRESHOLD_CHANNEL",
    "THRESHOLD_VERSION_KEY",
    "_handle_threshold_message",
    "broadcast_thresholds",
    "load_thresholds_from_db",
    "start_threshold_subscriber",
]
