"""理想稳态时间计算工具函数

根据 GB/T 44693.2-2024 附录 B.4 规范，理想稳态时间 T' 是衡量回路响应速度的基准。

支持三种配置方式，按优先级从高到低选择：
1. 回路级手动配置（最高优先级）
2. 基于过程模型参数自动计算（次优先级）
3. 基于回路类型的默认值（最低优先级）

参考文档：《关键算法设计说明》§4.5.2
"""

from __future__ import annotations

from enum import StrEnum


class ControlType(StrEnum):
    """控制类型枚举，用于确定理想稳态时间的经验系数"""

    FLOW = "FC"
    PRESSURE = "PC"
    TEMPERATURE = "TC"
    LEVEL = "LC"
    COMPOSITION = "CC"
    OTHER = "OTHER"


# 经验系数 α 取值表
# 来源：工业控制工程实践经验
EXPERIENCE_COEFFICIENTS: dict[ControlType, tuple[float, float]] = {
    ControlType.FLOW: (1.5, 1.5),
    ControlType.PRESSURE: (2.0, 2.0),
    ControlType.TEMPERATURE: (2.5, 3.0),
    ControlType.LEVEL: (3.0, 5.0),
    ControlType.COMPOSITION: (3.0, 4.0),
    ControlType.OTHER: (2.0, 2.0),
}

# 回路类型默认理想稳态时间（秒）
# 来源：典型工业过程的经验值
DEFAULT_SETTLING_TIMES: dict[ControlType, float] = {
    ControlType.FLOW: 30.0,
    ControlType.PRESSURE: 60.0,
    ControlType.TEMPERATURE: 180.0,
    ControlType.LEVEL: 600.0,
    ControlType.COMPOSITION: 300.0,
    ControlType.OTHER: 120.0,
}


def get_experience_coefficient(control_type: ControlType) -> float:
    """获取指定控制类型的经验系数 α。

    对于温度、液位、成分等惯性较大的过程，返回推荐范围的中间值。

    Args:
        control_type: 控制类型枚举值

    Returns:
        经验系数 α 的值
    """
    min_alpha, max_alpha = EXPERIENCE_COEFFICIENTS[control_type]
    return (min_alpha + max_alpha) / 2


def calculate_ideal_settling_time(
    manual_value: float | None = None,
    tau: float | None = None,
    theta: float | None = None,
    control_type: ControlType | None = None,
    use_conservative_alpha: bool = False,
) -> float:
    """计算理想稳态时间 T'（秒）。

    按优先级从高到低选择计算方式：
    1. 优先使用手动配置值（manual_value）
    2. 其次使用过程模型参数计算（α·(τ+θ)）
    3. 最后使用回路类型默认值

    Args:
        manual_value: 手动配置的理想稳态时间（秒），最高优先级
        tau: 过程时间常数（秒），用于模型计算
        theta: 过程纯滞后时间（秒），用于模型计算
        control_type: 控制类型，用于确定经验系数和默认值
        use_conservative_alpha: 是否使用保守的经验系数（取范围上限），默认为 False

    Returns:
        理想稳态时间（秒），最小返回值为 1.0 秒

    Raises:
        ValueError: 当无法确定理想稳态时间时（无手动配置、无模型参数、无控制类型）

    Examples:
        >>> # 使用手动配置值
        >>> calculate_ideal_settling_time(manual_value=60.0)
        60.0

        >>> # 使用模型参数计算（流量回路，τ=10秒，θ=2秒）
        >>> calculate_ideal_settling_time(tau=10.0, theta=2.0, control_type=ControlType.FLOW)
        18.0

        >>> # 使用回路类型默认值
        >>> calculate_ideal_settling_time(control_type=ControlType.TEMPERATURE)
        180.0

        >>> # 使用保守经验系数
        >>> calculate_ideal_settling_time(tau=180.0, theta=30.0,
        ...                              control_type=ControlType.TEMPERATURE,
        ...                              use_conservative_alpha=True)
        630.0
    """
    # 方式一：回路级手动配置（最高优先级）
    if manual_value is not None and manual_value > 0:
        return max(1.0, manual_value)

    # 方式二：基于过程模型参数自动计算（次优先级）
    if tau is not None and theta is not None and tau >= 0 and theta >= 0:
        if control_type is None:
            control_type = ControlType.OTHER

        min_alpha, max_alpha = EXPERIENCE_COEFFICIENTS[control_type]
        alpha = max_alpha if use_conservative_alpha else (min_alpha + max_alpha) / 2

        result = alpha * (tau + theta)
        return max(1.0, result)

    # 方式三：基于回路类型的默认值（最低优先级）
    if control_type is not None:
        return DEFAULT_SETTLING_TIMES[control_type]

    # 无法确定理想稳态时间
    raise ValueError(
        "无法计算理想稳态时间：缺少必要参数。"
        "请至少提供以下参数之一：manual_value、(tau+theta+control_type)、control_type"
    )


def parse_control_type(loop_tag_name: str) -> ControlType:
    """从回路位号解析控制类型。

    根据位号中的控制类型标识（FC/PC/TC/LC/CC）推断控制类型。

    Args:
        loop_tag_name: 回路位号，如 "101-FC-1023"

    Returns:
        解析出的控制类型

    Examples:
        >>> parse_control_type("101-FC-1023")
        <ControlType.FLOW: 'FC'>

        >>> parse_control_type("101-TC-2001")
        <ControlType.TEMPERATURE: 'TC'>

        >>> parse_control_type("P-101")
        <ControlType.OTHER: 'OTHER'>
    """
    upper_name = loop_tag_name.upper()

    if "-FC-" in upper_name or upper_name.endswith("-FC"):
        return ControlType.FLOW
    elif "-PC-" in upper_name or upper_name.endswith("-PC"):
        return ControlType.PRESSURE
    elif "-TC-" in upper_name or upper_name.endswith("-TC"):
        return ControlType.TEMPERATURE
    elif "-LC-" in upper_name or upper_name.endswith("-LC"):
        return ControlType.LEVEL
    elif "-CC-" in upper_name or upper_name.endswith("-CC"):
        return ControlType.COMPOSITION
    else:
        return ControlType.OTHER


def calculate_from_model_params(
    k: float | None,
    tau: float,
    theta: float,
    control_type: ControlType,
    use_conservative_alpha: bool = False,
) -> float:
    """从过程模型参数计算理想稳态时间。

    计算公式：T' = α · (τ + θ)

    Args:
        k: 过程增益（无量纲），当前未使用，预留参数
        tau: 过程时间常数（秒）
        theta: 过程纯滞后时间（秒）
        control_type: 控制类型
        use_conservative_alpha: 是否使用保守的经验系数

    Returns:
        理想稳态时间（秒）

    Examples:
        >>> # 温度控制回路，夹套加热
        >>> calculate_from_model_params(k=0.5, tau=120.0, theta=20.0,
        ...                             control_type=ControlType.TEMPERATURE)
        175.0

        >>> # 液位控制回路，大储罐
        >>> calculate_from_model_params(k=1.0, tau=300.0, theta=30.0,
        ...                             control_type=ControlType.LEVEL,
        ...                             use_conservative_alpha=True)
        1650.0
    """
    if tau < 0:
        raise ValueError(f"过程时间常数 tau 不能为负数，当前值: {tau}")
    if theta < 0:
        raise ValueError(f"过程纯滞后时间 theta 不能为负数，当前值: {theta}")

    return calculate_ideal_settling_time(
        tau=tau,
        theta=theta,
        control_type=control_type,
        use_conservative_alpha=use_conservative_alpha,
    )


def get_default_by_control_type(control_type: ControlType) -> float:
    """获取指定控制类型的默认理想稳态时间。

    Args:
        control_type: 控制类型枚举值

    Returns:
        默认理想稳态时间（秒）

    Examples:
        >>> get_default_by_control_type(ControlType.FLOW)
        30.0

        >>> get_default_by_control_type(ControlType.LEVEL)
        600.0
    """
    return DEFAULT_SETTLING_TIMES[control_type]


def calculate_fast_rate(actual_settling_time: float, ideal_settling_time: float) -> float:
    """计算快速率 F（0~100%）。

    根据 GB/T 44693.2-2024 附录 B.4，快速率衡量回路响应速度。

    计算公式（分段指数映射）：
    - 当 T <= T'（实际响应速度达标或优于理想）：F = 100%
    - 当 T > T'（实际响应速度慢于理想）：F = 1/e^((T-T')/T') × 100%

    Args:
        actual_settling_time: 实际稳态时间 T（秒），响应从扰动开始到进入±5%稳态带的时间
        ideal_settling_time: 理想稳态时间 T'（秒），可配置的期望响应时间

    Returns:
        快速率（0~100%），最小返回值为 0.0%

    Raises:
        ValueError: 当参数为负数或理想稳态时间为0时

    Examples:
        >>> # 实际响应速度达标（T = T'）
        >>> calculate_fast_rate(30.0, 30.0)
        100.0

        >>> # 实际响应速度优于理想（T < T'）
        >>> calculate_fast_rate(20.0, 30.0)
        100.0

        >>> # 实际响应速度稍慢于理想（T = 1.5×T'）
        >>> calculate_fast_rate(45.0, 30.0)
        60.653...

        >>> # 实际响应速度明显偏慢（T = 2×T'）
        >>> calculate_fast_rate(60.0, 30.0)
        36.787...

        >>> # 实际响应速度严重偏慢（T = 3×T'）
        >>> calculate_fast_rate(90.0, 30.0)
        13.533...
    """
    if actual_settling_time < 0:
        raise ValueError(f"实际稳态时间不能为负数，当前值: {actual_settling_time}")
    if ideal_settling_time <= 0:
        raise ValueError(f"理想稳态时间必须大于0，当前值: {ideal_settling_time}")

    if actual_settling_time <= ideal_settling_time:
        return 100.0

    import math

    ratio = (actual_settling_time - ideal_settling_time) / ideal_settling_time
    return max(0.0, (1.0 / math.exp(ratio)) * 100.0)
