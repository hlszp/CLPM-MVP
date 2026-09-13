"""G21：SOPDT 模型不得被 FOPDT 整定公式静默整定（S3 算法契约与量纲）。

事实来源：`docs/设计文档/03-ADS/关键算法设计说明.md` §6
- §6.2 定义 SOPDT 模型 G(s) = K·e^(-θs)/(T1·T2·s² + (T1+T2)·s + 1)；
- §6.3~§6.7（IMC/Lambda/Z-N/Cohen-Coon/SIMC）**全部以 FOPDT
  G(s) = K·e^(-θs)/(τs+1) 为前提**（§6.3.1 明文"基于 FOPDT 模型"）；
- 规格**未定义** SOPDT 整定公式，故正确处置是 fail-closed，而非自造折算口径。

缺陷（修复前实测）：SOPDT 的参数契约是 (K, T1, T2, theta)，不含 tau
（见 tuning.py 的 _model_params_match / 步骤辨识校验 required 表）。
tune_pid 原以 `model_params.get("tau") or 0` 取时间常数，对 SOPDT 恒得 0，
再套 FOPDT 公式：SOPDT{K=2,T1=100,T2=30,θ=10} 经 IMC 得
kp=0.1667 / ti=5.0 / td=0.0；同一对象按 τ_eff=T1+T2/2=115 折算应为
kp≈1.7 / ti≈137.5。**ti 缩小约 27 倍 = 积分作用放大 27 倍**，属可直接
引发振荡的整定建议，违反"只输出可靠建议"红线。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import BizError
from app.services.tuning import TuningModelAuthorization, tune_pid

_SOPDT_PARAMS = {"K": 2.0, "T1": 100.0, "T2": 30.0, "theta": 10.0}
_FOPDT_PARAMS = {"K": 2.0, "tau": 100.0, "theta": 10.0}


def _ctx(model_type: str, params: dict[str, float]) -> TuningModelAuthorization:
    """直接构造门禁解析结果（生产链路由 authorize_tuning_model 构造）。"""
    return TuningModelAuthorization(
        model_type=model_type,
        model_params=dict(params),
        loop_id="loop-g21",
        model_source="STEP_EXPERIMENT",
        source_record_id=None,
        risk_confirmed=True,
    )


class TestSopdtTuningMustFailClosed:
    """SOPDT 无对应整定公式 → 必须拒绝，不得静默套用 FOPDT 公式。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("algorithm", ["IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"])
    async def test_sopdt_rejected_for_every_algorithm(self, algorithm: str) -> None:
        """五种整定算法均只定义于 FOPDT，故 SOPDT 一律拒绝。

        修复前：本用例全红——IMC 返回 kp=0.1667/ti=5.0/td=0.0，静默产出
        积分作用被放大 27 倍的整定建议。
        """
        ctx = _ctx("SOPDT", _SOPDT_PARAMS)
        with pytest.raises(BizError) as exc:
            await tune_pid("SOPDT", dict(_SOPDT_PARAMS), algorithm, source_context=ctx)
        assert exc.value.code == "ERR_MODEL_TYPE_NOT_TUNABLE"
        assert "FOPDT" in exc.value.message and "SOPDT" in exc.value.message

    @pytest.mark.asyncio
    async def test_caller_model_type_cannot_override_authorization(self) -> None:
        """裸 model_type 不得覆盖门禁解析结果以绕过适用性校验。

        tune_pid 已声明"防御性地只使用门禁解析后的模型参数"；模型类型必须
        同源，否则调用方传 model_type="FOPDT" 即可让 SOPDT 参数走 FOPDT 公式。
        """
        ctx = _ctx("SOPDT", _SOPDT_PARAMS)
        with pytest.raises(BizError) as exc:
            await tune_pid("FOPDT", dict(_SOPDT_PARAMS), "IMC", source_context=ctx)
        assert exc.value.code == "ERR_MODEL_TYPE_NOT_TUNABLE"

    @pytest.mark.asyncio
    async def test_fopdt_missing_or_nonpositive_tau_rejected(self) -> None:
        """FOPDT 但 tau 缺失/非正 → 拒绝（兜住其余参数异常入口）。"""
        for bad in ({"K": 2.0, "theta": 10.0}, {"K": 2.0, "tau": 0.0, "theta": 10.0}):
            ctx = _ctx("FOPDT", bad)
            with pytest.raises(BizError) as exc:
                await tune_pid("FOPDT", dict(bad), "IMC", source_context=ctx)
            assert exc.value.code == "ERR_MODEL_PARAMS_MISSING"


class TestFopdtTuningUnchanged:
    """回归护栏：合法 FOPDT 整定结果不得因本次修复而改变。"""

    @pytest.mark.asyncio
    async def test_imc_anchor_unchanged(self) -> None:
        """IMC 锚点（手算核实，禁止实现输出反推）。

        τ=100, θ=10, λ=1.0×θ=10：
            Kp = (τ + θ/2) / (K·(λ + θ/2)) = (100+5) / (2×(10+5)) = 3.5
            Ti = τ + θ/2 = 105
            Td = τ·θ / (2·(τ+θ/2)) = 1000 / 210 = 4.7619
        """
        ctx = _ctx("FOPDT", _FOPDT_PARAMS)
        result = await tune_pid("FOPDT", dict(_FOPDT_PARAMS), "IMC", source_context=ctx)
        pid = result["recommendedPid"]
        assert pid["kp"] == pytest.approx(3.5, abs=1e-9)
        assert pid["ti"] == pytest.approx(105.0, abs=1e-9)
        # 出口按 4 位小数舍入，故 Td 取舍入锚点（kp/ti 为精确值）
        assert pid["td"] == pytest.approx(4.7619, abs=1e-9)


class TestSopdtParameterContract:
    """锁住"为什么需要上面的守卫"：SOPDT 参数契约不含 tau。"""

    def test_sopdt_to_dict_has_no_tau_key(self) -> None:
        from app.services.tuning_identification.types import ModelParams, ModelType

        d = ModelParams(model_type=ModelType.SOPDT, K=2.0, T1=100.0, T2=30.0, theta=10.0).to_dict()
        assert set(d) == {"K", "T1", "T2", "theta"}
        assert "tau" not in d, "SOPDT 模型不应带 tau——若新增 tau，须同步评估本节守卫是否仍需要"

    def test_fopdt_to_dict_has_tau(self) -> None:
        from app.services.tuning_identification.types import ModelParams, ModelType

        d = ModelParams(model_type=ModelType.FOPDT, K=2.0, tau=100.0, theta=10.0).to_dict()
        assert set(d) == {"K", "tau", "theta"}
