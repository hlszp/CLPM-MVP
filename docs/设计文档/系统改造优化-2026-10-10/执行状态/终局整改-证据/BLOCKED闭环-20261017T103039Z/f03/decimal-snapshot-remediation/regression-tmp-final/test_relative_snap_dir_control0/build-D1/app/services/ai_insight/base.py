"""场景策略抽象基类。

每个场景（诊断/性能评估/回路整定/工作台）实现一个 SceneStrategy 子类，
各自负责：
- load_context：从对应业务服务加载结构化数据
- build_system_prompt：构造角色化系统提示词（每场景独立优化）
- build_user_prompt：把上下文数据序列化为用户提示词
- generate_template：LLM 不可用时的规则模板 fallback

通用编排 generate_insight 不感知场景内部结构，只调用策略接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_insight.context import AiInsightContext


class SceneStrategy(ABC):
    """AI 洞察场景策略抽象基类。"""

    @property
    @abstractmethod
    def scene_id(self) -> str:
        """场景标识（如 'diagnosis'/'performance'/'tuning'/'workbench'）。"""

    @property
    @abstractmethod
    def scene_name(self) -> str:
        """场景中文名（如 '回路诊断'/'性能评估'）。"""

    @property
    def required_params(self) -> str:
        """该场景必填参数说明（用于错误提示）。默认空串。"""
        return ""

    @abstractmethod
    async def load_context(
        self,
        db: AsyncSession,
        *,
        loop_id: str | None = None,
        task_id: str | None = None,
    ) -> AiInsightContext:
        """加载场景上下文数据。

        Args:
            db: 数据库会话
            loop_id: 回路 ID（diagnosis/performance 场景使用）
            task_id: 整定任务 ID（tuning 场景使用）

        Returns:
            填充好 data 的 AiInsightContext

        Raises:
            BizError: 必填参数缺失或业务数据不存在
        """

    @abstractmethod
    def build_system_prompt(self, ctx: AiInsightContext) -> str:
        """构造系统提示词（角色化，每场景独立优化）。"""

    @abstractmethod
    def build_user_prompt(self, ctx: AiInsightContext) -> str:
        """构造用户提示词（上下文数据序列化）。"""

    @abstractmethod
    def generate_template(self, ctx: AiInsightContext) -> str:
        """规则模板生成（LLM 不可用/失败时的 fallback）。"""

    def build_knowledge_section(self, ctx: AiInsightContext) -> str:
        """构造参考资料段（RAG 扩展点）。

        第一期 ctx.knowledgeContext 恒 None，返回空串。
        未来知识库接入后，把检索到的文档拼装为「参考资料」段嵌入 prompt。
        """
        if not ctx.knowledgeContext:
            return ""
        return f"\n\n【参考资料】\n{ctx.knowledgeContext}"
