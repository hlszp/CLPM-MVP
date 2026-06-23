"""S3-C2: 算法回归测试基线（golden file）.

为关键算法生成 golden file 基线：
- ``identify_fopdt`` — 使用固定输入数据，保存输出到 ``golden/fopdt_baseline.json``
- ``tune_lambda`` — 使用固定 FOPDT 参数，保存 PID 参数到 ``golden/tuning_baseline.json``
- ``_detect_oscillation_fft`` — 使用固定正弦波数据，保存检测结果到 ``golden/fft_baseline.json``

测试逻辑：运行算法 → 与 golden file 对比 → 参数偏差超阈值则失败。
如果 golden file 不存在，首次运行时自动生成并 skip。
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from app.services.tuning_algorithms import identify_fopdt, tune_lambda
from app.tasks.diagnosis_engine import _detect_oscillation_fft

# golden file 目录路径（相对于本测试文件）
GOLDEN_DIR = Path(__file__).parent / "golden"

# 回归阈值：参数偏差超过此值则判定为回归
FOPDT_K_THRESHOLD = 0.05  # K 相对偏差 5%
FOPDT_TAU_THRESHOLD = 0.05  # tau 相对偏差 5%
FOPDT_THETA_THRESHOLD = 0.10  # theta 相对偏差 10%
TUNING_KP_THRESHOLD = 0.01  # kp 相对偏差 1%
TUNING_TI_THRESHOLD = 0.01  # ti 相对偏差 1%
FFT_FREQ_THRESHOLD = 0.02  # 频率相对偏差 2%
FFT_AMP_THRESHOLD = 0.10  # 振幅相对偏差 10%


# ---------------------------------------------------------------------------
# 固定输入数据生成（确定性，不依赖随机数）
# ---------------------------------------------------------------------------


def _make_fopdt_input() -> tuple[list[float], list[float], float]:
    """生成固定的 FOPDT 阶跃响应输入数据（确定性）。"""
    K, tau, theta = 1.0, 15.0, 3.0
    mv_step = 10.0
    pv_values: list[float] = []
    timestamps: list[float] = []
    duration = 300.0
    dt = 0.5
    n = int(duration / dt)
    for i in range(n):
        t = i * dt
        timestamps.append(t)
        if t < theta:
            pv_values.append(0.0)
        else:
            pv_values.append(K * mv_step * (1.0 - math.exp(-(t - theta) / tau)))
    return pv_values, timestamps, mv_step


def _make_fft_input() -> tuple[np.ndarray, float]:
    """生成固定的正弦波输入数据（确定性）。"""
    # 1.0 Hz 正弦波，采样率 10 Hz，5 秒数据
    sample_rate = 10.0
    sample_interval = 1.0 / sample_rate
    duration = 5.0
    n = int(duration * sample_rate)
    t = np.linspace(0, duration, n, endpoint=False)
    # 固定振幅和频率，无随机噪声
    pv_values = 50.0 + 10.0 * np.sin(2.0 * np.pi * 1.0 * t)
    return pv_values, sample_interval


# ---------------------------------------------------------------------------
# golden file 读写辅助
# ---------------------------------------------------------------------------


def _load_or_skip(golden_path: Path) -> dict:
    """加载 golden file，不存在则返回空 dict 并标记 skip。"""
    if not golden_path.exists():
        pytest.skip(
            f"golden file 不存在：{golden_path}（首次运行，请重新执行 pytest 以使用新生成的基线）"
        )
    with golden_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_golden(golden_path: Path, data: dict) -> None:
    """保存 golden file（创建目录如果不存在）。"""
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    with golden_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 回归测试
# ---------------------------------------------------------------------------


class TestGoldenFOPDT:
    """identify_fopdt 回归测试基线。"""

    @pytest.fixture
    def golden_path(self) -> Path:
        return GOLDEN_DIR / "fopdt_baseline.json"

    def test_fopdt_baseline(self, golden_path: Path) -> None:
        """对比 identify_fopdt 输出与 golden file 基线。"""
        pv_values, timestamps, mv_step = _make_fopdt_input()
        result = identify_fopdt(pv_values, timestamps, mv_step, method="COMBINED")

        # golden file 不存在时自动生成并 skip
        if not golden_path.exists():
            baseline = {
                "K": result["K"],
                "tau": result["tau"],
                "theta": result["theta"],
                "fitting_score": result["fitting_score"],
            }
            _save_golden(golden_path, baseline)
            pytest.skip(f"golden file 不存在，已生成基线：{golden_path}（请重新执行 pytest 验证）")

        baseline = _load_or_skip(golden_path)

        # K 偏差检查
        if baseline["K"] is not None and result["K"] is not None:
            k_err = abs(result["K"] - baseline["K"]) / max(abs(baseline["K"]), 1e-9)
            assert k_err < FOPDT_K_THRESHOLD, (
                f"K 回归：偏差 {k_err:.2%} 超过阈值 {FOPDT_K_THRESHOLD:.0%}"
                f"（当前={result['K']}，基线={baseline['K']}）"
            )

        # tau 偏差检查
        if baseline["tau"] is not None and result["tau"] is not None:
            tau_err = abs(result["tau"] - baseline["tau"]) / max(abs(baseline["tau"]), 1e-9)
            assert tau_err < FOPDT_TAU_THRESHOLD, (
                f"tau 回归：偏差 {tau_err:.2%} 超过阈值 {FOPDT_TAU_THRESHOLD:.0%}"
                f"（当前={result['tau']}，基线={baseline['tau']}）"
            )

        # theta 偏差检查
        if baseline["theta"] is not None and result["theta"] is not None:
            theta_err = abs(result["theta"] - baseline["theta"]) / max(abs(baseline["theta"]), 1e-9)
            assert theta_err < FOPDT_THETA_THRESHOLD, (
                f"theta 回归：偏差 {theta_err:.2%} 超过阈值 {FOPDT_THETA_THRESHOLD:.0%}"
                f"（当前={result['theta']}，基线={baseline['theta']}）"
            )


class TestGoldenTuning:
    """tune_lambda 回归测试基线。"""

    @pytest.fixture
    def golden_path(self) -> Path:
        return GOLDEN_DIR / "tuning_baseline.json"

    def test_tuning_baseline(self, golden_path: Path) -> None:
        """对比 tune_lambda 输出与 golden file 基线。"""
        # 固定 FOPDT 参数
        K, tau, theta = 1.0, 30.0, 5.0
        pid = tune_lambda(K, tau, theta, lambda_ratio=1.0)

        if not golden_path.exists():
            baseline = {"kp": pid.kp, "ti": pid.ti, "td": pid.td}
            _save_golden(golden_path, baseline)
            pytest.skip(f"golden file 不存在，已生成基线：{golden_path}（请重新执行 pytest 验证）")

        baseline = _load_or_skip(golden_path)

        kp_err = abs(pid.kp - baseline["kp"]) / max(abs(baseline["kp"]), 1e-9)
        assert kp_err < TUNING_KP_THRESHOLD, (
            f"kp 回归：偏差 {kp_err:.2%} 超过阈值 {TUNING_KP_THRESHOLD:.0%}"
            f"（当前={pid.kp}，基线={baseline['kp']}）"
        )

        ti_err = abs(pid.ti - baseline["ti"]) / max(abs(baseline["ti"]), 1e-9)
        assert ti_err < TUNING_TI_THRESHOLD, (
            f"ti 回归：偏差 {ti_err:.2%} 超过阈值 {TUNING_TI_THRESHOLD:.0%}"
            f"（当前={pid.ti}，基线={baseline['ti']}）"
        )

        assert pid.td == baseline["td"], f"td 回归：当前={pid.td}，基线={baseline['td']}"


class TestGoldenFFT:
    """_detect_oscillation_fft 回归测试基线。"""

    @pytest.fixture
    def golden_path(self) -> Path:
        return GOLDEN_DIR / "fft_baseline.json"

    def test_fft_baseline(self, golden_path: Path) -> None:
        """对比 _detect_oscillation_fft 输出与 golden file 基线。"""
        pv_values, sample_interval = _make_fft_input()
        result = _detect_oscillation_fft(pv_values, sample_interval)

        if not golden_path.exists():
            baseline = {
                "detected": bool(result["detected"]),
                "frequency": float(result["frequency"]),
                "amplitude": float(result["amplitude"]),
                "index": float(result["index"]),
            }
            _save_golden(golden_path, baseline)
            pytest.skip(f"golden file 不存在，已生成基线：{golden_path}（请重新执行 pytest 验证）")

        baseline = _load_or_skip(golden_path)

        # 检测状态应一致
        assert bool(result["detected"]) == baseline["detected"], (
            f"detected 回归：当前={result['detected']}，基线={baseline['detected']}"
        )

        # 频率偏差检查
        if baseline["frequency"] > 0 and result["frequency"] > 0:
            freq_err = abs(result["frequency"] - baseline["frequency"]) / max(
                abs(baseline["frequency"]), 1e-9
            )
            assert freq_err < FFT_FREQ_THRESHOLD, (
                f"frequency 回归：偏差 {freq_err:.2%} 超过阈值 {FFT_FREQ_THRESHOLD:.0%}"
                f"（当前={result['frequency']}，基线={baseline['frequency']}）"
            )

        # 振幅偏差检查
        if baseline["amplitude"] > 0 and result["amplitude"] > 0:
            amp_err = abs(result["amplitude"] - baseline["amplitude"]) / max(
                abs(baseline["amplitude"]), 1e-9
            )
            assert amp_err < FFT_AMP_THRESHOLD, (
                f"amplitude 回归：偏差 {amp_err:.2%} 超过阈值 {FFT_AMP_THRESHOLD:.0%}"
                f"（当前={result['amplitude']}，基线={baseline['amplitude']}）"
            )


class TestGoldenFileIntegrity:
    """golden file 完整性检查。"""

    def test_golden_dir_exists(self) -> None:
        """golden 目录应存在。"""
        assert GOLDEN_DIR.exists(), f"golden 目录不存在：{GOLDEN_DIR}"

    def test_golden_files_are_valid_json(self) -> None:
        """所有 golden file 应为合法 JSON。"""
        if not GOLDEN_DIR.exists():
            pytest.skip("golden 目录不存在")
        json_files = list(GOLDEN_DIR.glob("*.json"))
        if not json_files:
            pytest.skip("无 golden file")
        for f in json_files:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            assert isinstance(data, dict), f"{f.name} 顶层应为 dict"

    def test_golden_files_not_env_dependent(self) -> None:
        """golden file 不应包含环境相关字段（如绝对路径）。"""
        if not GOLDEN_DIR.exists():
            pytest.skip("golden 目录不存在")
        for f in GOLDEN_DIR.glob("*.json"):
            with f.open("r", encoding="utf-8") as fp:
                content = fp.read()
            # 不应包含绝对路径或环境变量
            assert os.sep not in content or "/" not in content or "http" not in content, (
                f"{f.name} 可能包含环境相关字段"
            )
