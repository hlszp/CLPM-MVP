#!/usr/bin/env python3
"""3 单元真实回路秒级历史数据仿真器。

从 PostgreSQL 动态加载 3 个工艺单元（脱甲烷精馏/醛化反应/急冷分离）的全部控制回路，
按 1Hz 采样生成 7 天历史时序数据，写入 TDengine，并补全缺失的 tag_registry / loop_tag_mapping。

特性：
    - 1 秒采样间隔（原始秒级数据，对齐 DDS §4.1 采集规范）
    - 7 天数据时长（默认，可 --days 调整）
    - 按 tag_name 前缀（FIC/LIC/PIC/TIC）推断控制类型与动态特性
    - 多场景分布（normal/oscillation/valve_stiction/op_saturation/manual/
      overconservative/overaggressive）确保 27 回路 KPI 表现多样
    - 异常值注入（spike 尖峰/flatline 停滞/out-of-range 超量程）
    - 非 Good 质量戳注入（~5%：Bad 聚簇 + Uncertain 散点）
    - --clean 清空 3 单元回路旧 TDengine 子表 + 旧 tag 映射后重建

用法::

    cd backend && uv run python scripts/simulate_unit_loops.py
    cd backend && uv run python scripts/simulate_unit_loops.py --days 7 --clean
    cd backend && uv run python scripts/simulate_unit_loops.py --days 3

注意：27 回路 × 7 天 × 1Hz ≈ 1630 万行，写入约 10-20 分钟。
"""

from __future__ import annotations

import argparse
import asyncio
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

SAMPLE_INTERVAL = 1              # 1Hz 采样间隔（秒）
BATCH_SIZE = 5000                # TDengine 单批写入行数（1Hz 数据量大，增大批量）
MAX_CONCURRENT = 8               # TDengine 并发写入数
PROGRESS_INTERVAL = 200_000      # 进度打印间隔（行）

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
    """回路位号 → TDengine 子表名。

    示例: 41FIC40504_PIDA → d_loop_41fic40504_pida
    """
    name = tag_name.lower().replace("-", "_").replace(".", "_")
    name = re.sub(r"_+", "_", name)
    return "d_loop_" + name


def fmt_ts(dt: datetime) -> str:
    """格式化时间戳为 TDengine 字符串（毫秒精度）。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def fmt_float(v: float | None) -> str:
    """格式化浮点数用于 SQL，处理 NaN/Inf。"""
    if v is None or math.isnan(v) or math.isinf(v):
        return "NULL"
    return f"{v:.4f}"


def sql_str(s: str) -> str:
    """转义 SQL 字符串。"""
    return "'" + s.replace("'", "''") + "'"


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
# 回路配置：从 PostgreSQL 动态加载 + 场景/基值分配
# ============================================================================

# 控制类型 → 动态特性参数
TYPE_PARAMS: dict[str, dict[str, Any]] = {
    "FLOW": {
        "tau": 8.0,            # 一阶滞后时间常数（秒）
        "noise_pct": 0.005,    # 噪声占量程比例
        "base_sp_range": (40.0, 200.0),   # t/h
        "pv_range_pct": 1.0,   # PV 量程 = base_sp
    },
    "LEVEL": {
        "tau": 120.0,
        "noise_pct": 0.003,
        "base_sp_range": (35.0, 75.0),    # %
        "pv_range_pct": 100.0,
    },
    "PRESSURE": {
        "tau": 25.0,
        "noise_pct": 0.004,
        "base_sp_range": (0.3, 3.5),      # MPa
        "pv_range_pct": 1.0,
    },
    "TEMPERATURE": {
        "tau": 60.0,
        "noise_pct": 0.003,
        "base_sp_range": (80.0, 380.0),   # °C
        "pv_range_pct": 1.0,
    },
    "STABLE": {
        "tau": 45.0,
        "noise_pct": 0.004,
        "base_sp_range": (40.0, 120.0),
        "pv_range_pct": 1.0,
    },
}

# 控制类型 → 候选场景（每个回路按索引轮选，保证多样性）
TYPE_SCENARIOS: dict[str, list[str]] = {
    "FLOW": ["normal", "oscillation", "valve_stiction", "normal"],
    "LEVEL": ["normal", "op_saturation", "manual", "normal"],
    "PRESSURE": ["normal", "overconservative", "normal"],
    "TEMPERATURE": ["normal", "overaggressive", "overconservative", "normal"],
    "STABLE": ["normal", "oscillation", "normal"],
}


async def load_loops_from_db() -> list[dict[str, Any]]:
    """从 PostgreSQL 加载 3 个单元的全部控制回路。"""
    loops: list[dict[str, Any]] = []
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

        for idx, (loop_id, tag_name, desc, unit_id, unit_name) in enumerate(rows):
            ctype = infer_control_type(tag_name)
            params = TYPE_PARAMS[ctype]
            scenarios = TYPE_SCENARIOS[ctype]
            # 按索引轮选场景
            scenario = scenarios[idx % len(scenarios)]

            # 确定性生成 base_sp（基于 tag_name hash，保证可复现）
            h = abs(hash(tag_name))
            sp_lo, sp_hi = params["base_sp_range"]
            base_sp = round(sp_lo + (h % 10000) / 10000 * (sp_hi - sp_lo), 2)
            pv_range = base_sp * params["pv_range_pct"] if params["pv_range_pct"] <= 1.0 else params["pv_range_pct"]
            base_pv = round(base_sp + random.uniform(-pv_range * 0.01, pv_range * 0.01), 2)
            base_op = round(random.uniform(35, 65), 2)

            # PID 参数随控制类型
            if ctype == "FLOW":
                pid_p, pid_i, pid_d = 2.0, 10.0, 0.0
            elif ctype == "LEVEL":
                pid_p, pid_i, pid_d = 1.0, 60.0, 0.0
            elif ctype == "PRESSURE":
                pid_p, pid_i, pid_d = 1.2, 20.0, 2.0
            elif ctype == "TEMPERATURE":
                pid_p, pid_i, pid_d = 1.5, 30.0, 5.0
            else:
                pid_p, pid_i, pid_d = 1.5, 30.0, 2.0

            loops.append({
                "id": loop_id,
                "tag_name": tag_name,
                "description": desc or f"{tag_name} 控制回路",
                "unit_id": unit_id,
                "unit_name": unit_name,
                "control_type": ctype,
                "scenario": scenario,
                "tau": params["tau"],
                "noise_pct": params["noise_pct"],
                "base_sp": base_sp,
                "base_pv": base_pv,
                "base_op": base_op,
                "pv_range": pv_range,
                "pid_p": pid_p,
                "pid_i": pid_i,
                "pid_d": pid_d,
            })
    return loops


# ============================================================================
# SP / PID / MODE 调度
# ============================================================================

def generate_sp_schedule(base_sp: float, pv_range: float, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """SP 阶跃调度：每 2-4 小时变化一次，幅度 ±5-10%。"""
    schedule = [(start, base_sp)]
    t = start
    while t < end:
        t += timedelta(seconds=random.randint(2 * 3600, 4 * 3600))
        if t >= end:
            break
        change = random.uniform(-0.10, 0.10)
        new_sp = round(base_sp * (1 + change), 2)
        new_sp = clamp(new_sp, base_sp - pv_range * 0.2, base_sp + pv_range * 0.2)
        schedule.append((t, new_sp))
    return schedule


def generate_pid_schedule(cfg: dict, start: datetime, end: datetime) -> list[tuple[datetime, float, float, float]]:
    """PID 参数每日 1-2 次微调（±5%）。"""
    base_p, base_i, base_d = cfg["pid_p"], cfg["pid_i"], cfg["pid_d"]
    schedule = [(start, base_p, base_i, base_d)]
    t = start
    while t < end:
        t += timedelta(seconds=random.randint(12 * 3600, 24 * 3600))
        if t >= end:
            break
        p = round(base_p * random.uniform(0.95, 1.05), 3)
        i = round(base_i * random.uniform(0.95, 1.05), 3)
        d = round(base_d * random.uniform(0.95, 1.05), 3)
        schedule.append((t, p, i, d))
    return schedule


def generate_mode_schedule(scenario: str, start: datetime, end: datetime) -> list[tuple[datetime, int]]:
    """MODE 调度：实际工程至少 4 小时变化一次。

    语义：0=Manual, 1=Auto, 2=Cascade
    - manual 场景：始终 0
    - 其他场景：在 Auto(1) ↔ Cascade(2) 之间切换，每 4-8 小时一次；
      偶尔短暂切 Manual(0) 维护（约 10% 概率，持续 15-30 分钟）。
    """
    if scenario == "manual":
        return [(start, 0)]

    schedule: list[tuple[datetime, int]] = [(start, 1)]
    t = start
    while t < end:
        # 主切换间隔：4-8 小时
        t += timedelta(seconds=random.randint(4 * 3600, 8 * 3600))
        if t >= end:
            break
        cur = schedule[-1][1]
        # 10% 概率短暂切 Manual 维护
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
# 场景化 PV/OP 生成（1Hz 适配）
# ============================================================================

def _gen_normal(sp: float, prev_op: float, prev_pv: float, cfg: dict) -> tuple[float, float]:
    """正常回路：PV 紧跟 SP，OP 平缓。"""
    noise = abs(sp) * cfg["noise_pct"]
    pv = sp + random.gauss(0, noise)
    op = prev_op + (sp - prev_pv) * 0.02 + random.gauss(0, 0.2)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_oscillation(sp: float, prev_op: float, prev_pv: float, t: float, cfg: dict) -> tuple[float, float]:
    """振荡回路：PV 正弦振荡，周期 ~10 分钟。"""
    amplitude = cfg["pv_range"] * 0.05
    period = 600.0
    omega = 2 * math.pi / period
    noise = abs(sp) * cfg["noise_pct"]
    pv = sp + amplitude * math.sin(omega * t) + random.gauss(0, noise)
    op = prev_op + 0.8 * math.sin(omega * t + math.pi) + random.gauss(0, 0.15)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_valve_stiction(sp: float, prev_op: float, prev_pv: float, cfg: dict, state: dict) -> tuple[float, float]:
    """阀门粘滞：OP 阶跃式变化。"""
    error = sp - prev_pv
    desired_op = prev_op + error * 0.15
    stiction_band = 3.0
    if abs(desired_op - prev_op) >= stiction_band:
        op = desired_op
    else:
        op = prev_op
    op = clamp(op, 0, 100)
    tau = cfg["tau"]
    target_pv = sp + (op - 50) * (cfg["pv_range"] * 0.005)
    pv = prev_pv + (target_pv - prev_pv) * (SAMPLE_INTERVAL / tau) + random.gauss(0, 0.15)
    return round(pv, 4), round(op, 4)


def _gen_op_saturation(sp: float, prev_op: float, prev_pv: float, t: float, cfg: dict, state: dict) -> tuple[float, float]:
    """OP 饱和：OP 长时间停留 95-100% 或 0-5%。"""
    sat_until = state.get("sat_until", 0.0)
    norm_until = state.get("norm_until", 0.0)
    if t < sat_until:
        op = clamp(97.0 + random.gauss(0, 0.5), 95, 100)
    elif t < norm_until:
        op = prev_op + (sp - prev_pv) * 0.1 + random.gauss(0, 0.2)
        op = clamp(op, 0, 100)
    else:
        if random.random() < 0.4:
            state["sat_until"] = t + random.randint(1800, 3600)
            state["norm_until"] = 0.0
            op = clamp(97.0 + random.gauss(0, 0.5), 95, 100)
        else:
            state["norm_until"] = t + random.randint(3600, 7200)
            state["sat_until"] = 0.0
            op = prev_op + (sp - prev_pv) * 0.1 + random.gauss(0, 0.2)
            op = clamp(op, 0, 100)
    target_pv = sp + (op - 50) * (cfg["pv_range"] * 0.008)
    pv = prev_pv + (target_pv - prev_pv) * 0.05 + random.gauss(0, 0.2)
    return round(pv, 4), round(op, 4)


def _gen_overconservative(sp: float, prev_op: float, prev_pv: float, cfg: dict) -> tuple[float, float]:
    """过保守：PV 响应慢，稳态偏差 8%。"""
    tau = cfg["tau"] * 4.0
    target_pv = sp * 0.92
    pv = prev_pv + (target_pv - prev_pv) * (SAMPLE_INTERVAL / tau) + random.gauss(0, 0.15)
    op = prev_op + (sp - pv) * 0.02 + random.gauss(0, 0.1)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_overaggressive(sp: float, prev_op: float, prev_pv: float, t: float, cfg: dict, state: dict) -> tuple[float, float]:
    """过激进：PV 过冲大，振荡后收敛。"""
    last_sp = state.get("last_sp", sp)
    sp_change_t = state.get("sp_change_t", 0.0)
    if sp != last_sp:
        state["sp_change_t"] = t
        state["last_sp"] = sp
        sp_change_t = t
    elapsed = t - sp_change_t
    omega = 0.015
    zeta = 0.2
    overshoot = 0.3
    noise = abs(sp) * cfg["noise_pct"]
    if elapsed < 1200:
        decay = math.exp(-zeta * omega * elapsed)
        pv = sp + overshoot * sp * decay * math.cos(omega * elapsed) + random.gauss(0, noise)
    else:
        pv = sp + random.gauss(0, noise)
    op = prev_op + (sp - pv) * 0.2 + random.gauss(0, 0.4)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_manual(cfg: dict, sp: float, prev_op: float, prev_pv: float, t: float, state: dict) -> tuple[float, float]:
    """手动模式：OP 由操作员阶跃调节，PV 跟随 OP。"""
    next_change = state.get("next_change", 0.0)
    op_target = state.get("op_target", prev_op)
    if t >= next_change:
        op_target = clamp(prev_op + random.uniform(-15, 15), 10, 90)
        state["op_target"] = op_target
        state["next_change"] = t + random.randint(3600, 10800)
    op = prev_op + (op_target - prev_op) * 0.1
    op = clamp(op, 0, 100)
    target_pv = cfg["base_sp"] + (op - 50) * (cfg["pv_range"] * 0.005)
    pv = prev_pv + (target_pv - prev_pv) * 0.03 + random.gauss(0, 0.15)
    return round(pv, 4), round(op, 4)


# ============================================================================
# 异常值 + 非 Good 质量戳注入
# ============================================================================

class AnomalyInjector:
    """异常值与质量戳注入器。

    策略：
        - spike（尖峰）：随机时刻 PV 突变到 5-10x 量程外，持续 1-5 秒
        - flatline（停滞）：PV 锁定固定值 60-300 秒
        - out_of_range（超量程）：PV 超出 [range_min, range_max] 边界
        - bad_cluster（坏质量聚簇）：30-120 秒窗口 pv_quality=0
        - uncertain_scatter（不确定散点）：~1% 单点 pv_quality=2
    """

    def __init__(self, cfg: dict, n_points: int) -> None:
        self.cfg = cfg
        self.n = n_points
        self.pv_range = cfg["pv_range"]
        self.base_sp = cfg["base_sp"]

        # 预生成异常事件
        self.spike_events = self._gen_spikes()
        self.flatline_events = self._gen_flatlines()
        self.bad_clusters = self._gen_bad_clusters()
        # out_of_range 标记点（~0.05%）
        self.oor_indices = set(random.sample(range(n_points), max(1, n_points // 2000)))
        # uncertain 散点（~1%）
        self.uncertain_indices = set(random.sample(range(n_points), max(1, n_points // 100)))

    def _gen_spikes(self) -> list[tuple[int, int, float]]:
        """生成尖峰事件：(起始索引, 持续秒数, 尖峰值)。约 0.05% 的点。"""
        events = []
        n_spikes = max(3, self.n // 2000)
        for _ in range(n_spikes):
            start = random.randint(60, self.n - 300)
            dur = random.randint(1, 5)
            # 尖峰方向：向上或向下
            magnitude = self.pv_range * random.uniform(5, 10) * random.choice([-1, 1])
            spike_val = self.base_sp + magnitude
            events.append((start, dur, spike_val))
        return events

    def _gen_flatlines(self) -> list[tuple[int, int, float]]:
        """生成停滞事件：(起始索引, 持续秒数, 锁定值)。约 0.2% 的点。"""
        events = []
        n_flats = max(2, self.n // 5000)
        for _ in range(n_flats):
            start = random.randint(60, self.n - 300)
            dur = random.randint(30, 90)
            val = self.base_sp + random.uniform(-self.pv_range * 0.1, self.pv_range * 0.1)
            events.append((start, dur, val))
        return events

    def _gen_bad_clusters(self) -> list[tuple[int, int]]:
        """生成坏质量聚簇：(起始索引, 持续秒数)。约 3% 的点。"""
        clusters = []
        total_bad = 0
        target_bad = int(self.n * 0.03)
        while total_bad < target_bad:
            start = random.randint(60, self.n - 300)
            dur = random.randint(30, 120)
            clusters.append((start, dur))
            total_bad += dur
        return clusters

    def apply(self, idx: int, pv: float, ts_sec: float) -> tuple[float, int]:
        """对单点应用异常注入，返回 (pv_after, quality)。

        quality: 1=Good, 0=Bad, 2=Uncertain
        """
        quality = 1

        # 1. spike 尖峰
        for (s, dur, val) in self.spike_events:
            if s <= idx < s + dur:
                pv = val
                quality = 0  # 尖峰视为 Bad
                return round(pv, 4), quality

        # 2. flatline 停滞
        for (s, dur, val) in self.flatline_events:
            if s <= idx < s + dur:
                pv = val
                # 停滞期间标记 Uncertain
                if quality == 1:
                    quality = 2
                return round(pv, 4), quality

        # 3. out_of_range 超量程
        if idx in self.oor_indices:
            pv = pv + self.pv_range * random.uniform(0.15, 0.25) * random.choice([-1, 1])
            if quality == 1:
                quality = 2
            return round(pv, 4), quality

        # 4. bad_cluster 坏质量聚簇
        for (s, dur) in self.bad_clusters:
            if s <= idx < s + dur:
                quality = 0
                return round(pv, 4), quality

        # 5. uncertain 散点
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
    sp_schedule = generate_sp_schedule(cfg["base_sp"], cfg["pv_range"], start, end)
    pid_schedule = generate_pid_schedule(cfg, start, end)
    mode_schedule = generate_mode_schedule(cfg["scenario"], start, end)

    sp_idx = 0
    cur_sp = sp_schedule[0][1]
    pid_idx = 0
    cur_p, cur_i, cur_d = pid_schedule[0][1], pid_schedule[0][2], pid_schedule[0][3]
    mode_idx = 0
    cur_mode = mode_schedule[0][1]

    prev_pv = cfg["base_pv"]
    prev_op = cfg["base_op"]
    scenario = cfg["scenario"]
    state: dict[str, Any] = {}

    # 计算总点数
    total_sec = int((end - start).total_seconds())
    n_points = total_sec // SAMPLE_INTERVAL + 1

    injector = AnomalyInjector(cfg, n_points)

    points: list[tuple] = []
    t = start
    idx = 0
    total_seconds = 0.0

    while t <= end:
        # 更新调度
        while sp_idx < len(sp_schedule) and sp_schedule[sp_idx][0] <= t:
            cur_sp = sp_schedule[sp_idx][1]
            sp_idx += 1
        while pid_idx < len(pid_schedule) and pid_schedule[pid_idx][0] <= t:
            cur_p, cur_i, cur_d = pid_schedule[pid_idx][1], pid_schedule[pid_idx][2], pid_schedule[pid_idx][3]
            pid_idx += 1
        while mode_idx < len(mode_schedule) and mode_schedule[mode_idx][0] <= t:
            cur_mode = mode_schedule[mode_idx][1]
            mode_idx += 1

        # 场景化 PV/OP
        if scenario == "normal":
            pv, op = _gen_normal(cur_sp, prev_op, prev_pv, cfg)
        elif scenario == "oscillation":
            pv, op = _gen_oscillation(cur_sp, prev_op, prev_pv, total_seconds, cfg)
        elif scenario == "valve_stiction":
            pv, op = _gen_valve_stiction(cur_sp, prev_op, prev_pv, cfg, state)
        elif scenario == "op_saturation":
            pv, op = _gen_op_saturation(cur_sp, prev_op, prev_pv, total_seconds, cfg, state)
        elif scenario == "overconservative":
            pv, op = _gen_overconservative(cur_sp, prev_op, prev_pv, cfg)
        elif scenario == "overaggressive":
            pv, op = _gen_overaggressive(cur_sp, prev_op, prev_pv, total_seconds, cfg, state)
        elif scenario == "manual":
            pv, op = _gen_manual(cfg, cur_sp, prev_op, prev_pv, total_seconds, state)
        else:
            pv, op = _gen_normal(cur_sp, prev_op, prev_pv, cfg)

        # 异常值 + 质量戳注入
        pv, pv_quality = injector.apply(idx, pv, total_seconds)

        points.append((fmt_ts(t), pv, cur_sp, op, cur_mode, cur_p, cur_i, cur_d, pv_quality))

        prev_pv = pv
        prev_op = op
        t += interval
        idx += 1
        total_seconds += SAMPLE_INTERVAL

    return points


# ============================================================================
# PostgreSQL 元数据补全
# ============================================================================

async def setup_postgres(loops: list[dict[str, Any]], clean: bool = False) -> None:
    """补全 tag_registry + loop_tag_mapping（每回路 7 个 Tag 角色）。"""
    loop_ids = [c["id"] for c in loops]
    async with AsyncSessionLocal() as session:
        if clean:
            # 1. 先删除这些回路的 loop_tag_mapping（解除外键引用）
            await session.execute(text(
                "DELETE FROM loop_tag_mapping WHERE loop_id = ANY(:ids)"
            ), {"ids": loop_ids})
            # 2. 删除 tag_registry 中不再被任何 loop_tag_mapping 引用的 tag
            #    （只删除这些回路的 7 角色 tag，且确认无其他回路引用）
            tag_names_to_clean = [
                f"{cfg['tag_name']}.{role}"
                for cfg in loops
                for role in TAG_ROLES
            ]
            await session.execute(text(
                "DELETE FROM tag_registry "
                "WHERE tag_name = ANY(:tns) "
                "AND id NOT IN (SELECT tag_id FROM loop_tag_mapping)"
            ), {"tns": tag_names_to_clean})
            await session.commit()
            print(f"  ✓ 清理旧 tag 映射（{len(loops)} 回路）")

        # 创建 tag_registry + loop_tag_mapping（使用 RETURNING 获取实际 tag_id，避免 FK 冲突）
        n_tags = 0
        n_mappings = 0
        for cfg in loops:
            for role in TAG_ROLES:
                tag_name = f"{cfg['tag_name']}.{role}"
                tag_desc = f"{cfg['description']} {role}"

                # 当前值
                if role == "PV":
                    cur_val = cfg["base_pv"]
                elif role == "SP":
                    cur_val = cfg["base_sp"]
                elif role == "OP":
                    cur_val = cfg["base_op"]
                elif role == "MODE":
                    cur_val = 1.0 if cfg["scenario"] != "manual" else 0.0
                elif role == "PID_P":
                    cur_val = cfg["pid_p"]
                elif role == "PID_I":
                    cur_val = cfg["pid_i"]
                else:  # PID_D
                    cur_val = cfg["pid_d"]

                # range_min / range_max / unit
                ctype = cfg["control_type"]
                if ctype == "FLOW":
                    range_min, range_max, unit = 0.0, cfg["pv_range"] * 1.2, "t/h"
                elif ctype == "LEVEL":
                    range_min, range_max, unit = 0.0, 100.0, "%"
                elif ctype == "PRESSURE":
                    range_min, range_max, unit = 0.0, cfg["pv_range"] * 1.5, "MPa"
                elif ctype == "TEMPERATURE":
                    range_min, range_max, unit = 0.0, cfg["pv_range"] * 1.2, "°C"
                else:
                    range_min, range_max, unit = 0.0, cfg["pv_range"] * 1.2, ""

                # upsert tag_registry 并 RETURNING 实际 id（无论新建还是已存在都返回正确 id）
                result = await session.execute(text("""
                    INSERT INTO tag_registry
                        (id, tag_name, tag_description, tag_type, current_value, quality,
                         last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id)
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
                """), {
                    "id": str(uuid.uuid4()), "tn": tag_name, "desc": tag_desc, "type": role,
                    "val": cur_val, "rmin": range_min, "rmax": range_max, "unit": unit,
                    "mtype": ctype, "tdtag": subtable_name(cfg["tag_name"]),
                })
                actual_tag_id = result.scalar()
                n_tags += 1

                # 用实际 tag_id upsert loop_tag_mapping，FK 约束不会冲突
                is_required = role in REQUIRED_ROLES
                await session.execute(text("""
                    INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at)
                    VALUES (:id, :loop_id, :tag_id, :role, :req, NOW())
                    ON CONFLICT (loop_id, tag_role) DO UPDATE SET
                        tag_id = EXCLUDED.tag_id,
                        is_required = EXCLUDED.is_required
                """), {
                    "id": str(uuid.uuid4()), "loop_id": cfg["id"], "tag_id": actual_tag_id,
                    "role": role, "req": is_required,
                })
                n_mappings += 1

        await session.commit()

    print(f"  ✓ PostgreSQL 元数据补全：{n_tags} Tag / {n_mappings} 映射（{len(loops)} 回路）")


# ============================================================================
# TDengine 操作
# ============================================================================

async def td_execute(client: httpx.AsyncClient, sql: str, use_db: bool = True, retries: int = 3) -> dict | None:
    url = TD_REST_DB_URL if use_db else TD_REST_BASE
    for attempt in range(retries):
        try:
            resp = await client.post(url, content=sql.encode("utf-8"), headers={"Content-Type": "text/plain"})
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


async def setup_tdengine(client: httpx.AsyncClient, loops: list[dict[str, Any]], clean: bool = False) -> None:
    """创建数据库、超级表、子表。clean=True 时先 DROP 旧子表。"""
    # 1. 数据库 + 超级表
    await td_execute(client, "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'", use_db=False)
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

    # 2. 子表
    for cfg in loops:
        sub = subtable_name(cfg["tag_name"])
        if clean:
            await td_execute(client, f"DROP TABLE IF EXISTS {sub}")
        await td_execute(client, (
            f"CREATE TABLE IF NOT EXISTS {sub} "
            f"USING st_loop_data TAGS ('{cfg['id']}', '{cfg['unit_id']}')"
        ))

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


async def write_all_tdengine_data(client: httpx.AsyncClient, loops: list[dict[str, Any]], start: datetime, end: datetime) -> int:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    progress = ProgressTracker()
    tasks = [
        write_loop_data(client, semaphore, cfg, start, end, progress)
        for cfg in loops
    ]
    results = await asyncio.gather(*tasks)
    total = sum(results)
    print(f"  ✓ TDengine 时序数据写入完成：{total} 行")
    return total


# ============================================================================
# 主函数
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLPM 3 单元真实回路秒级数据仿真器")
    parser.add_argument("--days", type=int, default=7, help="历史数据天数（默认 7）")
    parser.add_argument("--clean", action="store_true", help="清空旧 TDengine 子表 + 旧 tag 映射后重建")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    start_time = datetime.now().replace(microsecond=0) - timedelta(days=args.days)
    end_time = datetime.now().replace(microsecond=0)

    print("=" * 70)
    print("  CLPM 3 单元真实回路秒级数据仿真器")
    print(f"  时间范围: {start_time} ~ {end_time} ({args.days} 天)")
    print(f"  采样间隔: {SAMPLE_INTERVAL} 秒 (1Hz)")
    print(f"  清理旧数据: {'是' if args.clean else '否'}")
    print("=" * 70)

    # 1. 加载回路配置
    print("\n📋 [1/4] 从 PostgreSQL 加载 3 单元回路配置...")
    loops = await load_loops_from_db()
    print(f"  ✓ 加载 {len(loops)} 个回路：")
    # 按单元分组打印
    units_seen: dict[str, list[str]] = {}
    for cfg in loops:
        units_seen.setdefault(cfg["unit_name"], []).append(
            f"{cfg['tag_name']}({cfg['control_type']}/{cfg['scenario']})"
        )
    for uname, tags in units_seen.items():
        print(f"    {uname}: {len(tags)} 回路")

    # 2. PostgreSQL 元数据补全（始终用 upsert，不删除已有 tag，避免 FK 冲突）
    print("\n📋 [2/4] 补全 PostgreSQL tag_registry / loop_tag_mapping...")
    await setup_postgres(loops, clean=False)

    # 3. TDengine 设置 + 数据写入（--clean 时 DROP 旧子表重建）
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
    print(f"  时序数据:     {total_rows} 行（{SAMPLE_INTERVAL}Hz × {args.days} 天）")
    expected = len(loops) * (args.days * 86400 + 1)
    print(f"  预期行数:     {expected}")
    print(f"  写入率:       {total_rows / expected * 100:.2f}%" if expected else "")
    print("=" * 70)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
