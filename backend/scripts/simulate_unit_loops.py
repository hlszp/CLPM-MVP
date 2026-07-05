#!/usr/bin/env python3
"""27 回路秒级历史数据仿真器（基于 189 测点真实量程）。

从 PostgreSQL 动态加载 3 个工艺单元（脱甲烷精馏/醛化反应/急冷分离）的 27 个控制回路，
读取每个回路 PV 角色的真实量程（range_min/range_max），按 1Hz 采样生成历史时序数据写入 TDengine。

核心特性（对齐用户 8 条仿真要求）：
    1. SP 每 ~4 小时变化一次（3.5-4.5h），幅度 ±5-10%，不频繁变化
    2. MODE 每 ~8 小时变化一次（7-9h），手动模式(0)持续不超过 30 分钟
    3. PID 参数全程不变；微分时间(D)仅温度回路设置非零
    4. PV 跟随 SP 采用物理模型：
       - 流量/压力：一阶纯滞后（FOPDT）
       - 温度：二阶纯滞后（SOPDT）
       - 液位：积分对象
    5. 3 个流量回路模拟阀门卡滞（valve_stiction 场景）
    6. SP/PV/OP 值约束在各自量程范围内
    7. 异常值模拟在量程范围内，PV 异常比例 < 5%
    8. 数据变化速度按控制类型差异化（通过 tau 时间常数体现）：
       流量 tau=2s（1-2s 响应）、压力 tau=3s（2-3s）、液位 tau=5s（3-5s）、温度 tau=8s（5-10s）

用法::

    cd backend && uv run python scripts/simulate_unit_loops.py --clean
    # 默认：2026-06-27 00:00:00 起 72 小时，1Hz，清空 TDengine 后写入

注意：27 回路 × 72h × 1Hz ≈ 700 万行，写入约 5-10 分钟。
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import math
import random
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.db import AsyncSessionLocal, engine

# ============================================================================
# 常量
# ============================================================================

SAMPLE_INTERVAL = 1  # 1Hz 采样间隔（秒）
BATCH_SIZE = 5000  # TDengine 单批写入行数
MAX_CONCURRENT = 8  # TDengine 并发写入数
PROGRESS_INTERVAL = 200_000  # 进度打印间隔（行）

# 默认时间范围：2026-06-27 00:00:00 起 72 小时
DEFAULT_START = datetime(2026, 6, 27, 0, 0, 0)
DEFAULT_HOURS = 72

# 3 个目标单元（脱甲烷精馏/醛化反应/急冷分离）
TARGET_UNIT_IDS: list[str] = [
    "3353a2b2-2d4f-4907-9964-fb2aac837352",  # 脱甲烷精馏单元
    "07f43143-4f47-4f31-869c-bcdae8ecd865",  # 醛化反应单元
    "ad6a0993-0e83-4645-87f8-edecd2c85356",  # 急冷分离单元
]

# 7 个 OPC Tag 角色
TAG_ROLES = ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]
REQUIRED_ROLES = {"PV", "SP", "OP", "MODE"}

# TDengine REST API URL
_TD_REST_PORT = settings.TDENGINE_PORT + 11
TD_REST_BASE = f"http://{settings.TDENGINE_HOST}:{_TD_REST_PORT}/rest/sql"
TD_REST_DB_URL = f"{TD_REST_BASE}/{settings.TDENGINE_DB}"

random.seed(42)


# ============================================================================
# 工具函数
# ============================================================================


def subtable_name(tag_name: str) -> str:
    """回路位号 → TDengine 子表名（P3 #54：复用 app.core.tdengine.make_subtable_name）."""
    from app.core.tdengine import make_subtable_name

    return make_subtable_name(tag_name)


def fmt_ts(dt: datetime) -> str:
    """格式化时间戳为 TDengine 字符串（毫秒精度，带 UTC Z 后缀）。

    TDengine 容器时区为 Asia/Shanghai，无时区标识的字符串会被按本地时区解释，
    导致时间偏移 8 小时。显式标注 Z 后缀确保 TDengine 按 UTC 正确存储，
    与后端查询（tdengine.py 用 isoformat + Z）保持一致。
    """
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def fmt_float(v: float | None) -> str:
    if v is None or math.isnan(v) or math.isinf(v):
        return "NULL"
    return f"{v:.4f}"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def infer_control_type(tag_name: str) -> str:
    """根据位号前缀推断控制类型：FIC→FLOW, LIC→LEVEL, PIC→PRESSURE, TIC→TEMPERATURE。"""
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
# 物理模型：PV 跟随 SP
# ============================================================================


class FOPDTModel:
    """一阶纯滞后模型（First-Order Plus Dead Time）。

    用于流量、压力回路。PV 快速跟随 SP，有纯滞后 theta。
    离散化：PV[n] = PV[n-1] * (1-alpha) + K * SP_delayed * alpha + noise
    其中 alpha = 1 - exp(-dt/tau)
    """

    def __init__(self, tau: float, theta: float, k: float = 1.0, noise_sigma: float = 0.0) -> None:
        self.tau = max(tau, 0.1)
        self.theta = max(theta, 0.0)
        self.k = k
        self.noise_sigma = noise_sigma
        self.dt = float(SAMPLE_INTERVAL)
        self.alpha = 1.0 - math.exp(-self.dt / self.tau)
        delay_len = max(1, int(round(self.theta / self.dt)))
        self._delay_queue: collections.deque[float] = collections.deque(maxlen=delay_len)

    def step(self, sp_input: float, pv_prev: float) -> float:
        # 纯滞后：入队当前 SP，取 theta 秒前的 SP
        self._delay_queue.append(sp_input)
        if len(self._delay_queue) >= self._delay_queue.maxlen:
            sp_delayed = self._delay_queue[0]
        else:
            sp_delayed = sp_input
        pv = pv_prev * (1.0 - self.alpha) + self.k * sp_delayed * self.alpha
        if self.noise_sigma > 0:
            pv += random.gauss(0.0, self.noise_sigma)
        return pv


class SOPDTModel:
    """二阶纯滞后模型（Second-Order Plus Dead Time）。

    用于温度回路。两个一阶环节串联 + 纯滞后，响应更慢，可能有超调。
    G(s) = K / ((tau1*s+1)(tau2*s+1)) * exp(-theta*s)
    """

    def __init__(self, tau: float, theta: float, k: float = 1.0, noise_sigma: float = 0.0) -> None:
        self.tau1 = max(tau, 0.1)
        self.tau2 = max(tau * 0.6, 0.1)  # 第二个时间常数为第一个的 60%
        self.theta = max(theta, 0.0)
        self.k = k
        self.noise_sigma = noise_sigma
        self.dt = float(SAMPLE_INTERVAL)
        self.a1 = 1.0 - math.exp(-self.dt / self.tau1)
        self.a2 = 1.0 - math.exp(-self.dt / self.tau2)
        delay_len = max(1, int(round(self.theta / self.dt)))
        self._delay_queue: collections.deque[float] = collections.deque(maxlen=delay_len)
        self._x1 = 0.0  # 中间状态（第一个一阶环节输出）

    def reset(self, pv_init: float) -> None:
        self._x1 = pv_init
        self._delay_queue.clear()

    def step(self, sp_input: float, pv_prev: float) -> float:
        self._delay_queue.append(sp_input)
        sp_delayed = self._delay_queue[0] if len(self._delay_queue) >= self._delay_queue.maxlen else sp_input
        # 两个一阶环节串联
        self._x1 = self._x1 * (1.0 - self.a1) + sp_delayed * self.a1
        pv = pv_prev * (1.0 - self.a2) + self.k * self._x1 * self.a2
        if self.noise_sigma > 0:
            pv += random.gauss(0.0, self.noise_sigma)
        return pv


class IntegratorModel:
    """积分对象模型。

    用于液位回路。PV 无稳态偏差，持续朝 SP 积分趋近。
    液位变化由 OP（阀门开度）控制的进出料平衡决定：
        dPV/dt = K * (OP - OP_balance) / range
    闭环反馈使 PV 趋向 SP。
    """

    def __init__(self, tau: float, k: float = 1.0, noise_sigma: float = 0.0) -> None:
        self.tau = max(tau, 0.1)
        self.k = k
        self.noise_sigma = noise_sigma
        self.dt = float(SAMPLE_INTERVAL)
        # 积分增益：使响应时间约等于 tau
        self.ki = 1.0 / self.tau

    def step(self, sp_input: float, pv_prev: float, op: float) -> float:
        # 积分对象：PV 朝 SP 积分，同时受 OP 偏差影响
        error = sp_input - pv_prev
        # 积分项：error * ki * dt（朝 SP 趋近）
        # OP 项：OP 偏离 50% 引起的额外变化（模拟进出料不平衡）
        op_imbalance = (op - 50.0) * 0.0005  # 小系数，避免发散
        pv = pv_prev + (error * self.ki * self.dt + op_imbalance) * self.k
        if self.noise_sigma > 0:
            pv += random.gauss(0.0, self.noise_sigma)
        return pv


# ============================================================================
# 控制类型参数：时间常数 / 纯滞后 / 噪声 / PID
# ============================================================================

# 各控制类型的物理模型参数（tau 体现"数据变化间隔"要求）
TYPE_PARAMS: dict[str, dict[str, Any]] = {
    "FLOW": {
        "tau": 2.0,          # 流量：1-2s 响应
        "theta": 0.5,        # 纯滞后 0.5s
        "model": "fopdt",    # 一阶纯滞后
        "noise_pct": 0.005,  # 噪声占量程 0.5%
    },
    "LEVEL": {
        "tau": 5.0,          # 液位：3-5s 响应
        "theta": 0.0,
        "model": "integrator",  # 积分对象
        "noise_pct": 0.003,
    },
    "PRESSURE": {
        "tau": 3.0,          # 压力：2-3s 响应
        "theta": 1.0,        # 纯滞后 1s
        "model": "fopdt",    # 一阶纯滞后
        "noise_pct": 0.004,
    },
    "TEMPERATURE": {
        "tau": 8.0,          # 温度：5-10s 响应
        "theta": 3.0,        # 纯滞后 3s
        "model": "sopdt",    # 二阶纯滞后
        "noise_pct": 0.003,
    },
    "STABLE": {
        "tau": 5.0,
        "theta": 1.0,
        "model": "fopdt",
        "noise_pct": 0.004,
    },
}

# PID 参数（全程不变）；D 仅温度回路非零
TYPE_PID: dict[str, tuple[float, float, float]] = {
    "FLOW": (2.0, 10.0, 0.0),
    "LEVEL": (1.0, 60.0, 0.0),
    "PRESSURE": (1.2, 20.0, 0.0),
    "TEMPERATURE": (1.5, 30.0, 5.0),  # D=5.0 仅温度
    "STABLE": (1.5, 30.0, 0.0),
}

# 阀门卡滞回路（3 个流量回路）
VALVE_STICTION_LOOPS = {
    "41FIC20021_PIDA",
    "41FIC40504_PIDA",
    "80FIC11906_PIDA",
}


# ============================================================================
# 从 PostgreSQL 加载回路（含量程）
# ============================================================================


async def load_loops_from_db() -> list[dict[str, Any]]:
    """从 PostgreSQL 加载 27 回路，读取 PV 角色真实量程。"""
    loops: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as s:
        # 回路基本信息
        r = await s.execute(
            text("""
            SELECT l.id, l.tag_name, l.description, l.unit_id, p.name AS unit_name
            FROM loop_ledger l
            JOIN plant_node p ON l.unit_id = p.id
            WHERE l.unit_id = ANY(:unit_ids) AND l.is_active = TRUE
            ORDER BY l.unit_id, l.tag_name
        """),
            {"unit_ids": TARGET_UNIT_IDS},
        )
        base_rows = r.fetchall()

        # 查询每个回路 PV 角色的量程
        for idx, (loop_id, tag_name, desc, unit_id, unit_name) in enumerate(base_rows):
            ctype = infer_control_type(tag_name)
            params = TYPE_PARAMS[ctype]

            # 读取 PV 角色量程
            pv_r = await s.execute(
                text("""
                SELECT t.range_min, t.range_max, t.unit
                FROM loop_tag_mapping m
                JOIN tag_registry t ON t.id = m.tag_id
                WHERE m.loop_id = :lid AND m.tag_role = 'PV'
            """),
                {"lid": loop_id},
            )
            pv_row = pv_r.fetchone()
            if pv_row and pv_row[1] is not None and pv_row[0] is not None:
                range_min = float(pv_row[0])
                range_max = float(pv_row[1])
                pv_unit = pv_row[2] or ""
            else:
                # 兜底量程
                range_min, range_max, pv_unit = 0.0, 100.0, ""

            pv_range = range_max - range_min
            # base_sp：量程 40%-60% 范围内，基于 tag_name hash 确定性生成
            h = abs(hash(tag_name))
            base_sp = round(range_min + pv_range * (0.4 + (h % 2000) / 2000 * 0.2), 4)
            base_pv = round(base_sp + random.uniform(-pv_range * 0.01, pv_range * 0.01), 4)
            base_op = round(random.uniform(40, 60), 2)

            # PID 参数（按控制类型，D 仅温度）
            pid_p, pid_i, pid_d = TYPE_PID[ctype]

            # 场景分配
            if tag_name in VALVE_STICTION_LOOPS:
                scenario = "valve_stiction"
            else:
                scenario = _assign_scenario(idx, ctype)

            loops.append(
                {
                    "id": loop_id,
                    "tag_name": tag_name,
                    "description": desc or f"{tag_name} 控制回路",
                    "unit_id": unit_id,
                    "unit_name": unit_name,
                    "control_type": ctype,
                    "scenario": scenario,
                    "tau": params["tau"],
                    "theta": params["theta"],
                    "model_type": params["model"],
                    "noise_pct": params["noise_pct"],
                    "range_min": range_min,
                    "range_max": range_max,
                    "pv_range": pv_range,
                    "pv_unit": pv_unit,
                    "base_sp": base_sp,
                    "base_pv": base_pv,
                    "base_op": base_op,
                    "pid_p": pid_p,
                    "pid_i": pid_i,
                    "pid_d": pid_d,
                }
            )
    return loops


def _assign_scenario(idx: int, ctype: str) -> str:
    """按索引轮选场景（保证多样性），排除 valve_stiction（已单独指定）。"""
    scenarios_by_type = {
        "FLOW": ["normal", "oscillation", "normal", "op_saturation"],
        "LEVEL": ["normal", "manual", "normal"],
        "PRESSURE": ["normal", "overconservative", "normal"],
        "TEMPERATURE": ["normal", "overaggressive", "overconservative", "normal"],
        "STABLE": ["normal", "oscillation", "normal"],
    }
    scs = scenarios_by_type.get(ctype, ["normal", "oscillation", "normal"])
    return scs[idx % len(scs)]


# ============================================================================
# SP / MODE 调度（PID 全程不变，无需调度）
# ============================================================================


def generate_sp_schedule(
    base_sp: float, range_min: float, range_max: float, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """SP 阶跃调度：每 ~4 小时变化一次（3.5-4.5h），幅度 ±5-10%，约束在量程内。"""
    pv_range = range_max - range_min
    # SP 允许范围：量程的 15%-85%，避免边界
    sp_lo = range_min + pv_range * 0.15
    sp_hi = range_max - pv_range * 0.15
    schedule = [(start, clamp(base_sp, sp_lo, sp_hi))]
    t = start
    while t < end:
        # 每 3.5-4.5 小时变化一次（围绕 4 小时）
        t += timedelta(seconds=random.randint(int(3.5 * 3600), int(4.5 * 3600)))
        if t >= end:
            break
        change_pct = random.uniform(0.05, 0.10)  # 量程的 5-10%
        direction = random.choice([-1, 1])
        new_sp = schedule[-1][1] + direction * pv_range * change_pct
        new_sp = clamp(new_sp, sp_lo, sp_hi)
        schedule.append((t, round(new_sp, 4)))
    return schedule


def generate_mode_schedule(
    scenario: str, start: datetime, end: datetime
) -> list[tuple[datetime, int]]:
    """MODE 调度：每 ~8 小时变化一次（7-9h）。

    语义：0=Manual, 1=Auto, 2=Cascade
    - manual 场景：始终 0
    - 其他场景：在 Auto(1) ↔ Cascade(2) 之间切换，每 7-9 小时一次；
      10% 概率短暂切 Manual(0) 维护，持续 15-30 分钟（不超过 30 分钟）。
    """
    if scenario == "manual":
        return [(start, 0)]

    schedule: list[tuple[datetime, int]] = [(start, 1)]
    t = start
    while t < end:
        # 主切换间隔：7-9 小时（围绕 8 小时）
        t += timedelta(seconds=random.randint(7 * 3600, 9 * 3600))
        if t >= end:
            break
        cur = schedule[-1][1]
        # 10% 概率短暂切 Manual 维护（15-30 分钟）
        if random.random() < 0.10:
            schedule.append((t, 0))
            maintenance_end = t + timedelta(seconds=random.randint(15 * 60, 30 * 60))
            if maintenance_end < end:
                schedule.append((maintenance_end, 1))
                t = maintenance_end
            continue
        # 在 Auto(1) ↔ Cascade(2) 之间切换
        new_mode = 2 if cur == 1 else 1
        schedule.append((t, new_mode))
    return schedule


# ============================================================================
# 场景化 PV/OP 生成（基于物理模型）
# ============================================================================


class LoopSimulator:
    """单回路仿真器：封装物理模型 + 场景逻辑 + 状态。"""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.scenario = cfg["scenario"]
        self.range_min = cfg["range_min"]
        self.range_max = cfg["range_max"]
        self.pv_range = cfg["pv_range"]
        self.tau = cfg["tau"]
        self.theta = cfg["theta"]
        self.noise_sigma = cfg["noise_pct"] * self.pv_range
        self.model_type = cfg["model_type"]

        # 初始化物理模型
        if self.model_type == "sopdt":
            self.model = SOPDTModel(self.tau, self.theta, k=1.0, noise_sigma=self.noise_sigma)
            self.model.reset(cfg["base_pv"])
        elif self.model_type == "integrator":
            self.model = IntegratorModel(self.tau, k=1.0, noise_sigma=self.noise_sigma)
        else:  # fopdt
            self.model = FOPDTModel(self.tau, self.theta, k=1.0, noise_sigma=self.noise_sigma)

        # 阀门卡滞状态
        self._stiction_last_op = cfg["base_op"]
        self._stiction_band = 3.0

        # op_saturation 状态
        self._sat_until = 0.0
        self._norm_until = 0.0

        # manual 状态
        self._manual_next_change = 0.0
        self._manual_op_target = cfg["base_op"]

        # overaggressive 状态
        self._last_sp = cfg["base_sp"]
        self._sp_change_t = 0.0

    def step(self, sp: float, prev_pv: float, prev_op: float, t_sec: float) -> tuple[float, float]:
        """单步仿真，返回 (pv, op)，均约束在量程内。"""
        if self.scenario == "valve_stiction":
            pv, op = self._gen_valve_stiction(sp, prev_pv, prev_op)
        elif self.scenario == "manual":
            pv, op = self._gen_manual(sp, prev_pv, prev_op, t_sec)
        elif self.scenario == "op_saturation":
            pv, op = self._gen_op_saturation(sp, prev_pv, prev_op, t_sec)
        elif self.scenario == "oscillation":
            pv, op = self._gen_oscillation(sp, prev_pv, t_sec)
        elif self.scenario == "overaggressive":
            pv, op = self._gen_overaggressive(sp, prev_pv, t_sec)
        elif self.scenario == "overconservative":
            pv, op = self._gen_overconservative(sp, prev_pv)
        else:  # normal
            pv, op = self._gen_normal(sp, prev_pv, prev_op)

        # 量程约束
        pv = clamp(pv, self.range_min, self.range_max)
        op = clamp(op, 0.0, 100.0)
        return round(pv, 4), round(op, 4)

    def _gen_normal(self, sp: float, prev_pv: float, prev_op: float) -> tuple[float, float]:
        """正常回路：PV 按物理模型跟随 SP，OP 平缓调节。"""
        if self.model_type == "integrator":
            pv = self.model.step(sp, prev_pv, prev_op)
        elif self.model_type == "sopdt":
            pv = self.model.step(sp, prev_pv)
        else:
            pv = self.model.step(sp, prev_pv)
        # OP 缓慢调节（PI 控制）
        error = sp - pv
        op = prev_op + error * 0.02 + random.gauss(0, 0.2)
        return pv, op

    def _gen_oscillation(self, sp: float, prev_pv: float, t_sec: float) -> tuple[float, float]:
        """振荡回路：PV 正弦振荡，周期 ~10 分钟。"""
        amplitude = self.pv_range * 0.05
        period = 600.0
        omega = 2 * math.pi / period
        if self.model_type == "sopdt":
            pv = self.model.step(sp, prev_pv)
        elif self.model_type == "integrator":
            pv = self.model.step(sp, prev_pv, 50.0)
        else:
            pv = self.model.step(sp, prev_pv)
        pv = pv + amplitude * math.sin(omega * t_sec)
        op = 50 + 0.8 * math.sin(omega * t_sec + math.pi) * self.pv_range * 0.01
        return pv, op

    def _gen_valve_stiction(self, sp: float, prev_pv: float, prev_op: float) -> tuple[float, float]:
        """阀门卡滞：OP 阶跃式变化（累积误差超过 stiction_band 才动作），PV 响应极慢。"""
        error = sp - prev_pv
        desired_op = prev_op + error * 0.15
        if abs(desired_op - self._stiction_last_op) >= self._stiction_band:
            op = clamp(desired_op, 0, 100)
            self._stiction_last_op = op
        else:
            op = prev_op  # 粘滞，OP 不变
        # PV 响应极慢（系数远小于正常 alpha）
        if self.model_type == "integrator":
            pv = prev_pv + (sp - prev_pv) * 0.02 * (SAMPLE_INTERVAL / self.tau)
        else:
            pv = prev_pv + (sp - prev_pv) * 0.02 * (SAMPLE_INTERVAL / self.tau)
        pv += random.gauss(0, self.noise_sigma * 0.3)  # 粘滞时噪声更小
        return pv, op

    def _gen_op_saturation(self, sp: float, prev_pv: float, prev_op: float, t_sec: float) -> tuple[float, float]:
        """OP 饱和：OP 长时间停留 95-100% 或 0-5%。"""
        if t_sec < self._sat_until:
            op = clamp(97.0 + random.gauss(0, 0.5), 95, 100)
        elif t_sec < self._norm_until:
            error = sp - prev_pv
            op = prev_op + error * 0.1 + random.gauss(0, 0.2)
        else:
            if random.random() < 0.4:
                self._sat_until = t_sec + random.randint(1800, 3600)
                self._norm_until = 0.0
                op = clamp(97.0 + random.gauss(0, 0.5), 95, 100)
            else:
                self._norm_until = t_sec + random.randint(3600, 7200)
                self._sat_until = 0.0
                error = sp - prev_pv
                op = prev_op + error * 0.1 + random.gauss(0, 0.2)
        # PV 跟随
        if self.model_type == "integrator":
            pv = self.model.step(sp, prev_pv, op)
        elif self.model_type == "sopdt":
            pv = self.model.step(sp, prev_pv)
        else:
            pv = self.model.step(sp, prev_pv)
        return pv, op

    def _gen_overconservative(self, sp: float, prev_pv: float) -> tuple[float, float]:
        """过保守：PV 响应慢，稳态偏差 ~8%。"""
        target_pv = sp - self.pv_range * 0.08 * (1 if sp > prev_pv else -1)
        # 用更大的 tau 模拟慢响应
        slow_alpha = 1.0 - math.exp(-SAMPLE_INTERVAL / (self.tau * 4.0))
        pv = prev_pv + (target_pv - prev_pv) * slow_alpha + random.gauss(0, self.noise_sigma)
        op = 50 + (sp - pv) * 0.02
        return pv, op

    def _gen_overaggressive(self, sp: float, prev_pv: float, t_sec: float) -> tuple[float, float]:
        """过激进：SP 变化后 PV 过冲大，振荡后收敛。"""
        if sp != self._last_sp:
            self._sp_change_t = t_sec
            self._last_sp = sp
        elapsed = t_sec - self._sp_change_t
        overshoot = 0.3
        omega = 0.015
        zeta = 0.2
        if elapsed < 1200:
            decay = math.exp(-zeta * omega * elapsed)
            pv = sp + overshoot * self.pv_range * 0.1 * decay * math.cos(omega * elapsed)
        else:
            pv = sp
        pv += random.gauss(0, self.noise_sigma)
        op = 50 + (sp - pv) * 0.2 + random.gauss(0, 0.4)
        return pv, op

    def _gen_manual(self, sp: float, prev_pv: float, prev_op: float, t_sec: float) -> tuple[float, float]:
        """手动模式：OP 由操作员阶跃调节，PV 跟随 OP。"""
        if t_sec >= self._manual_next_change:
            self._manual_op_target = clamp(prev_op + random.uniform(-15, 15), 10, 90)
            self._manual_next_change = t_sec + random.randint(3600, 10800)
        op = prev_op + (self._manual_op_target - prev_op) * 0.1
        # PV 受 OP 影响（手动模式 SP 不跟随）
        if self.model_type == "integrator":
            pv = self.model.step(sp, prev_pv, op)
        elif self.model_type == "sopdt":
            pv = self.model.step(prev_pv, prev_pv)  # 手动时 SP 不起作用
        else:
            pv = self.model.step(prev_pv, prev_pv)
        return pv, op


# ============================================================================
# 异常值注入（量程内，PV 异常 < 5%）
# ============================================================================


class AnomalyInjector:
    """异常值与质量戳注入器（所有异常值约束在量程范围内，PV 异常比例 < 5%）。

    策略：
        - flatline（停滞）：PV 锁定固定值 30-90 秒，标记 Uncertain
        - bad_cluster（坏质量聚簇）：30-60 秒窗口 pv_quality=0（仅质量，不改 PV 值）
        - uncertain_scatter（不确定散点）：~1% 单点 pv_quality=2
    总异常点比例控制在 < 5%。

    注：spike（尖峰）生成逻辑已于本次数据清洗任务移除，避免再次注入毛刺数据
    （历史毛刺已由 scripts/clean_tdengine_spikes.py 清洗）。
    """

    def __init__(self, cfg: dict, n_points: int) -> None:
        self.cfg = cfg
        self.n = n_points
        self.range_min = cfg["range_min"]
        self.range_max = cfg["range_max"]
        self.pv_range = cfg["pv_range"]
        self.base_sp = cfg["base_sp"]

        # 预生成异常事件（控制总比例 < 5%，spike 已移除）
        self.flatline_events = self._gen_flatlines()
        self.bad_clusters = self._gen_bad_clusters()
        # uncertain 散点 ~0.5%
        n_uncertain = max(1, n_points // 200)
        self.uncertain_indices = set(random.sample(range(n_points), n_uncertain))

    def _gen_flatlines(self) -> list[tuple[int, int, float]]:
        """停滞事件：(起始, 持续秒, 锁定值)。约 0.15%。"""
        events = []
        n_flats = max(1, self.n // 6000)
        for _ in range(n_flats):
            start = random.randint(60, self.n - 300)
            dur = random.randint(20, 60)
            val = self.base_sp + random.uniform(-self.pv_range * 0.05, self.pv_range * 0.05)
            val = clamp(val, self.range_min, self.range_max)
            events.append((start, dur, round(val, 4)))
        return events

    def _gen_bad_clusters(self) -> list[tuple[int, int]]:
        """坏质量聚簇：(起始, 持续秒)。约 1.5%（仅质量戳，不改 PV 值）。"""
        clusters = []
        total_bad = 0
        target_bad = int(self.n * 0.015)
        while total_bad < target_bad:
            start = random.randint(60, self.n - 300)
            dur = random.randint(20, 50)
            clusters.append((start, dur))
            total_bad += dur
        return clusters

    def apply(self, idx: int, pv: float) -> tuple[float, int]:
        """对单点应用异常注入，返回 (pv_after, quality)。quality: 1=Good, 0=Bad, 2=Uncertain。"""
        quality = 1

        # 1. flatline 停滞（spike 已移除）
        for s, dur, val in self.flatline_events:
            if s <= idx < s + dur:
                if quality == 1:
                    quality = 2  # Uncertain
                return val, quality

        # 2. bad_cluster 坏质量聚簇（仅质量戳）
        for s, dur in self.bad_clusters:
            if s <= idx < s + dur:
                return round(pv, 4), 0  # Bad

        # 3. uncertain 散点
        if idx in self.uncertain_indices:
            quality = 2

        return round(pv, 4), quality


# ============================================================================
# 时序数据生成
# ============================================================================


def generate_timeseries(cfg: dict, start: datetime, end: datetime) -> list[tuple]:
    """为单个回路生成完整时间序列。

    返回 list of (ts_str, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
    """
    interval = timedelta(seconds=SAMPLE_INTERVAL)
    sp_schedule = generate_sp_schedule(cfg["base_sp"], cfg["range_min"], cfg["range_max"], start, end)
    mode_schedule = generate_mode_schedule(cfg["scenario"], start, end)

    sp_idx = 0
    cur_sp = sp_schedule[0][1]
    mode_idx = 0
    cur_mode = mode_schedule[0][1]

    # PID 全程不变
    cur_p, cur_i, cur_d = cfg["pid_p"], cfg["pid_i"], cfg["pid_d"]

    prev_pv = cfg["base_pv"]
    prev_op = cfg["base_op"]
    simulator = LoopSimulator(cfg)

    total_sec = int((end - start).total_seconds())
    n_points = total_sec // SAMPLE_INTERVAL + 1
    injector = AnomalyInjector(cfg, n_points)

    points: list[tuple] = []
    t = start
    idx = 0
    t_sec = 0.0

    while t <= end:
        # 更新 SP 调度
        while sp_idx < len(sp_schedule) and sp_schedule[sp_idx][0] <= t:
            cur_sp = sp_schedule[sp_idx][1]
            sp_idx += 1
        # 更新 MODE 调度
        while mode_idx < len(mode_schedule) and mode_schedule[mode_idx][0] <= t:
            cur_mode = mode_schedule[mode_idx][1]
            mode_idx += 1

        # 场景化 PV/OP 生成（物理模型）
        pv, op = simulator.step(cur_sp, prev_pv, prev_op, t_sec)

        # 异常值 + 质量戳注入
        pv, pv_quality = injector.apply(idx, pv)

        points.append((fmt_ts(t), pv, cur_sp, op, cur_mode, cur_p, cur_i, cur_d, pv_quality))

        prev_pv = pv
        prev_op = op
        t += interval
        idx += 1
        t_sec += SAMPLE_INTERVAL

    return points


# ============================================================================
# PostgreSQL 元数据补全
# ============================================================================


async def setup_postgres(loops: list[dict[str, Any]]) -> None:
    """补全 tag_registry + loop_tag_mapping（每回路 7 个 Tag 角色）。"""
    loop_ids = [c["id"] for c in loops]
    n_tags = 0
    n_mappings = 0
    async with AsyncSessionLocal() as session:
        for cfg in loops:
            for role in TAG_ROLES:
                tag_name = f"{cfg['tag_name']}.{role}"
                tag_desc = f"{cfg['description']} {role}"

                if role == "PV":
                    cur_val = cfg["base_pv"]
                    rmin, rmax, unit = cfg["range_min"], cfg["range_max"], cfg["pv_unit"]
                elif role == "SP":
                    cur_val = cfg["base_sp"]
                    rmin, rmax, unit = cfg["range_min"], cfg["range_max"], cfg["pv_unit"]
                elif role == "OP":
                    cur_val = cfg["base_op"]
                    rmin, rmax, unit = 0.0, 100.0, "%"
                elif role == "MODE":
                    cur_val = 1.0 if cfg["scenario"] != "manual" else 0.0
                    rmin, rmax, unit = 0.0, 10.0, ""
                elif role == "PID_P":
                    cur_val = cfg["pid_p"]
                    rmin, rmax, unit = 0.0, 100.0, ""
                elif role == "PID_I":
                    cur_val = cfg["pid_i"]
                    rmin, rmax, unit = 0.0, 1000.0, "s"
                else:  # PID_D
                    cur_val = cfg["pid_d"]
                    rmin, rmax, unit = 0.0, 1000.0, "s"

                result = await session.execute(
                    text("""
                    INSERT INTO tag_registry
                        (id, tag_name, tag_description, tag_type, current_value, quality,
                         last_sync_at, is_linked, range_min, range_max, unit,
                         measure_type, tdengine_tag_id)
                    VALUES (:id, :tn, :desc, :type, :val, 'GOOD', NOW(), TRUE,
                            :rmin, :rmax, :unit, :mtype, :tdtag)
                    ON CONFLICT (tag_name) DO UPDATE SET
                        tag_description = EXCLUDED.tag_description,
                        tag_type = EXCLUDED.tag_type,
                        current_value = EXCLUDED.current_value,
                        quality = EXCLUDED.quality,
                        last_sync_at = NOW(),
                        is_linked = TRUE,
                        range_min = EXCLUDED.range_min,
                        range_max = EXCLUDED.range_max,
                        unit = EXCLUDED.unit,
                        tdengine_tag_id = EXCLUDED.tdengine_tag_id
                    RETURNING id
                """),
                    {
                        "id": str(uuid.uuid4()),
                        "tn": tag_name,
                        "desc": tag_desc,
                        "type": role,
                        "val": cur_val,
                        "rmin": rmin,
                        "rmax": rmax,
                        "unit": unit,
                        "mtype": cfg["control_type"],
                        "tdtag": subtable_name(cfg["tag_name"]),
                    },
                )
                actual_tag_id = result.scalar()
                n_tags += 1

                is_required = role in REQUIRED_ROLES
                await session.execute(
                    text("""
                    INSERT INTO loop_tag_mapping
                        (id, loop_id, tag_id, tag_role, is_required, created_at)
                    VALUES (:id, :loop_id, :tag_id, :role, :req, NOW())
                    ON CONFLICT (loop_id, tag_role) DO UPDATE SET
                        tag_id = EXCLUDED.tag_id,
                        is_required = EXCLUDED.is_required
                """),
                    {
                        "id": str(uuid.uuid4()),
                        "loop_id": cfg["id"],
                        "tag_id": actual_tag_id,
                        "role": role,
                        "req": is_required,
                    },
                )
                n_mappings += 1

        await session.commit()

    print(f"  ✓ PostgreSQL 元数据补全：{n_tags} Tag / {n_mappings} 映射（{len(loops)} 回路）")


# ============================================================================
# TDengine 操作
# ============================================================================


async def td_execute(
    client: httpx.AsyncClient, sql: str, use_db: bool = True, retries: int = 3
) -> dict | None:
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
                print(f"  ⚠ TDengine SQL 错误: {desc[:200]}")
                return None
            await asyncio.sleep(2 ** attempt)
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  ⚠ TDengine 请求异常: {exc}")
                return None
            await asyncio.sleep(2 ** attempt)
    return None


async def setup_tdengine(
    client: httpx.AsyncClient, loops: list[dict[str, Any]], clean: bool = False
) -> None:
    """创建数据库、超级表、子表。clean=True 时 DROP 超级表（清空全部数据）后重建。"""
    # 1. 数据库
    await td_execute(
        client,
        "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'",
        use_db=False,
    )

    # 2. clean=True 时 DROP 超级表（清空全部时序数据）
    if clean:
        await td_execute(client, "DROP STABLE IF EXISTS st_loop_data")
        print("  ✓ 已清空 TDengine 全部数据（DROP STABLE st_loop_data）")

    # 3. 超级表
    await td_execute(
        client,
        """
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
    """,
    )

    # 4. 子表
    for cfg in loops:
        sub = subtable_name(cfg["tag_name"])
        await td_execute(
            client,
            (
                f"CREATE TABLE IF NOT EXISTS {sub} "
                f"USING st_loop_data TAGS ('{cfg['id']}', '{cfg['unit_id']}')"
            ),
        )

    print(f"  ✓ TDengine 子表创建完成（{len(loops)} 张，clean={clean}）")


# ============================================================================
# 时序数据写入
# ============================================================================


class ProgressTracker:
    def __init__(self) -> None:
        self.total = 0
        self._lock = asyncio.Lock()

    async def add(self, n: int) -> None:
        async with self._lock:
            old = self.total
            self.total += n
            if old // PROGRESS_INTERVAL != self.total // PROGRESS_INTERVAL:
                print(f"  📊 已写入 {self.total} 行...")


async def write_loop_data(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    cfg: dict,
    start: datetime,
    end: datetime,
    progress: ProgressTracker,
) -> int:
    """为单个回路生成并写入时序数据，返回写入行数。"""
    sub = subtable_name(cfg["tag_name"])
    points = generate_timeseries(cfg, start, end)
    total_written = 0

    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        parts = [f"INSERT INTO {sub} VALUES"]
        for pt in batch:
            ts_str, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_q = pt
            parts.append(
                f"('{ts_str}', {fmt_float(pv)}, {fmt_float(sp)}, {fmt_float(op)}, "
                f"{mode}, {fmt_float(pid_p)}, {fmt_float(pid_i)}, {fmt_float(pid_d)}, {pv_q})"
            )
        sql = " ".join(parts)
        async with semaphore:
            result = await td_execute(client, sql)
            if result is not None:
                total_written += len(batch)
                await progress.add(len(batch))

    return total_written


async def write_all_tdengine_data(
    client: httpx.AsyncClient, loops: list[dict[str, Any]], start: datetime, end: datetime
) -> int:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    progress = ProgressTracker()
    tasks = [write_loop_data(client, semaphore, cfg, start, end, progress) for cfg in loops]
    results = await asyncio.gather(*tasks)
    total = sum(results)
    print(f"  ✓ TDengine 时序数据写入完成：{total} 行")
    return total


# ============================================================================
# 主函数
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLPM 27 回路秒级数据仿真器（189 测点真实量程）")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help=f"历史数据小时数（默认 {DEFAULT_HOURS}）")
    parser.add_argument("--start", type=str, default=None, help=f"起始时间 YYYY-MM-DD HH:MM:SS（默认 {DEFAULT_START}）")
    parser.add_argument("--clean", action="store_true", help="清空 TDengine 全部数据后重新写入")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    start_time = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S") if args.start else DEFAULT_START
    end_time = start_time + timedelta(hours=args.hours)

    print("=" * 70)
    print("  CLPM 27 回路秒级数据仿真器（189 测点真实量程）")
    print(f"  时间范围: {start_time} ~ {end_time} ({args.hours} 小时)")
    print(f"  采样间隔: {SAMPLE_INTERVAL} 秒 (1Hz)")
    print(f"  清空 TDengine: {'是' if args.clean else '否'}")
    print("=" * 70)

    # 1. 加载回路配置
    print("\n📋 [1/4] 从 PostgreSQL 加载 27 回路配置（含真实量程）...")
    loops = await load_loops_from_db()
    print(f"  ✓ 加载 {len(loops)} 个回路：")
    units_seen: dict[str, list[str]] = {}
    for cfg in loops:
        units_seen.setdefault(cfg["unit_name"], []).append(
            f"{cfg['tag_name']}({cfg['control_type']}/{cfg['scenario']} 量程[{cfg['range_min']},{cfg['range_max']}])"
        )
    for uname, tags in units_seen.items():
        print(f"    {uname}: {len(tags)} 回路")

    # 物理模型分布
    model_dist: dict[str, int] = {}
    for cfg in loops:
        model_dist[cfg["model_type"]] = model_dist.get(cfg["model_type"], 0) + 1
    print(f"  物理模型分布: {model_dist}")
    valve_loops = [c["tag_name"] for c in loops if c["scenario"] == "valve_stiction"]
    print(f"  阀门卡滞回路({len(valve_loops)}): {valve_loops}")

    # 2. PostgreSQL 元数据补全
    print("\n📋 [2/4] 补全 PostgreSQL tag_registry / loop_tag_mapping...")
    await setup_postgres(loops)

    # 3. TDengine 设置 + 数据写入
    print("\n📊 [3/4] 设置 TDengine 并写入时序数据...")
    async with httpx.AsyncClient(
        auth=httpx.BasicAuth(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD),
        timeout=httpx.Timeout(120.0),
    ) as client:
        await setup_tdengine(client, loops, clean=args.clean)
        total_rows = await write_all_tdengine_data(client, loops, start_time, end_time)

    # 4. 汇总
    print("\n" + "=" * 70)
    print("  ✅ 数据生成完成！")
    print(f"  回路数:       {len(loops)}")
    print(f"  Tag 数:       {len(loops) * len(TAG_ROLES)}")
    print(f"  时序数据:     {total_rows} 行（{SAMPLE_INTERVAL}Hz × {args.hours} 小时）")
    expected = len(loops) * (args.hours * 3600 + 1)
    print(f"  预期行数:     {expected}")
    if expected:
        print(f"  写入率:       {total_rows / expected * 100:.2f}%")
    print(f"  时间范围:     {start_time} ~ {end_time}")
    print("=" * 70)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
