"""P4-03 C25 真实端到端并发负例（验收正本，03§7 P4-03 卡）.

与 mock 单测的区别：**全链路真实，无 mock 替代**（14 号文口径）——

1. **真实 PostgreSQL scratch 库**：bootstrap SQL（01+02 含 p403 guard 行）
   + stamp head；scenario 种子（A/B 装置树、回路、tag 绑定、共享/未绑定
   tag、scope 用户）直接落库；
2. **真实 uvicorn 子进程**：完整 app（lifespan 自动拉起 **真实 Celery
   Worker/Beat 子进程**——隔离 broker，禁与 dev 共 broker 抢任务）；
3. **真实 WebSocket**：`websockets` 客户端连 `WS /api/v1/ws/realtime` 与
   `WS /api/v1/ws/alerts`，帧经**真实 Redis Pub/Sub**（隔离实例）发布，
   服务端分发过滤/发送栅栏/失权关闭全链真实；
4. **真实锁并发**：guard 行 FOR SHARE/FOR UPDATE 在真实 PG 上验证
   撤权与提交的可线性排序（共享锁持有期间排他锁阻塞的交错实证）。

C25 反例覆盖（03§7 验收原文逐项）：
- A/B 节点全 HTTP 入口正反例（loops 列表交集/详情 403/原始 tag rejected/
  报告聚合交集/任务产物下载 403）；
- 共享/无绑定 tag（共享只回值不泄露回路元信息；未绑定仅 ADMIN）；
- 未订阅旧客户端（最多转发授权集合；raw 载荷仅 ADMIN）；
- 预警推送不泄漏 B（loopId 过滤 + 无 loopId 仅 ADMIN）；
- 连接内降权/禁用/重置后无新越权帧（不靠前端退订；4003 关闭）；
- 排队/运行中任务撤权（真实 Worker 消费隔离 broker 任务，禁用并发下
  无新结果发布 + REVOKED 审计；线性不变量：撤权完成后发布的 run 不存在）；
- 提交栅栏并发（真实锁交错：FOR SHARE 持有 → disable 阻塞；反向 →
  SubjectRevokedError）；
- 缓存 key 授权 hash+revision（结构断言在单测；此处验证 guard revision
  随撤权推进 → 缓存 key 自然换）。

安全边界（双确认）：
- PG：复用 ``CLPM_BOOTSTRAP_TEST_ADMIN_DSN``（loopback+管理库护栏，
  与 test_p4_01_real_pg.py 同款）；每运行一次性 scratch 库 finally DROP；
- Redis：**显式隔离实例**——``CLPM_C25_ISOLATED_REDIS_PORT`` 必须指向
  本机一次性容器端口；17103（dev）/7103（原项目）端口显式拒绝；
  运行示例：
      docker run -d --name clpm-p403-redis-e2e -p 127.0.0.1::6379 \\
          redis:7-alpine --save "" --appendonly no
      CLPM_C25_ISOLATED_REDIS_PORT=<打印的端口> \\
      CLPM_BOOTSTRAP_TEST_ADMIN_DSN=postgresql://clpm:***@127.0.0.1:17102/postgres \\
          uv run pytest tests/integration/test_p4_03_c25_real.py -v -m integration
- TDengine：计算读 dev MVP 实例（127.0.0.1:17104，**只读**，scenario
  回路无历史数据 → 诊断产出 DATA_INSUFFICIENT 正式结论——发布物存在性
  即验收对象，与数据内容无关）。

CI 跳过：pyproject addopts -m "not integration" 默认排除。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import httpx
import pytest
import redis.asyncio as aioredis
import websockets

_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SCHEMA_SQL = _ROOT / "db" / "postgresql" / "01_schema.sql"
_SEED_SQL = _ROOT / "db" / "postgresql" / "02_seed_data.sql"
_ADMIN_ENV = "CLPM_BOOTSTRAP_TEST_ADMIN_DSN"
_REDIS_PORT_ENV = "CLPM_C25_ISOLATED_REDIS_PORT"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
#: 显式禁用的共享 Redis 端口（dev MVP=17103，原项目=7103）——隔离实例必须避开
_FORBIDDEN_REDIS_PORTS = {17103, 7103}
_PASSWORD = "Admin@123"

pytestmark = [pytest.mark.integration]


def _admin_dsn() -> str:
    dsn = os.getenv(_ADMIN_ENV)
    if not dsn:
        pytest.skip(f"{_ADMIN_ENV} 未设置；拒绝猜测或复用开发/生产数据库")
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        pytest.fail(f"{_ADMIN_ENV} 必须使用 postgresql:// DSN")
    if parsed.hostname not in _LOOPBACK_HOSTS:
        pytest.fail(f"{_ADMIN_ENV} 仅允许 loopback PostgreSQL，实际为 {parsed.hostname!r}")
    if parsed.path.lstrip("/") not in {"postgres", "template1"}:
        pytest.fail(f"{_ADMIN_ENV} 必须指向 postgres/template1 管理库")
    return dsn


def _isolated_redis_port() -> int:
    raw = os.getenv(_REDIS_PORT_ENV)
    if not raw:
        pytest.skip(
            f"{_REDIS_PORT_ENV} 未设置；C25 必须使用显式隔离 Redis 实例"
            "（docker run -d -p 127.0.0.1::6379 redis:7-alpine，禁与 dev 共 broker）"
        )
    port = int(raw)
    if port in _FORBIDDEN_REDIS_PORTS:
        pytest.fail(f"隔离 Redis 端口 {port} 属共享实例（dev/原项目），拒绝使用")
    return port


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _pg_dsn(admin_dsn: str, database: str, scheme: str = "postgresql") -> str:
    parsed = urlsplit(admin_dsn)
    return urlunsplit(parsed._replace(path=f"/{database}", scheme=scheme, query="", fragment=""))


# ---------------------------------------------------------------------------
# scenario 种子（A/B 双装置树 + 回路/tag 绑定 + scope 用户）
# ---------------------------------------------------------------------------


async def _seed_scenario(raw_dsn: str) -> dict[str, str]:
    """落 scenario 种子，返回关键 ID 集。"""
    from app.core.security import hash_password

    ids = {k: str(uuid4()) for k in ("factory_a", "unit_a1", "factory_b", "unit_b1")}
    tag_ids = {
        "TAG_A_PV": str(uuid4()),
        "TAG_B_PV": str(uuid4()),
        "TAG_SH_PV": str(uuid4()),
        "TAG_UN_PV": str(uuid4()),
    }
    ids.update({f"tag_{k}": v for k, v in tag_ids.items()})
    ids["loop_a"] = str(uuid4())
    ids["loop_b"] = str(uuid4())

    conn = await asyncpg.connect(raw_dsn)
    try:
        await conn.execute("BEGIN")
        # 装置树：ROOT（既有种子根）→ FACTORY_A/B → UNIT
        root = await conn.fetchval("SELECT id FROM plant_node WHERE parent_id IS NULL LIMIT 1")
        for key, parent, name, ntype in (
            ("factory_a", root, "C25-A工厂", "FACTORY"),
            ("unit_a1", ids["factory_a"], "C25-A1单元", "UNIT"),
            ("factory_b", root, "C25-B工厂", "FACTORY"),
            ("unit_b1", ids["factory_b"], "C25-B1单元", "UNIT"),
        ):
            await conn.execute(
                "INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled)"
                " VALUES ($1, $2, $3, $4, TRUE)",
                ids[key],
                name,
                ntype,
                parent,
            )
        # tag 注册表（A/B 专属 + 共享 + 未绑定；tag_ids: name -> uuid）
        for name, tid in tag_ids.items():
            await conn.execute(
                "INSERT INTO tag_registry (id, tag_name, tag_type, last_sync_at)"
                " VALUES ($1, $2, 'MEASURE', now())",
                tid,
                name,
            )
        # 回路（A1/B1 各一；共享 tag 同时绑定两回路）
        await conn.execute(
            "INSERT INTO loop_ledger (id, tag_name, unit_id, status, is_active,"
            " include_in_evaluation) VALUES ($1, 'C25-LIC-A101', $2, 'READY', TRUE, TRUE)",
            ids["loop_a"],
            ids["unit_a1"],
        )
        await conn.execute(
            "INSERT INTO loop_ledger (id, tag_name, unit_id, status, is_active,"
            " include_in_evaluation) VALUES ($1, 'C25-LIC-B201', $2, 'READY', TRUE, TRUE)",
            ids["loop_b"],
            ids["unit_b1"],
        )
        for loop_id, role, tid, required in (
            (ids["loop_a"], "PV", tag_ids["TAG_A_PV"], True),
            (ids["loop_a"], "SP", tag_ids["TAG_SH_PV"], True),
            (ids["loop_b"], "PV", tag_ids["TAG_B_PV"], True),
            (ids["loop_b"], "OP", tag_ids["TAG_SH_PV"], True),
        ):
            await conn.execute(
                "INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required)"
                " VALUES ($1, $2, $3, $4, $5)",
                str(uuid4()),
                loop_id,
                tid,
                role,
                required,
            )
        # scope 用户（IC_A=NODE_SUBTREE A 工厂；IC_B=NODE_SUBTREE B 工厂；
        # IC_ALL=ALL；IC_C=A 工厂——任务撤权专用，独立于 WS 用例）
        pw = hash_password(_PASSWORD)
        for uname, role in (
            ("c25_ic_a", "IC_ENGINEER"),
            ("c25_ic_b", "IC_ENGINEER"),
            ("c25_ic_all", "IC_ENGINEER"),
            ("c25_ic_c", "IC_ENGINEER"),
        ):
            uid = str(uuid4())
            ids[uname] = uid
            await conn.execute(
                "INSERT INTO sys_user (id, username, password_hash, display_name, role,"
                " is_active) VALUES ($1, $2, $3, $4, $5, TRUE)",
                uid,
                uname,
                pw,
                uname,
                role,
            )
        for uname, stype, node in (
            ("c25_ic_a", "NODE_SUBTREE", ids["factory_a"]),
            ("c25_ic_b", "NODE_SUBTREE", ids["factory_b"]),
            ("c25_ic_all", "ALL", None),
            ("c25_ic_c", "NODE_SUBTREE", ids["factory_a"]),
        ):
            await conn.execute(
                "INSERT INTO sys_user_scope (user_id, scope_type, node_id) VALUES ($1, $2, $3)",
                ids[uname],
                stype,
                node,
            )
        # 关闭 SignalR 订阅（scenario 无实时源；realtime:updates 由本测试直发
        # 隔离 Redis Pub/Sub——链路真实，仅去掉无关外部依赖）
        await conn.execute(
            "UPDATE sys_config SET value='false' WHERE key='datasource.signalr_enabled'"
        )
        await conn.execute("COMMIT")
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# 完整栈 fixture：scratch PG + 隔离 Redis + 真实 uvicorn（含真实 Worker）
# ---------------------------------------------------------------------------


class _Stack:
    def __init__(self, base_url: str, redis_port: int, ids: dict[str, str], raw_dsn: str):
        self.base_url = base_url
        self.redis_port = redis_port
        self.ids = ids
        self.raw_dsn = raw_dsn
        self._tokens: dict[str, str] = {}

    async def token(self, username: str, password: str | None = None) -> str:
        if password is None:
            # 种子 admin 密码=admin123；scenario 用户密码=Admin@123
            password = "admin123" if username == "admin" else _PASSWORD
        if username not in self._tokens:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as c:
                resp = await c.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                )
                assert resp.status_code == 200, resp.text
                self._tokens[username] = resp.json()["data"]["accessToken"]
        return self._tokens[username]

    def drop_token(self, username: str) -> None:
        self._tokens.pop(username, None)


@pytest.fixture(scope="module")
async def stack() -> AsyncIterator[_Stack]:
    admin_dsn = _admin_dsn()
    redis_port = _isolated_redis_port()
    database = f"clpm_p403_c25_{uuid4().hex[:8]}"

    # 0. 隔离 Redis 连通性（真实实例，非 fakeredis）
    r = aioredis.Redis(host="127.0.0.1", port=redis_port)
    assert await r.ping(), "隔离 Redis PING 失败"
    await r.flushdb()
    await r.aclose()

    # 1. scratch PG：建库 → bootstrap → stamp head
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{database}"')
    await admin.close()
    raw_dsn = _pg_dsn(admin_dsn, database)
    app_port = _free_port()
    proc: subprocess.Popen | None = None
    log_path = _BACKEND_DIR / f".c25_uvicorn_{database}.log"
    try:
        conn = await asyncpg.connect(raw_dsn)
        await conn.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))
        await conn.execute(_SEED_SQL.read_text(encoding="utf-8"))
        await conn.close()
        stamp_env = {
            **os.environ,
            "POSTGRES_HOST": urlsplit(admin_dsn).hostname or "127.0.0.1",
            "POSTGRES_PORT": str(urlsplit(admin_dsn).port or 5432),
            "POSTGRES_USER": urlsplit(admin_dsn).username or "clpm",
            "POSTGRES_PASSWORD": quote_plus(urlsplit(admin_dsn).password or ""),
            "POSTGRES_DB": database,
            "CLPM_TEST_MODE": "",
        }
        stamp = subprocess.run(
            [sys.executable, "-m", "alembic", "stamp", "p403scope01"],
            cwd=_BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=300,
            env=stamp_env,
        )
        assert stamp.returncode == 0, stamp.stderr

        # 2. scenario 种子（需 app 的 hash_password；无 CLPM_TEST_MODE）
        ids = await _seed_scenario(raw_dsn)

        # 3. 真实 uvicorn 子进程（lifespan 自启真实 Celery Worker/Beat——
        #    broker=隔离 Redis，与 dev 完全无关）
        uv_env = {
            **os.environ,
            "POSTGRES_HOST": urlsplit(admin_dsn).hostname or "127.0.0.1",
            "POSTGRES_PORT": str(urlsplit(admin_dsn).port or 5432),
            "POSTGRES_USER": urlsplit(admin_dsn).username or "clpm",
            "POSTGRES_PASSWORD": quote_plus(urlsplit(admin_dsn).password or ""),
            "POSTGRES_DB": database,
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "CELERY_BROKER_URL": f"redis://127.0.0.1:{redis_port}/1",
            "TDENGINE_HOST": "127.0.0.1",
            "TDENGINE_PORT": "17104",
            "JWT_SECRET_KEY": os.environ.get(
                "JWT_SECRET_KEY", "c25-e2e-secret-key-at-least-32-characters-long!"
            ),
            "CLPM_TEST_MODE": "",
            "ENV": "development",
            "PYTHONUNBUFFERED": "1",
        }
        log_f = open(log_path, "w")  # noqa: SIM115
        # start_new_session：独立进程组——teardown 组内 SIGTERM/SIGKILL 可连带
        # 清理 lifespan 派生的 Celery Worker/Beat 子进程（防孤儿 worker 跨运行
        # 挂在同一隔离 broker 上抢任务）
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(app_port),
                "--log-level",
                "warning",
            ],
            cwd=_BACKEND_DIR,
            env=uv_env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        base_url = f"http://127.0.0.1:{app_port}"
        deadline = time.monotonic() + 90
        ready = False
        async with httpx.AsyncClient(base_url=base_url, timeout=5) as c:
            while time.monotonic() < deadline:
                try:
                    if (await c.get("/health")).status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1.0)
        assert ready, f"uvicorn 未就绪；日志: {log_path.read_text(encoding='utf-8')[-4000:]}"

        yield _Stack(base_url, redis_port, ids, raw_dsn)
    finally:
        if proc is not None:
            # 整个进程组终止（uvicorn + 其 Celery Worker/Beat 子进程）
            import signal as _sig

            try:
                os.killpg(os.getpgid(proc.pid), _sig.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.send_signal(_sig.SIGTERM)
            try:
                proc.wait(timeout=25)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                proc.wait(timeout=10)
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()
        # 保留 uvicorn 日志供验收复核（路径含 scratch 库名；下次运行自然覆盖）
        # log_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


async def _publish_frames(stack: _Stack, frames: list[dict]) -> None:
    """按 RealtimeSubscriber 真实载荷格式发布到 realtime:updates（数组格式）."""
    r = aioredis.Redis(host="127.0.0.1", port=stack.redis_port)
    await r.publish("realtime:updates", json.dumps(frames, ensure_ascii=False))
    await r.aclose()


async def _publish_raw(stack: _Stack, body: dict) -> None:
    """无 tagCode 载荷（raw 透传链——仅 ADMIN 全量客户端可见）."""
    r = aioredis.Redis(host="127.0.0.1", port=stack.redis_port)
    await r.publish("realtime:updates", json.dumps(body, ensure_ascii=False))
    await r.aclose()


def _frame(tag: str, value: str | float = "1") -> dict:
    return {
        "tagCode": tag,
        "value": value,
        "quality": "Good",
        "collectTime": "2026-10-12T00:00:00Z",
        "valueValid": True,
        "recvAt": "2026-10-12T00:00:00Z",
        "stale": False,
    }


async def _recv_until(ws, timeout: float = 5.0) -> list[dict]:
    """收集 timeout 内全部帧（ping 帧忽略）。"""
    out: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
        except TimeoutError:
            break
        msg = json.loads(raw)
        if msg.get("type") == "ping":
            continue
        if msg.get("type") == "batch":
            out.extend(msg.get("items") or [])
            continue
        out.append(msg)
    return out


async def _db_conn(stack: _Stack) -> asyncpg.Connection:
    return await asyncpg.connect(stack.raw_dsn)


async def _set_user_active(stack: _Stack, username: str, active: bool) -> None:
    async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
        resp = await c.put(
            f"/api/v1/users/{stack.ids[username]}",
            json={"isActive": active},
            headers={"Authorization": f"Bearer {await stack.token('admin')}"},
        )
        assert resp.status_code in (200, 404), resp.text


# ---------------------------------------------------------------------------
# C25-1 A/B 节点全 HTTP 入口正反例
# ---------------------------------------------------------------------------


class TestC25HttpScope:
    async def test_loop_list_intersection_and_detail_403(self, stack: _Stack) -> None:
        """列表=交集（B 回路不出现）；B 回路详情/监控详情=403 通用拒绝。"""
        ids = stack.ids
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            tok = await stack.token("c25_ic_a")
            hdr = {"Authorization": f"Bearer {tok}"}
            # 列表（无筛选）：仅 A 子树回路
            resp = await c.get("/api/v1/loops", headers=hdr, params={"pageSize": 100})
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]["items"]
            got_ids = {i["loopId"] for i in items}
            assert ids["loop_a"] in got_ids
            assert ids["loop_b"] not in got_ids
            # 显式 B 装置筛选 → 空交集
            resp = await c.get("/api/v1/loops", headers=hdr, params={"plantNodeId": ids["unit_b1"]})
            assert resp.status_code == 200
            assert resp.json()["data"]["items"] == []
            # B 回路详情 → 403 通用拒绝
            resp = await c.get(f"/api/v1/loops/{ids['loop_b']}", headers=hdr)
            assert resp.status_code == 403
            assert resp.json()["code"] == "ERR_SCOPE_FORBIDDEN"
            # 正例：A 回路详情 200
            resp = await c.get(f"/api/v1/loops/{ids['loop_a']}", headers=hdr)
            assert resp.status_code == 200

    async def test_raw_tag_endpoint_rejects_out_of_scope_and_unbound(self, stack: _Stack) -> None:
        """原始 tag HTTP：B tag/未绑定 tag → rejected；A tag/共享 tag → 正常。"""
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            tok = await stack.token("c25_ic_a")
            hdr = {"Authorization": f"Bearer {tok}"}
            # 预置实时缓存（真实 Redis 键 realtime:<tag>，与订阅器写入格式一致）
            rc = aioredis.Redis(host="127.0.0.1", port=stack.redis_port)
            for tag in ("TAG_A_PV", "TAG_SH_PV", "TAG_B_PV"):
                frame = _frame(tag, "1")
                frame["quality"] = 1  # HTTP 响应模型 quality 为 int 质量码
                await rc.set(f"realtime:{tag}", json.dumps(frame), ex=3600)
            await rc.aclose()
            resp = await c.get(
                "/api/v1/realtime",
                headers=hdr,
                params=[
                    ("tagCodes", "TAG_A_PV"),
                    ("tagCodes", "TAG_SH_PV"),
                    ("tagCodes", "TAG_B_PV"),
                    ("tagCodes", "TAG_UN_PV"),
                ],
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert set(data["rejected"] or []) == {"TAG_B_PV", "TAG_UN_PV"}
            codes = {i["tagCode"] for i in data["items"]}
            assert codes == {"TAG_A_PV", "TAG_SH_PV"}  # 未缓存的 tag 不含值但仍可见
            # 共享 tag 仅回 tag 本身（items 元数据只含 tagCode/value 系字段，
            # 不含回路元信息——结构断言）
            item = data["items"][0]
            assert not any(k for k in item if k in ("loopId", "loopName", "loops"))

    async def test_admin_sees_unbound_tag(self, stack: _Stack) -> None:
        """未绑定 tag 默认仅 ADMIN 可读（ALL≠任何 tag）。"""
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            hdr = {"Authorization": f"Bearer {await stack.token('admin')}"}
            resp = await c.get("/api/v1/realtime", headers=hdr, params={"tagCodes": "TAG_UN_PV"})
            assert resp.status_code == 200
            assert resp.json()["data"]["rejected"] is None

    async def test_diagnosis_batch_cross_scope_rejected(self, stack: _Stack) -> None:
        """批量跨 scope 对象整体拒绝（发起诊断含 B 回路 → 403）。"""
        ids = stack.ids
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            hdr = {"Authorization": f"Bearer {await stack.token('c25_ic_a')}"}
            resp = await c.post(
                "/api/v1/diagnosis/run",
                headers=hdr,
                json={"loopIds": [ids["loop_a"], ids["loop_b"]]},
            )
            assert resp.status_code == 403
            assert resp.json()["code"] == "ERR_SCOPE_FORBIDDEN"

    async def test_report_and_performance_aggregates_intersected(self, stack: _Stack) -> None:
        """报告/性能聚合入口：NODE_SUBTREE 无筛选 → 范围根生效；B 显式筛选 → 空结果。"""
        ids = stack.ids
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            hdr = {"Authorization": f"Bearer {await stack.token('c25_ic_a')}"}
            # B 装置筛选 → 哨兵空结果（200 空 items，不报对象详情）
            resp = await c.get(
                "/api/v1/performance/loops/snapshots",
                headers=hdr,
                params={"plantNodeId": ids["unit_b1"], "latestOnly": True},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["items"] == []
            # 节点详情（B 单元趋势）→ 403 通用拒绝
            resp = await c.get(
                f"/api/v1/performance/nodes/{ids['unit_b1']}/trend",
                headers=hdr,
                params={"startTime": "2026-10-01T00:00:00", "endTime": "2026-10-02T00:00:00"},
            )
            assert resp.status_code == 403
            # A 单元趋势正例 200
            resp = await c.get(
                f"/api/v1/performance/nodes/{ids['unit_a1']}/trend",
                headers=hdr,
                params={"startTime": "2026-10-01T00:00:00", "endTime": "2026-10-02T00:00:00"},
            )
            assert resp.status_code == 200

    async def test_task_artifact_download_out_of_scope_403(self, stack: _Stack) -> None:
        """任务产物下载继承回路 scope：任务目标含 B 回路 → 403（创建人自身）。"""
        ids = stack.ids
        task_id = f"c25-artifact-{uuid4().hex[:8]}"
        r = aioredis.Redis(host="127.0.0.1", port=stack.redis_port)
        await r.hset(
            f"task:{task_id}",
            mapping={
                "task_id": task_id,
                "task_type": "LOOP_EXPORT",
                "status": "SUCCESS",
                "created_by": "c25_ic_a",
                "loop_ids": json.dumps([ids["loop_b"]]),
                "file_path": "/tmp/c25/x.csv",
                "file_name": "x.csv",
                "created_at": "2026-10-12T00:00:00",
            },
        )
        await r.aclose()
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            hdr = {"Authorization": f"Bearer {await stack.token('c25_ic_a')}"}
            resp = await c.get(f"/api/v1/tasks/{task_id}/download", headers=hdr)
            assert resp.status_code == 403
            assert resp.json()["code"] == "ERR_SCOPE_FORBIDDEN"


# ---------------------------------------------------------------------------
# C25-2/3 直接 WS 正反例（旧客户端 / 订阅交集 / raw / 回路订阅）
# ---------------------------------------------------------------------------


class TestC25WsRealtime:
    async def test_legacy_client_authorized_set_only(self, stack: _Stack) -> None:
        """未订阅旧客户端：非 ADMIN 最多转发授权集合（A 收到，B/未绑定不收；raw 不透传）。"""
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
        async with websockets.connect(
            uri + f"?token={await stack.token('c25_ic_a')}", open_timeout=10
        ) as ws:
            await asyncio.sleep(0.5)  # 握手后稳定
            await _publish_frames(
                stack,
                [_frame("TAG_A_PV", "11"), _frame("TAG_B_PV", "22"), _frame("TAG_UN_PV", "33")],
            )
            await _publish_raw(stack, {"type": "notice", "body": "leak?"})
            frames = await _recv_until(ws, timeout=5)
            tags = {f.get("tagCode") for f in frames if f.get("tagCode")}
            assert "TAG_A_PV" in tags
            assert "TAG_B_PV" not in tags
            assert "TAG_UN_PV" not in tags
            assert not any(f.get("type") == "notice" for f in frames)  # raw 仅 ADMIN

    async def test_admin_legacy_full_and_raw(self, stack: _Stack) -> None:
        """ADMIN 旧客户端：全量转发含未绑定 tag 与 raw 载荷。"""
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
        async with websockets.connect(
            uri + f"?token={await stack.token('admin')}", open_timeout=10
        ) as ws:
            await asyncio.sleep(0.5)
            await _publish_frames(stack, [_frame("TAG_UN_PV", "9")])
            await _publish_raw(stack, {"type": "notice", "body": "admin-only"})
            frames = await _recv_until(ws, timeout=5)
            tags = {f.get("tagCode") for f in frames if f.get("tagCode")}
            assert "TAG_UN_PV" in tags
            assert any(f.get("type") == "notice" for f in frames)

    async def test_subscribe_intersection_and_loop_subscribe(self, stack: _Stack) -> None:
        """订阅交集（B tag 拒绝与未知同列表）；回路订阅（B 回路整体拒绝）。"""
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
        async with websockets.connect(
            uri + f"?token={await stack.token('c25_ic_b')}", open_timeout=10
        ) as ws:
            await asyncio.sleep(0.5)
            await ws.send(
                json.dumps({"type": "subscribe", "tags": ["TAG_B_PV", "TAG_A_PV", "NO_SUCH"]})
            )
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert ack["type"] == "subscribed"
            assert ack["tags"] == ["TAG_B_PV"]
            assert set(ack["rejected"]) == {"TAG_A_PV", "NO_SUCH"}
            # 越界 A tag 帧不转发，B tag 帧转发
            await _publish_frames(stack, [_frame("TAG_B_PV", "1"), _frame("TAG_A_PV", "2")])
            frames = await _recv_until(ws, timeout=5)
            tags = {f.get("tagCode") for f in frames if f.get("tagCode")}
            assert tags == {"TAG_B_PV"}
            # 回路订阅：B 回路解析并入订阅；A 回路（范围外）与未知回路同列表拒绝
            await ws.send(
                json.dumps(
                    {"type": "subscribe", "loops": ["C25-LIC-B201", "C25-LIC-A101", "NO_LOOP"]}
                )
            )
            ack2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert ack2["type"] == "subscribed"
            assert "C25-LIC-B201" in (ack2.get("loops") or {})
            assert {"C25-LIC-A101", "NO_LOOP"} <= set(ack2.get("rejected") or [])

    async def test_shared_tag_both_sides_see_value_only(self, stack: _Stack) -> None:
        """共享 tag：A/B 两侧均可见（各自绑定回路在范围内），仅 tag 值不泄露对侧回路。"""
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
        for user in ("c25_ic_a", "c25_ic_b"):
            async with websockets.connect(
                uri + f"?token={await stack.token(user)}", open_timeout=10
            ) as ws:
                await asyncio.sleep(0.5)
                await ws.send(json.dumps({"type": "subscribe", "tags": ["TAG_SH_PV"]}))
                ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert ack["type"] == "subscribed", ack
                assert "TAG_SH_PV" in ack["tags"]
                await _publish_frames(stack, [_frame("TAG_SH_PV", "77")])
                frames = await _recv_until(ws, timeout=5)
                assert any(f.get("tagCode") == "TAG_SH_PV" for f in frames)


# ---------------------------------------------------------------------------
# C25-4 预警推送不泄漏 B
# ---------------------------------------------------------------------------


class TestC25WsAlert:
    async def test_alert_filtered_by_loop_scope(self, stack: _Stack) -> None:
        """A 预警送达；B 预警不泄漏；无 loopId 预警仅 ADMIN。"""
        ids = stack.ids
        r = aioredis.Redis(host="127.0.0.1", port=stack.redis_port)
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/alerts"
        async with websockets.connect(
            uri + f"?token={await stack.token('c25_ic_a')}", open_timeout=10
        ) as ws:
            await asyncio.sleep(0.5)
            for loop_id, tag in (
                (ids["loop_a"], "A"),
                (ids["loop_b"], "B"),
            ):
                await r.publish(
                    "alert:notify",
                    json.dumps(
                        {
                            "type": "alert",
                            "ruleCode": f"C25_{tag}",
                            "ruleName": f"C25-{tag}",
                            "loopId": loop_id,
                            "severity": "WARN",
                            "triggeredValue": 1.0,
                            "triggeredAt": "2026-10-12T00:00:00Z",
                        }
                    ),
                )
            await r.publish(
                "alert:notify",
                json.dumps({"type": "alert", "ruleCode": "C25_NLOOP", "severity": "WARN"}),
            )
            await r.aclose()
            alerts = await _recv_until(ws, timeout=5)
            codes = {a.get("ruleCode") for a in alerts}
            assert "C25_A" in codes
            assert "C25_B" not in codes
            assert "C25_NLOOP" not in codes  # 无 loopId 仅 ADMIN
        async with websockets.connect(
            uri + f"?token={await stack.token('admin')}", open_timeout=10
        ) as ws_admin:
            await asyncio.sleep(0.5)
            r2 = aioredis.Redis(host="127.0.0.1", port=stack.redis_port)
            await r2.publish(
                "alert:notify",
                json.dumps({"type": "alert", "ruleCode": "C25_NLOOP", "severity": "WARN"}),
            )
            await r2.publish(
                "alert:notify",
                json.dumps(
                    {
                        "type": "alert",
                        "ruleCode": "C25_B",
                        "loopId": ids["loop_b"],
                        "severity": "WARN",
                    }
                ),
            )
            await r2.aclose()
            alerts = await _recv_until(ws_admin, timeout=5)
            codes = {a.get("ruleCode") for a in alerts}
            # ADMIN 全收（无 loopId 与 B 预警）
            assert {"C25_NLOOP", "C25_B"} <= codes


# ---------------------------------------------------------------------------
# C25-5 连接内降权/禁用/重置 → 无新越权帧（不靠前端退订）
# ---------------------------------------------------------------------------


class TestC25ConnectionRevocation:
    async def test_scope_narrowing_no_new_out_of_scope_frames(self, stack: _Stack) -> None:
        """ALL→A 范围收窄：已连接+已订阅 B tag 的连接，收窄后 B 帧不再送达、缓冲清除。"""
        ids = stack.ids
        admin_tok = await stack.token("admin")
        conn0 = await _db_conn(stack)
        try:
            current_revision = await conn0.fetchval(
                "SELECT revision FROM policy_publication_guard WHERE id=1"
            )
        finally:
            await conn0.close()
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
        async with websockets.connect(
            uri + f"?token={await stack.token('c25_ic_all')}", open_timeout=10
        ) as ws:
            await asyncio.sleep(0.5)
            await ws.send(json.dumps({"type": "subscribe", "tags": ["TAG_A_PV", "TAG_B_PV"]}))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert set(ack["tags"]) == {"TAG_A_PV", "TAG_B_PV"}
            # 收窄前 B 帧可达（正例）
            await _publish_frames(stack, [_frame("TAG_B_PV", "1")])
            frames = await _recv_until(ws, timeout=5)
            assert any(f.get("tagCode") == "TAG_B_PV" for f in frames)
            # 管理侧收窄 scope：ALL → NODE_SUBTREE(A 工厂)
            async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
                resp = await c.put(
                    f"/api/v1/system/authorization/users/{stack.ids['c25_ic_all']}/scope",
                    headers={"Authorization": f"Bearer {admin_tok}"},
                    json={
                        "expectedRevision": current_revision,
                        "scopeType": "NODE_SUBTREE",
                        "nodeId": ids["factory_a"],
                        "reason": "C25 连接内降权负例",
                    },
                )
                assert resp.status_code == 200, resp.text
            await asyncio.sleep(0.5)
            # 收窄后：B 帧不送达（缓冲重过滤），A 帧照常
            await _publish_frames(
                stack, [_frame("TAG_B_PV", "2"), _frame("TAG_A_PV", "3"), _frame("TAG_B_PV", "4")]
            )
            frames = await _recv_until(ws, timeout=5)
            tags = {f.get("tagCode") for f in frames if f.get("tagCode")}
            assert "TAG_A_PV" in tags
            assert "TAG_B_PV" not in tags

    async def test_disabled_user_no_new_frames_and_closed(self, stack: _Stack) -> None:
        """连接内禁用：发布帧触发发送栅栏 → 无新帧 + 4003 关闭。"""
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
        ws = await websockets.connect(
            uri + f"?token={await stack.token('c25_ic_a')}", open_timeout=10
        )
        try:
            await asyncio.sleep(0.5)
            await _set_user_active(stack, "c25_ic_a", False)
            await _publish_frames(stack, [_frame("TAG_A_PV", "5")])
            closed_code = None
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
                    msg = json.loads(raw)
                    # 禁用后不得有新数据帧
                    assert msg.get("type") != "batch" or not msg.get("items")
            except websockets.ConnectionClosed as e:
                closed_code = e.code
            assert closed_code == 4003, f"预期 4003 失权关闭，实际 {closed_code}"
        finally:
            await ws.close()
            await _set_user_active(stack, "c25_ic_a", True)
            stack.drop_token("c25_ic_a")

    async def test_reset_password_revokes_ws(self, stack: _Stack) -> None:
        """重置密码=撤销令牌：旧 token 连接在发送栅栏处失权关闭（4003）。"""
        old_token = await stack.token("c25_ic_b")
        uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
        ws = await websockets.connect(uri + f"?token={old_token}", open_timeout=10)
        try:
            await asyncio.sleep(0.5)
            async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
                resp = await c.put(
                    f"/api/v1/users/{stack.ids['c25_ic_b']}/reset-password",
                    headers={"Authorization": f"Bearer {await stack.token('admin')}"},
                    json={"newPassword": _PASSWORD},
                )
                assert resp.status_code in (200, 201), resp.text
            await _publish_frames(stack, [_frame("TAG_B_PV", "6")])
            closed_code = None
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
            except websockets.ConnectionClosed as e:
                closed_code = e.code
            assert closed_code == 4003, f"预期 4003，实际 {closed_code}"
            stack.drop_token("c25_ic_b")
            # 新 token 重登恢复（正例）
            async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
                resp = await c.post(
                    "/api/v1/auth/login",
                    json={"username": "c25_ic_b", "password": _PASSWORD},
                )
                assert resp.status_code == 200
        finally:
            await ws.close()


# ---------------------------------------------------------------------------
# C25-6/7 真实 Worker 任务撤权 + 提交栅栏并发（真实锁线性化）
# ---------------------------------------------------------------------------


async def _wait_task_terminal(stack: _Stack, task_id: str, tok: str, timeout: float = 180) -> dict:
    async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = await c.get(
                f"/api/v1/tasks/{task_id}", headers={"Authorization": f"Bearer {tok}"}
            )
            if resp.status_code == 200:
                data = resp.json()["data"]
                if data.get("status") in ("SUCCESS", "FAILED", "CANCELLED"):
                    return data
            await asyncio.sleep(1.5)
    pytest.fail(f"任务 {task_id} 未在 {timeout}s 内进入终态")


class TestC25TaskRevocation:
    async def test_positive_control_diagnosis_publishes(self, stack: _Stack) -> None:
        """正例：活跃用户经真实 Worker 跑诊断 → DiagnosisRun 发布（数据不足亦为正式结论）。"""
        ids = stack.ids
        tok = await stack.token("c25_ic_c")
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            resp = await c.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "loopIds": [ids["loop_a"]],
                    "timeWindow": {"preset": "last_24h"},
                },
            )
            assert resp.status_code == 200, resp.text
            task_id = resp.json()["data"]["taskId"]
        task = await _wait_task_terminal(stack, task_id, tok)
        assert task["status"] == "SUCCESS", f"正例任务未成功: {task}"
        conn = await _db_conn(stack)
        try:
            n = await conn.fetchval(
                "SELECT count(*) FROM diagnosis_run WHERE loop_id = $1", ids["loop_a"]
            )
        finally:
            await conn.close()
        assert n >= 1, f"正例必须发布诊断结果（DATA_INSUFFICIENT 亦为正式发布物）: {task}"

    async def test_revoke_concurrent_with_running_task(self, stack: _Stack) -> None:
        """提交任务后立即禁用主体（排队/运行窗口并发）→ 无撤权后发布的 run + REVOKED 审计。

        竞态两种结局均满足 C25（guard 全序）：
        - 提交先完成：run 保留（其 created_at 早于禁用账本行 created_at）；
        - 撤权先完成：该任务无新 run，TASK_REVOKED 审计存在。
        """
        ids = stack.ids
        tok = await stack.token("c25_ic_c")
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            resp = await c.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "loopIds": [ids["loop_a"]],
                    "timeWindow": {"preset": "last_24h"},
                },
            )
            assert resp.status_code == 200, resp.text
            task_id = resp.json()["data"]["taskId"]
        await _set_user_active(stack, "c25_ic_c", False)  # 撤权（guard 排他锁+入账）
        await _wait_task_terminal(stack, task_id, await stack.token("admin"))

        conn = await _db_conn(stack)
        try:
            disable_row = await conn.fetchrow(
                "SELECT revision, created_at FROM sys_authz_policy"
                " WHERE operation='USER_STATE' AND user_id=$1"
                " AND (after_value->>'isActive')::text = 'false'"
                " ORDER BY revision DESC LIMIT 1",
                ids["c25_ic_c"],
            )
            assert disable_row is not None, "禁用必须入账本（guard revision 推进）"
            runs = await conn.fetch(
                "SELECT id, created_at FROM diagnosis_run WHERE loop_id=$1"
                " AND created_at >= $2 ORDER BY created_at",
                ids["loop_a"],
                disable_row["created_at"],
            )
            revoked_audit = await conn.fetchval(
                "SELECT count(*) FROM sys_audit_log"
                " WHERE operation_type='TASK_REVOKED' AND operator=$1",
                ids["c25_ic_c"],
            )
        finally:
            await conn.close()
        # 线性不变量：撤权完成后发布的 run 数为 0；或任务以 REVOKED 审计收束
        assert len(runs) == 0 or revoked_audit >= 1, (
            f"违反线性化：撤权后仍有 {len(runs)} 条新 run 且无 REVOKED 审计"
        )

    async def test_fence_share_lock_blocks_disable_until_commit(self, stack: _Stack) -> None:
        """提交栅栏真实锁交错（一）：先持 guard 共享锁 → 禁用事务阻塞 → 提交完成后禁用完成。"""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from app.services.authz_scope import lock_guard_for_share
        from app.services.user import disable_user

        ids = stack.ids
        _test_mode = os.environ.pop("CLPM_TEST_MODE", None)  # 真实路径（非短路）
        sa_dsn = _pg_dsn(stack.raw_dsn, stack.raw_dsn.rsplit("/", 1)[-1], "postgresql+asyncpg")
        engine = create_async_engine(sa_dsn)
        Session = async_sessionmaker(engine, class_=AsyncSession)

        # 主体先恢复启用
        await _set_user_active(stack, "c25_ic_c", True)

        async with Session() as s1:
            rev = await lock_guard_for_share(s1)  # 提交栅栏：FOR SHARE 持有
            disable_done: list[bool] = []

            async def _try_disable() -> None:
                async with Session() as s2:
                    await disable_user(s2, user_id=ids["c25_ic_c"], operator="c25-admin")
                    await s2.commit()
                    disable_done.append(True)

            task = asyncio.create_task(_try_disable())
            await asyncio.sleep(2.0)
            assert not disable_done, "共享锁持有期间禁用事务不得提交（排他锁被阻塞）"
            assert not task.done(), "禁用事务应仍在等待 guard 排他锁"
            await s1.commit()  # 提交栅栏完成（业务提交）——共享锁释放
            await asyncio.wait_for(task, timeout=15)
            assert disable_done, "共享锁释放后禁用事务必须完成"
        # 两种结局皆合法，但禁用已最终完成 → 后续 validate 必须拒绝
        async with Session() as s3:
            from app.services.authz_task_fence import (
                SubjectRevokedError,
                TaskSubject,
                validate_for_commit,
            )

            subject = TaskSubject(
                subject_user_id=ids["c25_ic_c"],
                subject_role="IC_ENGINEER",
                action="diagnosis:trigger",
                policy_revision=rev,
                loop_ids=[ids["loop_a"]],
            )
            with pytest.raises(SubjectRevokedError):
                await validate_for_commit(s3, subject, ids["loop_a"])
        await engine.dispose()
        if _test_mode is not None:
            os.environ["CLPM_TEST_MODE"] = _test_mode
        await _set_user_active(stack, "c25_ic_c", True)

    async def test_fence_disable_first_then_validate_revoked(self, stack: _Stack) -> None:
        """提交栅栏交错（二）：禁用先完成 → 栅栏复核抛 SubjectRevokedError（无业务产物）。"""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from app.services.authz_task_fence import (
            SubjectRevokedError,
            TaskSubject,
            validate_for_commit,
        )
        from app.services.user import disable_user

        ids = stack.ids
        _test_mode = os.environ.pop("CLPM_TEST_MODE", None)  # 真实路径（非短路）
        sa_dsn = _pg_dsn(stack.raw_dsn, stack.raw_dsn.rsplit("/", 1)[-1], "postgresql+asyncpg")
        engine = create_async_engine(sa_dsn)
        Session = async_sessionmaker(engine, class_=AsyncSession)
        async with Session() as s0:
            await disable_user(s0, operator="c25-admin", user_id=ids["c25_ic_c"])
            await s0.commit()
        try:
            async with Session() as s:
                subject = TaskSubject(
                    subject_user_id=ids["c25_ic_c"],
                    subject_role="IC_ENGINEER",
                    action="diagnosis:trigger",
                    policy_revision=0,
                    loop_ids=[ids["loop_a"]],
                )
                with pytest.raises(SubjectRevokedError) as ei:
                    await validate_for_commit(s, subject, ids["loop_a"])
                assert "禁用" in ei.value.reason
        finally:
            await engine.dispose()
            if _test_mode is not None:
                os.environ["CLPM_TEST_MODE"] = _test_mode
            await _set_user_active(stack, "c25_ic_c", True)

    async def test_tag_rebind_enters_guard_ledger(self, stack: _Stack) -> None:
        """tag 改绑（真实 HTTP）→ guard 排他锁 + TAG_REBIND 账本行 + revision 推进。"""
        ids = stack.ids
        conn0 = await _db_conn(stack)
        try:
            rev_before = await conn0.fetchval(
                "SELECT revision FROM policy_publication_guard WHERE id=1"
            )
        finally:
            await conn0.close()
        async with httpx.AsyncClient(base_url=stack.base_url, timeout=30) as c:
            hdr = {"Authorization": f"Bearer {await stack.token('admin')}"}
            resp = await c.put(
                f"/api/v1/loops/{ids['loop_a']}/tags",
                headers=hdr,
                json={
                    "pv": ids["tag_TAG_A_PV"],
                    "sp": ids["tag_TAG_SH_PV"],
                    # 改绑 OP（换绑 B 的 tag）→ 绑定关系变化（可见集变更）
                    "op": ids["tag_TAG_B_PV"],
                    "mode": ids["tag_TAG_A_PV"],
                },
            )
            assert resp.status_code == 200, resp.text
        conn = await _db_conn(stack)
        try:
            row = await conn.fetchrow(
                "SELECT revision, operation FROM sys_authz_policy"
                " WHERE operation='TAG_REBIND' ORDER BY revision DESC LIMIT 1"
            )
            rev_after = await conn.fetchval(
                "SELECT revision FROM policy_publication_guard WHERE id=1"
            )
        finally:
            await conn.close()
        assert row is not None, "TAG_REBIND 必须入账本"
        assert row["revision"] == rev_before + 1
        assert rev_after == row["revision"], "guard revision 必须与账本同步推进"

    async def test_guard_revision_advances_and_cache_key_rotates(self, stack: _Stack) -> None:
        """guard revision 随策略变更推进（缓存 key 组成部分 → 全厂缓存不越界的换 key 机制）。"""
        conn = await _db_conn(stack)
        try:
            guard = await conn.fetchval("SELECT revision FROM policy_publication_guard WHERE id=1")
            ledger = await conn.fetchval("SELECT max(revision) FROM sys_authz_policy")
        finally:
            await conn.close()
        assert guard == ledger, "guard revision 必须与账本 max 对齐（每次推进有账本行）"
        assert guard >= 1


# ---------------------------------------------------------------------------
# C25-8 WS 握手主体校验（禁用主体拒绝连接）
# ---------------------------------------------------------------------------


class TestC25Handshake:
    async def test_disabled_subject_rejected_at_handshake(self, stack: _Stack) -> None:
        """禁用主体新连接被握手拒绝（4001，不进入分发）。"""
        stack.drop_token("c25_ic_b")
        tok = await stack.token("c25_ic_b")  # 禁用前签发（token 仍可解码）
        await _set_user_active(stack, "c25_ic_b", False)
        try:
            uri = f"{stack.base_url.replace('http', 'ws')}/api/v1/ws/realtime"
            with pytest.raises((websockets.WebSocketException, ConnectionError, OSError)):
                async with websockets.connect(uri + f"?token={tok}", open_timeout=10):
                    pass
        finally:
            await _set_user_active(stack, "c25_ic_b", True)
