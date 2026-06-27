#!/usr/bin/env python3
"""CLPM 实时仿真器：模拟 27 个控制回路的实时运行数据。

从 PostgreSQL 动态加载 27 个控制回路（3 单元 × 9 回路），按 1Hz 持续生成
实时运行数据（PV/SP/OP/MODE/PID_P/PID_I/PID_D/pv_quality），批量写入 TDengine，
用于测试 KPI 计算与诊断功能。

特性：
    - FOPDT 物理模型（一阶滞后 + 纯滞后）
    - 增量式 PID 控制器（含抗积分饱和 / 积分分离）
    - 8 种场景：normal/oscillation/saturation/slow_response/
      valve_stiction/manual/overaggressive/overconservative
    - 4 类异常注入：spike/flatline/out_of_range/bad_quality
    - 1Hz 主循环，每秒批量写入 27 行
    - 支持 Ctrl+C 优雅退出

用法::

    # 启动仿真（1Hz 持续运行）
    cd backend && uv run python scripts/realtime_simulator.py

    # 指定场景分布
    cd backend && uv run python scripts/realtime_simulator.py \\
        --scenario-distribution normal:15,oscillation:3,saturation:2,slow_response:2,manual:2,overaggressive:1,overconservative:1,valve_stiction:1

    # 指定运行时长（秒）
    cd backend && uv run python scripts/realtime_simulator.py --duration 3600

    # 禁用异常注入
    cd backend && uv run python scripts/realtime_simulator.py --no-anomaly

    # 设置 SP 变化间隔（秒）
    cd backend && uv run python scripts/realtime_simulator.py --sp-interval 300

    # 详细日志
    cd backend && uv run python scripts/realtime_simulator.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import re
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import AsyncSessionLocal, engine

# ============================================================================
# 常量
# ============================================================================

SAMPLE_INTERVAL = 1.0  # 1Hz 采样间隔（秒）

# 3 个目标单元（脱甲烷精馏 / 醛化反应 / 急冷分离）
TARGET_UNIT_IDS: list[str] = [
    "3353a2b2-2d4f-4907-9964-fb2aac837352",
    "07f43143-4f47-4f31-869c-bcdae8ecd865",
    "ad6a0993-0e83-4645-87f8-edecd2c85356",
]

# 默认场景分布（合计 27）
DEFAULT_SCENARIO_DISTRIBUTION: dict[str, int] = {
    "normal": 15,
    "oscillation": 3,
    "saturation": 2,
    "slow_response": 2,
    "manual": 2,
    "overaggressive": 1,
    "overconservative": 1,
    "valve_stiction": 1,
}

ALL_SCENARIOS = (
    "normal",
    "oscillation",
    "saturation",
    "slow_response",
    "valve_stiction",
    "manual",
    "overaggressive",
    "overconservative",
)

# TDengine REST API URL（端口 = native + 11）
_TD_REST_PORT = settings.TDENGINE_PORT + 11
TD_REST_BASE = f"http://{settings.TDENGINE_HOST}:{_TD_REST_PORT}/rest/sql"
TD_REST_DB_URL = f"{TD_REST_BASE}/{settings.TDENGINE_DB}"

logger = logging.getLogger("realtime_simulator")

random.seed(42)


# ============================================================================
# 工具函数（与 simulate_unit_loops.py 保持一致）
# ============================================================================

def subtable_name(tag_name: str) -> str:
    """回路位号 → TDengine 子表名。

    示例: 41FIC40504_PIDA → d_loop_41fic40504_pida
    """
    name = tag_name.lower().replace("-", "_").replace(".", "_")
    name = re.sub(r"_+", "_", name)
    return "d_loop_" + name


def fmt_ts_utc(dt: datetime) -> str:
    """格式化 UTC 时间戳为 TDengine 字符串（毫秒精度）。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def fmt_float(v: float | None) -> str:
    """格式化浮点数用于 SQL，处理 NaN/Inf。"""
    if v is None or math.isnan(v) or math.isinf(v):
        return "NULL"
    return f"{v:.4f}"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def infer_control_type(tag_name: str) -> str:
    """根据位号前缀推断控制类型。

    FIC→FLOW(流量), LIC→LEVEL(液位), PIC→PRESSURE(压力), TIC→TEMPERATURE(温度)
    其他默认 STABLE。
    """
    upper = tag_name.upper()
    if "FIC" in upper:
        return "FLOW"
    if "LIC" in upper:
        return "LEVEL"
    if "PIC" in upper:
        return "PRESSURE"
    if "TIC" in upper:
        return "TEMPERATURE"
    return "STABLE"


# ============================================================================
# 控制类型 → 动态特性参数
# ============================================================================

TYPE_PARAMS: dict[str, dict[str, Any]] = {
    "FLOW": {
        "tau": 8.0,            # 一阶滞后时间常数（秒）
        "theta_ratio": 0.1,    # 纯滞后 = tau * ratio
        "noise_pct": 0.005,    # 噪声占量程比例（0.5%）
        "base_sp_range": (40.0, 200.0),   # t/h
        "pv_range_pct": 1.0,
    },
    "LEVEL": {
        "tau": 120.0,
        "theta_ratio": 0.1,
        "noise_pct": 0.003,    # 0.3%
        "base_sp_range": (35.0, 75.0),    # %
        "pv_range_pct": 100.0,
    },
    "PRESSURE": {
        "tau": 25.0,
        "theta_ratio": 0.1,
        "noise_pct": 0.004,    # 0.4%
        "base_sp_range": (0.3, 3.5),      # MPa
        "pv_range_pct": 1.0,
    },
    "TEMPERATURE": {
        "tau": 60.0,
        "theta_ratio": 0.1,
        "noise_pct": 0.003,    # 0.3%
        "base_sp_range": (80.0, 380.0),   # °C
        "pv_range_pct": 1.0,
    },
    "STABLE": {
        "tau": 45.0,
        "theta_ratio": 0.1,
        "noise_pct": 0.004,
        "base_sp_range": (40.0, 120.0),
        "pv_range_pct": 1.0,
    },
}

# 控制类型 → 默认 PID 参数
TYPE_PID: dict[str, tuple[float, float, float]] = {
    "FLOW": (2.0, 10.0, 0.0),
    "LEVEL": (1.0, 60.0, 0.0),
    "PRESSURE": (1.2, 20.0, 2.0),
    "TEMPERATURE": (1.5, 30.0, 5.0),
    "STABLE": (1.5, 30.0, 2.0),
}

# 场景 → PID 参数缩放因子（manual / saturation / valve_stiction 不依赖 PID）
SCENARIO_PID_SCALE: dict[str, tuple[float, float, float]] = {
    "normal": (1.0, 1.0, 1.0),
    "oscillation": (3.0, 1.0, 1.0),         # Kp×3 → 振荡
    "saturation": (1.0, 1.0, 1.0),
    "slow_response": (0.3, 0.3, 1.0),       # Kp×0.3, Ki×0.3 → 慢响应
    "valve_stiction": (1.0, 1.0, 1.0),
    "manual": (1.0, 1.0, 1.0),
    "overaggressive": (2.0, 1.0, 3.0),      # Kp×2, Kd×3 → 超调
    "overconservative": (0.5, 0.2, 1.0),    # Kp×0.5, Ki×0.2 → 迟缓
}


# ============================================================================
# FOPDT 物理模型（一阶滞后 + 纯滞后）
# ============================================================================

class FOPDTModel:
    """FOPDT（First-Order Plus Dead Time）物理模型。

    PV = K * (1 - exp(-dt/tau)) * SP_delayed + PV_prev * exp(-dt/tau) + noise

    参数：
        K:         增益（默认 1.0）
        tau:       时间常数（秒）
        theta:     纯滞后（秒）
        noise_pct: 噪声占量程比例
        pv_range:  PV 量程（用于计算噪声幅度）
    """

    def __init__(
        self,
        tau: float,
        theta: float,
        noise_pct: float,
        pv_range: float,
        k: float = 1.0,
    ) -> None:
        self.k = k
        self.tau = max(tau, 0.1)
        self.theta = max(theta, 0.0)
        self.noise_pct = noise_pct
        self.pv_range = max(pv_range, 1.0)
        # 延迟队列：保存 theta 秒内的 SP 输入（按 1Hz 采样）
        delay_len = max(1, int(round(self.theta / SAMPLE_INTERVAL)))
        self._delay_queue: deque[float] = deque(maxlen=delay_len)
        self._noise_sigma = self.pv_range * self.noise_pct

    def step(self, sp_input: float, pv_prev: float) -> float:
        """单步推进，返回新 PV。

        sp_input: 当前 SP（控制输出 OP 对应的稳态目标）
        pv_prev:  上一步 PV
        """
        # 入队当前输入
        self._delay_queue.append(sp_input)
        # 取出 theta 秒前的输入
        sp_delayed = self._delay_queue[0] if len(self._delay_queue) >= self._delay_queue.maxlen else sp_input

        dt = SAMPLE_INTERVAL
        alpha = 1.0 - math.exp(-dt / self.tau)
        pv = self.k * alpha * sp_delayed + pv_prev * (1.0 - alpha)
        # 叠加高斯噪声
        if self._noise_sigma > 0:
            pv += random.gauss(0.0, self._noise_sigma)
        return pv


# ============================================================================
# PID 控制器（位置式 + 抗积分饱和 / 积分分离）
# ============================================================================

class PIDController:
    """标准位置式 PID 控制器。

    OP = Kp * e + Ki * ∫e * dt + Kd * de/dt

    特性：
        - 输出限幅: 0-100%
        - 抗积分饱和: 积分分离（误差大时不积分） + 输出饱和时停止积分
    """

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        output_min: float = 0.0,
        output_max: float = 100.0,
        integral_separation_threshold: float = 0.3,  # 误差占量程比例阈值
        pv_range: float = 100.0,
    ) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.integral_separation_threshold = integral_separation_threshold
        self.pv_range = max(pv_range, 1.0)
        self._integral = 0.0
        self._prev_error = 0.0
        self._first_tick = True

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_error = 0.0
        self._first_tick = True

    def compute(self, sp: float, pv: float, dt: float = SAMPLE_INTERVAL) -> float:
        """计算 PID 输出 OP（0-100）。"""
        error = sp - pv
        err_pct = abs(error) / self.pv_range

        # 积分分离：误差过大时停止积分
        if err_pct > self.integral_separation_threshold:
            new_integral = self._integral  # 不累加
        else:
            new_integral = self._integral + error * dt

        # 微分项（基于误差变化）
        if self._first_tick:
            derivative = 0.0
            self._first_tick = False
        else:
            derivative = (error - self._prev_error) / dt

        # 计算候选输出
        output = self.kp * error + self.ki * new_integral + self.kd * derivative

        # 抗积分饱和：若输出已饱和且积分仍在加剧饱和方向，则不更新积分
        if output > self.output_max:
            output = self.output_max
            if error > 0:
                pass  # 不更新 integral
            else:
                self._integral = new_integral
        elif output < self.output_min:
            output = self.output_min
            if error < 0:
                pass
            else:
                self._integral = new_integral
        else:
            self._integral = new_integral

        self._prev_error = error
        return clamp(output, self.output_min, self.output_max)


# ============================================================================
# 异常注入器（实时版，按事件调度）
# ============================================================================

class RealtimeAnomalyInjector:
    """实时异常注入器。

    事件类型：
        spike:        PV 突变到极端值，每小时 1-2 次，持续 1-2 秒
        flatline:     PV 保持不变，每小时 0-1 次，持续 10-30 秒
        out_of_range: PV 超出量程 ±20%，每小时 0-1 次，持续 5-10 秒
        bad_quality:  pv_quality=0，每小时 0-1 次，持续 3-5 秒
    """

    def __init__(
        self,
        pv_range: float,
        base_sp: float,
        range_min: float,
        range_max: float,
        enabled: bool = True,
    ) -> None:
        self.pv_range = max(pv_range, 1.0)
        self.base_sp = base_sp
        self.range_min = range_min
        self.range_max = range_max
        self.enabled = enabled

        # 当前活动事件：type → (end_time, value)
        self._active: dict[str, tuple[float, float | None]] = {}
        # 下次事件调度时间
        self._next_schedule: dict[str, float] = {
            "spike": time.monotonic() + random.uniform(30, 3600),
            "flatline": time.monotonic() + random.uniform(60, 3600),
            "out_of_range": time.monotonic() + random.uniform(120, 3600),
            "bad_quality": time.monotonic() + random.uniform(180, 3600),
        }
        # flatline 锁定值
        self._flatline_value: float | None = None

    def _schedule_next(self, event_type: str) -> None:
        """调度下一次事件。

        spike 每小时 1-2 次（间隔 1800-3600s）；
        flatline / out_of_range / bad_quality 每小时 0-1 次（间隔 3600-7200s）。
        """
        now = time.monotonic()
        if event_type == "spike":
            interval = random.uniform(1800, 3600)
        elif event_type in ("flatline", "out_of_range", "bad_quality"):
            # 50% 概率跳过本次（即 0 次/小时），否则 1 次/小时
            if random.random() < 0.5:
                interval = random.uniform(7200, 10800)
            else:
                interval = random.uniform(3600, 7200)
        else:
            interval = 3600.0
        self._next_schedule[event_type] = now + interval

    def apply(self, pv: float) -> tuple[float, int]:
        """对当前 PV 应用异常注入，返回 (pv_after, quality)。

        quality: 1=Good, 0=Bad, 2=Uncertain
        """
        if not self.enabled:
            return pv, 1

        now = time.monotonic()
        quality = 1

        # 1. 检查并触发新事件
        for event_type, next_t in list(self._next_schedule.items()):
            if now >= next_t and event_type not in self._active:
                self._trigger_event(event_type)

        # 2. 清理过期事件
        expired = [k for k, (end_t, _) in self._active.items() if now >= end_t]
        for k in expired:
            if k == "flatline":
                self._flatline_value = None
            self._active.pop(k, None)
            self._schedule_next(k)

        # 3. 应用活动事件
        # bad_quality（仅影响质量戳）
        if "bad_quality" in self._active:
            quality = 0

        # spike
        if "spike" in self._active:
            _, spike_val = self._active["spike"]
            if spike_val is not None:
                pv = spike_val
            if quality == 1:
                quality = 0  # 尖峰视为 Bad

        # flatline
        if "flatline" in self._active:
            if self._flatline_value is not None:
                pv = self._flatline_value
            if quality == 1:
                quality = 2  # 停滞视为 Uncertain

        # out_of_range
        if "out_of_range" in self._active:
            _, oor_val = self._active["out_of_range"]
            if oor_val is not None:
                pv = oor_val
            if quality == 1:
                quality = 2

        return round(pv, 4), quality

    def _trigger_event(self, event_type: str) -> None:
        """触发一个异常事件。"""
        now = time.monotonic()
        if event_type == "spike":
            duration = random.uniform(1, 2)
            # 突变到极端值（量程外 5-10 倍方向）
            magnitude = self.pv_range * random.uniform(5, 10) * random.choice([-1, 1])
            spike_val = self.base_sp + magnitude
            self._active["spike"] = (now + duration, spike_val)
        elif event_type == "flatline":
            duration = random.uniform(10, 30)
            self._flatline_value = self.base_sp + random.uniform(
                -self.pv_range * 0.05, self.pv_range * 0.05
            )
            self._active["flatline"] = (now + duration, None)
        elif event_type == "out_of_range":
            duration = random.uniform(5, 10)
            # 超出量程 ±20%
            offset = self.pv_range * random.uniform(0.2, 0.4) * random.choice([-1, 1])
            oor_val = self.base_sp + offset
            self._active["out_of_range"] = (now + duration, oor_val)
        elif event_type == "bad_quality":
            duration = random.uniform(3, 5)
            self._active["bad_quality"] = (now + duration, None)


# ============================================================================
# 回路仿真器
# ============================================================================

class LoopSimulator:
    """单回路实时仿真器。

    封装 FOPDT 模型 + PID 控制器 + 场景逻辑 + 异常注入。
    """

    def __init__(self, cfg: dict[str, Any], anomaly_enabled: bool = True, sp_interval: int = 600) -> None:
        self.cfg = cfg
        self.tag_name: str = cfg["tag_name"]
        self.loop_id: str = cfg["id"]
        self.unit_id: str = cfg["unit_id"]
        self.control_type: str = cfg["control_type"]
        self.scenario: str = cfg["scenario"]
        self.pv_range: float = cfg["pv_range"]
        self.range_min: float = cfg["range_min"]
        self.range_max: float = cfg["range_max"]
        self.base_sp: float = cfg["base_sp"]

        # 状态
        self._pv: float = cfg["base_pv"]
        self._op: float = cfg["base_op"]
        self._sp: float = cfg["base_sp"]
        self._mode: int = 0 if self.scenario == "manual" else 1
        self._pid_p: float = cfg["pid_p"]
        self._pid_i: float = cfg["pid_i"]
        self._pid_d: float = cfg["pid_d"]

        # SP 调度
        self._sp_interval_min = max(60, int(sp_interval * 0.5))
        self._sp_interval_max = max(self._sp_interval_min + 60, sp_interval * 3)
        self._next_sp_change = time.monotonic() + random.uniform(
            self._sp_interval_min, self._sp_interval_max
        )
        # 饱和场景：SP 设为极端值
        if self.scenario == "saturation":
            if random.random() < 0.5:
                self._sp = self.range_max * 0.95
            else:
                self._sp = self.range_min + (self.range_max - self.range_min) * 0.05

        # FOPDT 模型
        params = TYPE_PARAMS[self.control_type]
        tau = params["tau"]
        theta = tau * params["theta_ratio"]
        self._fopdt = FOPDTModel(
            tau=tau,
            theta=theta,
            noise_pct=params["noise_pct"],
            pv_range=self.pv_range,
            k=1.0,
        )

        # PID 控制器
        kp_base, ki_base, kd_base = TYPE_PID[self.control_type]
        scale = SCENARIO_PID_SCALE.get(self.scenario, (1.0, 1.0, 1.0))
        kp = kp_base * scale[0]
        ki = ki_base * scale[1]
        kd = kd_base * scale[2]
        self._pid = PIDController(
            kp=kp,
            ki=ki,
            kd=kd,
            output_min=0.0,
            output_max=100.0,
            pv_range=self.pv_range,
        )

        # 异常注入器
        self._anomaly = RealtimeAnomalyInjector(
            pv_range=self.pv_range,
            base_sp=self.base_sp,
            range_min=self.range_min,
            range_max=self.range_max,
            enabled=anomaly_enabled,
        )

        # 阀门粘滞状态
        self._stiction_band = 3.0
        self._stiction_last_op_jump = self._op

        # 手动模式 OP 目标
        self._manual_op_target = self._op
        self._manual_next_change = time.monotonic() + random.uniform(3600, 10800)

        # 过激进场景的 SP 变化追踪
        self._last_sp = self._sp
        self._sp_change_time = time.monotonic()

    def _maybe_change_sp(self) -> None:
        """根据场景更新 SP。"""
        now = time.monotonic()

        # manual 场景：SP 不变
        if self.scenario == "manual":
            return

        # saturation 场景：SP 保持极端值，偶尔切换方向
        if self.scenario == "saturation":
            if now >= self._next_sp_change:
                if self._sp > (self.range_min + self.range_max) / 2:
                    self._sp = self.range_min + (self.range_max - self.range_min) * 0.05
                else:
                    self._sp = self.range_max * 0.95
                self._next_sp_change = now + random.uniform(
                    self._sp_interval_min, self._sp_interval_max
                )
            return

        # 其他场景：每 sp_interval*0.5 ~ sp_interval*3 秒变化一次
        if now >= self._next_sp_change:
            change_pct = random.uniform(0.05, 0.15)  # 量程的 5-15%
            direction = random.choice([-1, 1])
            new_sp = self._sp + direction * self.pv_range * change_pct
            new_sp = clamp(new_sp, self.range_min + self.pv_range * 0.1,
                           self.range_max - self.pv_range * 0.1)
            self._sp = round(new_sp, 4)
            self._next_sp_change = now + random.uniform(
                self._sp_interval_min, self._sp_interval_max
            )

    def _compute_target_pv_from_op(self, op: float) -> float:
        """根据 OP 反推 FOPDT 输入（用于闭环）。"""
        # OP 0-100 → 稳态 PV 映射：以 base_sp 为中点
        return self.base_sp + (op - 50.0) * (self.pv_range * 0.01)

    def tick(self) -> tuple[float, float, float, int, float, float, float, int]:
        """单步推进（1 秒），返回 (pv, sp, op, mode, pid_p, pid_i, pid_d, quality)。"""
        now = time.monotonic()

        # 1. 更新 SP
        self._maybe_change_sp()

        # 2. 场景化计算 OP 和 PV
        if self.scenario == "manual":
            # 手动模式：OP 由操作员阶跃调节，PV 跟随 OP
            if now >= self._manual_next_change:
                self._manual_op_target = clamp(
                    self._op + random.uniform(-15, 15), 10, 90
                )
                self._manual_next_change = now + random.uniform(3600, 10800)
            # OP 缓慢趋近目标
            self._op = clamp(
                self._op + (self._manual_op_target - self._op) * 0.1, 0, 100
            )
            target_input = self._compute_target_pv_from_op(self._op)
            self._pv = self._fopdt.step(target_input, self._pv)
            quality = 1

        elif self.scenario == "valve_stiction":
            # 阀门粘滞：OP 阶跃式变化，PV 不响应小幅 OP 变化
            error = self._sp - self._pv
            desired_op = self._op + error * 0.15
            if abs(desired_op - self._stiction_last_op_jump) >= self._stiction_band:
                self._op = clamp(desired_op, 0, 100)
                self._stiction_last_op_jump = self._op
            else:
                # OP 不变，PV 因粘滞而停滞（仅噪声小幅波动）
                pass
            target_input = self._compute_target_pv_from_op(self._op)
            # 粘滞时 PV 响应极慢
            self._pv = self._pv + (target_input - self._pv) * 0.02
            if TYPE_PARAMS[self.control_type]["noise_pct"] > 0:
                self._pv += random.gauss(0, self.pv_range * 0.001)
            quality = 1

        else:
            # 闭环 PID 控制
            if self._mode == 1:
                op = self._pid.compute(self._sp, self._pv)
                self._op = op
            target_input = self._compute_target_pv_from_op(self._op)
            self._pv = self._fopdt.step(target_input, self._pv)
            quality = 1

        # 3. 异常注入
        self._pv, quality = self._anomaly.apply(self._pv)

        # 4. SP 变化跟踪（用于过激进场景衰减）
        if self._sp != self._last_sp:
            self._last_sp = self._sp
            self._sp_change_time = now

        return (
            round(self._pv, 4),
            round(self._sp, 4),
            round(self._op, 4),
            self._mode,
            self._pid_p,
            self._pid_i,
            self._pid_d,
            quality,
        )


# ============================================================================
# PostgreSQL 加载回路配置
# ============================================================================

async def load_loops_from_db(
    scenario_distribution: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """从 PostgreSQL 加载 3 个单元的全部控制回路，并分配场景。"""
    loops_raw: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("""
            SELECT l.id, l.tag_name, l.description, l.unit_id,
                   p.name AS unit_name
            FROM loop_ledger l
            JOIN plant_node p ON l.unit_id = p.id
            WHERE l.unit_id = ANY(:unit_ids)
              AND l.is_active = TRUE
            ORDER BY l.unit_id, l.tag_name
        """), {"unit_ids": TARGET_UNIT_IDS})
        rows = r.fetchall()

        for loop_id, tag_name, desc, unit_id, unit_name in rows:
            ctype = infer_control_type(tag_name)
            params = TYPE_PARAMS[ctype]
            h = abs(hash(tag_name))
            sp_lo, sp_hi = params["base_sp_range"]
            base_sp = round(sp_lo + (h % 10000) / 10000 * (sp_hi - sp_lo), 2)
            pv_range = (
                base_sp * params["pv_range_pct"]
                if params["pv_range_pct"] <= 1.0
                else params["pv_range_pct"]
            )
            base_pv = round(base_sp + random.uniform(-pv_range * 0.01, pv_range * 0.01), 2)
            base_op = round(random.uniform(35, 65), 2)
            range_min = 0.0
            range_max = pv_range * 1.2 if ctype != "LEVEL" else 100.0
            pid_p, pid_i, pid_d = TYPE_PID[ctype]

            loops_raw.append({
                "id": str(loop_id),
                "tag_name": tag_name,
                "description": desc or f"{tag_name} 控制回路",
                "unit_id": str(unit_id),
                "unit_name": unit_name,
                "control_type": ctype,
                "tau": params["tau"],
                "noise_pct": params["noise_pct"],
                "base_sp": base_sp,
                "base_pv": base_pv,
                "base_op": base_op,
                "pv_range": pv_range,
                "range_min": range_min,
                "range_max": range_max,
                "pid_p": pid_p,
                "pid_i": pid_i,
                "pid_d": pid_d,
            })

    # 场景分配
    n_loops = len(loops_raw)
    if scenario_distribution is None:
        # 默认按控制类型轮选（保证多样性）
        type_scenarios: dict[str, list[str]] = {
            "FLOW": ["normal", "oscillation", "valve_stiction", "normal"],
            "LEVEL": ["normal", "saturation", "manual", "normal"],
            "PRESSURE": ["normal", "overconservative", "normal"],
            "TEMPERATURE": ["normal", "overaggressive", "overconservative", "normal"],
            "STABLE": ["normal", "oscillation", "slow_response", "normal"],
        }
        for idx, cfg in enumerate(loops_raw):
            scenarios = type_scenarios.get(cfg["control_type"], ["normal"])
            cfg["scenario"] = scenarios[idx % len(scenarios)]
    else:
        # 按用户指定分布分配
        scenario_list: list[str] = []
        for s_name, count in scenario_distribution.items():
            scenario_list.extend([s_name] * count)
        # 不足补 normal，过多截断
        while len(scenario_list) < n_loops:
            scenario_list.append("normal")
        scenario_list = scenario_list[:n_loops]
        random.shuffle(scenario_list)
        for idx, cfg in enumerate(loops_raw):
            cfg["scenario"] = scenario_list[idx]

    return loops_raw


# ============================================================================
# TDengine 操作
# ============================================================================

async def td_execute(
    client: httpx.AsyncClient,
    sql: str,
    use_db: bool = True,
    retries: int = 3,
) -> dict | None:
    """执行 TDengine REST SQL。"""
    url = TD_REST_DB_URL if use_db else TD_REST_BASE
    for attempt in range(retries):
        try:
            resp = await client.post(
                url, content=sql.encode("utf-8"), headers={"Content-Type": "text/plain"}
            )
            result = resp.json()
            if result.get("code") == 0:
                return result
            desc = result.get("desc", "未知错误")
            if "already exists" in desc.lower():
                return result
            if attempt == retries - 1:
                logger.error("TDengine SQL 错误: %s", desc[:200])
                return None
            await asyncio.sleep(2 ** attempt)
        except Exception as exc:
            if attempt == retries - 1:
                logger.error("TDengine 请求异常: %s", exc)
                return None
            await asyncio.sleep(2 ** attempt)
    return None


async def setup_tdengine(client: httpx.AsyncClient, loops: list[dict[str, Any]]) -> None:
    """确保数据库、超表、子表存在。"""
    await td_execute(
        client,
        "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'",
        use_db=False,
    )
    await td_execute(client, """
        CREATE STABLE IF NOT EXISTS st_loop_data (
            ts          TIMESTAMP,
            pv          FLOAT,
            sp          FLOAT,
            op          FLOAT,
            mode        TINYINT,
            pid_p       FLOAT,
            pid_i       FLOAT,
            pid_d       FLOAT,
            pv_quality  TINYINT
        ) TAGS (
            loop_id     BINARY(36),
            unit_id     BINARY(36)
        )
    """)
    for cfg in loops:
        sub = subtable_name(cfg["tag_name"])
        await td_execute(
            client,
            f"CREATE TABLE IF NOT EXISTS {sub} "
            f"USING st_loop_data TAGS ('{cfg['id']}', '{cfg['unit_id']}')",
        )
    logger.info("TDengine 子表就绪（%d 张）", len(loops))


async def write_batch_to_tdengine(
    client: httpx.AsyncClient,
    loops: list[LoopSimulator],
    batch: list[tuple[float, float, float, int, float, float, float, int]],
    ts: datetime,
) -> bool:
    """批量写入一秒内的 27 行数据。

    使用 TDengine 多表批量 INSERT 语法：
        INSERT INTO t1 VALUES (...) t2 VALUES (...) ...
    （子表已在 setup_tdengine 阶段创建，无需 USING/TAGS）
    """
    ts_str = fmt_ts_utc(ts)
    parts: list[str] = ["INSERT INTO"]
    for sim, data in zip(loops, batch):
        sub = subtable_name(sim.tag_name)
        pv, sp, op, mode, pid_p, pid_i, pid_d, quality = data
        parts.append(
            f"{sub} VALUES "
            f"('{ts_str}', {fmt_float(pv)}, {fmt_float(sp)}, {fmt_float(op)}, "
            f"{mode}, {fmt_float(pid_p)}, {fmt_float(pid_i)}, {fmt_float(pid_d)}, {quality})"
        )
    sql = " ".join(parts)
    result = await td_execute(client, sql)
    return result is not None


# ============================================================================
# 主循环
# ============================================================================

running = True


def _signal_handler(signum: int, frame: Any) -> None:
    """Ctrl+C 信号处理：优雅退出。"""
    global running
    running = False
    logger.info("收到退出信号 (%s)，正在优雅退出...", signum)


async def run(
    loops_cfg: list[dict[str, Any]],
    duration: float | None,
    anomaly_enabled: bool,
    sp_interval: int,
    verbose: bool,
) -> None:
    """主仿真循环。"""
    global running
    running = True

    # 初始化回路仿真器
    loops: list[LoopSimulator] = [
        LoopSimulator(cfg, anomaly_enabled=anomaly_enabled, sp_interval=sp_interval)
        for cfg in loops_cfg
    ]
    logger.info("已初始化 %d 个回路仿真器", len(loops))

    # 场景分布统计
    scenario_counts: dict[str, int] = {}
    for sim in loops:
        scenario_counts[sim.scenario] = scenario_counts.get(sim.scenario, 0) + 1
    logger.info("场景分布: %s", scenario_counts)

    async with httpx.AsyncClient(
        auth=httpx.BasicAuth(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD),
        timeout=httpx.Timeout(30.0),
    ) as client:
        # 确保 TDengine 表就绪
        await setup_tdengine(client, loops_cfg)

        start_time = time.monotonic()
        total_written = 0
        total_failed = 0
        tick_count = 0

        logger.info("仿真启动：1Hz × %d 回路 = %d 行/秒", len(loops), len(loops))
        if duration:
            logger.info("运行时长: %.0f 秒", duration)
        else:
            logger.info("运行时长: 无限（Ctrl+C 退出）")

        while running:
            loop_start = time.time()
            tick_count += 1

            # 1. 每个回路 tick 一次
            batch: list[tuple[float, float, float, int, float, float, float, int]] = []
            for sim in loops:
                data = sim.tick()
                batch.append(data)

            # 2. 批量写入 TDengine
            ts = datetime.now(timezone.utc)
            ok = await write_batch_to_tdengine(client, loops, batch, ts)
            if ok:
                total_written += len(batch)
            else:
                total_failed += len(batch)

            # 3. 进度日志
            if verbose and tick_count % 5 == 0:
                elapsed = time.monotonic() - start_time
                rate = total_written / elapsed if elapsed > 0 else 0
                logger.info(
                    "tick=%d  已写入=%d  失败=%d  速率=%.1f 行/秒  耗时=%.3fs",
                    tick_count, total_written, total_failed, rate, time.time() - loop_start,
                )
            elif tick_count % 60 == 0:
                elapsed = time.monotonic() - start_time
                rate = total_written / elapsed if elapsed > 0 else 0
                logger.info(
                    "tick=%d  已写入=%d  失败=%d  速率=%.1f 行/秒",
                    tick_count, total_written, total_failed, rate,
                )

            # 4. 检查运行时长
            if duration is not None and (time.monotonic() - start_time) >= duration:
                logger.info("达到指定运行时长 %.0f 秒，退出", duration)
                break

            # 5. 等待下一个周期
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, SAMPLE_INTERVAL - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        # 汇总
        elapsed_total = time.monotonic() - start_time
        logger.info("=" * 60)
        logger.info("仿真结束")
        logger.info("  总 tick 数:    %d", tick_count)
        logger.info("  总写入行数:    %d", total_written)
        logger.info("  失败行数:      %d", total_failed)
        logger.info("  总耗时:        %.1f 秒", elapsed_total)
        logger.info("  平均写入速率:  %.1f 行/秒",
                    total_written / elapsed_total if elapsed_total > 0 else 0)
        logger.info("=" * 60)

    await engine.dispose()


# ============================================================================
# CLI
# ============================================================================

def parse_scenario_distribution(s: str) -> dict[str, int]:
    """解析场景分布字符串 'normal:15,oscillation:3' → dict。"""
    result: dict[str, int] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"场景分布格式错误: {part}（应为 name:count）")
        name, count_str = part.split(":", 1)
        name = name.strip()
        if name not in ALL_SCENARIOS:
            raise ValueError(f"未知场景: {name}（合法: {ALL_SCENARIOS}）")
        try:
            count = int(count_str.strip())
        except ValueError:
            raise ValueError(f"场景数量非整数: {count_str}")
        if count < 0:
            raise ValueError(f"场景数量不能为负: {name}={count}")
        result[name] = count
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CLPM 实时仿真器：模拟 27 个控制回路 1Hz 实时数据写入 TDengine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  # 默认启动（1Hz 持续运行）
  uv run python scripts/realtime_simulator.py

  # 指定场景分布
  uv run python scripts/realtime_simulator.py \\
      --scenario-distribution normal:15,oscillation:3,saturation:2

  # 运行 1 小时
  uv run python scripts/realtime_simulator.py --duration 3600

  # 禁用异常注入
  uv run python scripts/realtime_simulator.py --no-anomaly

  # 详细日志
  uv run python scripts/realtime_simulator.py --verbose
""",
    )
    parser.add_argument(
        "--scenario-distribution",
        type=str,
        default=None,
        help="场景分布，格式 'normal:15,oscillation:3,...'（默认自动分配）",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="运行时长（秒，默认无限）",
    )
    parser.add_argument(
        "--no-anomaly",
        action="store_true",
        help="禁用异常注入",
    )
    parser.add_argument(
        "--sp-interval",
        type=int,
        default=600,
        help="SP 变化基础间隔（秒，默认 600，实际 300-1800）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示详细日志（每 5 秒打印一次）",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    # 日志配置
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 抑制 httpx / httpcore / sqlalchemy 的 DEBUG 日志（即使 verbose 也不打印 HTTP/SQL 细节）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    # 信号处理
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print("=" * 70)
    print("  CLPM 实时仿真器")
    print(f"  采样频率: {SAMPLE_INTERVAL} Hz")
    print(f"  运行时长: {args.duration if args.duration else '无限'} 秒")
    print(f"  异常注入: {'禁用' if args.no_anomaly else '启用'}")
    print(f"  SP 间隔:  {args.sp_interval} 秒（基础）")
    print(f"  场景分布: {args.scenario_distribution or '自动分配'}")
    print(f"  详细日志: {'是' if args.verbose else '否'}")
    print("=" * 70)

    # 1. 加载回路配置
    print("\n📋 [1/2] 从 PostgreSQL 加载 3 单元回路配置...")
    scenario_dist = None
    if args.scenario_distribution:
        scenario_dist = parse_scenario_distribution(args.scenario_distribution)
        print(f"  场景分布: {scenario_dist}")
    loops_cfg = await load_loops_from_db(scenario_distribution=scenario_dist)
    print(f"  ✓ 加载 {len(loops_cfg)} 个回路：")
    units_seen: dict[str, list[str]] = {}
    for cfg in loops_cfg:
        units_seen.setdefault(cfg["unit_name"], []).append(
            f"{cfg['tag_name']}({cfg['control_type']}/{cfg['scenario']})"
        )
    for uname, tags in units_seen.items():
        print(f"    {uname}: {len(tags)} 回路")

    if not loops_cfg:
        print("  ⚠ 未加载到任何回路，退出")
        return

    # 2. 启动仿真主循环
    print("\n🚀 [2/2] 启动实时仿真主循环...")
    await run(
        loops_cfg=loops_cfg,
        duration=args.duration,
        anomaly_enabled=not args.no_anomaly,
        sp_interval=args.sp_interval,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")
        sys.exit(0)
