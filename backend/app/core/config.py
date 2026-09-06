"""Application configuration via pydantic-settings.

Loads environment variables from `.env` (case-sensitive).

安全：
- 数据库/TDengine/Redis 密码为必填项，无硬编码默认值
- DEBUG 默认 False，生产环境不暴露 /docs
- 生产环境（DEBUG=False）启动时强制校验 JWT_SECRET_KEY 和密码安全性
"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 开发环境已知的不安全密码（生产环境禁止使用）
_INSECURE_PG_PASSWORD = "clpm_dev_2026"
_INSECURE_TD_PASSWORD = "taosdata"


class Settings(BaseSettings):
    """Global application settings."""

    # ---- Application ----
    APP_NAME: str = "CLPM"
    APP_VERSION: str = "7.0.0"
    DEBUG: bool = False
    ENV: str = "development"  # development / production

    # ---- PostgreSQL ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 7102
    POSTGRES_USER: str = "clpm"
    POSTGRES_PASSWORD: str = ""  # 必填，通过 .env 设置
    POSTGRES_DB: str = "clpm"

    # ---- TDengine ----
    TDENGINE_HOST: str = "localhost"
    TDENGINE_PORT: int = 7104
    TDENGINE_USER: str = "root"
    TDENGINE_PASSWORD: str = ""  # 必填，通过 .env 设置
    TDENGINE_DB: str = "clpm_ts"
    # 批量写入批次大小（一条 INSERT SQL 插入的行数，实测 1000 行 7ms）
    TDENGINE_BATCH_SIZE: int = 1000
    # TDengine REST 阻塞操作超时（秒）。taosrest 默认 timeout=None（无限等待），
    # TDengine 侧连接半开/无响应时导入任务的写库调用会无限挂起（表现为"进度停滞"），
    # 显式超时后异常上抛走分块级容错与失败上报。
    TDENGINE_REST_TIMEOUT: int = 60
    # 实时数据 flush 间隔（秒，RealtimeSubscriber 缓冲区刷新频率）
    TDENGINE_FLUSH_INTERVAL: float = 1.0

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 7103
    REDIS_PASSWORD: str = ""  # 必填，通过 .env 设置
    REDIS_DB: int = 0

    # ---- JWT ----
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ---- Celery ----
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    # ---- AAS (OPC UA) ----
    AAS_ENDPOINT: str = "opc.tcp://localhost:4840"
    AAS_MOCK_MODE: bool = True
    AAS_SYNC_INTERVAL_SECONDS: int = 300  # 5 分钟
    AAS_SYNC_ENABLED: bool = True
    AAS_CONNECT_TIMEOUT_SECONDS: int = 10
    AAS_REQUEST_TIMEOUT_SECONDS: int = 30
    AAS_SECURITY_MODE: str = "SignAndEncrypt"  # None/Sign/SignAndEncrypt

    # ---- 历史数据导入接口（已废止"数据源切换"概念）----
    # 架构决策（2026-07-20）：计算类历史数据查询（性能评估/诊断/整定）一律走本地
    # TDengine；远端历史数据接口（remote_api）仅"数据管理→历史数据导入"任务直接调用。
    # 本配置仅作兼容保留，不再影响计算路径的数据源选择。
    DATA_SOURCE_TYPE: str = "remote_api"

    # ---- 网络模式（局域网/公网切换，控制 Tailscale 子网路由）----
    # lan: 局域网直连（默认，生产环境）；wan: 公网走 Tailscale（调试用）
    NETWORK_MODE: str = "lan"

    # ---- 外部历史数据 API（HistoryDataAppService）----
    HISTORY_DATA_API_URL: str = ""  # 如 http://localhost:7106/api/services/v1/HistoryData/Get
    HISTORY_DATA_API_TOKEN: str = ""  # Bearer Token（可选）
    HISTORY_DATA_API_TIMEOUT: float = 120.0  # 请求超时（秒，远端大跨度查询可能慢）

    # ---- 远端 API 调用保护（限流 + 熔断，防止压垮边缘 API 或向其持续施压）----
    REMOTE_API_MAX_CONCURRENCY: int = 4  # 单进程对远端 API 的最大并发请求数
    REMOTE_API_CIRCUIT_FAILURES: int = 5  # 连续失败达到该次数后触发熔断
    REMOTE_API_CIRCUIT_OPEN_SECONDS: float = 300.0  # 熔断持续秒数，到期后半开探测

    # ---- 实时数据 SignalR/WebSocket ----
    SIGNALR_HUB_URL: str = ""  # 如 ws://localhost:7106/signalr/realValueForClpmHub
    SIGNALR_ENABLED: bool = True  # 是否启用实时数据订阅（sys_config 预载可覆盖）
    SIGNALR_RECONNECT_INTERVAL: int = 5  # 断线重连基础间隔（秒，指数退避起点）
    SIGNALR_RECONNECT_MAX_INTERVAL: int = 30  # 断线重连最大间隔（秒，指数退避上限）
    # WS 客户端参数（放宽默认值，适配过载边缘服务器）
    # 协议级 ping 默认禁用（0）：生产 AAS（.NET SignalR）不应答 WebSocket 协议级
    # ping，启用后每 ping_interval+ping_timeout 周期误判"keepalive ping timeout"
    # 断线重连，造成周期数据缺口（2026-09-05 zpdev 实锤，~105s 一次）。
    # 连接活性由 SignalR 应用层 ping（type=6，自动回 pong）+ 30s recv 看门狗
    # + SIGNALR_STALL_TIMEOUT_SECONDS 停滞阈值覆盖；仅对明确应答协议 ping 的
    # Hub（如本地仿真器）才建议设 >0。
    SIGNALR_PING_INTERVAL: int = 0  # 心跳间隔（秒），0=禁用协议级 ping
    SIGNALR_PING_TIMEOUT: int = 60  # 心跳超时（秒），默认 60
    SIGNALR_OPEN_TIMEOUT: int = 15  # 连接建立超时（秒），默认 15
    # 数据停滞看门狗：N 秒无消息主动断开重连（覆盖"WS 活着但上游停推"盲区）
    SIGNALR_STALL_TIMEOUT_SECONDS: int = 300  # 默认 5 分钟
    # 全量重订阅间隔（秒）：AAS 口径"同一点位只订阅一次"，重订阅仅用于低频信号
    # （SP/MODE/PID）保鲜，禁止高频重发（2026-09-05 曾为 60s 全量 8649 点重发，
    # 疑诱发 AAS 网关会话回收）。刷新自愈检查不受此间隔影响，仍每分钟执行。
    SIGNALR_RESUBSCRIBE_INTERVAL: int = 1800  # 默认 30 分钟
    # 是否将实时数据写回本地 TDengine 宽表（数据架构优化 Phase 1）
    REALTIME_WRITEBACK_ENABLED: bool = True
    # 多 worker 进程订阅单例：Leader 锁 TTL（秒），持锁进程每 TTL/3 续期；
    # 持锁进程崩溃/退出后其他 worker 在 TTL 内接管（uvicorn --workers 4 防护）
    SUBSCRIBER_LEADER_LOCK_TTL_SECONDS: int = 30

    # ---- 实时历史缓存与 WS 扇出预算（数据链路整改 R02/R15，S0 固定默认值）----
    # 每回路 Redis 历史缓存（realtime:history:*）最大行数：LPUSH 后 LTRIM 截断，
    # 超出部分由读取方回源本地 TDengine（原 4500 行在 ~961 回路满载下可超
    # 512MiB 容器预算，见审查报告 R02）
    REALTIME_HISTORY_MAX_POINTS_PER_LOOP: int = 1200
    # 全部 realtime:history:* 键的近似 JSON 载荷预算（字节）：超预算时新回路
    # 停止写入历史缓存（最新值缓存与 TDengine 写回不受影响），并计入丢弃计数
    REALTIME_HISTORY_GLOBAL_BUDGET_BYTES: int = 64 * 1024 * 1024
    # WS 每客户端有界队列上限（按 tag 合并后的条目数；溢出判定慢消费者，
    # 断开连接要求客户端恢复快照）
    WS_CLIENT_QUEUE_MAX: int = 500
    # WS 单帧发送期限（秒）：超期限定该客户端为慢消费者并断开
    WS_SEND_TIMEOUT_SECONDS: float = 5.0

    # ---- 断点续传（实时数据缺口自动补全）----
    # SignalR 断线/进程重启导致的数据缺口，重连成功后自动调用远端历史数据接口补全
    GAP_BACKFILL_ENABLED: bool = False  # 默认关闭，运行时经 sys_config（UI 链路配置页）调整
    GAP_BACKFILL_MIN_GAP_SECONDS: int = 600  # 默认 10 分钟；运行时经 sys_config 调整
    GAP_BACKFILL_MAX_HOURS: int = 24  # 单次补数最大窗口（超出部分截断并告警，需手工导入）
    GAP_BACKFILL_RETRY_BASE_SECONDS: int = 300  # 补数失败重试起步退避（5 分钟，连接在线也生效）
    GAP_BACKFILL_RETRY_MAX_SECONDS: int = 1800  # 补数失败重试退避上限（30 分钟，指数翻倍封顶）
    # 断点续传 SETNX 分布式锁（多副本防重复补数）
    GAP_BACKFILL_LOCK_TTL_SECONDS: int = 7200  # 锁 TTL（2 小时，覆盖单次补数最长时长）

    # ---- 导入任务生命周期 ----
    IMPORT_TASK_TTL_DAYS: int = 30  # 导入任务 Redis 记录 TTL（天）
    IMPORT_TASK_RUNNING_TIMEOUT_SECONDS: int = 7200  # RUNNING 超时阈值（2h，兜底旧任务无心跳）
    # 进度停滞阈值（秒）：任务每完成一个分块都会写 last_progress_at 心跳，
    # 超过该时长无任何进度推进即判定卡死（清扫置 FAILED / 重投允许接续）。
    # 需大于最慢单分块耗时上限（远端 120s×4 次重试 + 写库 60s×11 批 ≈ 19min）。
    IMPORT_TASK_STALL_TIMEOUT_SECONDS: int = 1800

    # ---- 数据链路监控 ----
    DATA_LINK_CHECK_INTERVAL_MINUTES: int = 10  # 链路健康检查 Beat 间隔（分钟）
    DATA_LINK_FRESHNESS_THRESHOLD_MINUTES: int = 30  # TDengine 数据新鲜度阈值（分钟）

    # ---- Alerting ----
    ALERT_WEBHOOK_URL: str = ""  # 告警 webhook URL，为空则仅记录日志

    # ---- CORS ----
    CORS_ORIGINS: list[str] = [
        "http://localhost:7100",
        "http://localhost:7141",
        "http://127.0.0.1:7100",
        "http://127.0.0.1:7141",
    ]

    @property
    def postgres_dsn(self) -> str:
        """Async PostgreSQL DSN for SQLAlchemy 2.0."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{quote_plus(self.POSTGRES_PASSWORD)}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        """Redis URL with optional password."""
        if self.REDIS_PASSWORD:
            return f"redis://:{quote_plus(self.REDIS_PASSWORD)}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def celery_broker_url(self) -> str:
        """Celery broker URL (Redis with password)."""
        if self.CELERY_BROKER_URL:
            return self.CELERY_BROKER_URL
        if self.REDIS_PASSWORD:
            return (
                f"redis://:{quote_plus(self.REDIS_PASSWORD)}@{self.REDIS_HOST}:{self.REDIS_PORT}/1"
            )
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/1"

    @property
    def celery_result_backend(self) -> str:
        """Celery result backend URL (Redis with password)."""
        if self.CELERY_RESULT_BACKEND:
            return self.CELERY_RESULT_BACKEND
        if self.REDIS_PASSWORD:
            return (
                f"redis://:{quote_plus(self.REDIS_PASSWORD)}@{self.REDIS_HOST}:{self.REDIS_PORT}/2"
            )
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/2"

    def validate_security(self) -> None:
        """生产环境安全校验：ENV=production 时强制校验密钥、密码和 AAS 安全模式。"""
        if self.ENV != "production":
            return

        # JWT 密钥校验
        if not self.JWT_SECRET_KEY:
            raise RuntimeError("生产环境必须通过环境变量 JWT_SECRET_KEY 设置密钥。")
        if len(self.JWT_SECRET_KEY) < 32:
            raise RuntimeError("生产环境 JWT_SECRET_KEY 长度不得少于 32 字符。")

        # 数据库密码校验
        if not self.POSTGRES_PASSWORD:
            raise RuntimeError("生产环境必须通过环境变量 POSTGRES_PASSWORD 设置数据库密码。")
        if self.POSTGRES_PASSWORD == _INSECURE_PG_PASSWORD:
            raise RuntimeError("生产环境不得使用开发默认数据库密码。")

        # TDengine 密码校验（计算类历史数据查询一律走本地 TDengine，必须校验）
        if not self.TDENGINE_PASSWORD:
            raise RuntimeError("生产环境必须通过环境变量 TDENGINE_PASSWORD 设置 TDengine 密码。")
        if self.TDENGINE_PASSWORD == _INSECURE_TD_PASSWORD:
            raise RuntimeError("生产环境不得使用 TDengine 默认密码 taosdata。")

        # Redis 密码校验
        if not self.REDIS_PASSWORD:
            raise RuntimeError("生产环境必须通过环境变量 REDIS_PASSWORD 设置 Redis 密码。")

        # AAS OPC UA 安全模式校验
        if self.AAS_SECURITY_MODE == "None":
            raise RuntimeError(
                "生产环境 AAS_SECURITY_MODE 不得为 None，必须使用 Sign 或 SignAndEncrypt。"
            )

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()

# 启动时执行安全校验（仅生产环境）
settings.validate_security()
