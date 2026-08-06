"""AI 洞察通用编排服务。

generate_insight 是 4 场景统一入口，编排流程：
1. 从 SCENE_REGISTRY 取场景策略
2. load_context 加载场景数据
3. 按 mode 调度：template=直接模板；llm/auto=LLM 优先，失败 fallback 模板

各场景的 prompt 与模板逻辑由 SceneStrategy 子类承担，本模块只负责编排与容错。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.services.ai_insight.scenes import SCENE_REGISTRY

logger = logging.getLogger(__name__)


async def generate_insight(
    db: AsyncSession,
    scene: str,
    *,
    loop_id: str | None = None,
    task_id: str | None = None,
    mode: str = "auto",
) -> dict:
    """生成 AI 洞察（通用编排）。

    Args:
        db: 数据库会话
        scene: 场景标识（diagnosis/performance/tuning/workbench）
        loop_id: 回路 ID（diagnosis/performance 场景必填）
        task_id: 整定任务 ID（tuning 场景必填）
        mode: 生成模式
            - "template": 仅规则模板
            - "llm": 仅 LLM（不可用则抛 503）
            - "auto": 优先 LLM，fallback 到模板（默认）

    Returns:
        dict: {
            "insight": str,       # 洞察文本
            "source": "template" | "llm",
            "model": str | None,  # LLM 模型名（source=llm 时有值）
            "scene": str,         # 场景标识
            "generatedAt": str,   # ISO 8601
        }

    Raises:
        BizError: ERR_INVALID_SCENE / ERR_INVALID_MODE / ERR_LLM_UNAVAILABLE
    """
    strategy = SCENE_REGISTRY.get(scene)
    if strategy is None:
        raise BizError(
            code="ERR_INVALID_SCENE",
            message=f"不支持的 AI 洞察场景：{scene}，可选：{', '.join(SCENE_REGISTRY.keys())}",
            status_code=404,
        )

    ctx = await strategy.load_context(db, loop_id=loop_id, task_id=task_id)

    if mode == "template":
        return _build_result(strategy.generate_template(ctx), "template", None, scene)

    if mode in ("llm", "auto"):
        try:
            from app.services.llm_provider import call_llm, is_llm_available

            if not await is_llm_available(db):
                if mode == "llm":
                    raise BizError(
                        code="ERR_LLM_UNAVAILABLE",
                        message="LLM 未启用或配置缺失，请在系统管理-LLM 配置中开启",
                        status_code=503,
                    )
                logger.info("LLM 不可用，fallback 规则模板（scene=%s）", scene)
                return _build_result(strategy.generate_template(ctx), "template", None, scene)

            system_prompt = strategy.build_system_prompt(ctx)
            user_prompt = strategy.build_user_prompt(ctx)
            text, model_name = await call_llm(db, system_prompt, user_prompt)
            return _build_result(text, "llm", model_name, scene)
        except BizError:
            if mode == "llm":
                raise
            logger.warning("LLM 调用失败，fallback 规则模板（scene=%s）", scene)
            return _build_result(strategy.generate_template(ctx), "template", None, scene)
        except Exception:
            logger.exception("LLM 调用异常，fallback 规则模板（scene=%s）", scene)
            if mode == "llm":
                raise BizError(
                    code="ERR_LLM_UNAVAILABLE",
                    message="LLM 调用失败",
                    status_code=503,
                ) from None
            return _build_result(strategy.generate_template(ctx), "template", None, scene)

    raise BizError(
        code="ERR_INVALID_MODE",
        message=f"mode 必须为 auto/template/llm 之一，收到 {mode}",
        status_code=422,
    )


def _build_result(insight: str, source: str, model: str | None, scene: str) -> dict:
    """构建统一结果字典。"""
    return {
        "insight": insight,
        "source": source,
        "model": model,
        "scene": scene,
        "generatedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }


__all__ = ["generate_insight", "SCENE_REGISTRY"]
