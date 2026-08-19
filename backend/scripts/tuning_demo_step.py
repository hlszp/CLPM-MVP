#!/usr/bin/env python3
"""整定演示：单阶跃测试合成数据写入（09 设计方案阶跃路径联调用途）。

向指定回路 TDengine 子表写入一段单阶跃响应窗口，两种场景：
- ``op``：开环阶跃实验（MODE=0 手动）——OP 人工单阶跃 50→55，PV 开环
  FOPDT 响应。用于阶跃辨识路径（POST /tuning/identify）正向跑通。
- ``sp``：闭环 SP 阶跃（MODE=1 自动）——SP 单阶跃 2.5→2.625 MPa，PI 控制器
  （Kp=1.5, Ti=30s）驱动 OP 使 PV 跟踪。用于验证阶跃路径的单阶跃校验器
  按设计拒绝闭环数据（MV 非瞬时单阶跃），该场景应走历史辨识路径（CLIVC）。

对象真值与 tuning_demo_excitation.py 一致：FOPDT K=0.025 MPa/%, τ=20s, θ=5s。
写入前 DELETE 窗口内既有数据（幂等，可重复执行）。

用法::

    cd backend && uv run python scripts/tuning_demo_step.py \
        --scenario op --loop-tag 90PIC51212A_PIDA \
        --start 2026-08-18T14:30:00Z --hours 1
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import settings

BATCH = 1000
REST_URL = (
    f"http://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT + 11}/rest/sql/{settings.TDENGINE_DB}"
)

# 对象真值（与历史辨识演示窗口一致，匹配 90PIC51212A_PIDA 量程 0-5 MPa）
K_TRUE = 0.025  # MPa/%
TAU_TRUE = 20.0  # s
THETA_TRUE = 5.0  # s
PV_STEADY = 2.5  # MPa（OP=50% 稳态工作点）
PV_OFFSET = PV_STEADY - K_TRUE * 50.0
OP0 = 50.0
NOISE_SIGMA = 0.002

# OP 阶跃场景参数
OP_STEP_TO = 55.0  # +5% → PV 稳态 +0.125 MPa

# SP 阶跃场景参数（闭环 PI 控制器，pid 列写入同组参数保持一致性）
SP_STEP_TO = 2.625  # +0.125 MPa → OP 稳态需 +5%
PID_KP = 1.5
PID_TI = 30.0  # s
PID_TD_WRITTEN = 5.0  # s（仅写入 pid_d 列；仿真用 PI 已足够，D 对平滑信号贡献可忽略）

STEP_FRACTION = 1 / 3  # 阶跃发生在窗口 1/3 处（校验器要求两侧留 ≥10% 边距）


def _subtable(loop_tag: str) -> str:
    return f"d_loop_{loop_tag.lower().replace('-', '_')}"


def _sql(client: httpx.Client, sql: str) -> dict:
    resp = client.post(
        REST_URL, content=sql, auth=(settings.TDENGINE_USER, settings.TDENGINE_PASSWORD)
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"TDengine SQL 失败: {data.get('desc')} | SQL: {sql[:120]}")
    return data


def _get_tags(client: httpx.Client, table: str) -> tuple[str, str]:
    data = _sql(client, f"SELECT loop_id, unit_id FROM {table} LIMIT 1")
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"子表 {table} 无数据，无法读取 loop_id/unit_id 标签")
    return str(rows[0][0]), str(rows[0][1])


def _simulate(scenario: str, n: int) -> list[tuple[float, float, float, int]]:
    """生成 (pv, sp, op, mode) 四元组序列（1s 网格）。"""
    a = math.exp(-1.0 / TAU_TRUE)
    d = int(round(THETA_TRUE))
    step_idx = int(n * STEP_FRACTION)
    pv = PV_STEADY
    integral = OP0 / (PID_KP / PID_TI)  # 使初始 OP=50（e=0 稳态）
    op_hist = [OP0] * d  # 纯滞后缓冲：进入循环时长度恒为 d+i，op_hist[i] 即 d 秒前 OP
    out: list[tuple[float, float, float, int]] = []
    for i in range(n):
        if scenario == "op":
            mode = 0
            sp = PV_STEADY
            op = OP0 if i < step_idx else OP_STEP_TO
        else:  # sp 闭环
            mode = 1
            sp = PV_STEADY if i < step_idx else SP_STEP_TO
            err = sp - pv  # 以上一拍 PV 计算（含上一拍噪声）
            integral += err
            op = PID_KP * err + (PID_KP / PID_TI) * integral
        delayed_op = op_hist[i]
        op_hist.append(op)
        pv = a * pv + K_TRUE * (1 - a) * delayed_op + (1 - a) * PV_OFFSET
        pv_meas = pv + random.gauss(0, NOISE_SIGMA)
        out.append((pv_meas, sp, op, mode))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="整定演示：单阶跃测试合成数据写入")
    parser.add_argument(
        "--scenario",
        choices=["op", "sp"],
        required=True,
        help="op=开环 OP 单阶跃（阶跃路径正向）；sp=闭环 SP 单阶跃（校验器拒绝演示）",
    )
    parser.add_argument("--loop-tag", default="90PIC51212A_PIDA")
    parser.add_argument("--start", required=True, help="窗口起始（UTC ISO）")
    parser.add_argument("--hours", type=float, default=1.0)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(UTC)
    n = int(args.hours * 3600)
    table = _subtable(args.loop_tag)

    random.seed(42)
    with httpx.Client(timeout=60) as client:
        loop_id, unit_id = _get_tags(client, table)
        end = start + timedelta(seconds=n)
        _sql(
            client,
            f"DELETE FROM {table} WHERE ts >= '{start:%Y-%m-%dT%H:%M:%S}.000Z' "
            f"AND ts < '{end:%Y-%m-%dT%H:%M:%S}.000Z'",
        )

        rows: list[str] = []
        for i, (pv, sp, op, mode) in enumerate(_simulate(args.scenario, n)):
            ts = start + timedelta(seconds=i)
            rows.append(
                f"('{ts:%Y-%m-%dT%H:%M:%S}.000Z', {pv:.4f}, {sp:.4f}, {op:.4f}, "
                f"{mode}, {PID_KP}, {PID_TI}, {PID_TD_WRITTEN}, 1)"
            )
            if len(rows) >= BATCH:
                _sql(client, f"INSERT INTO {table} VALUES {' '.join(rows)}")
                rows.clear()
        if rows:
            _sql(client, f"INSERT INTO {table} VALUES {' '.join(rows)}")

        cnt = _sql(
            client,
            f"SELECT COUNT(*) FROM {table} WHERE ts >= '{start:%Y-%m-%dT%H:%M:%S}.000Z' "
            f"AND ts < '{end:%Y-%m-%dT%H:%M:%S}.000Z'",
        )
        step_at = start + timedelta(seconds=int(n * STEP_FRACTION))
        print(
            f"写入完成[{args.scenario}]: {table} 窗口 [{start:%Y-%m-%d %H:%M}Z, "
            f"{end:%Y-%m-%d %H:%M}Z) 共 {cnt['data'][0][0]} 行"
            f"（loop_id={loop_id}, unit_id={unit_id}）"
        )
        if args.scenario == "op":
            print(f"OP 单阶跃: {OP0}→{OP_STEP_TO} @ {step_at:%H:%M:%S}Z，MODE=0（手动开环）")
        else:
            print(f"SP 单阶跃: {PV_STEADY}→{SP_STEP_TO} @ {step_at:%H:%M:%S}Z，MODE=1（闭环 PI）")
        print(f"对象真值: FOPDT K={K_TRUE} MPa/%, τ={TAU_TRUE}s, θ={THETA_TRUE}s")


if __name__ == "__main__":
    main()
