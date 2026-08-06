"""AI 洞察通用接口（4 场景统一入口）。

单端点 POST /ai-insight/{scene} 服务 4 场景：
- diagnosis：回路诊断解读（迁移自 POST /diagnosis/{loopId}/interpret）
- performance：性能评估分析（基于 6 大 KPI 指标）
- tuning：回路整定建议（基于辨识结果 + 推荐 PID + 仿真）
- workbench：工作台运维洞察（基于全局看板）

设计要点：
- 前端只传 scene + 可选 loopId/taskId + mode，后端按 scene 自取上下文（防注入、保证一致性）
- LLM 未启用或调用失败时自动 fallback 规则模板，功能不阻断
- 旧端点 POST /diagnosis/{loopId}/interpret 内部代理到本服务，向后兼容

权限：ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT（SPONSOR 只读，禁止生成洞察消耗 LLM 配额）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.ai_insight import InsightRequest, InsightResult, SceneInfo
from app.schemas.common import ApiResponse, success
from app.services.ai_insight import generate_insight
from app.services.ai_insight.scenes import SCENE_LIST

router = APIRouter(prefix="/ai-insight", tags=["AI 洞察"])


@router.get("/scenes", response_model=ApiResponse[list[SceneInfo]])
async def list_scenes_endpoint(
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT", "SPONSOR")),
) -> dict:
    """列出可用的 AI 洞察场景（供前端动态渲染按钮/卡片）。"""
    return success(data=SCENE_LIST)


@router.post("/{scene}", response_model=ApiResponse[InsightResult])
async def generate_insight_endpoint(
    scene: str,
    body: InsightRequest,
    db: AsyncSession = Depends(get_db),
    # WS-D 性能#7 R1：SPONSOR 只读，禁止生成洞察（消耗 LLM 配额）
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> dict:
    """生成 AI 洞察（4 场景统一入口）。

    按 scene 调度对应策略，从业务服务自取上下文组装 prompt，调用 LLM 生成洞察。
    LLM 未启用或失败时自动 fallback 规则模板。

    生成模式（mode）：
    - **auto**（默认）：优先 LLM，不可用或失败时自动 fallback 到规则模板
    - **template**：仅规则模板（离线可用）
    - **llm**：仅 LLM，不可用时抛 503（供前端强制走 LLM）

    场景必填参数：
    - diagnosis / performance：loopId
    - tuning：taskId
    - workbench：无必填参数
    """
    data = await generate_insight(
        db,
        scene,
        loop_id=body.loopId,
        task_id=body.taskId,
        mode=body.mode,
    )
    return success(data=data, message="AI 洞察生成成功")
