"""工作台场景策略。

基于全局看板数据（KPI 汇总 + 部分数据警告 + 趋势）生成运维管理视角的洞察，
聚焦「全局健康概览 → 重点关注 → 趋势预警 → 建议动作」。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_insight.base import SceneStrategy
from app.services.ai_insight.context import AiInsightContext
from app.services.performance import get_board


def _to_pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


class WorkbenchScene(SceneStrategy):
    """工作台场景：从运维管理视角解读全局控制回路健康度与重点关注事项。"""

    @property
    def scene_id(self) -> str:
        return "workbench"

    @property
    def scene_name(self) -> str:
        return "工作台"

    @property
    def required_params(self) -> str:
        return "无必填参数（读取全局看板）"

    async def load_context(
        self,
        db: AsyncSession,
        *,
        loop_id: str | None = None,
        task_id: str | None = None,
    ) -> AiInsightContext:
        # 全局看板（默认全厂、近 24 小时）
        board = await get_board(db, plant_node_id=None, time_window="today")
        return AiInsightContext(
            scene=self.scene_id,
            data={"board": board},
        )

    def build_system_prompt(self, ctx: AiInsightContext) -> str:
        return (
            "你是危化企业控制回路运维管理顾问，擅长从全局视角识别风险与优先事项。\n"
            "请基于提供的全厂控制回路 KPI 看板数据，生成一段运维洞察，包含以下部分：\n"
            "【全局健康概览】概述全厂控制回路整体性能水平（综合评分/关键 KPI 达标情况）\n"
            "【重点关注】指出最需关注的问题领域（如振荡率偏高/自控率偏低/数据质量异常）\n"
            "【趋势预警】基于趋势数据提示可能的恶化方向\n"
            "【建议动作】给出运维层面的优先动作建议（如组织排查/整定/仪表维护）\n\n"
            "要求：\n"
            "1. 从管理者视角出发，聚焦风险与优先级，而非单个回路细节\n"
            "2. 用数据支撑结论（如'自控率 78% 低于 90% 目标'）\n"
            "3. 建议要可落地（明确动作类型与负责角色）\n"
            "4. 总长度控制在 300-500 字"
        ) + self.build_knowledge_section(ctx)

    def build_user_prompt(self, ctx: AiInsightContext) -> str:
        board: dict[str, Any] = ctx.data["board"]
        kpi_summary = board.get("kpiSummary", {}) or {}
        kpi_cards = board.get("kpiCards", []) or []
        partial_warning = board.get("partialWarning", {}) or {}
        steady_trend = board.get("steadyRateTrend", {}) or {}
        filter_scope = board.get("filterScope", {}) or {}

        # 卡片精简
        cards_simplified = [
            {
                "metric": c.get("metricName") or c.get("metricKey"),
                "value": c.get("value"),
                "status": c.get("status"),
            }
            for c in kpi_cards
        ]
        # 趋势取最近 5 个点
        trend_values = steady_trend.get("values", [])[-5:] if steady_trend else []

        data = {
            "scope": filter_scope,
            "KPI汇总": {
                "综合评分": kpi_summary.get("composite_score"),
                "优良值率": kpi_summary.get("good_value_rate"),
                "自控率": kpi_summary.get("auto_mode_rate"),
                "有效自控率": kpi_summary.get("effective_auto_rate"),
                "稳定率": kpi_summary.get("steady_rate"),
                "准确率": kpi_summary.get("accuracy_rate"),
                "振荡率": kpi_summary.get("oscillation_rate"),
                "饱和率": kpi_summary.get("saturation_rate"),
                "仪表故障率": kpi_summary.get("instrument_fault_rate"),
                "状态": kpi_summary.get("status"),
            },
            "KPI卡片": cards_simplified,
            "部分数据警告": partial_warning,
            "稳定率趋势(近5点)": trend_values,
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        return f"请分析以下全厂控制回路看板数据：\n{payload}"

    def generate_template(self, ctx: AiInsightContext) -> str:
        board: dict[str, Any] = ctx.data["board"]
        kpi_summary = board.get("kpiSummary", {}) or {}
        partial_warning = board.get("partialWarning", {}) or {}
        scope = board.get("filterScope", {}) or {}
        scope_name = scope.get("plantNodeName", "全厂")

        score = kpi_summary.get("composite_score")
        score_text = f"{score:.1f}" if score is not None else "—"
        auto_rate = kpi_summary.get("auto_mode_rate")
        steady_rate = kpi_summary.get("steady_rate")
        osc_rate = kpi_summary.get("oscillation_rate")
        sat_rate = kpi_summary.get("saturation_rate")
        fault_rate = kpi_summary.get("instrument_fault_rate")
        good_rate = kpi_summary.get("good_value_rate")
        inconclusive = partial_warning.get("inconclusiveCount", 0)

        lines: list[str] = []

        # 全局健康概览
        lines.append("【全局健康概览】")
        lines.append(
            f"{scope_name}控制回路当前综合评分 {score_text}，"
            f"自控率 {_to_pct(auto_rate)}、稳定率 {_to_pct(steady_rate)}、"
            f"优良值率 {_to_pct(good_rate)}。"
        )
        if score is not None and score >= 85:
            lines.append("整体控制回路运行状态良好，关键指标基本达标。")
        elif score is not None and score >= 70:
            lines.append("整体运行状态合格，部分指标存在提升空间。")
        else:
            lines.append("整体运行状态不理想，多个关键指标未达标，建议重点关注。")

        # 重点关注
        lines.append("")
        lines.append("【重点关注】")
        concerns: list[str] = []
        if osc_rate is not None and osc_rate > 0.10:
            concerns.append(f"  • 振荡率 {_to_pct(osc_rate)} 偏高，存在振荡回路需排查")
        if auto_rate is not None and auto_rate < 0.90:
            concerns.append(f"  • 自控率 {_to_pct(auto_rate)} 低于 90% 目标，部分回路频繁切手动")
        if sat_rate is not None and sat_rate > 0.10:
            concerns.append(f"  • 饱和率 {_to_pct(sat_rate)} 偏高，执行器可能达极限")
        if fault_rate is not None and fault_rate > 0.05:
            concerns.append(f"  • 仪表故障率 {_to_pct(fault_rate)} 偏高，测量回路需检修")
        if inconclusive > 0:
            concerns.append(f"  • 存在 {inconclusive} 个不确定结果，数据完整性需关注")
        if concerns:
            lines.extend(concerns)
        else:
            lines.append("  各项指标均在正常范围，暂无明显风险点。")

        # 趋势预警
        lines.append("")
        lines.append("【趋势预警】")
        steady_trend = board.get("steadyRateTrend", {}) or {}
        values = steady_trend.get("values", []) if steady_trend else []
        if len(values) >= 2:
            recent = [v for v in values[-5:] if v is not None]
            if len(recent) >= 2:
                delta = recent[-1] - recent[0]
                if delta < -0.05:
                    lines.append(
                        f"  ⚠ 稳定率近 5 点下降 {abs(delta) * 100:.1f}%，有恶化趋势，建议及时排查。"
                    )
                elif delta > 0.05:
                    lines.append(f"  稳定率近 5 点上升 {delta * 100:.1f}%，趋势向好。")
                else:
                    lines.append("  稳定率近期平稳，无明显波动。")
            else:
                lines.append("  趋势数据不足，暂无法判断方向。")
        else:
            lines.append("  趋势数据不足，暂无法判断方向。")

        # 建议动作
        lines.append("")
        lines.append("【建议动作】")
        if not concerns:
            lines.append("  维持常规监测，定期复评即可。")
        else:
            if osc_rate is not None and osc_rate > 0.10:
                lines.append("  • 组织仪控工程师进入诊断中心排查振荡回路（优先紧急等级）")
            if fault_rate is not None and fault_rate > 0.05:
                lines.append("  • 联系仪表维护团队检修高故障率测量回路")
            if auto_rate is not None and auto_rate < 0.90:
                lines.append("  • 排查频繁切手动回路的控制逻辑，优化投用条件")
            if inconclusive > 0:
                lines.append("  • 补齐不确定回路的历史数据导入，恢复评估可信度")
            lines.append("  • 将低效回路纳入整定计划，按优先级分批优化")

        return "\n".join(lines)
