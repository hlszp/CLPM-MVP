#!/usr/bin/env python3
"""指定回路历史数据仿真器 —— 闭环仿真生成 PV/OP/MODE 历史曲线写入 TDengine 宽表。

针对指定回路、指定时间窗口生成仿真历史数据（overwrite 语义：先删除窗口内
旧数据再写入），用于回路评估、诊断、整定功能的演示与验证。

仿真架构（V2，2026-08-24 重写）::

    数据由真实闭环仿真生成：PI 控制器 + FOPDT 过程对象（K/tau/theta），
    OP→PV 存在真实因果传递函数。整定辨识（tuning_identification）用
    ARX/ARMAX/CLIVC 恢复过程模型时验证集自由仿真 R² 可达 0.9+，
    通过 A/B 级可信度门禁。

    V1 的缺陷：PV 直接围绕 SP 生成、OP 独立累积，OP 与 PV 无因果链，
    辨识自由仿真必然失败 → D/E 级可信度门禁拦截。

场景设计::

    90PIC51212A_PIDA  PI 适中整定，SP 四次小幅阶跃，PV 跟随良好
    80TIC40108_PIDA   前半程手动（OP 阶跃=开环黄金辨识数据），6:00 切自动
    80TIC10506_PIDA   过激进 PI（3× 增益），每次 SP 阶跃后大幅超调衰减振荡

用法::

    cd backend && uv run python scripts/simulate_loop_history.py
    cd backend && uv run python scripts/simulate_loop_history.py \
        --start '2026-08-24 00:00:00' --end '2026-08-24 15:00:00'
    cd backend && uv run python scripts/simulate_loop_history.py --dry-run  # 仅本地自检辨识可信度
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import datetime, timedelta

import httpx

from app.core.config import settings

# ============================================================================
# 常量
# ============================================================================

SAMPLE_INTERVAL = 10  # 采样间隔（秒）
BATCH_SIZE = 1000  # TDengine 批量写入行数
TD_REST_BASE = f"http://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT + 11}/rest/sql"
TD_REST_DB_URL = f"{TD_REST_BASE}/{settings.TDENGINE_DB}"

random.seed(20260824)  # 可复现随机种子


# ============================================================================
# 回路仿真配置
#
# 过程对象均为 FOPDT: G(s) = K·e^(-theta·s) / (tau·s + 1)
# theta 为采样周期整数倍（保证离散延迟搜索可精确命中）
# 控制器为增量式 PI（生产 DCS 常见形态），过激进场景增益取 3×
# ============================================================================

LOOP_CONFIGS: list[dict] = [
    {
        "tag_name": "90PIC51212A_PIDA",
        "subtable": "d_loop_90pic51212a_pida",
        "description": "TK521A辛醇罐顶部压力（PI适中，SP阶跃跟随良好）",
        # 稳态基点：OP=u0 时 PV=sp0
        "u0": 50.0,
        "sp0": 2.50,
        # 过程对象 FOPDT
        "K": 0.010,  # MPa/%（OP 变 10% → PV 变 0.1）
        "tau": 40.0,  # 秒
        "theta": 10.0,  # 秒（= 1 个采样周期）
        # PI 控制器（λ 法整定，λ=tau）
        "kc": 80.0,  # %/MPa
        "ti": 40.0,  # 秒
        # SP 阶跃计划: (小时偏移, SP)。9.5h 步落在辨识验证集窗口（9-12h）
        "sp_schedule": [(0.0, 2.50), (3.5, 2.55), (7.0, 2.45), (9.5, 2.52), (11.5, 2.50)],
        # 记录用 PID 显示值（只读，来自当前实时值）
        "pid_display": (0.6, 0.06, 0.0),
        "pv_noise": 0.0002,  # PV 测量噪声标准差（MPa）
        "op_quant": 0.1,  # OP 量化步长（%）
        "manual_windows": [],  # 无手动段
    },
    {
        "tag_name": "80TIC40108_PIDA",
        "subtable": "d_loop_80tic40108_pida",
        "description": "T-101塔顶轻组分分析（前段手动OP阶跃→开环辨识黄金数据，6:00切自动）",
        "u0": 50.0,
        "sp0": 0.83,
        "K": 0.005,  # 单位/%
        "tau": 200.0,
        "theta": 20.0,  # = 2 个采样周期
        "kc": 333.0,
        "ti": 100.0,
        "sp_schedule": [(0.0, 0.83), (8.0, 0.84), (10.0, 0.825), (12.5, 0.83)],
        "pid_display": (1.0, 0.05, 0.0),
        "pv_noise": 0.00015,
        "op_quant": 0.1,
        # 手动段 OP 阶跃计划（开环激励）: (小时偏移, OP)
        "manual_op_steps": [(0.0, 50.0), (1.0, 46.0), (2.5, 54.0), (4.0, 50.0), (5.0, 52.0)],
        "switch_auto_hour": 6.0,  # 6:00 切自动（OP 无扰切换：从当前值起步）
    },
    {
        "tag_name": "80TIC10506_PIDA",
        "subtable": "d_loop_80tic10506_pida",
        "description": "T-101塔底温度（过激进PI 3×增益：SP阶跃后大幅超调衰减振荡）",
        "u0": 58.0,
        "sp0": 56.1,
        "K": 0.20,  # °C/%（OP 变 10% → PV 变 2°C）
        "tau": 150.0,
        "theta": 30.0,  # = 3 个采样周期
        # 过激进：λ 法正常 kc≈4.2，取 3 倍 + 短 Ti → 欠阻尼
        "kc": 13.0,
        "ti": 40.0,
        "sp_schedule": [(0.0, 56.1), (4.0, 58.0), (8.0, 56.5), (10.5, 57.8), (12.5, 57.2)],
        "pid_display": (2.0, 0.03, 0.0),
        "pv_noise": 0.005,
        "op_quant": 0.1,
        "manual_windows": [],
    },
]


# ============================================================================
# 工具函数
# ============================================================================


def fmt_ts(dt: datetime) -> str:
    """格式化时间戳为 TDengine 字符串（毫秒精度）。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def fmt_float(v: float | None) -> str:
    if v is None or v != v or v in (float("inf"), float("-inf")):
        return "NULL"
    return f"{v:.4f}"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def sp_at(schedule: list[tuple[float, float]], hours: float) -> float:
    cur = schedule[0][1]
    for h, sp in schedule:
        if hours >= h:
            cur = sp
        else:
            break
    return cur


def manual_op_at(steps: list[tuple[float, float]], hours: float) -> float:
    cur = steps[0][1]
    for h, v in steps:
        if hours >= h:
            cur = v
        else:
            break
    return cur


def gen_quality() -> int:
    """PV 质量码：99.4% GOOD / 0.3% BAD / 0.3% UNCERTAIN。"""
    r = random.random()
    if r < 0.994:
        return 1
    if r < 0.997:
        return 0
    return 2


# ============================================================================
# 闭环仿真器（PI 控制器 + FOPDT 过程）
# ============================================================================


def simulate_closed_loop(cfg: dict, start: datetime, end: datetime) -> list[tuple]:
    """闭环仿真：增量式 PI + FOPDT（ZOH 离散）。

    过程（偏差变量 x = PV - sp0，v = OP - u0）::
        x[k] = x[k-1] + (ts/tau)·(K·v[k-d] - x[k-1]) + w[k]

    控制器（增量式 PI，带输出限幅与积分冻结抗饱和）::
        e[k] = SP[k] - PV_meas[k]
        Δop = kc·(e[k] - e[k-1]) + kc·ts/ti·e[k]
        op[k] = clamp(op[k-1] + Δop)

    手动模式：OP 由操作员阶跃计划（一阶平滑趋向目标），过程照常响应。

    返回 list of (ts_str, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
    """
    dt = SAMPLE_INTERVAL
    d = int(round(cfg["theta"] / dt))  # 离散延迟（采样周期数）
    n_delay_buf = max(d, 1)

    kc, ti = cfg["kc"], cfg["ti"]
    K, tau = cfg["K"], cfg["tau"]
    u0, sp0 = cfg["u0"], cfg["sp0"]
    noise = cfg["pv_noise"]
    opq = cfg.get("op_quant", 0.0)

    pid_p, pid_i, pid_d = cfg["pid_display"]
    manual_steps = cfg.get("manual_op_steps") or []
    switch_auto_hour = cfg.get("switch_auto_hour")

    # 状态初始化（稳态）
    x = 0.0  # 过程偏差变量
    op = u0
    e_prev = 0.0
    v_hist = [0.0] * n_delay_buf  # OP 偏差延迟缓冲

    points: list[tuple] = []
    t = start
    while t < end:
        hours = (t - start).total_seconds() / 3600.0
        sp = sp_at(cfg["sp_schedule"], hours)

        # 判定模式
        if manual_steps and (switch_auto_hour is None or hours < switch_auto_hour):
            mode = 0
        else:
            mode = 1

        # PV 测量值（真值 + 测量噪声）
        pv_meas = sp0 + x + random.gauss(0, noise)

        if mode == 0:
            # 手动：OP 平滑趋向操作员目标（斜率 2%/min 量级）
            op_target = manual_op_at(manual_steps, hours)
            op += clamp(op_target - op, -0.5, 0.5)
        else:
            # 自动：增量式 PI
            e = sp - pv_meas
            d_op = kc * (e - e_prev) + kc * dt / ti * e
            new_op = op + d_op
            # 抗饱和：饱和时冻结积分项（保留比例项）
            if new_op <= 0.0 or new_op >= 100.0:
                _new_op_sat = clamp(new_op, 0.0, 100.0)
                # 仅保留比例分量
                new_op = op + kc * (e - e_prev)
                new_op = clamp(new_op, 0.0, 100.0)
            op = new_op
            e_prev = e

        # OP 量化（模拟 DCS 输出精度）
        if opq > 0:
            op = round(op / opq) * opq

        # FOPDT 过程响应（偏差变量；注意延迟缓冲推进顺序）
        v = op - u0
        v_hist.append(v)
        v_delayed = v_hist.pop(0) if d >= 1 else v
        x += (dt / tau) * (K * v_delayed - x)

        points.append(
            (
                fmt_ts(t),
                round(pv_meas, 4),
                sp,
                round(op, 2),
                mode,
                pid_p,
                pid_i,
                pid_d,
                gen_quality(),
            )
        )
        t += timedelta(seconds=dt)

    return points


# ============================================================================
# 本地辨识自检（写入前验证可信度，避免写库后才发现场景不可辨识）
# ============================================================================


def verify_identification(cfg: dict, points: list[tuple]) -> dict:
    """对生成的仿真数据直接跑整定辨识管线，返回可信度摘要。"""
    from app.services.tuning_identification import identify_from_history

    op = [p[3] for p in points]
    pv = [p[1] for p in points]
    sp = [p[2] for p in points]
    mode = [p[4] for p in points]

    result = identify_from_history(op=op, pv=pv, sp=sp, mode=mode, ts=float(SAMPLE_INTERVAL))
    if not result.success:
        return {"success": False, "reason": result.reason}
    best = result.best_model
    return {
        "success": True,
        "model_type": best.params.model_type.value,
        "K": round(best.params.K, 6),
        "tau": round(best.params.tau, 2),
        "theta": round(best.params.theta, 2),
        "fitting": best.fitting_score,
        "confidence": best.confidence.value,
        "reason": best.reason[:120],
    }


# ============================================================================
# TDengine 操作
# ============================================================================


async def td_execute(client: httpx.AsyncClient, sql: str) -> bool:
    resp = await client.post(
        TD_REST_DB_URL, content=sql.encode("utf-8"), headers={"Content-Type": "text/plain"}
    )
    result = resp.json()
    if result.get("code") != 0:
        print(f"  ✗ TDengine SQL 错误: {result.get('desc', '未知')[:200]}")
        return False
    return True


async def delete_window(
    client: httpx.AsyncClient, subtable: str, start: datetime, end: datetime
) -> bool:
    """删除时间窗口内旧数据（overwrite 语义，end 闭合边界用 999ms）。"""
    sql = (
        f"DELETE FROM {subtable} WHERE ts >= '{fmt_ts(start)}' "
        f"AND ts <= '{end.strftime('%Y-%m-%d %H:%M:%S')}.999'"
    )
    return await td_execute(client, sql)


async def write_points(client: httpx.AsyncClient, subtable: str, points: list[tuple]) -> int:
    """分批写入仿真数据。"""
    written = 0
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        parts = [f"INSERT INTO {subtable} VALUES"]
        for pt in batch:
            ts_str, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_q = pt
            parts.append(
                f"('{ts_str}', {fmt_float(pv)}, {fmt_float(sp)}, {fmt_float(op)}, "
                f"{mode}, {fmt_float(pid_p)}, {fmt_float(pid_i)}, {fmt_float(pid_d)}, {pv_q})"
            )
        if await td_execute(client, " ".join(parts)):
            written += len(batch)
    return written


# ============================================================================
# 主流程
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="指定回路历史数据仿真器（闭环仿真 V2）")
    parser.add_argument("--start", default="2026-08-24 00:00:00", help="起始时间（本地时间）")
    parser.add_argument("--end", default="2026-08-24 15:00:00", help="结束时间（本地时间，不含）")
    parser.add_argument("--no-delete", action="store_true", help="跳过窗口内旧数据删除")
    parser.add_argument(
        "--dry-run", action="store_true", help="仅生成本地数据并自检辨识可信度，不写库"
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(args.end, "%Y-%m-%d %H:%M:%S")

    print("=" * 64)
    print("  指定回路历史数据仿真器（闭环仿真 V2：PI + FOPDT 因果链）")
    print(f"  时间窗口: {start} ~ {end}（{SAMPLE_INTERVAL}s 间隔）")
    print(f"  模式: {'自检（不写库）' if args.dry_run else '写入 TDengine'}")
    print("=" * 64)

    all_points: list[tuple[dict, list[tuple]]] = []

    # 1. 生成 + 本地辨识自检
    for cfg in LOOP_CONFIGS:
        print(f"\n▶ {cfg['tag_name']}（{cfg['description']}）")
        print(
            f"  过程对象: K={cfg['K']}, tau={cfg['tau']}s, theta={cfg['theta']}s; "
            f"PI: kc={cfg['kc']}, ti={cfg['ti']}s"
        )
        points = simulate_closed_loop(cfg, start, end)
        print(f"  生成 {len(points)} 个仿真点")

        print("  本地辨识自检...")
        verdict = verify_identification(cfg, points)
        if verdict["success"]:
            print(
                f"    → {verdict['model_type']} K={verdict['K']} tau={verdict['tau']}s "
                f"theta={verdict['theta']}s | 拟合 {verdict['fitting']}% | "
                f"可信度 {verdict['confidence']}"
            )
        else:
            print(f"    → ✗ 辨识失败: {verdict['reason'][:150]}")
        all_points.append((cfg, points))

    if args.dry_run:
        print("\n✅ 自检完成（--dry-run，未写库）")
        return

    # 2. 写入 TDengine
    async with httpx.AsyncClient(
        auth=httpx.BasicAuth(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD),
        timeout=httpx.Timeout(60.0),
    ) as client:
        for cfg, points in all_points:
            print(f"\n▶ 写入 {cfg['tag_name']}")
            if not args.no_delete:
                ok = await delete_window(client, cfg["subtable"], start, end)
                print(f"  删除窗口旧数据: {'✓' if ok else '✗（中止该回路写入）'}")
                if not ok:
                    continue
            written = await write_points(client, cfg["subtable"], points)
            print(f"  写入 TDengine: {written} 行")

    print("\n✅ 仿真数据生成完成")


if __name__ == "__main__":
    asyncio.run(main())
