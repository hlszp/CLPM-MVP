"""模拟远端数据服务 — 实时数据生成器.

简化版 realtime_simulator：按固定间隔生成正弦波 + 随机噪声的模拟数据，
供 WebSocket Hub 推送给订阅客户端。
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RealtimeGenerator:
    """实时数据生成器.

    为每个 tagCode 生成正弦波 + 噪声的模拟实时值。
    MODE 角色生成离散值 0/1/2，至少 4 小时变化一次。
    生成频率可配置（默认 1Hz）。
    """

    # MODE 切换间隔（秒），实际工程至少 4 小时变化一次
    _MODE_CHANGE_INTERVAL = 4 * 3600  # 4 小时
    # MODE 离散值：0=Manual, 1=Auto, 2=Cascade
    _MODE_VALUES = [0, 1, 2]

    def __init__(self, interval: float = 1.0) -> None:
        self._interval = interval
        self._tag_configs: dict[str, dict] = {}  # tagCode → {base, amplitude, phase, noise}
        self._mode_states: dict[str, dict] = {}  # tagCode → {current, rng, offset, last_period}
        self._id_counter = 1000
        self._lock = asyncio.Lock()

    def _get_tag_config(self, tag_code: str) -> dict:
        """获取或初始化 tag 配置（每个 tag 有不同的波形参数）."""
        if tag_code not in self._tag_configs:
            # 基于 tag_code 哈希生成稳定的波形参数
            seed = hash(tag_code) % 1000
            rng = random.Random(seed)
            self._tag_configs[tag_code] = {
                "base": rng.uniform(20, 80),
                "amplitude": rng.uniform(5, 15),
                "phase": rng.uniform(0, 2 * math.pi),
                "noise": rng.uniform(0.1, 0.5),
                "period": rng.uniform(60, 300),  # 振荡周期（秒）
            }
        return self._tag_configs[tag_code]

    def _generate_mode_value(self, tag_code: str, now: float) -> int:
        """生成 MODE 离散值 0/1/2，至少 4 小时变化一次.

        使用 tag_code hash 确定初始模式和相位偏移，
        确保不同回路的 MODE 变化时刻不同但可复现。
        """
        if tag_code not in self._mode_states:
            seed = hash(tag_code) % 1000
            rng = random.Random(seed)
            self._mode_states[tag_code] = {
                "current": rng.choice(self._MODE_VALUES),
                "rng": rng,
                "offset": rng.uniform(0, self._MODE_CHANGE_INTERVAL),
                "last_period": None,
            }
        state = self._mode_states[tag_code]
        # 按（时间 + 偏移）/ 4小时 判断是否进入新周期
        period = int((now + state["offset"]) // self._MODE_CHANGE_INTERVAL)
        if period != state["last_period"]:
            state["last_period"] = period
            # 切换到不同的模式（不重复当前模式）
            choices = [v for v in self._MODE_VALUES if v != state["current"]]
            state["current"] = state["rng"].choice(choices)
        return state["current"]

    def generate_value(self, tag_code: str) -> dict:
        """生成单个 tag 的实时值.

        MODE 角色：离散值 0/1/2，至少 4 小时变化一次。
        其他角色：正弦波 + 噪声。

        Returns:
            {"id": int, "tagCode": str, "value": str, "quality": int, "collectTime": str}
        """
        now = time.time()

        # MODE 角色：生成离散值，不随正弦波变化
        if tag_code.upper().endswith(".MODE"):
            value = self._generate_mode_value(tag_code, now)
        else:
            config = self._get_tag_config(tag_code)
            # 正弦波 + 噪声
            value = config["base"] + config["amplitude"] * math.sin(
                2 * math.pi * now / config["period"] + config["phase"]
            )
            value += random.uniform(-config["noise"], config["noise"])

        # 质量码约定与 HisDATA_API.md 一致：1=Good, 0=Bad/未知
        # 异常比例由 QUALITY_BAD_RATIO 配置控制（工控场景典型值 10%）
        from mock_data_server.config import config
        quality_code = 0 if random.random() < config.QUALITY_BAD_RATIO else 1

        self._id_counter += 1
        return {
            "id": self._id_counter,
            "tagCode": tag_code,
            "value": f"{value:.3f}",
            "quality": quality_code,
            "collectTime": datetime.now(timezone.utc).isoformat(),
        }

    def generate_batch(self, tag_codes: list[str]) -> list[dict]:
        """批量生成多个 tag 的实时值."""
        return [self.generate_value(tc) for tc in tag_codes]


# 全局单例
_generator: RealtimeGenerator | None = None


def get_generator() -> RealtimeGenerator:
    """获取全局 RealtimeGenerator 单例."""
    global _generator
    if _generator is None:
        from mock_data_server.config import config

        _generator = RealtimeGenerator(interval=config.REALTIME_INTERVAL)
    return _generator
