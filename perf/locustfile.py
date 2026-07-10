"""CLPM Locust 主压测脚本.

覆盖清单中的 6 个 API 性能用例（PERF-API-001 ~ PERF-API-006）。

用法示例:
    # 启动 Web UI（默认 http://localhost:8089）
    locust -f perf/locustfile.py --host=http://localhost:7101

    # 无头模式运行单个场景（PERF-API-001 登录接口）
    locust -f perf/locustfile.py --host=http://localhost:7101 \
        --headless -u 50 -r 10 -t 120s --tags login

    # 运行回路相关场景（PERF-API-002 + PERF-API-003）
    locust -f perf/locustfile.py --host=http://localhost:7101 \
        --headless -u 100 -r 10 -t 180s --tags loop

    # 运行全部场景
    locust -f perf/locustfile.py --host=http://localhost:7101 \
        --headless -u 100 -r 10 -t 180s

环境变量（可选，覆盖默认值）:
    CLPM_PERF_HOST          后端 host（同 --host）
    CLPM_PERF_USERNAME      登录用户名（默认 admin）
    CLPM_PERF_PASSWORD      登录密码（默认 admin123）
    CLPM_PERF_USERS         逗号分隔的用户名列表，用于多用户轮换
"""

from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta, timezone

from locust import HttpUser, between, task, tag

# ---------------------------------------------------------------------------
# 配置（环境变量覆盖）
# ---------------------------------------------------------------------------

DEFAULT_HOST = os.environ.get("CLPM_PERF_HOST", "http://localhost:7101")
DEFAULT_USERNAME = os.environ.get("CLPM_PERF_USERNAME", "admin")
DEFAULT_PASSWORD = os.environ.get("CLPM_PERF_PASSWORD", "admin123")
# 多用户轮换池：5 个种子用户，密码均为 admin123
USER_POOL = [
    u for u in os.environ.get(
        "CLPM_PERF_USERS",
        "admin,ic_engineer,pe_engineer,sponsor,expert",
    ).split(",") if u.strip()
]
PASSWORD = DEFAULT_PASSWORD

# 已知回路 ID（来自 db/postgresql/02_seed_data.sql），用于波形/详情查询
LOOP_IDS = [
    "00000000-0000-0000-0000-000000000201",
    "00000000-0000-0000-0000-000000000202",
    "00000000-0000-0000-0000-000000000203",
]

# 已知装置/单元 ID，用于筛选
PLANT_NODE_IDS = [
    "00000000-0000-0000-0000-000000000101",  # 加氢联合车间
    "00000000-0000-0000-0000-000000000102",  # 加氢精制
    "00000000-0000-0000-0000-000000000103",  # 加氢裂化
    "00000000-0000-0000-0000-000000000104",  # S Zorb
]


def _iso(dt: datetime) -> str:
    """格式化为 ISO 8601 字符串（不带微秒，带时区）。"""
    return dt.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# ClpmUser — 模拟 CLPM 平台真实用户
# ---------------------------------------------------------------------------


class ClpmUser(HttpUser):
    """CLPM 平台压测用户.

    - on_start: 登录获取 accessToken，写入 self.client.headers
    - wait_time: 1~3 秒，模拟用户思考/操作间隔
    - 通过 @tag 分类任务，可用 --tags 选择运行场景
    """

    # wait_time 1-3 秒，覆盖 PERF 用例要求
    wait_time = between(1, 3)
    host = DEFAULT_HOST

    # 默认使用 admin；每个实例从用户池随机选一个，模拟多角色并发
    username: str = DEFAULT_USERNAME
    password: str = PASSWORD
    access_token: str | None = None

    def on_start(self) -> None:
        """用户启动时登录获取 token."""
        # 从用户池随机选一个用户（每个 locust 实例固定一个，避免并发登录同一账号被锁）
        self.username = random.choice(USER_POOL)
        self.password = PASSWORD
        self._login()

    def _login(self) -> None:
        """调用 /api/v1/auth/login 获取 accessToken."""
        with self.client.post(
            "/api/v1/auth/login",
            json={
                "username": self.username,
                "password": self.password,
                "rememberMe": False,
            },
            name="POST /auth/login",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"登录失败 status={resp.status_code} body={resp.text[:200]}")
                return
            try:
                data = resp.json().get("data", {})
                token = data.get("accessToken")
            except (ValueError, AttributeError):
                resp.failure("登录响应解析失败")
                return
            if not token:
                resp.failure("登录响应缺少 accessToken")
                return
            self.access_token = token
            # 后续所有请求自动带上 Authorization 头
            self.client.headers.update({"Authorization": f"Bearer {token}"})
            resp.success()

    # ------------------------------------------------------------------
    # PERF-API-001: 登录接口（50 并发，2 分钟，P95 < 200ms）
    # ------------------------------------------------------------------
    @task(1)
    @tag("login", "perf-api-001")
    def login(self) -> None:
        """PERF-API-001: 登录接口压测.

        验收: 50 并发 / 2 分钟 / P95 < 200ms
        运行: locust -f perf/locustfile.py --tags login -u 50 -r 10 -t 120s
        """
        # 每次重新登录（使用当前用户名 + 随机扰动，避免 token 缓存）
        with self.client.post(
            "/api/v1/auth/login",
            json={
                "username": self.username,
                "password": self.password,
                "rememberMe": False,
            },
            name="PERF-API-001 登录",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200 and resp.json().get("data", {}).get("accessToken"):
                resp.success()
            else:
                resp.failure(f"登录失败 status={resp.status_code}")

    # ------------------------------------------------------------------
    # PERF-API-002: 回路列表查询（100 并发，3 分钟，P95 < 300ms）
    # ------------------------------------------------------------------
    @task(3)
    @tag("loop", "perf-api-002")
    def list_loops(self) -> None:
        """PERF-API-002: GET /api/v1/loops 回路列表查询.

        验收: 100 并发 / 3 分钟 / P95 < 300ms
        运行: locust -f perf/locustfile.py --tags loop -u 100 -r 10 -t 180s
        """
        params = {
            "page": 1,
            "pageSize": 20,
        }
        # 随机附加筛选条件，模拟真实查询
        if random.random() < 0.3:
            params["plantNodeId"] = random.choice(PLANT_NODE_IDS)
        if random.random() < 0.2:
            params["controlMode"] = random.choice(["Manual", "Auto", "Cascade"])
        if random.random() < 0.2:
            params["keyword"] = random.choice(["TIC", "FIC", "LIC", "PIC"])

        with self.client.get(
            "/api/v1/loops",
            params=params,
            name="PERF-API-002 回路列表",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"回路列表失败 status={resp.status_code}")

    # ------------------------------------------------------------------
    # PERF-API-003: 回路监控列表（100 并发，3 分钟，P95 < 500ms）
    # ------------------------------------------------------------------
    @task(3)
    @tag("loop", "monitor", "perf-api-003")
    def list_loop_monitor(self) -> None:
        """PERF-API-003: GET /api/v1/loops/monitor 回路监控列表.

        验收: 100 并发 / 3 分钟 / P95 < 500ms
        运行: locust -f perf/locustfile.py --tags monitor -u 100 -r 10 -t 180s
        """
        params = {
            "page": 1,
            "pageSize": 20,
            "view": random.choice(["list", "card"]),
        }
        if random.random() < 0.3:
            params["plantNodeId"] = random.choice(PLANT_NODE_IDS)

        with self.client.get(
            "/api/v1/loops/monitor",
            params=params,
            name="PERF-API-003 回路监控列表",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"回路监控列表失败 status={resp.status_code}")

    # ------------------------------------------------------------------
    # PERF-API-004: 诊断列表（80 并发，3 分钟，P95 < 400ms）
    # ------------------------------------------------------------------
    @task(2)
    @tag("diagnosis", "perf-api-004")
    def list_diagnosis(self) -> None:
        """PERF-API-004: GET /api/v1/diagnosis/list 诊断列表.

        验收: 80 并发 / 3 分钟 / P95 < 400ms
        运行: locust -f perf/locustfile.py --tags diagnosis -u 80 -r 10 -t 180s
        """
        params = {
            "page": 1,
            "pageSize": 20,
        }
        if random.random() < 0.3:
            params["plantNodeId"] = random.choice(PLANT_NODE_IDS)
        if random.random() < 0.3:
            params["timeWindow"] = random.choice(
                ["last_24_hours", "last_7_days", "last_30_days"]
            )
        if random.random() < 0.2:
            params["actionStatus"] = random.choice(
                ["PENDING", "IN_PROGRESS", "RESOLVED", "IGNORED"]
            )

        with self.client.get(
            "/api/v1/diagnosis/list",
            params=params,
            name="PERF-API-004 诊断列表",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"诊断列表失败 status={resp.status_code}")

    # ------------------------------------------------------------------
    # PERF-API-005: 波形查询-24小时（50 并发，2 分钟，P95 < 500ms）
    # ------------------------------------------------------------------
    @task(2)
    @tag("diagnosis", "waveform", "perf-api-005")
    def query_waveform_24h(self) -> None:
        """PERF-API-005: GET /api/v1/timeseries/{loopId}/waveform 波形查询-24小时.

        验收: 50 并发 / 2 分钟 / P95 < 500ms
        运行: locust -f perf/locustfile.py --tags waveform -u 50 -r 10 -t 120s
        """
        loop_id = random.choice(LOOP_IDS)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=24)
        params = {
            "startTime": _iso(start_time),
            "endTime": _iso(end_time),
            "maxPoints": 5000,
        }

        with self.client.get(
            f"/api/v1/timeseries/{loop_id}/waveform",
            params=params,
            name="PERF-API-005 波形查询-24h",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"波形查询失败 status={resp.status_code}")

    # ------------------------------------------------------------------
    # PERF-API-006: 工作台聚合 API（100 并发，3 分钟，P95 < 500ms）
    # ------------------------------------------------------------------
    @task(3)
    @tag("dashboard", "perf-api-006")
    def dashboard_overview(self) -> None:
        """PERF-API-006: GET /api/v1/dashboard/overview 工作台聚合 API.

        验收: 100 并发 / 3 分钟 / P95 < 500ms
        运行: locust -f perf/locustfile.py --tags dashboard -u 100 -r 10 -t 180s
        """
        params = {
            "granularity": random.choice(["day", "week", "month"]),
        }
        if random.random() < 0.3:
            params["plantId"] = random.choice(PLANT_NODE_IDS)

        with self.client.get(
            "/api/v1/dashboard/overview",
            params=params,
            name="PERF-API-006 工作台聚合",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"工作台聚合失败 status={resp.status_code}")
