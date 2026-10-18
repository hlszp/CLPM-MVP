"""AI 洞察上下文数据结构。

场景策略（SceneStrategy）在 load_context 阶段组装 AiInsightContext，
随后传给 build_system_prompt / build_user_prompt / generate_template。

设计要点：
- ``data`` 字段承载场景特定数据（由各场景策略自行填充，通用编排不感知内部结构）
- ``knowledge_context`` 是 RAG 扩展点：第一期恒 None，未来从知识库注入参考资料，
  prompt builder 接收并嵌入「参考资料」段，让 AI 基于领域知识 + 实时接口数据生成更精准解读
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AiInsightContext:
    """AI 洞察上下文。

    Attributes:
        scene: 场景标识（diagnosis/performance/tuning/workbench）
        loopId: 回路 ID（diagnosis/performance 场景必填）
        taskId: 整定任务 ID（tuning 场景必填）
        tagName: 回路编号（用于 prompt 中指代回路，提升可读性）
        data: 场景特定数据字典，由 SceneStrategy.load_context 填充
        knowledgeContext: RAG 检索到的参考资料文本；第一期恒 None，
            未来知识库模块接入后由检索器注入。prompt builder 在非 None 时
            嵌入「参考资料」段
    """

    scene: str
    loopId: str | None = None
    taskId: str | None = None
    tagName: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    knowledgeContext: str | None = None
