"""G27#3 复核：IV 求解的病态降级可观测性（S3 算法契约与量纲）。

原判"IV 病态降级"——**成立**，本轮把退化路径变成**可观测信息**（不设阈值）。

事实依据
--------
`identify_iv` / `identify_clivc` 对 `Z^T·Phi` 只做：

    try:
        theta = np.linalg.solve(ZtPhi, Zty)
    except np.linalg.LinAlgError:
        theta, _, _, _ = np.linalg.lstsq(ZtPhi, Zty, rcond=None)

两个问题：
1. `np.linalg.solve` **仅在矩阵精确奇异时**抛 `LinAlgError`；
   **病态但非奇异**（真实场景：激励不足、工具变量与回归元近共线）时它会"成功"
   返回**数值上无意义**的 θ，**连告警都没有**——这是"病态降级"的核心。
2. 即便真的走了 `lstsq` 回退，调用方也**无从得知**：`IVResult` 无相应字段，
   管道无法据此降权或标注，证据链也看不到。

修复（可观测化，不自造阈值）
----------------------------
`IVResult` 增加两个字段：
- `condition_number`：`cond(Z^T·Phi)`，无论走哪条路径都记录；
- `solve_mode`：`"solve"` 或 `"lstsq_fallback"`（后者表示退化解）。

按 G27#4 的教训（阈值标定属决策），**本轮不设阈值、不改判定行为**，
只让病态程度与求解路径可见。
"""

from __future__ import annotations

import math

import numpy as np

from app.services.tuning_identification.iv import identify_iv


def _series(n: int = 600, seed: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    sp = np.sin(t * 2 * np.pi / 40.0) + 0.5 * np.sin(t * 2 * np.pi / 13.0)
    u = np.clip(50.0 + 20.0 * sp, 0.0, 100.0)
    y = np.convolve(u, np.exp(-np.arange(30) / 8.0), mode="same")
    y = y + rng.normal(0.0, 0.05, n)
    return u, y, sp


class TestConditioningIsObservable:
    """条件数与求解路径必须可被调用方读到。"""

    def test_well_conditioned_solve_is_recorded(self) -> None:
        u, y, sp = _series()
        r = identify_iv(u, y, sp, 3, na=1, nb=1)
        assert r.solve_mode == "solve"
        assert math.isfinite(r.condition_number)
        assert r.condition_number > 0.0

    def test_ill_conditioned_solve_still_reports_condition_number(self) -> None:
        """工具变量取 u 自身（Z 与 Phi 的 B 列同源）：solve 不抛异常，
        但条件数必须被记录——这正是原实现的盲区（无异常、无告警、无字段）。
        """
        u, y, _ = _series()
        r = identify_iv(u, y, u * 1.0, 3, na=1, nb=1)
        assert r.solve_mode == "solve"  # 未抛 LinAlgError
        assert math.isfinite(r.condition_number)
        assert r.condition_number > 0.0

    def test_condition_number_reflects_instrument_choice(self) -> None:
        """字段确由 Z^T·Phi 实算（随工具变量变化），不是常量占位。

        实测（600 点、400 阶过程、d=3）：工具变量取外生 SP 时 cond≈2547，
        取 u 自身时 cond≈587——两者不同即证明该字段反映了实际矩阵。
        注：本数据下"工具变量退化为 u"并不使条件数上升，故不假设单调性；
        阈值与降权口径属决策（同 G27#4），本轮只保证可观测。
        """
        u, y, sp = _series()
        cond_sp = identify_iv(u, y, sp, 3, na=1, nb=1).condition_number
        cond_u = identify_iv(u, y, u * 1.0, 3, na=1, nb=1).condition_number
        assert math.isfinite(cond_sp) and math.isfinite(cond_u)
        assert cond_sp != cond_u

    def test_fields_default_for_backward_compatibility(self) -> None:
        """两个新字段有默认值，旧构造点不受影响。"""
        from app.services.tuning_identification.iv import IVResult

        r = IVResult(
            a_coeffs=[-0.5],
            b_coeffs=[0.1],
            d=1,
            residual_var=0.01,
            n_samples=100,
            r_squared=0.9,
            iterations=1,
        )
        assert r.solve_mode == "solve"
        assert math.isinf(r.condition_number)
