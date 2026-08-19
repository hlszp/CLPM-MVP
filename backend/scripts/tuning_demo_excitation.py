#!/usr/bin/env python3
"""整定演示：激励充分合成数据写入（09 设计方案联调用途）。

向指定回路 TDengine 子表写入一段 FOPDT 合成响应窗口：
- OP：伪随机多电平阶跃序列（每 5 min 换档，全窗口持续激励，种子固定可复现）
- PV：按已知 FOPDT（K=0.025 MPa/%, τ=20s, θ=5s）ZOH 离散响应 + 小噪声（σ=0.002）
- MODE=0（手动——开环阶跃物理一致）、pv_quality=1（Good）、SP 恒值
- 写入前 DELETE 窗口内既有数据（幂等，可重复执行）

用法::

    cd backend && uv run python scripts/tuning_demo_excitation.py \
        --loop-tag 90PIC51212A_PIDA --start 2026-08-18T10:00:00Z --hours 4
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

# FOPDT 真值（演示目标模型；PV 稳态 = PV_OFFSET + K×OP，匹配 90PIC51212A_PIDA 量程 0-5 MPa）
K_TRUE = 0.025  # MPa/%（OP ±5% → PV ±0.125 MPa，信噪比充足）
TAU_TRUE = 20.0  # s（FAST 压力回路）
THETA_TRUE = 5.0  # s
PV_STEADY_AT_OP50 = 2.5  # MPa（对齐该回路实时运行工况）
PV_OFFSET = PV_STEADY_AT_OP50 - K_TRUE * 50.0
OP0 = 50.0
NOISE_SIGMA = 0.002
STEP_PERIOD_S = 300  # 5 min/档 = 15τ，每档充分到达稳态
# 多电平激励必须覆盖整个窗口：辨识按 60/20/20 时序切分 train/val/test，
# 若序列提前耗尽、尾部恒定，验证段无激励会导致 R²_val=0 / MAGNITUDE_MISMATCH。
# 这里按窗口长度动态生成伪随机多电平序列（种子固定，可复现）。
_OP_LEVEL_CHOICES = (47, 48, 49, 52, 53, 54, 55, 56)  # ±3~6% 围绕 OP0=50


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


def main() -> None:
    parser = argparse.ArgumentParser(description="整定演示：激励充分合成数据写入")
    parser.add_argument("--loop-tag", default="90PIC51212A_PIDA")
    parser.add_argument("--start", default="2026-08-18T10:00:00Z", help="窗口起始（UTC ISO）")
    parser.add_argument("--hours", type=float, default=4.0)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(UTC)
    n = int(args.hours * 3600)
    table = _subtable(args.loop_tag)

    random.seed(42)
    with httpx.Client(timeout=60) as client:
        loop_id, unit_id = _get_tags(client, table)
        end = start + timedelta(seconds=n)
        # 幂等：先清窗口
        _sql(
            client,
            f"DELETE FROM {table} WHERE ts >= '{start:%Y-%m-%dT%H:%M:%S}.000Z' "
            f"AND ts < '{end:%Y-%m-%dT%H:%M:%S}.000Z'",
        )

        # 生成 FOPDT 响应（ZOH 离散：pv[k]=a·pv[k-1]+K(1-a)·op[k-d]+c；c 保证 OP=50 稳态 2.5 MPa）
        a = math.exp(-1.0 / TAU_TRUE)
        d = int(round(THETA_TRUE))
        # 全窗口伪随机多电平 OP 序列（首档 OP0 起步，之后逐档随机换电平）
        n_steps = n // STEP_PERIOD_S + 1
        op_levels = [OP0] + [random.choice(_OP_LEVEL_CHOICES) for _ in range(n_steps)]
        op_series = [op_levels[min(i // STEP_PERIOD_S, len(op_levels) - 1)] for i in range(n)]
        pv = PV_STEADY_AT_OP50
        rows: list[str] = []
        for i in range(n):
            delayed_op = op_series[i - d] if i >= d else OP0
            pv = (
                a * pv
                + K_TRUE * (1 - a) * delayed_op
                + (1 - a) * PV_OFFSET
                + random.gauss(0, NOISE_SIGMA)
            )
            ts = start + timedelta(seconds=i)
            rows.append(
                f"('{ts:%Y-%m-%dT%H:%M:%S}.000Z', {pv:.4f}, {PV_STEADY_AT_OP50:.2f}, "
                f"{op_series[i]:.2f}, 0, 1.5, 30.0, 5.0, 1)"
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
        print(
            f"写入完成: {table} 窗口 [{start:%Y-%m-%d %H:%M}Z, {end:%Y-%m-%d %H:%M}Z) "
            f"共 {cnt['data'][0][0]} 行（loop_id={loop_id}, unit_id={unit_id}）"
        )
        print(f"合成模型真值: FOPDT K={K_TRUE}, τ={TAU_TRUE}s, θ={THETA_TRUE}s")


if __name__ == "__main__":
    main()
