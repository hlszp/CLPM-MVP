"""AI 洞察场景策略注册表。

新增场景只需在此注册，通用编排 generate_insight 即可调度。
"""

from __future__ import annotations

from app.services.ai_insight.base import SceneStrategy

# MVP 精简：已屏蔽诊断/整定场景
# from app.services.ai_insight.scenes.diagnosis import DiagnosisScene
from app.services.ai_insight.scenes.performance import PerformanceScene

# from app.services.ai_insight.scenes.tuning import TuningScene
from app.services.ai_insight.scenes.workbench import WorkbenchScene

# 场景注册表：scene_id → 策略实例（MVP 仅保留 performance/workbench）
SCENE_REGISTRY: dict[str, SceneStrategy] = {
    # "diagnosis": DiagnosisScene(),
    "performance": PerformanceScene(),
    # "tuning": TuningScene(),
    "workbench": WorkbenchScene(),
}

# 场景列表（供 API 元数据端点返回）
SCENE_LIST: list[dict[str, str]] = [
    {"sceneId": s.scene_id, "sceneName": s.scene_name, "requiredParams": s.required_params}
    for s in SCENE_REGISTRY.values()
]

__all__ = ["SCENE_REGISTRY", "SCENE_LIST"]
