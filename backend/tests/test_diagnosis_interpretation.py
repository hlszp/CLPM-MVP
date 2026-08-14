"""P3-04 自然语言诊断解读测试.

测试覆盖：
- generate_interpretation：mode 编排（template/llm/auto + fallback）
- _generate_template：规则模板引擎（概述/主因分析/风险提示结构 + 置信度排序 + 特征值引用）
- llm_provider：is_llm_available 配置完整性检查 / call_llm 成功与异常（超时/HTTP/空响应）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.exceptions import BizError
from app.services import diagnosis_interpretation as interp
from app.services import llm_provider

pytestmark = pytest.mark.skip(reason="MVP: diagnosis/tuning/AAS/tracker module disabled")

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_detail(
    tag_name: str = "FIC-101",
    composite_score: float = 62.5,
    confidence_level: str = "B",
    valid_rate: float = 0.93,
    labels: list[dict] | None = None,
    feature_values: dict | None = None,
) -> dict:
    """构造 get_diagnosis_detail 返回的字典。"""
    if labels is None:
        labels = [
            {
                "label": "OSCILLATION",
                "labelName": "振荡",
                "confidence": 0.78,
            },
            {
                "label": "OVERAGGRESSIVE",
                "labelName": "参数过激",
                "confidence": 0.55,
            },
        ]
    return {
        "loopId": "loop-001",
        "tagName": tag_name,
        "compositeScore": composite_score,
        "confidenceLevel": confidence_level,
        "validRate": valid_rate,
        "diagnosisLabels": labels,
        "featureValues": feature_values or {},
    }


def _make_sys_config(key: str, value: str) -> MagicMock:
    """构造 SysConfig mock。"""
    cfg = MagicMock()
    cfg.key = key
    cfg.value = value
    return cfg


# ===========================================================================
# generate_interpretation：mode 编排
# ===========================================================================


class TestGenerateInterpretation:
    """测试 generate_interpretation 服务编排。"""

    @pytest.mark.asyncio
    async def test_template_mode_returns_template_source(self) -> None:
        """mode=template 直接返回规则模板，source=template。"""
        db = AsyncMock()
        detail = _make_detail()
        with patch(
            "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
            new=AsyncMock(return_value=detail),
        ):
            data = await interp.generate_interpretation(db, loop_id="loop-001", mode="template")

        assert data["source"] == "template"
        assert data["model"] is None
        assert "interpretation" in data
        assert "generatedAt" in data
        assert "FIC-101" in data["interpretation"]

    @pytest.mark.asyncio
    async def test_auto_mode_falls_back_to_template_when_llm_unavailable(self) -> None:
        """mode=auto 且 LLM 不可用时，fallback 到规则模板。"""
        db = AsyncMock()
        detail = _make_detail()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=detail),
            ),
            patch(
                "app.services.llm_provider.is_llm_available",
                new=AsyncMock(return_value=False),
            ),
        ):
            data = await interp.generate_interpretation(db, loop_id="loop-001", mode="auto")

        assert data["source"] == "template"
        assert data["model"] is None

    @pytest.mark.asyncio
    async def test_auto_mode_uses_llm_when_available(self) -> None:
        """mode=auto 且 LLM 可用时，返回 LLM 解读，source=llm。"""
        db = AsyncMock()
        detail = _make_detail()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=detail),
            ),
            patch(
                "app.services.llm_provider.is_llm_available",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.llm_provider.call_llm",
                new=AsyncMock(return_value=("这是 LLM 生成的解读内容。", "gpt-4o")),
            ),
        ):
            data = await interp.generate_interpretation(db, loop_id="loop-001", mode="auto")

        assert data["source"] == "llm"
        assert data["model"] == "gpt-4o"
        assert data["interpretation"] == "这是 LLM 生成的解读内容。"

    @pytest.mark.asyncio
    async def test_llm_mode_raises_when_unavailable(self) -> None:
        """mode=llm 且 LLM 不可用时，抛 ERR_LLM_UNAVAILABLE（503）。"""
        db = AsyncMock()
        detail = _make_detail()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=detail),
            ),
            patch(
                "app.services.llm_provider.is_llm_available",
                new=AsyncMock(return_value=False),
            ),
        ):
            with pytest.raises(BizError) as exc_info:
                await interp.generate_interpretation(db, loop_id="loop-001", mode="llm")

        assert exc_info.value.code == "ERR_LLM_UNAVAILABLE"
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_llm_mode_raises_when_call_fails(self) -> None:
        """mode=llm 且 call_llm 抛异常时，抛 ERR_LLM_UNAVAILABLE。"""
        db = AsyncMock()
        detail = _make_detail()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=detail),
            ),
            patch(
                "app.services.llm_provider.is_llm_available",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.llm_provider.call_llm",
                new=AsyncMock(side_effect=Exception("network error")),
            ),
        ):
            with pytest.raises(BizError) as exc_info:
                await interp.generate_interpretation(db, loop_id="loop-001", mode="llm")

        assert exc_info.value.code == "ERR_LLM_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_auto_mode_falls_back_when_call_fails(self) -> None:
        """mode=auto 且 call_llm 抛异常时，fallback 到规则模板。"""
        db = AsyncMock()
        detail = _make_detail()
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=detail),
            ),
            patch(
                "app.services.llm_provider.is_llm_available",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.llm_provider.call_llm",
                new=AsyncMock(side_effect=Exception("timeout")),
            ),
        ):
            data = await interp.generate_interpretation(db, loop_id="loop-001", mode="auto")

        # fallback 到模板
        assert data["source"] == "template"
        assert data["model"] is None

    @pytest.mark.asyncio
    async def test_auto_mode_falls_back_on_bizerror_from_call(self) -> None:
        """mode=auto 且 call_llm 抛 BizError（如超时）时，fallback 到模板而非抛错。"""
        db = AsyncMock()
        detail = _make_detail()
        biz_err = BizError(
            code="ERR_LLM_UNAVAILABLE",
            message="LLM 调用超时",
            status_code=504,
        )
        with (
            patch(
                "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value=detail),
            ),
            patch(
                "app.services.llm_provider.is_llm_available",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.llm_provider.call_llm",
                new=AsyncMock(side_effect=biz_err),
            ),
        ):
            data = await interp.generate_interpretation(db, loop_id="loop-001", mode="auto")

        assert data["source"] == "template"

    @pytest.mark.asyncio
    async def test_invalid_mode_raises(self) -> None:
        """无效 mode 抛 ERR_INVALID_MODE（422）。"""
        db = AsyncMock()
        detail = _make_detail()
        with patch(
            "app.services.ai_insight.scenes.diagnosis.get_diagnosis_detail",
            new=AsyncMock(return_value=detail),
        ):
            with pytest.raises(BizError) as exc_info:
                await interp.generate_interpretation(db, loop_id="loop-001", mode="invalid")

        assert exc_info.value.code == "ERR_INVALID_MODE"
        assert exc_info.value.status_code == 422


# ===========================================================================
# _generate_template：规则模板引擎
# ===========================================================================


class TestGenerateTemplate:
    """测试规则模板生成核心。"""

    def test_empty_labels_emits_no_anomaly_message(self) -> None:
        """无诊断标签时输出"暂无诊断标签"提示。"""
        detail = _make_detail(labels=[])
        text = interp._generate_template(detail)

        assert "暂无诊断标签" in text
        assert "FIC-101" in text
        assert "62.5" in text  # 综合评分

    def test_output_contains_three_sections(self) -> None:
        """有标签时输出包含【概述】【主因分析】【风险提示】三段。"""
        detail = _make_detail()
        text = interp._generate_template(detail)

        assert "【概述】" in text
        assert "【主因分析】" in text
        assert "【风险提示】" in text

    def test_labels_sorted_by_confidence_desc(self) -> None:
        """标签按置信度降序排列（0.78 振荡在前，0.55 参数过激在后）。"""
        detail = _make_detail()
        text = interp._generate_template(detail)

        osc_pos = text.find("振荡")
        agg_pos = text.find("参数过激")
        assert osc_pos != -1 and agg_pos != -1
        assert osc_pos < agg_pos

    def test_feature_values_referenced_in_text(self) -> None:
        """关键特征值数值出现在解读文本中。"""
        detail = _make_detail(
            feature_values={
                "similarity_score": 0.8234,
                "zero_crossings": 12,
                "dominant_freq": 0.15,
            }
        )
        text = interp._generate_template(detail)

        assert "similarity_score=0.8234" in text
        assert "zero_crossings=12" in text

    def test_high_urgency_emits_warning(self) -> None:
        """包含高紧急度标签（OSCILLATION）时输出警告提示。"""
        detail = _make_detail()
        text = interp._generate_template(detail)

        assert "紧急程度：紧急" in text
        assert "建议动作：PID 整定" in text
        assert "⚠" in text

    def test_low_urgency_only_labels(self) -> None:
        """仅含低紧急度标签（MANUAL_REVIEW）时不输出高优先级警告。"""
        detail = _make_detail(
            labels=[
                {
                    "label": "MANUAL_REVIEW",
                    "labelName": "人工复核",
                    "confidence": 0.4,
                }
            ]
        )
        text = interp._generate_template(detail)

        assert "紧急程度：低" in text
        assert "⚠" not in text

    def test_missing_score_shows_dash(self) -> None:
        """综合评分为 None 时显示"—"。"""
        detail = _make_detail(composite_score=None, valid_rate=None)
        text = interp._generate_template(detail)

        assert "—" in text

    def test_build_template_result_structure(self) -> None:
        """_build_template_result 返回结构完整（interpretation/source/model/generatedAt）。"""
        detail = _make_detail()
        result = interp._build_template_result(detail)

        assert result["source"] == "template"
        assert result["model"] is None
        assert isinstance(result["interpretation"], str)
        assert isinstance(result["generatedAt"], str)


# ===========================================================================
# llm_provider：配置检查与 API 调用
# ===========================================================================


def _config_result(value: str | None) -> MagicMock:
    """构造 db.execute 返回值：value=None 表示配置缺失。"""
    r = MagicMock()
    if value is None:
        r.scalar_one_or_none.return_value = None
    else:
        r.scalar_one_or_none.return_value = _make_sys_config("any", value)
    return r


class TestIsLlmAvailable:
    """测试 is_llm_available 配置完整性检查。

    is_llm_available 按顺序查询 4 个 key：enabled/endpoint/api_key/model，
    用有序列表 side_effect 依次返回。
    """

    @pytest.mark.asyncio
    async def test_disabled_returns_false(self) -> None:
        """enabled=false 时返回 False（仅查询 enabled 即返回）。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_config_result("false"))

        assert await llm_provider.is_llm_available(db) is False

    @pytest.mark.asyncio
    async def test_enabled_but_missing_api_key_returns_false(self) -> None:
        """enabled=true 且 endpoint 存在，但 api_key 缺失时返回 False。"""
        db = AsyncMock()
        # is_llm_available（enabled=true 时）依次查询 4 个 key，无短路
        db.execute = AsyncMock(
            side_effect=[
                _config_result("true"),
                _config_result("https://api.openai.com"),
                _config_result(None),  # api_key 缺失
                _config_result("gpt-4o"),
            ]
        )

        assert await llm_provider.is_llm_available(db) is False

    @pytest.mark.asyncio
    async def test_all_config_present_returns_true(self) -> None:
        """enabled=true 且 endpoint/api_key/model 均存在时返回 True。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _config_result("true"),
                _config_result("https://api.openai.com"),
                _config_result("sk-xxx"),
                _config_result("gpt-4o"),
            ]
        )

        assert await llm_provider.is_llm_available(db) is True


class TestCallLlm:
    """测试 call_llm API 调用（成功与异常）。"""

    @pytest.mark.asyncio
    async def test_success_returns_text_and_model(self) -> None:
        """调用成功返回 (text, model)。"""
        db = AsyncMock()
        config = {
            "endpoint": "https://api.openai.com",
            "apiKey": "sk-xxx",
            "model": "gpt-4o",
            "timeout": 30.0,
            "maxTokens": 4096,
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": "LLM 解读内容"}}]}
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(llm_provider, "_load_llm_config", new=AsyncMock(return_value=config)),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            text, model = await llm_provider.call_llm(db, "system prompt", "user prompt")

        assert text == "LLM 解读内容"
        assert model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_timeout_raises_bizerror(self) -> None:
        """超时抛 BizError（504）。"""
        db = AsyncMock()
        config = {
            "endpoint": "https://api.openai.com",
            "apiKey": "sk-xxx",
            "model": "gpt-4o",
            "timeout": 5.0,
            "maxTokens": 4096,
        }

        with (
            patch.object(llm_provider, "_load_llm_config", new=AsyncMock(return_value=config)),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(BizError) as exc_info:
                await llm_provider.call_llm(db, "s", "u")

        assert exc_info.value.code == "ERR_LLM_UNAVAILABLE"
        assert exc_info.value.status_code == 504

    @pytest.mark.asyncio
    async def test_http_error_raises_bizerror(self) -> None:
        """HTTP 状态错误抛 BizError（502）。"""
        db = AsyncMock()
        config = {
            "endpoint": "https://api.openai.com",
            "apiKey": "sk-xxx",
            "model": "gpt-4o",
            "timeout": 30.0,
            "maxTokens": 4096,
        }
        err_response = MagicMock()
        err_response.status_code = 401
        err_response.text = "unauthorized"

        with (
            patch.object(llm_provider, "_load_llm_config", new=AsyncMock(return_value=config)),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=err_response)
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(BizError) as exc_info:
                await llm_provider.call_llm(db, "s", "u")

        assert exc_info.value.code == "ERR_LLM_UNAVAILABLE"
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_empty_choices_raises_bizerror(self) -> None:
        """LLM 返回空 choices 抛 BizError（502）。"""
        db = AsyncMock()
        config = {
            "endpoint": "https://api.openai.com",
            "apiKey": "sk-xxx",
            "model": "gpt-4o",
            "timeout": 30.0,
            "maxTokens": 4096,
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(llm_provider, "_load_llm_config", new=AsyncMock(return_value=config)),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(BizError) as exc_info:
                await llm_provider.call_llm(db, "s", "u")

        assert exc_info.value.code == "ERR_LLM_UNAVAILABLE"
        assert exc_info.value.status_code == 502


class TestLoadLlmConfig:
    """测试 _load_llm_config 配置加载与校验。

    _load_llm_config 按顺序查询 6 个 key：enabled/endpoint/api_key/model/timeout/max_tokens，
    用有序列表 side_effect 依次返回。
    """

    @pytest.mark.asyncio
    async def test_disabled_raises(self) -> None:
        """LLM 未启用时抛 BizError（仅查询 enabled 即返回）。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_config_result("false"))

        with pytest.raises(BizError) as exc_info:
            await llm_provider._load_llm_config(db)
        assert exc_info.value.code == "ERR_LLM_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_missing_endpoint_raises(self) -> None:
        """enabled=true 但 endpoint 缺失时抛 BizError。"""
        db = AsyncMock()
        # _load_llm_config 依次查询 6 个 key 后才校验，无短路
        db.execute = AsyncMock(
            side_effect=[
                _config_result("true"),
                _config_result(None),  # endpoint 缺失
                _config_result("sk-xxx"),
                _config_result("gpt-4o"),
                _config_result("30"),
                _config_result("4096"),
            ]
        )

        with pytest.raises(BizError) as exc_info:
            await llm_provider._load_llm_config(db)
        assert exc_info.value.code == "ERR_LLM_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_complete_config_returns_dict(self) -> None:
        """配置完整时返回 dict，timeout 从配置读取。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _config_result("true"),
                _config_result("https://api.openai.com"),
                _config_result("sk-xxx"),
                _config_result("gpt-4o"),
                _config_result("45"),
                _config_result("4096"),
            ]
        )

        config = await llm_provider._load_llm_config(db)
        assert config["endpoint"] == "https://api.openai.com"
        assert config["model"] == "gpt-4o"
        assert config["timeout"] == 45.0
        assert config["maxTokens"] == 4096

    @pytest.mark.asyncio
    async def test_timeout_defaults_to_30_when_missing(self) -> None:
        """timeout 缺失时默认 30 秒。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _config_result("true"),
                _config_result("https://api.openai.com"),
                _config_result("sk-xxx"),
                _config_result("gpt-4o"),
                _config_result(None),  # timeout 缺失
                _config_result("4096"),
            ]
        )

        config = await llm_provider._load_llm_config(db)
        assert config["timeout"] == 30.0
        assert config["maxTokens"] == 4096
