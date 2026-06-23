#!/usr/bin/env python3
"""工业过程数据仿真器 —— 生成高仿真演示数据。

生成 10 个控制回路 × N 天的历史时序数据，写入 TDengine（REST API）和 PostgreSQL。
支持 --days 控制天数、--clean 清理旧数据。

用法::

    cd backend && uv run python scripts/data_simulator.py
    cd backend && uv run python scripts/data_simulator.py --days 7
    cd backend && uv run python scripts/data_simulator.py --days 3 --clean

注意：使用 httpx（项目已有依赖）替代 aiohttp 进行异步 HTTP 请求。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
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

SAMPLE_INTERVAL = 10          # 采样间隔（秒）
BATCH_SIZE = 1000             # TDengine 批量写入行数
MAX_CONCURRENT = 5            # TDengine 并发写入数
PROGRESS_INTERVAL = 10000     # 进度打印间隔（行）

# TDengine REST API URL（端口 = 原生端口 + 11，如 6030→6041）
TD_REST_BASE = f"http://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT + 11}/rest/sql"
TD_REST_DB_URL = f"{TD_REST_BASE}/{settings.TDENGINE_DB}"

# 随机种子（保证可复现）
random.seed(42)

# ============================================================================
# 10 个回路配置
# ============================================================================

LOOP_CONFIGS: list[dict[str, Any]] = [
    {
        "id": "00000000-0000-0000-0000-000000000201",
        "tag_name": "HDS-RX-TIC-101",
        "description": "R-101 反应器入口温度调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000111",   # HDS-RX 反应系统
        "scenario": "normal",
        "base_sp": 360.0, "base_pv": 358.5, "base_op": 62.3,
        "pid_p": 1.5, "pid_i": 30.0, "pid_d": 5.0,
    },
    {
        "id": "00000000-0000-0000-0000-000000000202",
        "tag_name": "HDS-FR-FIC-201",
        "description": "E-201 分馏塔进料流量调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000112",   # HDS-FR 分馏系统
        "scenario": "oscillation",
        "base_sp": 85.0, "base_pv": 85.2, "base_op": 48.5,
        "pid_p": 2.0, "pid_i": 10.0, "pid_d": 0.0,
    },
    {
        "id": "00000000-0000-0000-0000-000000000203",
        "tag_name": "HDC-RX-TIC-301",
        "description": "R-301 反应器入口温度调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000113",   # HDC-RX 反应系统
        "scenario": "valve_stiction",
        "base_sp": 375.0, "base_pv": 372.1, "base_op": 55.8,
        "pid_p": 1.5, "pid_i": 30.0, "pid_d": 5.0,
    },
    {
        "id": str(uuid.uuid4()),
        "tag_name": "HDS-DE-LIC-102",
        "description": "T-102 塔底液位调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000116",   # HDS-DE 分馏系统（新建）
        "scenario": "op_saturation",
        "base_sp": 50.0, "base_pv": 50.0, "base_op": 45.0,
        "pid_p": 1.0, "pid_i": 60.0, "pid_d": 0.0,
    },
    {
        "id": str(uuid.uuid4()),
        "tag_name": "HDC-FR-FIC-302",
        "description": "E-302 换热器流量调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000114",   # HDC-FR 分馏系统
        "scenario": "normal",
        "base_sp": 120.0, "base_pv": 120.0, "base_op": 55.0,
        "pid_p": 2.0, "pid_i": 10.0, "pid_d": 0.0,
    },
    {
        "id": str(uuid.uuid4()),
        "tag_name": "HDS-RX-PIC-103",
        "description": "V-103 压力控制调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000111",   # HDS-RX 反应系统
        "scenario": "overconservative",
        "base_sp": 1.2, "base_pv": 1.18, "base_op": 40.0,
        "pid_p": 1.2, "pid_i": 20.0, "pid_d": 2.0,
    },
    {
        "id": str(uuid.uuid4()),
        "tag_name": "HDC-DE-TIC-303",
        "description": "T-303 塔顶温度调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000117",   # HDC-DE 分馏系统（新建）
        "scenario": "overaggressive",
        "base_sp": 180.0, "base_pv": 178.0, "base_op": 50.0,
        "pid_p": 2.0, "pid_i": 15.0, "pid_d": 3.0,
    },
    {
        "id": str(uuid.uuid4()),
        "tag_name": "HDS-FR-FIC-104",
        "description": "F-104 燃料气流量调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000112",   # HDS-FR 分馏系统
        "scenario": "normal",
        "base_sp": 200.0, "base_pv": 200.0, "base_op": 60.0,
        "pid_p": 2.0, "pid_i": 10.0, "pid_d": 0.0,
    },
    {
        "id": str(uuid.uuid4()),
        "tag_name": "HDC-RX-LIC-304",
        "description": "D-304 分离器液位调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000113",   # HDC-RX 反应系统
        "scenario": "manual",
        "base_sp": 60.0, "base_pv": 58.0, "base_op": 35.0,
        "pid_p": 1.0, "pid_i": 60.0, "pid_d": 0.0,
    },
    {
        "id": str(uuid.uuid4()),
        "tag_name": "HDS-DE-PIC-105",
        "description": "D-105 塔顶压力调节回路",
        "unit_id": "00000000-0000-0000-0000-000000000116",   # HDS-DE 分馏系统（新建）
        "scenario": "normal",
        "base_sp": 0.8, "base_pv": 0.79, "base_op": 45.0,
        "pid_p": 1.2, "pid_i": 20.0, "pid_d": 2.0,
    },
]

# Tag 角色
TAG_ROLES = ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]
REQUIRED_ROLES = {"PV", "SP", "OP", "MODE"}

# 新建装置节点
NEW_PLANT_NODES = [
    {
        "id": "00000000-0000-0000-0000-000000000116",
        "name": "HDS-DE 分馏系统",
        "type": "EQUIPMENT",
        "parent_id": "00000000-0000-0000-0000-000000000102",  # 加氢精制
    },
    {
        "id": "00000000-0000-0000-0000-000000000117",
        "name": "HDC-DE 分馏系统",
        "type": "EQUIPMENT",
        "parent_id": "00000000-0000-0000-0000-000000000103",  # 加氢裂化
    },
]

# 场景 → KPI 范围
SCENARIO_KPI: dict[str, dict[str, tuple[float, float]]] = {
    "normal":           {"gvr": (95, 99), "amr": (95, 100), "sr": (85, 95), "ar": (85, 95), "or": (1, 5),  "sat": (0, 3)},
    "oscillation":      {"gvr": (95, 99), "amr": (95, 100), "sr": (25, 40), "ar": (50, 65), "or": (40, 60), "sat": (1, 5)},
    "valve_stiction":   {"gvr": (95, 99), "amr": (95, 100), "sr": (40, 55), "ar": (60, 75), "or": (20, 35), "sat": (2, 8)},
    "op_saturation":    {"gvr": (95, 99), "amr": (95, 100), "sr": (50, 65), "ar": (55, 70), "or": (5, 15),  "sat": (30, 45)},
    "overconservative": {"gvr": (95, 99), "amr": (95, 100), "sr": (70, 85), "ar": (55, 70), "or": (3, 10),  "sat": (1, 5)},
    "overaggressive":   {"gvr": (95, 99), "amr": (95, 100), "sr": (35, 50), "ar": (60, 75), "or": (25, 40), "sat": (2, 8)},
    "manual":           {"gvr": (95, 99), "amr": (0, 5),    "sr": (65, 80), "ar": (70, 85), "or": (3, 10),  "sat": (1, 5)},
}

# 诊断结果配置
DIAGNOSIS_CONFIGS: list[dict[str, Any]] = [
    {
        "scenario": "oscillation",
        "diag_label": "OSCILLATION",
        "confidence": (80, 95),
        "feature_values": {"amplitude": 3.5, "period_s": 600, "frequency_hz": 0.00167, "snr": 8.5},
        "evidence_chain": {"method": "IAE_FFT", "peak_frequency_hz": 0.00167, "zero_crossings": 42, "threshold": 0.004},
    },
    {
        "scenario": "valve_stiction",
        "diag_label": "VALVE_STICTION",
        "confidence": (75, 90),
        "feature_values": {"ngi": 0.005, "nli": 0.08, "stiction_index": 0.75, "r2": 0.82},
        "evidence_chain": {"method": "CHOUDHURY_NGI_NLI", "pv_op_ellipse": True, "fitting_score": 0.82},
    },
    {
        "scenario": "op_saturation",
        "diag_label": "OUTPUT_SATURATION",
        "confidence": (85, 95),
        "feature_values": {"saturation_rate": 35.5, "high_saturation_s": 7200, "low_saturation_s": 0},
        "evidence_chain": {"method": "OP_LIMIT_STAT", "op_high_limit": 100, "op_low_limit": 0, "epsilon": 2},
    },
    {
        "scenario": "overconservative",
        "diag_label": "OVERCONSERVATIVE",
        "confidence": (70, 85),
        "feature_values": {"settling_time_s": 600, "iae_ratio": 2.5, "op_activity": 0.005},
        "evidence_chain": {"method": "EXPERT_RULE", "steady_state_error_pct": 8.5, "settling_ratio": 5.0},
    },
    {
        "scenario": "overaggressive",
        "diag_label": "OVERAGGRESSIVE",
        "confidence": (75, 90),
        "feature_values": {"overshoot_pct": 32.5, "decay_ratio": 0.45, "harris_index": 0.55},
        "evidence_chain": {"method": "EXPERT_RULE", "step_response_analysis": True, "overshoot_threshold": 25},
    },
]


# ============================================================================
# 工具函数
# ============================================================================

def subtable_name(tag_name: str) -> str:
    """回路位号 → TDengine 子表名（如 HDS-RX-TIC-101 → d_loop_hds_rx_tic_101）。"""
    return "d_loop_" + tag_name.lower().replace("-", "_")


def fmt_ts(dt: datetime) -> str:
    """格式化时间戳为 TDengine 字符串（毫秒精度）。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def fmt_float(v: float | None) -> str:
    """格式化浮点数用于 SQL，处理 NaN/Inf。"""
    if v is None or math.isnan(v) or math.isinf(v):
        return "NULL"
    return f"{v:.4f}"


def sql_str(s: str) -> str:
    """转义 SQL 字符串（单引号转义）。"""
    return "'" + s.replace("'", "''") + "'"


def clamp(v: float, lo: float, hi: float) -> float:
    """限制数值范围。"""
    return max(lo, min(hi, v))


# ============================================================================
# SP / PID / MODE 调度生成
# ============================================================================

def generate_sp_schedule(base_sp: float, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """生成 SP 阶跃变化时间表（每 2-4 小时变化一次，±5-10%）。"""
    schedule = [(start, base_sp)]
    t = start
    while t < end:
        step = timedelta(seconds=random.randint(2 * 3600, 4 * 3600))
        t += step
        if t >= end:
            break
        change_pct = random.uniform(-0.10, 0.10)
        new_sp = round(base_sp * (1 + change_pct), 2)
        # 限制 SP 在合理范围
        new_sp = clamp(new_sp, base_sp * 0.7, base_sp * 1.3)
        schedule.append((t, new_sp))
    return schedule


def generate_pid_schedule(cfg: dict, start: datetime, end: datetime) -> list[tuple[datetime, float, float, float]]:
    """生成 PID 参数变化时间表（每天 1-2 次微调，±5%）。"""
    base_p, base_i, base_d = cfg["pid_p"], cfg["pid_i"], cfg["pid_d"]
    schedule = [(start, base_p, base_i, base_d)]
    t = start
    while t < end:
        step = timedelta(seconds=random.randint(12 * 3600, 24 * 3600))
        t += step
        if t >= end:
            break
        p = round(base_p * random.uniform(0.95, 1.05), 3)
        i = round(base_i * random.uniform(0.95, 1.05), 3)
        d = round(base_d * random.uniform(0.95, 1.05), 3)
        schedule.append((t, p, i, d))
    return schedule


def generate_mode_schedule(scenario: str, start: datetime, end: datetime) -> list[tuple[datetime, int]]:
    """生成 MODE 变化时间表（每天 0-1 次切换，手动回路始终为 0）。"""
    if scenario == "manual":
        return [(start, 0)]

    schedule = [(start, 1)]  # 默认 Auto
    t = start
    while t < end:
        step = timedelta(seconds=random.randint(24 * 3600, 72 * 3600))
        t += step
        if t >= end:
            break
        new_mode = 0 if schedule[-1][1] == 1 else 1
        schedule.append((t, new_mode))
    return schedule


# ============================================================================
# 场景数据生成
# ============================================================================

def generate_timeseries(cfg: dict, start: datetime, end: datetime) -> list[tuple]:
    """为单个回路生成完整时间序列数据。

    返回 list of (ts_str, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
    """
    interval = timedelta(seconds=SAMPLE_INTERVAL)
    sp_schedule = generate_sp_schedule(cfg["base_sp"], start, end)
    pid_schedule = generate_pid_schedule(cfg, start, end)
    mode_schedule = generate_mode_schedule(cfg["scenario"], start, end)

    # 调度索引
    sp_idx = 0
    cur_sp = sp_schedule[0][1]
    pid_idx = 0
    cur_p, cur_i, cur_d = pid_schedule[0][1], pid_schedule[0][2], pid_schedule[0][3]
    mode_idx = 0
    cur_mode = mode_schedule[0][1]

    # 初始状态
    prev_pv = cfg["base_pv"]
    prev_op = cfg["base_op"]
    scenario = cfg["scenario"]
    state: dict[str, Any] = {}

    points: list[tuple] = []
    t = start
    total_seconds = 0.0

    while t <= end:
        # 更新 SP
        while sp_idx < len(sp_schedule) and sp_schedule[sp_idx][0] <= t:
            cur_sp = sp_schedule[sp_idx][1]
            sp_idx += 1
        # 更新 PID
        while pid_idx < len(pid_schedule) and pid_schedule[pid_idx][0] <= t:
            cur_p, cur_i, cur_d = pid_schedule[pid_idx][1], pid_schedule[pid_idx][2], pid_schedule[pid_idx][3]
            pid_idx += 1
        # 更新 MODE
        while mode_idx < len(mode_schedule) and mode_schedule[mode_idx][0] <= t:
            cur_mode = mode_schedule[mode_idx][1]
            mode_idx += 1

        # 根据场景生成 PV/OP
        if scenario == "normal":
            pv, op = _gen_normal(cur_sp, prev_op, prev_pv, total_seconds)
        elif scenario == "oscillation":
            pv, op = _gen_oscillation(cur_sp, prev_op, prev_pv, total_seconds)
        elif scenario == "valve_stiction":
            pv, op = _gen_valve_stiction(cur_sp, prev_op, prev_pv, state)
        elif scenario == "op_saturation":
            pv, op = _gen_op_saturation(cur_sp, prev_op, prev_pv, total_seconds, state)
        elif scenario == "overconservative":
            pv, op = _gen_overconservative(cur_sp, prev_op, prev_pv, total_seconds)
        elif scenario == "overaggressive":
            pv, op = _gen_overaggressive(cur_sp, prev_op, prev_pv, total_seconds, state)
        elif scenario == "manual":
            pv, op = _gen_manual(cfg, cur_sp, prev_op, prev_pv, total_seconds, state)
        else:
            pv, op = _gen_normal(cur_sp, prev_op, prev_pv, total_seconds)

        # PV 质量：99% GOOD, 0.5% BAD, 0.5% UNCERTAIN
        r = random.random()
        if r < 0.995:
            pv_quality = 1
        elif r < 0.9975:
            pv_quality = 0
        else:
            pv_quality = 2

        points.append((fmt_ts(t), pv, cur_sp, op, cur_mode, cur_p, cur_i, cur_d, pv_quality))

        prev_pv = pv
        prev_op = op
        t += interval
        total_seconds += SAMPLE_INTERVAL

    return points


def _gen_normal(sp: float, prev_op: float, prev_pv: float, t: float) -> tuple[float, float]:
    """正常回路：PV 紧跟 SP，OP 平缓变化。"""
    pv = sp + random.gauss(0, abs(sp) * 0.005)
    # OP 缓慢漂移 + 跟踪 SP 偏差
    op = prev_op + (sp - prev_pv) * 0.02 + random.gauss(0, 0.3)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_oscillation(sp: float, prev_op: float, prev_pv: float, t: float) -> tuple[float, float]:
    """振荡回路：PV 正弦振荡，周期约 10 分钟。"""
    amplitude = 3.5
    period = 600.0  # 10 分钟
    omega = 2 * math.pi / period
    pv = sp + amplitude * math.sin(omega * t) + random.gauss(0, 0.3)
    # OP 反向振荡（相位差 ~π）
    op = prev_op + 0.8 * math.sin(omega * t + math.pi) + random.gauss(0, 0.2)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_valve_stiction(sp: float, prev_op: float, prev_pv: float, state: dict) -> tuple[float, float]:
    """阀门粘滞：OP 阶跃式变化，PV-OP 椭圆轨迹。"""
    error = sp - prev_pv
    desired_op = prev_op + error * 0.15
    stiction_band = 3.0

    if abs(desired_op - prev_op) >= stiction_band:
        op = desired_op  # 滑动
    else:
        op = prev_op     # 粘滞
    op = clamp(op, 0, 100)

    # PV 一阶滞后跟随 OP
    tau = 60.0
    target_pv = sp + (op - 50) * 0.3
    pv = prev_pv + (target_pv - prev_pv) * (SAMPLE_INTERVAL / tau) + random.gauss(0, 0.2)
    return round(pv, 4), round(op, 4)


def _gen_op_saturation(sp: float, prev_op: float, prev_pv: float, t: float, state: dict) -> tuple[float, float]:
    """OP 饱和：OP 长时间停留在 95-100% 或 0-5%。"""
    sat_until = state.get("sat_until", 0)
    norm_until = state.get("norm_until", 0)

    if t < sat_until:
        # 饱和期
        op = 97.0 + random.gauss(0, 0.5)
        op = clamp(op, 95, 100)
    elif t < norm_until:
        # 正常期
        error = sp - prev_pv
        op = prev_op + error * 0.1 + random.gauss(0, 0.3)
        op = clamp(op, 0, 100)
    else:
        # 决定下一段
        if random.random() < 0.4:
            state["sat_until"] = t + random.randint(1800, 3600)
            state["norm_until"] = 0
            op = 97.0 + random.gauss(0, 0.5)
            op = clamp(op, 95, 100)
        else:
            state["norm_until"] = t + random.randint(3600, 7200)
            state["sat_until"] = 0
            error = sp - prev_pv
            op = prev_op + error * 0.1 + random.gauss(0, 0.3)
            op = clamp(op, 0, 100)

    # PV 跟随 OP
    target_pv = sp + (op - 50) * 0.5
    pv = prev_pv + (target_pv - prev_pv) * 0.05 + random.gauss(0, 0.3)
    return round(pv, 4), round(op, 4)


def _gen_overconservative(sp: float, prev_op: float, prev_pv: float, t: float) -> tuple[float, float]:
    """过保守：PV 响应慢，稳态偏差大。"""
    tau = 300.0  # 大时间常数
    target_pv = sp * 0.92  # 8% 稳态偏差
    pv = prev_pv + (target_pv - prev_pv) * (SAMPLE_INTERVAL / tau) + random.gauss(0, 0.2)
    # OP 移动缓慢
    error = sp - pv
    op = prev_op + error * 0.02 + random.gauss(0, 0.1)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_overaggressive(sp: float, prev_op: float, prev_pv: float, t: float, state: dict) -> tuple[float, float]:
    """过激进：PV 过冲大，振荡后收敛。"""
    last_sp = state.get("last_sp", sp)
    sp_change_t = state.get("sp_change_t", 0.0)

    if sp != last_sp:
        state["sp_change_t"] = t
        state["last_sp"] = sp
        sp_change_t = t

    elapsed = t - sp_change_t
    omega = 0.015   # 振荡角频率
    zeta = 0.2      # 阻尼比
    overshoot = 0.3  # 30% 过冲

    if elapsed < 1200:
        decay = math.exp(-zeta * omega * elapsed)
        oscillation = overshoot * sp * decay * math.cos(omega * elapsed)
        pv = sp + oscillation + random.gauss(0, abs(sp) * 0.005)
    else:
        pv = sp + random.gauss(0, abs(sp) * 0.005)

    # OP 激进变化
    error = sp - pv
    op = prev_op + error * 0.2 + random.gauss(0, 0.5)
    op = clamp(op, 0, 100)
    return round(pv, 4), round(op, 4)


def _gen_manual(cfg: dict, sp: float, prev_op: float, prev_pv: float, t: float, state: dict) -> tuple[float, float]:
    """手动模式：OP 由操作员手动调节（阶跃变化），PV 跟随 OP。"""
    next_change = state.get("next_change", 0.0)
    op_target = state.get("op_target", prev_op)

    if t >= next_change:
        op_target = prev_op + random.uniform(-15, 15)
        op_target = clamp(op_target, 10, 90)
        state["op_target"] = op_target
        state["next_change"] = t + random.randint(3600, 10800)

    # OP 缓慢趋向目标值
    op = prev_op + (op_target - prev_op) * 0.1
    op = clamp(op, 0, 100)

    # PV 跟随 OP
    target_pv = cfg["base_sp"] + (op - 50) * 0.3
    pv = prev_pv + (target_pv - prev_pv) * 0.03 + random.gauss(0, 0.2)
    return round(pv, 4), round(op, 4)


# ============================================================================
# PostgreSQL 数据填充
# ============================================================================

async def setup_postgres(clean: bool = False) -> None:
    """填充 PostgreSQL 数据（plant_node / loop_ledger / tag_registry / loop_tag_mapping）。"""
    async with AsyncSessionLocal() as session:
        # 1. 新建装置节点
        for node in NEW_PLANT_NODES:
            await session.execute(text("""
                INSERT INTO plant_node (id, name, type, parent_id, created_at, updated_at)
                VALUES (:id, :name, :type, :parent_id, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, type = EXCLUDED.type
            """), node)

        # 2. 清理旧 Tag 映射和 Tag 注册（保留 loop_ledger）
        loop_ids = [c["id"] for c in LOOP_CONFIGS]
        # 先删映射
        await session.execute(text("DELETE FROM loop_tag_mapping WHERE loop_id = ANY(:ids)"), {"ids": loop_ids})
        # 删旧格式 Tag（T-HDS-001-PV 等）
        await session.execute(text("DELETE FROM tag_registry WHERE tag_name LIKE 'T-HDS-%' OR tag_name LIKE 'T-HDC-%'"))
        # 删新格式 Tag（HDS-RX-TIC-101.PV 等），防止重复运行冲突
        for cfg in LOOP_CONFIGS:
            for role in TAG_ROLES:
                tag_name = f"{cfg['tag_name']}.{role}"
                await session.execute(text("DELETE FROM tag_registry WHERE tag_name = :tn"), {"tn": tag_name})

        # 3. Upsert loop_ledger
        for cfg in LOOP_CONFIGS:
            await session.execute(text("""
                INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, status, last_aas_sync_at, created_at, updated_at, created_by)
                VALUES (:id, :tag_name, :description, :unit_id, 1.00, TRUE, 'READY', NOW(), NOW(), NOW(), 'admin')
                ON CONFLICT (tag_name) DO UPDATE SET
                    description = EXCLUDED.description,
                    unit_id = EXCLUDED.unit_id,
                    status = EXCLUDED.status,
                    is_active = TRUE,
                    last_aas_sync_at = NOW(),
                    updated_at = NOW()
            """), {
                "id": cfg["id"],
                "tag_name": cfg["tag_name"],
                "description": cfg["description"],
                "unit_id": cfg["unit_id"],
            })

        # 4. 创建 Tag 注册 + 映射
        for cfg in LOOP_CONFIGS:
            loop_id = cfg["id"]
            for role in TAG_ROLES:
                tag_id = str(uuid.uuid4())
                tag_name = f"{cfg['tag_name']}.{role}"
                tag_desc = f"{cfg['description']} {role}"

                # 当前值
                if role == "PV":
                    current_val = cfg["base_pv"]
                elif role == "SP":
                    current_val = cfg["base_sp"]
                elif role == "OP":
                    current_val = cfg["base_op"]
                elif role == "MODE":
                    current_val = 1.0 if cfg["scenario"] != "manual" else 0.0
                elif role == "PID_P":
                    current_val = cfg["pid_p"]
                elif role == "PID_I":
                    current_val = cfg["pid_i"]
                else:  # PID_D
                    current_val = cfg["pid_d"]

                await session.execute(text("""
                    INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked)
                    VALUES (:id, :tag_name, :desc, :type, :val, 'GOOD', NOW(), TRUE)
                    ON CONFLICT (tag_name) DO UPDATE SET
                        tag_description = EXCLUDED.tag_description,
                        tag_type = EXCLUDED.tag_type,
                        current_value = EXCLUDED.current_value,
                        quality = EXCLUDED.quality,
                        last_sync_at = NOW(),
                        is_linked = TRUE
                """), {
                    "id": tag_id,
                    "tag_name": tag_name,
                    "desc": tag_desc,
                    "type": role,
                    "val": current_val,
                })

                # 创建映射
                mapping_id = str(uuid.uuid4())
                is_required = role in REQUIRED_ROLES
                await session.execute(text("""
                    INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at)
                    VALUES (:id, :loop_id, :tag_id, :role, :req, NOW())
                    ON CONFLICT (loop_id, tag_role) DO UPDATE SET
                        tag_id = EXCLUDED.tag_id,
                        is_required = EXCLUDED.is_required
                """), {
                    "id": mapping_id,
                    "loop_id": loop_id,
                    "tag_id": tag_id,
                    "role": role,
                    "req": is_required,
                })

        # 5. 清理旧 KPI 快照和诊断结果（如果 --clean）
        if clean:
            await session.execute(text("DELETE FROM kpi_snapshot_hourly WHERE loop_id = ANY(:ids)"), {"ids": loop_ids})
            await session.execute(text("DELETE FROM diagnosis_result WHERE loop_id = ANY(:ids)"), {"ids": loop_ids})

        await session.commit()

    print(f"  ✓ PostgreSQL 数据填充完成：10 回路 / 70 Tag / 70 映射")


# ============================================================================
# TDengine 操作
# ============================================================================

async def td_execute(client: httpx.AsyncClient, sql: str, use_db: bool = True, retries: int = 3) -> dict | None:
    """执行 TDengine SQL（带重试）。"""
    url = TD_REST_DB_URL if use_db else TD_REST_BASE
    for attempt in range(retries):
        try:
            resp = await client.post(url, content=sql.encode("utf-8"), headers={"Content-Type": "text/plain"})
            result = resp.json()
            if result.get("code") == 0:
                return result
            desc = result.get("desc", "未知错误")
            # 建表已存在等非致命错误不重试
            if "already exists" in desc.lower() or "table does not exist" not in desc.lower():
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


async def setup_tdengine(client: httpx.AsyncClient, clean: bool = False) -> None:
    """创建 TDengine 数据库、超级表、子表。"""
    # 1. 创建数据库
    await td_execute(client, "CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms'", use_db=False)

    # 2. 创建超级表
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

    # 3. 创建/重建子表
    for cfg in LOOP_CONFIGS:
        sub = subtable_name(cfg["tag_name"])
        if clean:
            await td_execute(client, f"DROP TABLE IF EXISTS {sub}")
        await td_execute(client, (
            f"CREATE TABLE IF NOT EXISTS {sub} "
            f"USING st_loop_data TAGS ('{cfg['id']}', '{cfg['unit_id']}')"
        ))

    print(f"  ✓ TDengine 数据库/超级表/子表创建完成（{len(LOOP_CONFIGS)} 张子表）")


# ============================================================================
# TDengine 时序数据写入
# ============================================================================

class ProgressTracker:
    """进度跟踪器。"""
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

    # 分批写入
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        # 构建 INSERT SQL
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


async def write_all_tdengine_data(client: httpx.AsyncClient, start: datetime, end: datetime) -> int:
    """并行写入所有回路的时序数据。"""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    progress = ProgressTracker()

    tasks = [
        write_loop_data(client, semaphore, cfg, start, end, progress)
        for cfg in LOOP_CONFIGS
    ]
    results = await asyncio.gather(*tasks)
    total = sum(results)
    print(f"  ✓ TDengine 时序数据写入完成：{total} 行")
    return total


# ============================================================================
# KPI 快照生成
# ============================================================================

def calc_score(gvr: float, amr: float, sr: float, ar: float, or_: float, sat: float) -> float:
    """根据 6 项 KPI 计算综合评分（权重 10/10/30/15/20/15）。"""
    score = (
        10 * gvr + 10 * amr + 30 * sr + 15 * ar
        + 20 * (100 - or_) + 15 * (100 - sat)
    ) / 100
    return round(clamp(score, 0, 100), 2)


async def generate_kpi_snapshots(start: datetime, end: datetime) -> int:
    """为 10 个回路生成过去 N 天每小时 KPI 快照。"""
    total = 0
    async with AsyncSessionLocal() as session:
        for cfg in LOOP_CONFIGS:
            loop_id = cfg["id"]
            kpi_range = SCENARIO_KPI.get(cfg["scenario"], SCENARIO_KPI["normal"])

            # 按小时生成
            t = start.replace(minute=0, second=0, microsecond=0)
            batch_values: list[str] = []

            while t < end:
                ts_start = t
                ts_end = t + timedelta(hours=1)

                # 在范围内随机生成 KPI
                gvr = round(random.uniform(*kpi_range["gvr"]), 2)
                amr = round(random.uniform(*kpi_range["amr"]), 2)
                sr = round(random.uniform(*kpi_range["sr"]), 2)
                ar = round(random.uniform(*kpi_range["ar"]), 2)
                or_ = round(random.uniform(*kpi_range["or"]), 2)
                sat = round(random.uniform(*kpi_range["sat"]), 2)
                score = calc_score(gvr, amr, sr, ar, or_, sat)

                # 状态：95% SUCCESS, 5% PARTIAL
                status = "PARTIAL" if random.random() < 0.05 else "SUCCESS"

                snap_id = str(uuid.uuid4())
                batch_values.append(
                    f"('{snap_id}', '{loop_id}', '{ts_start.strftime('%Y-%m-%d %H:%M:%S')}', "
                    f"'{ts_end.strftime('%Y-%m-%d %H:%M:%S')}', {score}, {gvr}, {amr}, "
                    f"{sr}, {ar}, {or_}, {sat}, '{status}')"
                )

                # 每 100 条批量插入
                if len(batch_values) >= 100:
                    sql = (
                        "INSERT INTO kpi_snapshot_hourly "
                        "(id, loop_id, ts_start, ts_end, score, good_value_rate, "
                        "auto_mode_rate, steady_rate, accuracy_rate, oscillation_rate, "
                        "saturation_rate, status) VALUES " + ",".join(batch_values)
                    )
                    await session.execute(text(sql))
                    total += len(batch_values)
                    batch_values = []

                t = ts_end

            # 插入剩余
            if batch_values:
                sql = (
                    "INSERT INTO kpi_snapshot_hourly "
                    "(id, loop_id, ts_start, ts_end, score, good_value_rate, "
                    "auto_mode_rate, steady_rate, accuracy_rate, oscillation_rate, "
                    "saturation_rate, status) VALUES " + ",".join(batch_values)
                )
                await session.execute(text(sql))
                total += len(batch_values)

            await session.commit()

    print(f"  ✓ KPI 快照生成完成：{total} 条")
    return total


# ============================================================================
# 诊断结果生成
# ============================================================================

async def generate_diagnosis_results() -> int:
    """为有问题的回路生成诊断结果。"""
    total = 0
    now = datetime.now()

    async with AsyncSessionLocal() as session:
        for diag_cfg in DIAGNOSIS_CONFIGS:
            # 找到对应场景的回路
            for cfg in LOOP_CONFIGS:
                if cfg["scenario"] != diag_cfg["scenario"]:
                    continue

                loop_id = cfg["id"]
                # 每个回路生成 2 条诊断结果（不同时间）
                for _ in range(2):
                    diag_id = str(uuid.uuid4())
                    confidence = round(random.uniform(*diag_cfg["confidence"]), 2)
                    diagnosed_at = now - timedelta(hours=random.randint(1, 168))

                    # 构建 SQL（JSON 字段用 ::json）
                    features_json = json.dumps(diag_cfg["feature_values"])
                    evidence_json = json.dumps(diag_cfg["evidence_chain"])

                    await session.execute(text("""
                        INSERT INTO diagnosis_result
                        (id, loop_id, diag_label, confidence, feature_values, evidence_chain, algorithm_version, diagnosed_at)
                        VALUES (:id, :loop_id, :label, :conf, CAST(:feat AS json), CAST(:evid AS json), :ver, :diag_at)
                    """), {
                        "id": diag_id,
                        "loop_id": loop_id,
                        "label": diag_cfg["diag_label"],
                        "conf": confidence,
                        "feat": features_json,
                        "evid": evidence_json,
                        "ver": "v1.0.0",
                        "diag_at": diagnosed_at,
                    })
                    total += 1

        await session.commit()

    print(f"  ✓ 诊断结果生成完成：{total} 条")
    return total


# ============================================================================
# 主函数
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLPM 工业过程数据仿真器")
    parser.add_argument("--days", type=int, default=7, help="历史数据天数（默认 7）")
    parser.add_argument("--clean", action="store_true", help="清理旧数据后重新生成")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    start_time = datetime.now().replace(microsecond=0) - timedelta(days=args.days)
    end_time = datetime.now().replace(microsecond=0)

    print("=" * 60)
    print("  CLPM 工业过程数据仿真器")
    print(f"  时间范围: {start_time} ~ {end_time} ({args.days} 天)")
    print(f"  清理旧数据: {'是' if args.clean else '否'}")
    print("=" * 60)

    # 1. PostgreSQL 数据填充
    print("\n📋 [1/5] 填充 PostgreSQL 配置数据...")
    await setup_postgres(clean=args.clean)

    # 2. TDengine 设置
    print("\n📊 [2/5] 设置 TDengine 数据库...")
    async with httpx.AsyncClient(
        auth=httpx.BasicAuth(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD),
        timeout=httpx.Timeout(60.0),
    ) as client:
        await setup_tdengine(client, clean=args.clean)

        # 3. 生成并写入时序数据
        print("\n📈 [3/5] 生成并写入 TDengine 时序数据...")
        total_rows = await write_all_tdengine_data(client, start_time, end_time)

    # 4. KPI 快照
    print("\n📊 [4/5] 生成 KPI 小时快照...")
    total_kpi = await generate_kpi_snapshots(start_time, end_time)

    # 5. 诊断结果
    print("\n🔍 [5/5] 生成诊断结果...")
    total_diag = await generate_diagnosis_results()

    # 汇总
    print("\n" + "=" * 60)
    print("  ✅ 数据生成完成！")
    print(f"  回路数:       {len(LOOP_CONFIGS)}")
    print(f"  Tag 数:       {len(LOOP_CONFIGS) * len(TAG_ROLES)}")
    print(f"  时序数据:     {total_rows} 行")
    print(f"  KPI 快照:     {total_kpi} 条")
    print(f"  诊断结果:     {total_diag} 条")
    print("=" * 60)

    # 关闭数据库引擎
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
