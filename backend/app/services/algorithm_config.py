"""指标算法参数配置服务（P0-B 配置化基础设施）.

三层配置合并链：
    1. 算法默认值（``_DEFAULTS``，与计算器硬编码常量一致）
    2. ``algorithm_parameter`` 表（系统级默认覆盖，按 control_type 分组）
    3. ``metric_config.threshold`` JSONB（指标级覆盖，已有字段复用）

热路径（指标计算器）通过 ``get_algorithm_params()`` 读取合并后的进程内缓存，
不查库。配置保存后通过 ``apply_runtime()`` 刷新缓存。

设计依据：HiaMonitor 借鉴重构计划评审报告 P0-B, P0-3, P1-2
复用模式：参照 ``app.services.preprocessing.outlier_params`` 的存储+缓存+应用三段式
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.algorithm_parameter import AlgorithmParameter
from app.models.metric import MetricConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 算法默认参数（与计算器内硬编码常量一致，作为配置链最底层回退）
# ---------------------------------------------------------------------------

#: 每个指标在每个控制类型下的算法默认参数
#: key = metric_code, value = {control_type: {param_name: value}}
#: 注意：默认值与计算器内硬编码常量一致，确保未配置时行为不变（behavior-preserving）。
_DEFAULTS: dict[str, dict[str, dict[str, Any]]] = {
    "oscillation_rate": {
        # min_half_period_samples：P1 抗噪门控（与诊断侧 _iae_kernel/stiction 同值 8）；
        # min_amplitude_ratio：P2 幅度门控（特征幅度占量程比例，0.2%）；
        # sp_step_*：P2 SP 阶跃剔除（2026-08-28 起默认开启，与 stability 同步：
        # 阶跃跟踪暂态同型段 IAE 相似率可达 1.0 会被误判振荡）。
        # DB 种子行不含新键时由本默认层兜底，无需迁移
        "STABLE": {
            "similarity_threshold": 0.4,
            "min_ratio": 0.05,
            "max_ratio": 15.0,
            "min_zero_crossings": 4,
            "min_half_period_samples": 8,
            "min_amplitude_ratio": 0.002,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
        "SLOW": {
            "similarity_threshold": 0.4,
            "min_ratio": 0.05,
            "max_ratio": 15.0,
            "min_zero_crossings": 4,
            "min_half_period_samples": 8,
            "min_amplitude_ratio": 0.002,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
        "FAST": {
            "similarity_threshold": 0.4,
            "min_ratio": 0.05,
            "max_ratio": 15.0,
            "min_zero_crossings": 4,
            "min_half_period_samples": 8,
            "min_amplitude_ratio": 0.002,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
        "LOGIC": {
            "similarity_threshold": 0.4,
            "min_ratio": 0.05,
            "max_ratio": 15.0,
            "min_zero_crossings": 4,
            "min_half_period_samples": 8,
            "min_amplitude_ratio": 0.002,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
    },
    # 整改 F2（2026-08-08）：原硬编码参数配置化，默认值与计算器常量一致（行为不变）
    "settling_time": {
        "STABLE": {"settling_threshold": 0.05, "noise_floor_ratio": 0.001},
        "SLOW": {"settling_threshold": 0.05, "noise_floor_ratio": 0.001},
        "FAST": {"settling_threshold": 0.05, "noise_floor_ratio": 0.001},
        "LOGIC": {"settling_threshold": 0.05, "noise_floor_ratio": 0.001},
    },
    "effective_auto_rate": {
        # saturation_epsilon：2026-10-10 用户裁决，饱和容差带可配置，默认 0=严格贴限
        "STABLE": {"default_e_max_ratio": 0.05, "saturation_epsilon": 0.0},
        "SLOW": {"default_e_max_ratio": 0.05, "saturation_epsilon": 0.0},
        "FAST": {"default_e_max_ratio": 0.05, "saturation_epsilon": 0.0},
        "LOGIC": {"default_e_max_ratio": 0.05, "saturation_epsilon": 0.0},
    },
    "saturation_rate": {
        # saturation_epsilon：饱和容差带（量程百分比，默认 0=严格贴限位端点）；
        # 回路级 CONFIG saturation_epsilon 信号优先于本配置链
        "STABLE": {"saturation_epsilon": 0.0},
        "SLOW": {"saturation_epsilon": 0.0},
        "FAST": {"saturation_epsilon": 0.0},
        "LOGIC": {"saturation_epsilon": 0.0},
    },
    "output_trip_index": {
        "STABLE": {"trip_inactive": 0.01, "trip_normal": 0.1, "trip_frequent": 1.0},
        "SLOW": {"trip_inactive": 0.01, "trip_normal": 0.1, "trip_frequent": 1.0},
        "FAST": {"trip_inactive": 0.01, "trip_normal": 0.1, "trip_frequent": 1.0},
        "LOGIC": {"trip_inactive": 0.01, "trip_normal": 0.1, "trip_frequent": 1.0},
    },
    "fast_rate": {
        # settling_tolerance=0.0 + ideal_settling_ratio=1.0
        # → 阈值=ideal_t，与原 actual_t<=ideal_t 一致
        # P2 抗扰性分析参数：anti_disturbance_enabled 默认 False（零回归），
        # 开启后用扰动恢复时间替代 ARMA 稳态时间作为 fast_rate 公式的 T 值。
        "STABLE": {
            "ideal_settling_ratio": 1.0,
            "settling_tolerance": 0.0,
            "anti_disturbance_enabled": False,
            "disturbance_band_sigma": 2.0,
            "recovery_persistence": 5,
            "min_disturbance_duration": 3.0,
            "sp_step_sigma": 3.0,
        },
        "SLOW": {
            "ideal_settling_ratio": 1.0,
            "settling_tolerance": 0.0,
            "anti_disturbance_enabled": False,
            "disturbance_band_sigma": 2.0,
            "recovery_persistence": 5,
            "min_disturbance_duration": 3.0,
            "sp_step_sigma": 3.0,
        },
        "FAST": {
            "ideal_settling_ratio": 1.0,
            "settling_tolerance": 0.0,
            "anti_disturbance_enabled": False,
            "disturbance_band_sigma": 2.0,
            "recovery_persistence": 5,
            "min_disturbance_duration": 3.0,
            "sp_step_sigma": 3.0,
        },
        "LOGIC": {
            "ideal_settling_ratio": 1.0,
            "settling_tolerance": 0.0,
            "anti_disturbance_enabled": False,
            "disturbance_band_sigma": 2.0,
            "recovery_persistence": 5,
            "min_disturbance_duration": 3.0,
            "sp_step_sigma": 3.0,
        },
    },
    "accuracy_rate": {
        # v2.2（2026-10-10 稳定回路误判整改）：e_max 归一化基准改工程容限
        # （量程比例，默认 2%）；原 e_max_percentile 随数据驱动 e_max 一并废弃
        "STABLE": {"e_max_tolerance_ratio": 0.02},
        "SLOW": {"e_max_tolerance_ratio": 0.02},
        "FAST": {"e_max_tolerance_ratio": 0.02},
        "LOGIC": {"e_max_tolerance_ratio": 0.02},
    },
    "stability_rate": {
        # decay_ratio=0.05（量程 5% 为指数衰减基准）与原硬编码一致，行为不变；
        # band_in_score_enabled=False → 默认仍为 GB/T 指数公式，石化惯例带内率
        # 仅作为 details.band_in_rate 辅助输出，开启后才替代分值；
        # sp_step_exclusion_enabled=True → SP 阶跃剔除默认开启（2026-08-27 起，
        # 此前默认关闭零回归）：剔除阶跃后 sp_tracking_window 个跟踪点再计算
        # σ 与带内率，避免操作员正常改设定值的跟踪暂态被误判为不平稳。
        "STABLE": {
            "decay_ratio": 0.05,
            "band_ratio": 0.01,
            "band_in_score_enabled": False,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
        "SLOW": {
            "decay_ratio": 0.05,
            "band_ratio": 0.01,
            "band_in_score_enabled": False,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
        "FAST": {
            "decay_ratio": 0.05,
            "band_ratio": 0.01,
            "band_in_score_enabled": False,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
        "LOGIC": {
            "decay_ratio": 0.05,
            "band_ratio": 0.01,
            "band_in_score_enabled": False,
            "sp_step_exclusion_enabled": True,
            "sp_step_sigma": 3.0,
            "sp_tracking_window": 60,
        },
    },
}

#: 支持的控制类型
_CONTROL_TYPES = ("STABLE", "SLOW", "FAST", "LOGIC")


#: 参数元数据注册表（P2-01 单源参数目录：服务端校验 + 前端元数据单源）
#:
#: 注册元数据全集（方案 §3.2，按现有结构渐进扩列）：
#: - key：参数键（本表键）
#: - label：名称（中文名）
#: - description：业务含义
#: - type：bool / int / float（缺省 "float"；int 类型额外拒绝 0.5 等非整数）
#: - unit：单位
#: - min / max：合法范围（闭区间）
#: - finite：有限数校验（数值型恒 True，NaN/Inf 拒绝）
#: - combo：组合约束关联键（见 ``_COMBO_RULES``）
#: - defaultBasis：默认值及范围依据（P0-02 数值语义冻结 v2 §6.1 为准，不发明新默认）
#: - kind：business（业务参数）/ internal（算法内部·运维参数），分别呈现
#: - risk / riskNote：变更风险（LOW/MEDIUM/HIGH + 说明）
#: - default / scope / maintainRoles：由 ``build_param_meta`` 从 ``_DEFAULTS`` 与
#:   常量注入（默认值单源在 ``_DEFAULTS``，避免双份漂移；scope=本表均为
#:   全局默认层·4 控制类型通用；maintainRoles 按 DEC-03 裁决=ADMIN）。
#: 结构：metric_code → param_key → 元数据字段
PARAM_META: dict[str, dict[str, dict[str, Any]]] = {
    "oscillation_rate": {
        "similarity_threshold": {
            "label": "相似度阈值",
            "min": 0.1,
            "max": 0.9,
            "unit": "",
            "description": "正/负半周期 IAE 相似度阈值",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致（行为不变）",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "阈值收紧/放宽直接改变振荡判定灵敏度",
        },
        "min_ratio": {
            "label": "振荡最小周期比",
            "min": 0.01,
            "max": 0.5,
            "unit": "",
            "description": "振荡判定最小比例",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "与 max_ratio 构成振荡周期比判别带",
            "combo": ["min_ratio", "max_ratio"],
        },
        "max_ratio": {
            "label": "振荡最大周期比",
            "min": 1.0,
            "max": 50.0,
            "unit": "",
            "description": "振荡判定最大比例",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "与 min_ratio 构成振荡周期比判别带",
            "combo": ["min_ratio", "max_ratio"],
        },
        "min_zero_crossings": {
            "label": "最少零交叉数",
            "type": "int",
            "min": 2,
            "max": 20,
            "unit": "个",
            "description": "振荡判定最少零交叉数",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "降低后白噪声伪穿越易误判振荡",
        },
        "min_half_period_samples": {
            "label": "抗噪最小半周期",
            "type": "int",
            "min": 1,
            "max": 100,
            "unit": "采样点",
            "description": "抗噪最小平均半周期（低于此值判非振荡，剔除白噪声伪穿越）",
            "defaultBasis": "P1 抗噪门控：与诊断侧 _iae_kernel/stiction 同值 8",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "抗噪门控常量，仅影响噪声剔除强度",
        },
        "min_amplitude_ratio": {
            "label": "幅度门控下限",
            "min": 0.0005,
            "max": 0.01,
            "unit": "",
            "description": "幅度门控下限（特征幅度占量程比例，低于此值判非振荡）",
            "defaultBasis": "P2 幅度门控：特征幅度占量程 0.2%",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "抗噪门控常量，仅影响微幅信号剔除",
        },
        "sp_step_exclusion_enabled": {
            "label": "SP 阶跃剔除开关",
            "type": "bool",
            "unit": "",
            "description": "SP 阶跃剔除开关（剔除设定值阶跃后的跟踪暂态，默认开启）",
            "defaultBasis": "2026-08-28 起默认开启（与 stability 同步）",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "关闭后阶跃跟踪暂态易被误判为振荡",
        },
        "sp_step_sigma": {
            "label": "SP 阶跃检测阈值",
            "min": 0.5,
            "max": 10.0,
            "unit": "σ",
            "description": "SP 阶跃检测阈值",
            "defaultBasis": "P0-02 §6.1 第 6 条 SP_STEP_SIGMA=3.0 冻结",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "阶跃检测灵敏度，影响剔除窗口起判",
        },
        "sp_tracking_window": {
            "label": "SP 跟踪剔除窗",
            "type": "int",
            "min": 5,
            "max": 3600,
            "unit": "点",
            "description": "SP 阶跃后剔除的跟踪窗点数（实际时长=点数×采样间隔）",
            "defaultBasis": "P0-02 §6.1 第 6 条 SP_TRACKING_WINDOW=60 冻结",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "窗口过大将缩小有效统计样本",
        },
    },
    "fast_rate": {
        "ideal_settling_ratio": {
            "label": "理想稳态时间比例",
            "min": 0.1,
            "max": 5.0,
            "unit": "",
            "description": "理想稳态时间比例",
            "defaultBasis": "P0-02 §6.1 第 5 条 ratio=1.0 冻结（阈值=ideal_t）",
            "kind": "business",
            "risk": "HIGH",
            "riskNote": "直接缩放快速率满分阈值 T_th",
            "combo": ["ideal_settling_ratio", "settling_tolerance"],
        },
        "settling_tolerance": {
            "label": "稳态容差",
            "min": 0.0,
            "max": 0.5,
            "unit": "",
            "description": "稳态容差",
            "defaultBasis": "P0-02 §6.1 第 5 条 tolerance=0.0 冻结（默认退化原式）",
            "kind": "business",
            "risk": "HIGH",
            "riskNote": "与 ideal_settling_ratio 共同决定满分阈值（乘性因子）",
            "combo": ["ideal_settling_ratio", "settling_tolerance"],
        },
        "anti_disturbance_enabled": {
            "label": "抗扰性分析开关",
            "type": "bool",
            "unit": "",
            "description": "抗扰性分析开关",
            "defaultBasis": "P2 抗扰性分析：默认 False（零回归）",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "开启后 fast_rate 的 T 值来源切换为扰动恢复时间",
        },
        "disturbance_band_sigma": {
            "label": "扰动带宽度",
            "min": 0.5,
            "max": 10.0,
            "unit": "σ",
            "description": "扰动带宽度",
            "defaultBasis": "P2 抗扰性分析默认值 2.0σ",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "仅抗扰分支生效",
        },
        "recovery_persistence": {
            "label": "恢复持续点数",
            "type": "int",
            "min": 1,
            "max": 50,
            "unit": "点",
            "description": "恢复持续点数",
            "defaultBasis": "P2 抗扰性分析默认值 5 点",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "仅抗扰分支生效",
        },
        "min_disturbance_duration": {
            "label": "最小扰动时长",
            "min": 1.0,
            "max": 600.0,
            "unit": "s",
            "description": "最小扰动时长",
            "defaultBasis": "P2 抗扰性分析默认值 3.0s",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "仅抗扰分支生效",
        },
        "sp_step_sigma": {
            "label": "SP 阶跃检测阈值",
            "min": 0.5,
            "max": 10.0,
            "unit": "σ",
            "description": "SP 阶跃检测阈值",
            "defaultBasis": "P2 抗扰性分析默认值 3.0σ（与 stability 同值）",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "阶跃检测灵敏度",
        },
    },
    "accuracy_rate": {
        "e_max_tolerance_ratio": {
            "label": "工程容限比例",
            "min": 0.002,
            "max": 0.2,
            "unit": "",
            "description": "工程容限比例（|E|max = ratio×量程，默认 2%）",
            "defaultBasis": (
                "v2.2（2026-10-10 稳定回路误判整改）：0.02=量程 2%，P0-02 §6.1 第 4 条冻结"
            ),
            "kind": "business",
            "risk": "HIGH",
            "riskNote": "直接决定准确率归一化基准，收紧即全面改变准确率分布",
            "combo": ["e_max_tolerance_ratio"],
        },
    },
    "stability_rate": {
        "decay_ratio": {
            "label": "指数衰减基准",
            "min": 0.01,
            "max": 0.2,
            "unit": "",
            "description": "指数衰减基准（量程比例，σ 达 decay_ratio×U 时 S≈36.8）",
            "defaultBasis": "P0-02 §6.1 第 6 条 DECAY_RATIO=0.05 冻结（与原硬编码一致）",
            "kind": "business",
            "risk": "HIGH",
            "riskNote": "稳定率指数公式分母基准",
        },
        "band_ratio": {
            "label": "平稳带比例",
            "min": 0.001,
            "max": 0.1,
            "unit": "",
            "description": "石化惯例平稳带（量程比例，|PV-SP|≤band_ratio×U 记平稳）",
            "defaultBasis": "P0-02 §6.1 第 6 条 BAND_RATIO=0.01 冻结",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "带内率统计口径（band_in_score_enabled 开启时直接作分值）",
        },
        "band_in_score_enabled": {
            "label": "带内率作分值开关",
            "type": "bool",
            "unit": "",
            "description": "以带内时间占比作为平稳率分值（石化惯例口径，不乘振荡修正）",
            "defaultBasis": "P0-02 §6.1 第 6 条：默认 False 保持 GB/T 指数公式",
            "kind": "business",
            "risk": "HIGH",
            "riskNote": "开启后稳定率评分口径整体切换（石化惯例 vs 国标指数）",
        },
        "sp_step_exclusion_enabled": {
            "label": "SP 阶跃剔除开关",
            "type": "bool",
            "unit": "",
            "description": "SP 阶跃剔除开关（剔除设定值阶跃后的跟踪暂态）",
            "defaultBasis": "2026-08-27 起默认开启",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "关闭后正常改设定值的跟踪暂态会被误判为不平稳",
        },
        "sp_step_sigma": {
            "label": "SP 阶跃检测阈值",
            "min": 0.5,
            "max": 10.0,
            "unit": "σ",
            "description": "SP 阶跃检测阈值",
            "defaultBasis": "P0-02 §6.1 第 6 条 SP_STEP_SIGMA=3.0 冻结",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "阶跃检测灵敏度",
        },
        "sp_tracking_window": {
            "label": "SP 跟踪剔除窗",
            "type": "int",
            "min": 5,
            "max": 3600,
            "unit": "点",
            "description": "SP 阶跃后剔除的跟踪窗点数（实际时长=点数×采样间隔）",
            "defaultBasis": "P0-02 §6.1 第 6 条 SP_TRACKING_WINDOW=60 冻结",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "窗口过大将缩小有效统计样本",
        },
    },
    "saturation_rate": {
        "saturation_epsilon": {
            "label": "饱和容差带",
            "min": 0.0,
            "max": 10.0,
            "unit": "%",
            "description": "饱和容差带（默认 0=严格贴限位端点；回路级 CONFIG 信号优先）",
            "defaultBasis": "P0-02 §6.1 第 13 条：ε=0.0（2026-10-10 裁决与 effective_auto 同口径）",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "放宽后限位邻域不再计饱和，饱和率系统性下降",
        },
    },
    "settling_time": {
        "settling_threshold": {
            "label": "Green 衰减阈值",
            "min": 0.01,
            "max": 0.2,
            "unit": "",
            "description": "Green 函数衰减阈值",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "business",
            "risk": "HIGH",
            "riskNote": "决定稳态时间判定收敛标准",
        },
        "noise_floor_ratio": {
            "label": "噪声底比例",
            "min": 0.0001,
            "max": 0.01,
            "unit": "",
            "description": "already_stable 噪声底（偏差σ < ratio×量程 判已稳态，默认 0.1%）",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "internal",
            "risk": "LOW",
            "riskNote": "仅 already_stable 预判分支生效",
        },
    },
    "effective_auto_rate": {
        "default_e_max_ratio": {
            "label": "默认偏差带比例",
            "min": 0.01,
            "max": 0.5,
            "unit": "",
            "description": "默认偏差带比例（量程归一化）",
            "defaultBasis": "P0-02 §6.1 第 7 条 DEFAULT_E_MAX_RATIO=0.05 冻结",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "决定有效自控的偏差带（无回路级 CONFIG 信号时）",
            "combo": ["default_e_max_ratio"],
        },
        "saturation_epsilon": {
            "label": "饱和容差带",
            "min": 0.0,
            "max": 10.0,
            "unit": "%",
            "description": "饱和容差带（默认 0=严格贴限位端点）",
            "defaultBasis": "P0-02 §6.1 第 7 条：ε=0.0（2026-10-10 裁决去容差带）",
            "kind": "business",
            "risk": "MEDIUM",
            "riskNote": "放宽后 OP 限位邻域仍计有效自控，有效自控率偏高",
        },
    },
    "output_trip_index": {
        "trip_inactive": {
            "label": "不活跃行程边界",
            "min": 0.001,
            "max": 0.1,
            "unit": "1/s",
            "description": "不活跃行程边界",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "business",
            "risk": "LOW",
            "riskNote": "行程分档下边界（须保持 trip_inactive < trip_normal < trip_frequent）",
            "combo": ["trip_inactive", "trip_normal", "trip_frequent"],
        },
        "trip_normal": {
            "label": "正常行程边界",
            "min": 0.01,
            "max": 1.0,
            "unit": "1/s",
            "description": "正常行程边界",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "business",
            "risk": "LOW",
            "riskNote": "行程分档中边界（须保持档位递增）",
            "combo": ["trip_inactive", "trip_normal", "trip_frequent"],
        },
        "trip_frequent": {
            "label": "频繁行程边界",
            "min": 0.1,
            "max": 10.0,
            "unit": "1/s",
            "description": "频繁行程边界",
            "defaultBasis": "整改 F2 配置化：与计算器原硬编码常量一致",
            "kind": "business",
            "risk": "LOW",
            "riskNote": "行程分档上边界（须保持档位递增）",
            "combo": ["trip_inactive", "trip_normal", "trip_frequent"],
        },
    },
}

#: 已废弃参数键 → 迁移提示（P2-01：旧键拒绝且明确迁移提示，不再静默当未知键）
_DEPRECATED_PARAM_KEYS: dict[tuple[str, str], str] = {
    ("accuracy_rate", "e_max_percentile"): (
        "e_max_percentile 已于 v2.2（2026-10-10 稳定回路误判整改）随数据驱动 e_max 基准废弃，"
        "请改用 e_max_tolerance_ratio（工程容限比例，|E|max = ratio×量程）"
    ),
}


#: 参数分组映射（整改 F6：配置页 category 分组展示）
#: 结构：metric_code → param_key → 中文分组名；未收录键归入"其他"
PARAM_CATEGORY: dict[str, dict[str, str]] = {
    "oscillation_rate": {
        "similarity_threshold": "判定阈值",
        "min_ratio": "判定阈值",
        "max_ratio": "判定阈值",
        "min_zero_crossings": "判定阈值",
        "min_half_period_samples": "判定阈值",
        "min_amplitude_ratio": "判定阈值",
        "sp_step_exclusion_enabled": "SP阶跃剔除",
        "sp_step_sigma": "SP阶跃剔除",
        "sp_tracking_window": "SP阶跃剔除",
    },
    "fast_rate": {
        "ideal_settling_ratio": "稳定判定",
        "settling_tolerance": "稳定判定",
        "anti_disturbance_enabled": "扰动分析",
        "disturbance_band_sigma": "扰动分析",
        "recovery_persistence": "扰动分析",
        "min_disturbance_duration": "扰动分析",
        "sp_step_sigma": "扰动分析",
    },
    "accuracy_rate": {"e_max_tolerance_ratio": "判定阈值"},
    "stability_rate": {
        "decay_ratio": "判定阈值",
        "band_ratio": "石化惯例",
        "band_in_score_enabled": "石化惯例",
        "sp_step_exclusion_enabled": "SP阶跃剔除",
        "sp_step_sigma": "SP阶跃剔除",
        "sp_tracking_window": "SP阶跃剔除",
    },
    "settling_time": {"settling_threshold": "判定阈值", "noise_floor_ratio": "判定阈值"},
    "effective_auto_rate": {"default_e_max_ratio": "判定阈值", "saturation_epsilon": "判定阈值"},
    "saturation_rate": {"saturation_epsilon": "判定阈值"},
    "output_trip_index": {
        "trip_inactive": "行程边界",
        "trip_normal": "行程边界",
        "trip_frequent": "行程边界",
    },
}


def build_param_meta(metric_code: str) -> dict[str, dict[str, Any]]:
    """构造指标参数元数据视图（P2-01 方案 §3.2 注册元数据全集单源下发）.

    在 PARAM_META（label/min/max/unit/description/type/kind/risk 等）基础上合并：
    - PARAM_CATEGORY（category 分组）
    - default（默认值，单源取自 ``_DEFAULTS``，避免双份漂移；各控制类型默认
      一致时取 STABLE，不一致时该键不注入 default 而是置 None）
    - scope / maintainRoles / finite（注册级常量：全局默认层·4 控制类型通用；
      DEC-03 裁决配置发布为 ADMIN 职责；数值型恒做有限数校验）
    未注册分组的键归入"其他"。
    """
    meta = PARAM_META.get(metric_code, {})
    cats = PARAM_CATEGORY.get(metric_code, {})
    # 默认值单源：_DEFAULTS 各控制类型取值（当前各 CT 一致，取 STABLE 代表）
    defaults_by_ct = _DEFAULTS.get(metric_code, {})
    defaults_stable = defaults_by_ct.get("STABLE", {})
    defaults_uniform = all(defaults_by_ct.get(ct, {}) == defaults_stable for ct in _CONTROL_TYPES)

    view: dict[str, dict[str, Any]] = {}
    for key, entry in meta.items():
        item = dict(entry)
        item.setdefault("type", "float")
        item["category"] = cats.get(key, "其他")
        item["default"] = defaults_stable.get(key) if defaults_uniform else None
        item["scope"] = "global-default"
        item["applicableControlTypes"] = list(_CONTROL_TYPES)
        item["maintainRoles"] = ["ADMIN"]
        item["finite"] = item.get("type") in ("int", "float")
        view[key] = item
    return view


# ---------------------------------------------------------------------------
# 参数校验（P2-01 CFG-05：类型/有限数/范围/组合约束统一校验）
# ---------------------------------------------------------------------------


def _combo_violations(metric_code: str, effective: dict[str, Any]) -> list[str]:
    """组合约束校验（在合并后有效参数上求值；缺键时跳过该条规则）.

    组合约束表（P2-01 任务卡点名 + 档位颠倒反例）：
    - oscillation_rate：min_ratio < max_ratio（档位颠倒拒绝）
    - output_trip_index：trip_inactive < trip_normal < trip_frequent（档位颠倒拒绝）
    - fast_rate：ideal_settling_ratio × (1 + settling_tolerance) > 0
      （满分阈值因子必须为正——settling_tolerance 与 ideal_settling_ratio 的关系）
    - accuracy_rate / effective_auto_rate：e_max 类偏差带比例必须 > 0
      （min 已保证 >0，此处为防御性组合约束，语义不变）
    """
    violations: list[str] = []

    def _num(key: str) -> float | None:
        v = effective.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    if metric_code == "oscillation_rate":
        lo, hi = _num("min_ratio"), _num("max_ratio")
        if lo is not None and hi is not None and lo >= hi:
            violations.append(
                f"组合约束: min_ratio={lo} 必须小于 max_ratio={hi}（振荡周期比判别带不可颠倒）"
            )
    elif metric_code == "output_trip_index":
        v1, v2, v3 = _num("trip_inactive"), _num("trip_normal"), _num("trip_frequent")
        if v1 is not None and v2 is not None and v1 >= v2:
            violations.append(
                f"组合约束: trip_inactive={v1} 必须小于 trip_normal={v2}（行程档位不可颠倒）"
            )
        if v2 is not None and v3 is not None and v2 >= v3:
            violations.append(
                f"组合约束: trip_normal={v2} 必须小于 trip_frequent={v3}（行程档位不可颠倒）"
            )
    elif metric_code == "fast_rate":
        ratio, tol = _num("ideal_settling_ratio"), _num("settling_tolerance")
        if ratio is not None and tol is not None and ratio * (1.0 + tol) <= 0:
            violations.append(
                f"组合约束: ideal_settling_ratio={ratio} × (1 + settling_tolerance={tol}) "
                "必须为正（快速率满分阈值因子）"
            )
    elif metric_code == "accuracy_rate":
        ratio = _num("e_max_tolerance_ratio")
        if ratio is not None and ratio <= 0:
            violations.append(f"组合约束: e_max_tolerance_ratio={ratio} 必须大于 0（偏差带基准）")
    elif metric_code == "effective_auto_rate":
        ratio = _num("default_e_max_ratio")
        if ratio is not None and ratio <= 0:
            violations.append(f"组合约束: default_e_max_ratio={ratio} 必须大于 0（偏差带基准）")
    return violations


def validate_metric_params(
    metric_code: str,
    params: dict[str, Any],
    *,
    base: dict[str, Any] | None = None,
) -> list[str]:
    """校验算法参数键与值域（P2-01 CFG-05 统一校验入口）.

    返回错误文案列表（空列表 = 通过）。拦截：
    - 未知键（含旧键迁移提示，见 ``_DEPRECATED_PARAM_KEYS``）
    - 类型错误：bool/int/float/enum 互不混用（数值布尔、布尔位置收数值均拒绝）
    - int 类型收非整数（如 0.5 点数）
    - 非有限数（NaN/Inf）
    - 越界（min/max 闭区间）
    - 组合约束（在 ``{**base, **params}`` 合并视图上求值，base 为保存前的
      有效参数时即可拦截"单次合法、合并后档位颠倒"的写入）

    Args:
        metric_code: 指标代码（须在 PARAM_META 注册）
        params: 本次写入的参数（部分覆盖）
        base: 合并基线（保存前该控制类型的 Layer1+2 有效参数）；None 时
            组合约束仅在 params 自身包含全部关联键时求值
    """
    meta = PARAM_META.get(metric_code)
    if meta is None:
        return [f"未知指标代码: {metric_code}"]
    errors: list[str] = []
    for key, value in params.items():
        m = meta.get(key)
        if m is None:
            hint = _DEPRECATED_PARAM_KEYS.get((metric_code, key))
            if hint:
                errors.append(f"已废弃参数键: {key}——{hint}")
            else:
                errors.append(f"未知参数键: {key}")
            continue
        ptype = m.get("type", "float")
        if ptype == "bool":
            if not isinstance(value, bool):
                errors.append(
                    f"参数 {key} 应为布尔值，收到 {type(value).__name__}（数值 0/1 不接受）"
                )
            continue
        # 数值型（int / float）
        if isinstance(value, bool):
            errors.append(f"参数 {key} 应为数值，收到布尔值")
            continue
        if not isinstance(value, (int, float)):
            errors.append(f"参数 {key} 应为数值，收到 {type(value).__name__}")
            continue
        if not math.isfinite(value):
            errors.append(f"参数 {key}={value} 不是有限数（NaN/Inf 拒绝）")
            continue
        if ptype == "int" and not float(value).is_integer():
            errors.append(f"参数 {key} 应为整数（点数/个数），收到 {value}")
            continue
        if "min" in m and value < m["min"]:
            errors.append(f"参数 {key}={value} 低于下限 {m['min']}")
        if "max" in m and value > m["max"]:
            errors.append(f"参数 {key}={value} 超过上限 {m['max']}")
    # 组合约束在合并后视图上求值（部分键缺失时跳过对应规则）
    if errors:
        return errors
    effective = {**(base or {}), **params}
    errors.extend(_combo_violations(metric_code, effective))
    return errors


def validate_metric_threshold(metric_code: str, threshold: dict[str, Any]) -> list[str]:
    """校验 ``metric_config.threshold`` 写入结构（P2-01 CFG-04）.

    该 JSONB 字段双语义：
    - metric_code 在算法参数注册表（PARAM_META）内 → Layer 3 算法参数覆盖，
      走严格 ``validate_metric_params``（未知键/非数值/非有限/越界/组合约束拒绝）；
    - 其余为展示类 KPI 阈值（种子形态如 ``{"min": 80, "max": 100, "alert": "warning"}``）
      → 宽松结构校验：键必须为字符串，值必须为有限数值/布尔/非空字符串，
      嵌套 dict/list 与 NaN/Inf 拒绝（坏结构原子拒绝，不静默入库）。
    """
    if not isinstance(threshold, dict):
        return [f"threshold 必须为 JSON 对象，收到 {type(threshold).__name__}"]
    if metric_code in PARAM_META:
        return validate_metric_params(metric_code, threshold)
    errors: list[str] = []
    for key, value in threshold.items():
        if not isinstance(key, str):
            errors.append(f"threshold 键必须为字符串，收到 {type(key).__name__}")
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                errors.append(f"threshold[{key}]={value} 不是有限数（NaN/Inf 拒绝）")
            continue
        if isinstance(value, str):
            if not value.strip():
                errors.append(f"threshold[{key}] 为空字符串")
            continue
        errors.append(
            f"threshold[{key}] 值类型非法: {type(value).__name__}（仅接受有限数值/布尔/字符串）"
        )
    return errors


def get_default_params(metric_code: str, control_type: str) -> dict[str, Any]:
    """获取算法默认参数（不含任何覆盖）."""
    return dict(_DEFAULTS.get(metric_code, {}).get(control_type, {}))


# ---------------------------------------------------------------------------
# 运行时合并缓存（热路径读取）
# ---------------------------------------------------------------------------

#: 合并后的参数缓存 key=(metric_code, control_type) value=dict
_merged_cache: dict[tuple[str, str], dict[str, Any]] = {}


def _rebuild_merged(
    table_overrides: dict[str, dict[str, dict[str, Any]]],
    metric_thresholds: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """合并三层配置：默认值 + algorithm_parameter 表覆盖 + metric_config.threshold 覆盖.

    Args:
        table_overrides: {metric_code: {control_type: {param: value}}}
        metric_thresholds: {metric_code: {param: value}}（指标级覆盖，不区分控制类型）

    Returns:
        {(metric_code, control_type): {param: value}}
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for metric_code, ct_map in _DEFAULTS.items():
        for ct in _CONTROL_TYPES:
            # Layer 1: 算法默认值
            params = dict(ct_map.get(ct, {}))
            # Layer 2: algorithm_parameter 表覆盖
            table_params = table_overrides.get(metric_code, {}).get(ct, {})
            params.update(table_params)
            # Layer 3: metric_config.threshold 指标级覆盖（不区分控制类型）
            mc_params = metric_thresholds.get(metric_code, {})
            params.update(mc_params)
            merged[(metric_code, ct)] = params
    return merged


def get_algorithm_params(metric_code: str, control_type: str | None) -> dict[str, Any]:
    """热路径读取：返回合并后的算法参数.

    Args:
        metric_code: 指标代码，如 ``"oscillation_rate"``
        control_type: 控制类型（STABLE/SLOW/FAST/LOGIC）；None 时回退 STABLE

    Returns:
        合并后的参数字典；未知指标/控制类型返回空字典
    """
    ct = control_type if control_type in _CONTROL_TYPES else "STABLE"
    return dict(_merged_cache.get((metric_code, ct), {}))


# ---------------------------------------------------------------------------
# DB 加载与运行时应用
# ---------------------------------------------------------------------------


async def load_stored_config(db: AsyncSession) -> dict[str, Any]:
    """从 algorithm_parameter 表加载全部配置.

    Returns:
        ``{metric_code: {control_type: {param: value}}}`` 字典
    """
    result = await db.execute(
        select(AlgorithmParameter).where(AlgorithmParameter.is_enabled.is_(True))
    )
    rows = result.scalars().all()

    stored: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        stored.setdefault(row.metric_code, {})[row.control_type] = dict(row.params or {})
    return stored


async def load_metric_thresholds(db: AsyncSession) -> dict[str, dict[str, Any]]:
    """从 metric_config.threshold JSONB 加载指标级覆盖.

    ``metric_config.threshold`` 原为阈值配置字段，P0-B 复用为算法参数覆盖。
    仅加载与 ``_DEFAULTS`` 中已知 metric_code 相关的行。

    Returns:
        ``{metric_code: {param: value}}`` 字典
    """
    known_metrics = tuple(_DEFAULTS.keys())
    result = await db.execute(
        select(MetricConfig.metric_code, MetricConfig.threshold).where(
            MetricConfig.metric_code.in_(known_metrics),
            MetricConfig.threshold.is_not(None),
        )
    )
    thresholds: dict[str, dict[str, Any]] = {}
    for row in result.all():
        mc = row.metric_code
        if row.threshold and isinstance(row.threshold, dict):
            thresholds[mc] = dict(row.threshold)
    return thresholds


async def preload_algorithm_params(db: AsyncSession) -> None:
    """lifespan + worker_process_init 预载：从 DB 加载并合并到进程内缓存."""
    global _merged_cache
    table_overrides = await load_stored_config(db)
    metric_thresholds = await load_metric_thresholds(db)
    _merged_cache = _rebuild_merged(table_overrides, metric_thresholds)
    logger.info(
        "算法参数配置已预载: metrics=%s, table_overridden=%s, metric_thresholds=%s",
        sorted(_DEFAULTS.keys()),
        {k: sorted(v.keys()) for k, v in table_overrides.items()},
        sorted(metric_thresholds.keys()),
    )


def apply_runtime(
    table_overrides: dict[str, dict[str, dict[str, Any]]],
    metric_thresholds: dict[str, dict[str, Any]] | None = None,
) -> None:
    """配置保存后刷新运行时缓存（不查库）.

    Args:
        table_overrides: 从 ``algorithm_parameter`` 表加载的覆盖
        metric_thresholds: 从 ``metric_config.threshold`` 加载的覆盖（可选）
    """
    global _merged_cache
    _merged_cache = _rebuild_merged(table_overrides, metric_thresholds or {})
    logger.info(
        "算法参数运行时缓存已刷新: %d 个 (metric, control_type) 组合",
        len(_merged_cache),
    )


def build_merged_view() -> dict[str, Any]:
    """构建 API 返回的合并视图（含默认值 + 覆盖标记）.

    Returns:
        ``{metric_code: {control_type: {params: {...}, defaults: {...}, overridden: bool}}}``
    """
    view: dict[str, Any] = {}
    for metric_code, ct_map in _DEFAULTS.items():
        view[metric_code] = {}
        for ct in _CONTROL_TYPES:
            defaults = dict(ct_map.get(ct, {}))
            merged = dict(_merged_cache.get((metric_code, ct), defaults))
            overridden = merged != defaults
            view[metric_code][ct] = {
                "params": merged,
                "defaults": defaults,
                "overridden": overridden,
            }
    return view


def _now_naive() -> datetime:
    """当前 UTC naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)
