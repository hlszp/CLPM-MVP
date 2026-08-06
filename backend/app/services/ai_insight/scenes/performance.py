"""性能评估场景策略。

基于回路最新 KPI 快照（6 大核心指标 + 综合评分 + 可信度）生成性能分析洞察，
聚焦「等级判定 → 短板分析 → 改善建议 → 优先级」，与诊断场景（聚焦标签根因）互补。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.services.ai_insight.base import SceneStrategy
from app.services.ai_insight.context import AiInsightContext
from app.services.performance import list_loop_snapshots

# 6 大 KPI 指标字段（与 KpiSnapshotHourly 列对齐）
_KPI_FIELDS = [
    "good_value_rate",
    "auto_mode_rate",
    "effective_auto_rate",
    "steady_rate",
    "accuracy_rate",
    "fast_rate",
    "oscillation_rate",
    "saturation_rate",
    "instrument_fault_rate",
]

# 指标中文名 + 优劣判定方向（True=越高越好，False=越低越好）
_KPI_META: dict[str, dict[str, Any]] = {
    "good_value_rate": {"name": "优良值率", "higher_better": True, "threshold": 0.95},
    "auto_mode_rate": {"name": "自控率", "higher_better": True, "threshold": 0.90},
    "effective_auto_rate": {"name": "有效自控率", "higher_better": True, "threshold": 0.85},
    "steady_rate": {"name": "稳定率", "higher_better": True, "threshold": 0.90},
    "accuracy_rate": {"name": "准确率", "higher_better": True, "threshold": 0.90},
    "fast_rate": {"name": "快速率", "higher_better": True, "threshold": 0.80},
    "oscillation_rate": {"name": "振荡率", "higher_better": False, "threshold": 0.10},
    "saturation_rate": {"name": "饱和率", "higher_better": False, "threshold": 0.10},
    "instrument_fault_rate": {"name": "仪表故障率", "higher_better": False, "threshold": 0.05},
}


def _grade_from_score(score: float | None) -> str:
    """根据综合评分推导性能等级（对齐 FDS §5.2.4 定级阈值）。"""
    if score is None:
        return "未知"
    if score >= 90:
        return "优秀"
    if score >= 80:
        return "良好"
    if score >= 60:
        return "合格"
    if score >= 40:
        return "警告"
    return "不合格"


def _to_pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


class PerformanceScene(SceneStrategy):
    """性能评估场景：基于 6 大 KPI 指标分析回路性能短板与改善方向。"""

    @property
    def scene_id(self) -> str:
        return "performance"

    @property
    def scene_name(self) -> str:
        return "性能评估"

    @property
    def required_params(self) -> str:
        return "loopId 必填"

    async def load_context(
        self,
        db: AsyncSession,
        *,
        loop_id: str | None = None,
        task_id: str | None = None,
    ) -> AiInsightContext:
        if not loop_id:
            raise BizError(
                code="ERR_MISSING_PARAM",
                message="性能评估场景需要 loopId",
                status_code=422,
            )
        rows, total = await list_loop_snapshots(
            db=db,
            loop_ids=[loop_id],
            latest_only=True,
            page=1,
            page_size=1,
        )
        if not rows:
            raise BizError(
                code="ERR_NO_KPI_DATA",
                message="该回路暂无 KPI 评估数据，请先触发性能评估",
                status_code=404,
            )
        snap, tag_name = rows[0]
        # 提取 KPI 指标值
        metrics: dict[str, float | None] = {}
        for f in _KPI_FIELDS:
            metrics[f] = getattr(snap, f, None)
        score = getattr(snap, "score", None)
        status = getattr(snap, "status", None)
        confidence_level = getattr(snap, "confidence_level", None)
        ts_start = getattr(snap, "ts_start", None)

        return AiInsightContext(
            scene=self.scene_id,
            loopId=loop_id,
            tagName=tag_name,
            data={
                "metrics": metrics,
                "score": float(score) if score is not None else None,
                "grade": _grade_from_score(float(score) if score else None),
                "status": status,
                "confidenceLevel": confidence_level,
                "tsStart": ts_start.isoformat() if ts_start else None,
                "total": total,
            },
        )

    def build_system_prompt(self, ctx: AiInsightContext) -> str:
        return (
            "你是危化企业控制回路性能评估专家，精通 GB/T 44693.2-2024 控制回路性能评估方法。\n"
            "请基于提供的回路 KPI 指标数据，生成一段性能分析洞察，包含以下部分：\n"
            "【等级判定】根据综合评分判定性能等级，概述回路整体表现\n"
            "【短板分析】逐项分析未达标的 KPI 指标，指出具体短板及可能的工艺/控制原因\n"
            "【改善建议】针对短板给出可操作的改善措施（如 PID 整定、仪表维护、工艺调整）\n"
            "【优先级】按改善收益从高到低排序，标注最该先处理的问题\n\n"
            "要求：\n"
            "1. 每个指标分析要引用具体数值与达标线对比\n"
            "2. 建议要具体可执行（如'稳定率 72% 低于 90% 达标线，建议减小积分时间'）\n"
            "3. 区分控制问题（PID 参数）与硬件问题（阀门/仪表）\n"
            "4. 总长度控制在 300-500 字"
        ) + self.build_knowledge_section(ctx)

    def build_user_prompt(self, ctx: AiInsightContext) -> str:
        d = ctx.data
        # 用中文名组装指标，便于 LLM 理解
        metric_lines: list[str] = []
        for f, meta in _KPI_META.items():
            v = d["metrics"].get(f)
            threshold = meta["threshold"]
            higher_better = meta["higher_better"]
            if v is None:
                status = "无数据"
            elif higher_better:
                status = "达标" if v >= threshold else "未达标"
            else:
                status = "达标" if v <= threshold else "未达标"
            metric_lines.append(
                f"  {meta['name']}（{f}）: {_to_pct(v)}，达标线 {_to_pct(threshold)}，{status}"
            )
        data = {
            "tagName": ctx.tagName,
            "compositeScore": d["score"],
            "grade": d["grade"],
            "confidenceLevel": d["confidenceLevel"],
            "status": d["status"],
            "评估时间": d["tsStart"],
            "KPI指标": "\n".join(metric_lines),
        }
        return f"请分析以下回路性能数据：\n{json.dumps(data, ensure_ascii=False, indent=2)}"

    def generate_template(self, ctx: AiInsightContext) -> str:
        d = ctx.data
        tag = ctx.tagName or "该回路"
        score = d["score"]
        grade = d["grade"]
        metrics: dict[str, float | None] = d["metrics"]
        conf = d.get("confidenceLevel") or "—"

        lines: list[str] = []
        # 概述
        score_text = f"{score:.1f}" if score is not None else "—"
        lines.append("【等级判定】")
        lines.append(
            f"{tag} 回路当前综合评分 {score_text}，性能等级「{grade}」，可信度 {conf} 级。"
        )
        if grade in ("优秀", "良好"):
            lines.append("整体性能良好，建议保持当前控制策略并定期监测。")
        elif grade == "合格":
            lines.append("整体性能合格但仍有提升空间，建议关注下列短板指标。")
        else:
            lines.append("整体性能不理想，存在明显短板，建议尽快排查处理。")

        # 短板分析
        lines.append("")
        lines.append("【短板分析】")
        shortfalls: list[str] = []
        for f, meta in _KPI_META.items():
            v = metrics.get(f)
            if v is None:
                continue
            threshold = meta["threshold"]
            higher_better = meta["higher_better"]
            is_shortfall = (v < threshold) if higher_better else (v > threshold)
            if is_shortfall:
                shortfalls.append(f"  • {meta['name']} {_to_pct(v)}（达标线 {_to_pct(threshold)}）")
        if shortfalls:
            lines.extend(shortfalls)
        else:
            lines.append("  所有指标均达标，无明显短板。")

        # 改善建议
        lines.append("")
        lines.append("【改善建议】")
        if not shortfalls:
            lines.append("  维持现状，定期复评即可。")
        else:
            # 基于短板类型给建议
            if metrics.get("oscillation_rate") and metrics["oscillation_rate"] > 0.10:
                lines.append("  • 振荡率偏高：建议进入诊断中心排查振荡源（PID 过激/阀门粘滞/外扰）")
            if metrics.get("saturation_rate") and metrics["saturation_rate"] > 0.10:
                lines.append("  • 饱和率偏高：检查阀门选型与工况负荷匹配度，必要时调整工艺降低负荷")
            if metrics.get("steady_rate") and metrics["steady_rate"] < 0.90:
                lines.append("  • 稳定率偏低：考虑 PID 参数整定（减小比例增益或增大积分时间）")
            if metrics.get("auto_mode_rate") and metrics["auto_mode_rate"] < 0.90:
                lines.append("  • 自控率偏低：排查频繁切手动的原因，优化控制逻辑或投用条件")
            if metrics.get("instrument_fault_rate") and metrics["instrument_fault_rate"] > 0.05:
                lines.append("  • 仪表故障率偏高：联系仪表人员检修测量回路，恢复数据质量")
            if metrics.get("fast_rate") and metrics["fast_rate"] < 0.80:
                lines.append("  • 快速率偏低：适当增大比例增益或减小积分时间以提升响应速度")

        # 优先级
        lines.append("")
        lines.append("【优先级】")
        if score is not None and score < 60:
            lines.append("  ⚠ 该回路性能等级低，建议作为高优先级尽快处置。")
        elif score is not None and score < 80:
            lines.append("  该回路存在中等短板，建议择机优化。")
        else:
            lines.append("  该回路性能良好，纳入常规监测即可。")

        return "\n".join(lines)
