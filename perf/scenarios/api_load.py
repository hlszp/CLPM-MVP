"""API 响应时间测试场景（PERF-API-001 ~ PERF-API-006）.

本模块提供 6 个独立的 TaskSet，每个对应一个 PERF-API 用例，可单独运行以
精确控制并发数、持续时间和验收阈值。

用法示例:
    # 单独运行 PERF-API-001 登录压测
    locust -f perf/scenarios/api_load.py:LoginLoadTest \
        --host=http://localhost:7101 --headless -u 50 -r 10 -t 120s

    # 单独运行 PERF-API-006 工作台聚合
    locust -f perf/scenarios/api_load.py:DashboardLoadTest \
        --host=http://localhost:7101 --headless -u 100 -r 10 -t 180s

验收标准（P95）:
    PERF-API-001 登录接口        < 200ms
    PERF-API-002 回路列表查询    < 300ms
    PERF-API-003 回路监控列表    < 500ms
    PERF-API-004 诊断列表        < 400ms
    PERF-API-005 波形查询-24h    < 500ms
    PERF-API-006 工作台聚合 API  < 500ms
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone

from locust import HttpUser, TaskSet, between, task

# ---------------------------------------------------------------------------
# 公共配置
# ---------------------------------------------------------------------------

HOST = os.environ.get("CLPM_PERF_HOST", "http://localhost:7101")
USERNAME = os.environ.get("CLPM_PERF_USERNAME", "admin")
PASSWORD = os.environ.get("CLPM_PERF_PASSWORD", "admin123")
USER_POOL = [
    u for u in os.environ.get(
        "CLPM_PERF_USERS",
        "admin,ic_engineer,pe_engineer,sponsor,expert",
    ).split(",") if u.strip()
]

LOOP_IDS = [
    "00000000-0000-0000-0000-000000000201",
    "00000000-0000-0000-0000-000000000202",
    "00000000-0000-0000-0000-000000000203",
]
PLANT_NODE_IDS = [
    "00000000-0000-0000-0000-000000000101",
    "00000000-0000-0000-0000-000000000102",
    "00000000-0000-0000-0000-000000000103",
    "00000000-0000-0000-0000-000000000104",
]


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def login_and_get_token(client, username: str, password: str) -> str | None:
    """通过 /api/v1/auth/login 获取 accessToken."""
    with client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "rememberMe": False},
        name="POST /auth/login (setup)",
        catch_response=True,
    ) as resp:
        if resp.status_code != 200:
            return None
        try:
            return resp.json().get("data", {}).get("accessToken")
        except (ValueError, AttributeError):
            return None


# ---------------------------------------------------------------------------
# PERF-API-001: 登录接口
# ---------------------------------------------------------------------------


class LoginTaskSet(TaskSet):
    """PERF-API-001: 登录接口压测（50 并发，2 分钟，P95 < 200ms）."""

    def on_start(self) -> None:
        self.username = random.choice(USER_POOL)

    @task
    def login(self) -> None:
        with self.client.post(
            "/api/v1/auth/login",
            json={
                "username": self.username,
                "password": PASSWORD,
                "rememberMe": False,
            },
            name="PERF-API-001 登录",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200 and resp.json().get("data", {}).get("accessToken"):
                resp.success()
            else:
                resp.failure(f"登录失败 status={resp.status_code}")


class LoginLoadTest(HttpUser):
    """PERF-API-001 入口类: locust -f perf/scenarios/api_load.py:LoginLoadTest."""

    wait_time = between(1, 3)
    host = HOST
    tasks = [LoginTaskSet]


# ---------------------------------------------------------------------------
# PERF-API-002: 回路列表查询
# ---------------------------------------------------------------------------


class LoopListTaskSet(TaskSet):
    """PERF-API-002: GET /api/v1/loops（100 并发，3 分钟，P95 < 300ms）."""

    def on_start(self) -> None:
        self.username = random.choice(USER_POOL)
        token = login_and_get_token(self.client, self.username, PASSWORD)
        if token:
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task
    def list_loops(self) -> None:
        params = {"page": 1, "pageSize": 20}
        if random.random() < 0.3:
            params["plantNodeId"] = random.choice(PLANT_NODE_IDS)
        if random.random() < 0.2:
            params["controlMode"] = random.choice(["Manual", "Auto", "Cascade"])
        if random.random() < 0.2:
            params["keyword"] = random.choice(["TIC", "FIC", "LIC"])

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


class LoopListLoadTest(HttpUser):
    """PERF-API-002 入口类."""

    wait_time = between(1, 3)
    host = HOST
    tasks = [LoopListTaskSet]


# ---------------------------------------------------------------------------
# PERF-API-003: 回路监控列表
# ---------------------------------------------------------------------------


class LoopMonitorTaskSet(TaskSet):
    """PERF-API-003: GET /api/v1/loops/monitor（100 并发，3 分钟，P95 < 500ms）."""

    def on_start(self) -> None:
        self.username = random.choice(USER_POOL)
        token = login_and_get_token(self.client, self.username, PASSWORD)
        if token:
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task
    def list_monitor(self) -> None:
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


class LoopMonitorLoadTest(HttpUser):
    """PERF-API-003 入口类."""

    wait_time = between(1, 3)
    host = HOST
    tasks = [LoopMonitorTaskSet]


# ---------------------------------------------------------------------------
# PERF-API-004: 诊断列表
# ---------------------------------------------------------------------------


class DiagnosisListTaskSet(TaskSet):
    """PERF-API-004: GET /api/v1/diagnosis/list（80 并发，3 分钟，P95 < 400ms）."""

    def on_start(self) -> None:
        self.username = random.choice(USER_POOL)
        token = login_and_get_token(self.client, self.username, PASSWORD)
        if token:
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task
    def list_diagnosis(self) -> None:
        params = {"page": 1, "pageSize": 20}
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


class DiagnosisListLoadTest(HttpUser):
    """PERF-API-004 入口类."""

    wait_time = between(1, 3)
    host = HOST
    tasks = [DiagnosisListTaskSet]


# ---------------------------------------------------------------------------
# PERF-API-005: 波形查询-24小时
# ---------------------------------------------------------------------------


class WaveformTaskSet(TaskSet):
    """PERF-API-005: GET /api/v1/timeseries/{loopId}/waveform（50 并发，2 分钟，P95 < 500ms）."""

    def on_start(self) -> None:
        self.username = random.choice(USER_POOL)
        token = login_and_get_token(self.client, self.username, PASSWORD)
        if token:
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task
    def query_waveform(self) -> None:
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


class WaveformLoadTest(HttpUser):
    """PERF-API-005 入口类."""

    wait_time = between(1, 3)
    host = HOST
    tasks = [WaveformTaskSet]


# ---------------------------------------------------------------------------
# PERF-API-006: 工作台聚合 API
# ---------------------------------------------------------------------------


class DashboardOverviewTaskSet(TaskSet):
    """PERF-API-006: GET /api/v1/dashboard/overview（100 并发，3 分钟，P95 < 500ms）."""

    def on_start(self) -> None:
        self.username = random.choice(USER_POOL)
        token = login_and_get_token(self.client, self.username, PASSWORD)
        if token:
            self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task
    def dashboard_overview(self) -> None:
        params = {"granularity": random.choice(["day", "week", "month"])}
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


class DashboardLoadTest(HttpUser):
    """PERF-API-006 入口类."""

    wait_time = between(1, 3)
    host = HOST
    tasks = [DashboardOverviewTaskSet]
