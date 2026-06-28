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
    生成频率可配置（默认 1Hz）。
    """

    def __init__(self, interval: float = 1.0) -> None:
        self._interval = interval
        self._tag_configs: dict[str, dict] = {}  # tagCode → {base, amplitude, phase, noise}
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

    def generate_value(self, tag_code: str) -> dict:
        """生成单个 tag 的实时值.

        Returns:
            {"id": int, "tagCode": str, "value": str, "quality": int, "collectTime": str}
        """
        config = self._get_tag_config(tag_code)
        now = time.time()

        # 正弦波 + 噪声
        value = config["base"] + config["amplitude"] * math.sin(
            2 * math.pi * now / config["period"] + config["phase"]
        )
        value += random.uniform(-config["noise"], config["noise"])

        # 质量码约定与 HisDATA_API.md 一致：1=Good, 0=Bad/未知
        # 5% 概率为 Bad（模拟真实采集场景中的偶发异常）
        quality_code = 0 if random.random() < 0.05 else 1

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
