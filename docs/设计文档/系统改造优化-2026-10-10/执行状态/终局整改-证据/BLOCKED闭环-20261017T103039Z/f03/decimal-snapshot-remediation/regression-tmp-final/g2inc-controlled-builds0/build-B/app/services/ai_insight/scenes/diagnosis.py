"""回路诊断场景策略。

复用 diagnosis_interpretation.py 的成熟实现（STRUCTURED_REPORT / 模板引擎 / prompt），
迁移到通用 SceneStrategy 框架下，保持行为一致与向后兼容。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.services.ai_insight.base import SceneStrategy
from app.services.ai_insight.context import AiInsightContext
from app.services.diagnosis import get_diagnosis_detail
from app.services.diagnosis_interpretation import (
    _build_system_prompt as _diag_build_system_prompt,
)
from app.services.diagnosis_interpretation import (
    _build_user_prompt as _diag_build_user_prompt,
)
from app.services.diagnosis_interpretation import (
    _generate_template as _diag_generate_template,
)


class DiagnosisScene(SceneStrategy):
    """回路诊断场景：把结构化诊断结果翻译为工程师可读的解读。"""

    @property
    def scene_id(self) -> str:
        return "diagnosis"

    @property
    def scene_name(self) -> str:
        return "回路诊断"

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
                message="诊断场景需要 loopId",
                status_code=422,
            )
        # get_diagnosis_detail 内部已处理回路不存在/无诊断结果错误
        detail = await get_diagnosis_detail(db=db, loop_id=loop_id)
        return AiInsightContext(
            scene=self.scene_id,
            loopId=loop_id,
            tagName=detail.get("tagName"),
            data={"detail": detail},
        )

    def build_system_prompt(self, ctx: AiInsightContext) -> str:
        return _diag_build_system_prompt() + self.build_knowledge_section(ctx)

    def build_user_prompt(self, ctx: AiInsightContext) -> str:
        return _diag_build_user_prompt(ctx.data["detail"])

    def generate_template(self, ctx: AiInsightContext) -> str:
        return _diag_generate_template(ctx.data["detail"])
