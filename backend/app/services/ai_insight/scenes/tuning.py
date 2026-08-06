"""回路整定场景策略。

基于整定任务详情（过程模型辨识结果 + 推荐 PID + 仿真对比 + 风险评估）生成整定建议洞察，
聚焦「模型质量评估 → 推荐参数解读 → 仿真改善分析 → 实施风险与回退」。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.services.ai_insight.base import SceneStrategy
from app.services.ai_insight.context import AiInsightContext
from app.services.tuning import get_tuning_task_detail


class TuningScene(SceneStrategy):
    """回路整定场景：解读辨识模型与推荐 PID，给出实施建议与风险提示。"""

    @property
    def scene_id(self) -> str:
        return "tuning"

    @property
    def scene_name(self) -> str:
        return "回路整定"

    @property
    def required_params(self) -> str:
        return "taskId 必填"

    async def load_context(
        self,
        db: AsyncSession,
        *,
        loop_id: str | None = None,
        task_id: str | None = None,
    ) -> AiInsightContext:
        if not task_id:
            raise BizError(
                code="ERR_MISSING_PARAM",
                message="整定场景需要 taskId",
                status_code=422,
            )
        # get_tuning_task_detail 内部已处理任务不存在错误
        detail = await get_tuning_task_detail(db, task_id)
        return AiInsightContext(
            scene=self.scene_id,
            loopId=str(detail.get("loopId")) if detail.get("loopId") else None,
            taskId=task_id,
            tagName=detail.get("tagName"),
            data={"detail": detail},
        )

    def build_system_prompt(self, ctx: AiInsightContext) -> str:
        return (
            "你是危化企业 PID 控制器整定专家，精通过程对象辨识与 PID 参数优化。\n"
            "请基于提供的整定任务数据，生成一段整定建议洞察，包含以下部分：\n"
            "【模型质量评估】评估过程对象辨识结果的可信度（拟合度/激励/残差），说明模型是否可靠\n"
            "【推荐参数解读】对比当前 PID 与推荐 PID，解释调整方向与预期效果\n"
            "【仿真改善分析】基于仿真对比结果，量化改善幅度（超调/调节时间/IAE）\n"
            "【实施风险与回退】说明实施风险点、建议的观察窗口与回退参数\n\n"
            "要求：\n"
            "1. 参数对比要给出具体数值与变化幅度（如'Kp 0.8→1.2，增大 50%'）\n"
            "2. 若模型可信度低（D/E 级或拟合度差），明确提示不建议直接实施\n"
            "3. 仿真改善要量化（如'超调量 25%→12%，调节时间缩短 40%'）\n"
            "4. 强调安全边界：参数由授权人员人工实施并留痕，平台不下写 DCS\n"
            "5. 总长度控制在 300-500 字"
        ) + self.build_knowledge_section(ctx)

    def build_user_prompt(self, ctx: AiInsightContext) -> str:
        d: dict[str, Any] = ctx.data["detail"]
        # 精简数据，避免传过大 payload
        simplified = {
            "tagName": d.get("tagName"),
            "modelType": d.get("modelType"),
            "algorithm": d.get("algorithm"),
            "modelParams": d.get("modelParams"),
            "currentPid": d.get("currentPid"),
            "recommendedPid": d.get("recommendedPid"),
            "fittingScore": d.get("fittingScore"),
            "confidenceLevel": d.get("confidenceLevel"),
            "confidenceReason": d.get("confidenceReason"),
            "excitationScore": d.get("excitationScore"),
            "residualTestPassed": d.get("residualTestPassed"),
            "status": d.get("status"),
            "riskAssessment": d.get("riskAssessment"),
            "rollbackPid": d.get("rollbackPid"),
            "simulationResult": d.get("simulationResult"),
        }
        payload = json.dumps(simplified, ensure_ascii=False, indent=2, default=str)
        return f"请解读以下整定任务数据：\n{payload}"

    def generate_template(self, ctx: AiInsightContext) -> str:
        d: dict[str, Any] = ctx.data["detail"]
        tag = d.get("tagName") or "该回路"
        model_type = d.get("modelType") or "未知"
        algorithm = d.get("algorithm") or "未知"
        fitting = d.get("fittingScore")
        conf_level = d.get("confidenceLevel") or "—"
        current_pid = d.get("currentPid") or {}
        recommended_pid = d.get("recommendedPid") or {}
        risk = d.get("riskAssessment")
        rollback = d.get("rollbackPid")
        sim = d.get("simulationResult")

        lines: list[str] = []

        # 模型质量评估
        lines.append("【模型质量评估】")
        fitting_text = f"{fitting:.1f}%" if fitting is not None else "—"
        lines.append(
            f"{tag} 回路采用 {algorithm} 算法辨识 {model_type} 模型，"
            f"拟合度 {fitting_text}，可信度 {conf_level} 级。"
        )
        reason = d.get("confidenceReason")
        if reason:
            lines.append(f"可信度说明：{reason}")
        if conf_level in ("D", "E") or (fitting is not None and fitting < 60):
            lines.append("⚠ 模型可信度较低，不建议直接实施推荐参数，建议补充激励数据重新辨识。")
        else:
            lines.append("模型可信度尚可，可参考推荐参数进行仿真验证。")

        # 推荐参数解读
        lines.append("")
        lines.append("【推荐参数解读】")

        # 兼容不同大小写键
        def _get_pid(pid: dict, key: str) -> Any:
            for k in (key, key.lower(), key.upper()):
                if k in pid:
                    return pid[k]
            return None

        for key in ("Kp", "Ti", "Td"):
            cur = _get_pid(current_pid, key)
            rec = _get_pid(recommended_pid, key)
            if cur is not None or rec is not None:
                cur_s = f"{cur:.4g}" if isinstance(cur, (int, float)) else str(cur or "—")
                rec_s = f"{rec:.4g}" if isinstance(rec, (int, float)) else str(rec or "—")
                lines.append(f"  • {key}: {cur_s} → {rec_s}")

        # 仿真改善分析
        lines.append("")
        lines.append("【仿真改善分析】")
        if sim and isinstance(sim, dict):
            for metric_key, label in [
                ("overshoot", "超调量"),
                ("settlingTime", "调节时间"),
                ("iae", "IAE"),
                ("riseTime", "上升时间"),
            ]:
                cur_v = sim.get(f"current{metric_key.capitalize()}") or sim.get(
                    f"current_{metric_key}"
                )
                rec_v = sim.get(f"recommended{metric_key.capitalize()}") or sim.get(
                    f"recommended_{metric_key}"
                )
                if cur_v is not None or rec_v is not None:
                    lines.append(f"  • {label}: {cur_v or '—'} → {rec_v or '—'}")
            if not any(
                sim.get(k)
                for k in [
                    "overshoot",
                    "settlingTime",
                    "iae",
                    "riseTime",
                    "currentOvershoot",
                    "recommendedOvershoot",
                ]
            ):
                lines.append("  仿真数据待补充。")
        else:
            lines.append("  暂无仿真对比数据，建议在整定工作台运行仿真后再评估。")

        # 实施风险与回退
        lines.append("")
        lines.append("【实施风险与回退】")
        if risk:
            if isinstance(risk, dict):
                risk_text = (
                    risk.get("description")
                    or risk.get("message")
                    or json.dumps(risk, ensure_ascii=False)
                )
            else:
                risk_text = str(risk)
            lines.append(f"  风险提示：{risk_text}")
        else:
            lines.append("  风险提示：参数调整可能引起过渡过程波动，建议在低负荷工况下实施。")
        if rollback:
            lines.append(f"  回退参数：{json.dumps(rollback, ensure_ascii=False)}")
        lines.append("  ⚠ 安全边界：参数须由授权人员人工下写 DCS 并留痕，平台不直接修改 DCS 参数。")

        return "\n".join(lines)
