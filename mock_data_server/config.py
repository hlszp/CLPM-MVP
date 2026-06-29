"""模拟远端数据服务 — 配置管理.

独立于主应用，直接从环境变量读取配置（不依赖主应用 Settings）。
"""

from __future__ import annotations

import os


class Config:
    """模拟服务配置（从环境变量读取）."""

    # TDengine 连接（复用主应用数据库）
    TDENGINE_HOST: str = os.getenv("TDENGINE_HOST", "localhost")
    TDENGINE_PORT: int = int(os.getenv("TDENGINE_PORT", "6030"))
    TDENGINE_USER: str = os.getenv("TDENGINE_USER", "root")
    TDENGINE_PASSWORD: str = os.getenv("TDENGINE_PASSWORD", "taosdata")
    TDENGINE_DB: str = os.getenv("TDENGINE_DB", "clpm_ts")

    # 服务端口
    PORT: int = int(os.getenv("MOCK_DATA_SERVER_PORT", "8100"))

    # 实时数据生成间隔（秒）
    REALTIME_INTERVAL: float = float(os.getenv("REALTIME_INTERVAL", "1.0"))

    # 质量码异常比例（0.0~1.0），模拟工控场景中的采集异常
    # 0.0 = 全部 Good；0.1 = 10% Bad（工控场景典型值）
    QUALITY_BAD_RATIO: float = float(os.getenv("QUALITY_BAD_RATIO", "0.0"))

    @property
    def tdengine_rest_url(self) -> str:
        """TDengine REST API base URL."""
        return f"http://{self.TDENGINE_HOST}:{self.TDENGINE_PORT + 11}"

    @property
    def tdengine_auth(self) -> tuple[str, str]:
        """TDengine BasicAuth."""
        return (self.TDENGINE_USER, self.TDENGINE_PASSWORD)


config = Config()
