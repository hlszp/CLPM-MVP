"""AI 洞察通用服务测试。

测试覆盖：
- generate_insight：4 场景 × 3 模式编排（template/auto-fallback/auto-llm/llm-unavailable）
- 场景注册表：无效 scene / 缺必填参数
- 各场景规则模板生成：输出包含关键段落
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from app.services.ai_insight import generate_insight
from app.services.ai_insight.context import AiInsightContext
from app.services.ai_insight.scenes import SCENE_REGISTRY
from app.services.ai_insight.scenes.diagnosis import DiagnosisScene
from app.services.ai_insight.scenes.performance import PerformanceScene
from app.services.ai_insight.scenes.workbench import WorkbenchScene

# ===========================================================================
# 辅助：构造各场景 mock 数据
# ===========================================================================


def _diag_detail() -> dict:
    return {
        "loopId": "loop-001",
        "tagName": "FIC-101",
        "compositeScore": 62.5,
        "confidenceLevel": "B",
        "validRate": 0.93,
        "diagnosisLabels": [
            {"label": "OSCILLATION", "labelName": "振荡", "confidence": 0.78},
        ],
        "featureValues": {"similarity_score": 0.82},
    }


def _perf_snapshot() -> tuple[MagicMock, str]:
    """构造 list_loop_snapshots 返回的 (rows, total)。"""
    snap = MagicMock()
    snap.score = 72.0
    snap.good_value_rate = 0.88
    snap.auto_mode_rate = 0.78
    snap.effective_auto_rate = 0.75
    snap.steady_rate = 0.72
    snap.accuracy_rate = 0.85
    snap.fast_rate = 0.68
    snap.oscillation_rate = 0.15
    snap.saturation_rate = 0.05
    snap.instrument_fault_rate = 0.02
    snap.status = "SUCCESS"
    snap.confidence_level = "B"
    from datetime import datetime

    snap.ts_start = datetime(2026, 8, 6, 10, 0, 0)
    return (snap, "FIC-101")


def _tuning_detail() -> dict:
    return {
        "id": "task-001",
        "loopId": "loop-001",
        "tagName": "FIC-101",
        "modelType": "FOPDT",
        "modelParams": {"k": 1.2, "T": 30.0, "L": 5.0},
        "algorithm": "arx",
        "recommendedPid": {"Kp": 1.2, "Ti": 20.0, "Td": 3.0},
        "currentPid": {"Kp": 0.8, "Ti": 25.0, "Td": 2.0},
        "fittingScore": 85.0,
        "status": "COMPLETED",
        "confidenceLevel": "B",
        "confidenceReason": "激励充分，残差通过白噪声检验",
        "riskAssessment": {"level": "low", "description": "参数变化幅度适中"},
        "rollbackPid": {"Kp": 0.8, "Ti": 25.0, "Td": 2.0},
        "simulationResult": None,
    }


def _board_data() -> dict:
    return {
        "filterScope": {"plantNodeName": "全厂", "timeWindow": "today"},
        "kpiCards": [
            {
                "metricKey": "auto_mode_rate",
                "metricName": "自控率",
                "value": 0.78,
                "status": "WARNING",
            },
        ],
        "kpiSummary": {
            "composite_score": 72.0,
            "good_value_rate": 0.88,
            "auto_mode_rate": 0.78,
            "steady_rate": 0.82,
            "oscillation_rate": 0.15,
            "saturation_rate": 0.05,
            "instrument_fault_rate": 0.02,
            "status": "SUCCESS",
        },
        "steadyRateTrend": {"timestamps": [], "values": [0.85, 0.83, 0.80, 0.78, 0.75]},
        "partialWarning": {
            "active": True,
            "inconclusiveCount": 3,
            "partialCount": 0,
            "message": "存在 3 个不确定结果",
        },
    }


# ===========================================================================
# 诊断场景
# ===========================================================================


@pytest.mark.skip(reason="MVP: diagnosis module disabled")
class TestDiagnosisScene:
    """诊断场景：复用 diagnosis_interpretation 模板与 prompt。"""

    @pytest.mark.asyncio
    async def test_template_mode(self) -> None:
        db = AsyncMock()
        with patch(
            "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
            new=AsyncMock(return_value=_diag_detail()),
        ):
            data = await generate_insight(db, "diagnosis", loop_id="loop-001", mode="template")
        assert data["source"] == "template"
        assert data["scene"] == "diagnosis"
        assert data["model"] is None
        assert "FIC-101" in data["insight"]

    @pytest.mark.asyncio
    async def test_auto_fallback_template_when_llm_unavailable(self) -> None:
        db = AsyncMock()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=_diag_detail()),
            ),
            patch("app.services.llm_provider.is_llm_available", new=AsyncMock(return_value=False)),
        ):
            data = await generate_insight(db, "diagnosis", loop_id="loop-001", mode="auto")
        assert data["source"] == "template"

    @pytest.mark.asyncio
    async def test_auto_uses_llm_when_available(self) -> None:
        db = AsyncMock()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=_diag_detail()),
            ),
            patch("app.services.llm_provider.is_llm_available", new=AsyncMock(return_value=True)),
            patch(
                "app.services.llm_provider.call_llm",
                new=AsyncMock(return_value=("AI 洞察内容", "gpt-4o")),
            ),
        ):
            data = await generate_insight(db, "diagnosis", loop_id="loop-001", mode="auto")
        assert data["source"] == "llm"
        assert data["model"] == "gpt-4o"
        assert data["insight"] == "AI 洞察内容"

    @pytest.mark.asyncio
    async def test_llm_mode_raises_when_unavailable(self) -> None:
        db = AsyncMock()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=_diag_detail()),
            ),
            patch("app.services.llm_provider.is_llm_available", new=AsyncMock(return_value=False)),
        ):
            with pytest.raises(BizError) as exc:
                await generate_insight(db, "diagnosis", loop_id="loop-001", mode="llm")
        assert exc.value.code == "ERR_LLM_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_missing_loop_id_raises(self) -> None:
        db = AsyncMock()
        with pytest.raises(BizError) as exc:
            await generate_insight(db, "diagnosis", mode="template")
        assert exc.value.code == "ERR_MISSING_PARAM"


# ===========================================================================
# 性能评估场景
# ===========================================================================


class TestPerformanceScene:
    """性能评估场景：基于 KPI 快照生成短板分析。"""

    @pytest.mark.asyncio
    async def test_template_mode(self) -> None:
        db = AsyncMock()
        snap, tag = _perf_snapshot()
        with patch(
            "app.services.ai_insight.scenes.performance.list_loop_snapshots",
            new=AsyncMock(return_value=([(snap, tag)], 1)),
        ):
            data = await generate_insight(db, "performance", loop_id="loop-001", mode="template")
        assert data["source"] == "template"
        assert data["scene"] == "performance"
        assert "【等级判定】" in data["insight"]
        assert "【短板分析】" in data["insight"]
        assert "【改善建议】" in data["insight"]

    @pytest.mark.asyncio
    async def test_auto_uses_llm(self) -> None:
        db = AsyncMock()
        snap, tag = _perf_snapshot()
        with (
            patch(
                "app.services.ai_insight.scenes.performance.list_loop_snapshots",
                new=AsyncMock(return_value=([(snap, tag)], 1)),
            ),
            patch("app.services.llm_provider.is_llm_available", new=AsyncMock(return_value=True)),
            patch(
                "app.services.llm_provider.call_llm",
                new=AsyncMock(return_value=("性能 AI 洞察", "deepseek-chat")),
            ),
        ):
            data = await generate_insight(db, "performance", loop_id="loop-001", mode="auto")
        assert data["source"] == "llm"
        assert data["model"] == "deepseek-chat"

    @pytest.mark.asyncio
    async def test_no_kpi_data_raises(self) -> None:
        db = AsyncMock()
        with patch(
            "app.services.ai_insight.scenes.performance.list_loop_snapshots",
            new=AsyncMock(return_value=([], 0)),
        ):
            with pytest.raises(BizError) as exc:
                await generate_insight(db, "performance", loop_id="loop-001", mode="template")
        assert exc.value.code == "ERR_NO_KPI_DATA"


# ===========================================================================
# 回路整定场景
# ===========================================================================


@pytest.mark.skip(reason="MVP: tuning module disabled")
class TestTuningScene:
    """整定场景：基于辨识结果与推荐 PID 生成建议。"""

    @pytest.mark.asyncio
    async def test_template_mode(self) -> None:
        db = AsyncMock()
        with patch(
            "app.services.ai_insight.scenes.tuning.get_tuning_task_detail",
            new=AsyncMock(return_value=_tuning_detail()),
        ):
            data = await generate_insight(db, "tuning", task_id="task-001", mode="template")
        assert data["source"] == "template"
        assert data["scene"] == "tuning"
        assert "【模型质量评估】" in data["insight"]
        assert "【推荐参数解读】" in data["insight"]
        assert "【实施风险与回退】" in data["insight"]

    @pytest.mark.asyncio
    async def test_auto_fallback_template(self) -> None:
        db = AsyncMock()
        with (
            patch(
                "app.services.ai_insight.scenes.tuning.get_tuning_task_detail",
                new=AsyncMock(return_value=_tuning_detail()),
            ),
            patch("app.services.llm_provider.is_llm_available", new=AsyncMock(return_value=False)),
        ):
            data = await generate_insight(db, "tuning", task_id="task-001", mode="auto")
        assert data["source"] == "template"

    @pytest.mark.asyncio
    async def test_missing_task_id_raises(self) -> None:
        db = AsyncMock()
        with pytest.raises(BizError) as exc:
            await generate_insight(db, "tuning", mode="template")
        assert exc.value.code == "ERR_MISSING_PARAM"


# ===========================================================================
# 工作台场景
# ===========================================================================


class TestWorkbenchScene:
    """工作台场景：基于全局看板生成运维洞察。"""

    @pytest.mark.asyncio
    async def test_template_mode(self) -> None:
        db = AsyncMock()
        with patch(
            "app.services.ai_insight.scenes.workbench.get_board",
            new=AsyncMock(return_value=_board_data()),
        ):
            data = await generate_insight(db, "workbench", mode="template")
        assert data["source"] == "template"
        assert data["scene"] == "workbench"
        assert "【全局健康概览】" in data["insight"]
        assert "【重点关注】" in data["insight"]
        assert "【建议动作】" in data["insight"]

    @pytest.mark.asyncio
    async def test_auto_uses_llm(self) -> None:
        db = AsyncMock()
        with (
            patch(
                "app.services.ai_insight.scenes.workbench.get_board",
                new=AsyncMock(return_value=_board_data()),
            ),
            patch("app.services.llm_provider.is_llm_available", new=AsyncMock(return_value=True)),
            patch(
                "app.services.llm_provider.call_llm",
                new=AsyncMock(return_value=("工作台 AI 洞察", "gpt-4o")),
            ),
        ):
            data = await generate_insight(db, "workbench", mode="auto")
        assert data["source"] == "llm"

    @pytest.mark.asyncio
    async def test_no_loop_id_required(self) -> None:
        """工作台场景不需要 loopId（读取全局看板）。"""
        db = AsyncMock()
        with patch(
            "app.services.ai_insight.scenes.workbench.get_board",
            new=AsyncMock(return_value=_board_data()),
        ):
            data = await generate_insight(db, "workbench", mode="template")
        assert data["scene"] == "workbench"


# ===========================================================================
# 场景注册表与通用编排
# ===========================================================================


class TestSceneRegistry:
    """场景注册表与通用编排边界。"""

    def test_registry_has_two_scenes(self) -> None:
        """MVP 精简：仅保留 performance/workbench 场景。"""
        assert set(SCENE_REGISTRY.keys()) == {"performance", "workbench"}

    def test_each_scene_has_strategy(self) -> None:
        for scene_id, strategy in SCENE_REGISTRY.items():
            assert strategy.scene_id == scene_id
            assert isinstance(strategy.scene_name, str)
            assert strategy.scene_name

    @pytest.mark.asyncio
    async def test_invalid_scene_raises(self) -> None:
        db = AsyncMock()
        with pytest.raises(BizError) as exc:
            await generate_insight(db, "nonexistent", mode="template")
        assert exc.value.code == "ERR_INVALID_SCENE"
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_mode_raises(self) -> None:
        """MVP 精简：使用 performance 场景测试无效 mode。"""
        db = AsyncMock()
        snap, tag = _perf_snapshot()
        with patch(
            "app.services.ai_insight.scenes.performance.list_loop_snapshots",
            new=AsyncMock(return_value=([(snap, tag)], 1)),
        ):
            with pytest.raises(BizError) as exc:
                await generate_insight(db, "performance", loop_id="loop-001", mode="invalid")
        assert exc.value.code == "ERR_INVALID_MODE"
        assert exc.value.status_code == 422


# ===========================================================================
# RAG 扩展点
# ===========================================================================


class TestKnowledgeContextExtension:
    """knowledge_context RAG 扩展点（第一期恒 None，prompt builder 接收）。"""

    def test_knowledge_section_empty_when_none(self) -> None:
        scene = DiagnosisScene()
        ctx = AiInsightContext(scene="diagnosis")
        assert scene.build_knowledge_section(ctx) == ""

    def test_knowledge_section_rendered_when_present(self) -> None:
        scene = WorkbenchScene()
        ctx = AiInsightContext(scene="workbench", knowledgeContext="国标 GB/T 44693.2 章节...")
        section = scene.build_knowledge_section(ctx)
        assert "【参考资料】" in section
        assert "国标 GB/T 44693.2 章节" in section

    def test_template_prompt_includes_knowledge_when_present(self) -> None:
        """LLM 系统提示词在有 knowledge_context 时嵌入参考资料段。"""
        scene = PerformanceScene()
        ctx = AiInsightContext(
            scene="performance",
            knowledgeContext="稳定率达标线 90% 来自 GB/T 44693.2",
        )
        prompt = scene.build_system_prompt(ctx)
        assert "【参考资料】" in prompt
