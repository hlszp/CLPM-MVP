"""AI 洞察通用接口 Schema。

单端点 POST /ai-insight/{scene} 服务 4 场景（诊断/性能评估/回路整定/工作台），
前端只传 scene + 可选 loopId/taskId + mode，后端按 scene 自取上下文组装 prompt。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel


class InsightRequest(CamelModel):
    """POST /ai-insight/{scene} 请求体。"""

    mode: Literal["auto", "template", "llm"] = Field(
        "auto",
        description="生成模式：auto（优先LLM，fallback模板）/ template（仅模板）/ llm（仅LLM）",
    )
    loopId: str | None = Field(
        None,
        description="回路 ID（diagnosis/performance 场景必填）",
    )
    taskId: str | None = Field(
        None,
        description="整定任务 ID（tuning 场景必填）",
    )


class InsightResult(CamelModel):
    """AI 洞察响应。"""

    insight: str = Field(..., description="洞察文本（结构化纯文本）")
    source: Literal["template", "llm"] = Field(..., description="实际来源：template/llm")
    model: str | None = Field(None, description="LLM 模型名（source=llm 时有值）")
    scene: str = Field(..., description="场景标识")
    generatedAt: str = Field(..., description="生成时间 ISO 8601")


class SceneInfo(CamelModel):
    """场景元信息。"""

    sceneId: str
    sceneName: str
    requiredParams: str
