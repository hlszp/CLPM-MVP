"""AI 洞察通用服务包。

提供 4 场景（诊断/性能评估/回路整定/工作台）统一的 AI 洞察生成能力：
- 规则模板生成（默认/离线可用）
- LLM API 接入（增强/可选，失败自动 fallback 模板）
- RAG 扩展点（knowledge_context，第一期 None，未来知识库注入）

入口：generate_insight(db, scene, *, loop_id, task_id, mode)
"""

from app.services.ai_insight.context import AiInsightContext
from app.services.ai_insight.scenes import SCENE_LIST, SCENE_REGISTRY
from app.services.ai_insight.service import generate_insight

__all__ = [
    "AiInsightContext",
    "generate_insight",
    "SCENE_LIST",
    "SCENE_REGISTRY",
]
