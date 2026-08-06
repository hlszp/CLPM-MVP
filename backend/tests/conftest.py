"""Shared pytest fixtures for auth tests.

Uses ``unittest.mock`` to stub the async DB session and Redis client so tests
run without external dependencies (no PostgreSQL/Redis required).
"""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# CI 环境兜底：确保 JWT_SECRET_KEY 和 DEBUG 在导入 app 之前已设置
os.environ.setdefault("JWT_SECRET_KEY", "ci-test-secret-key-at-least-32-characters-long!!!")
os.environ.setdefault("DEBUG", "true")

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password

# ---------------------------------------------------------------------------
# In-memory test users
# ---------------------------------------------------------------------------

TEST_PASSWORD = "Admin@123"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


def _make_user(
    username: str = "admin",
    role: str = "ADMIN",
    is_active: bool = True,
    user_id: str | None = None,
) -> MagicMock:
    """Create a mock SysUser object."""
    user = MagicMock()
    user.id = user_id or str(uuid4())
    user.username = username
    user.password_hash = TEST_PASSWORD_HASH
    user.display_name = f"测试-{username}"
    user.email = f"{username}@clpm.local"
    user.role = role
    user.is_active = is_active
    user.must_change_password = False
    user.last_login_at = datetime.now(UTC)
    return user


TEST_USERS: dict[str, MagicMock] = {
    "admin": _make_user("admin", "ADMIN", user_id="00000000-0000-0000-0000-000000000001"),
    "ic_engineer": _make_user(
        "ic_engineer", "IC_ENGINEER", user_id="00000000-0000-0000-0000-000000000002"
    ),
    "pe_engineer": _make_user(
        "pe_engineer", "PE_ENGINEER", user_id="00000000-0000-0000-0000-000000000003"
    ),
    "sponsor": _make_user("sponsor", "SPONSOR", user_id="00000000-0000-0000-0000-000000000004"),
    "expert": _make_user("expert", "EXPERT", user_id="00000000-0000-0000-0000-000000000005"),
}


# ---------------------------------------------------------------------------
# Fake Redis (in-memory)
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory async Redis mock.

    覆盖项目用到的全部 Redis 操作：strings / sets / hashes / sorted sets /
    lists / pub-sub / eval（Lua CAS 脚本以 Python 等价物模拟）。
    """

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._lists: dict[str, list[str]] = {}
        self._ttls: dict[str, float] = {}
        # _client attr: close_redis() accesses redis_client._client on shutdown
        self._client = None

    async def get(self, key: str) -> str | None:
        return self._strings.get(key)

    async def set(self, key: str, value: str, **kwargs: Any) -> str | None:
        """Set a key. Supports nx (only if not exists) and ex (TTL) kwargs."""
        if kwargs.get("nx") and key in self._strings:
            return None
        self._strings[key] = value
        if "ex" in kwargs:
            self._ttls[key] = float(kwargs["ex"])
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._strings[key] = value
        self._ttls[key] = ttl

    async def incr(self, key: str) -> int:
        val = int(self._strings.get(key, "0")) + 1
        self._strings[key] = str(val)
        return val

    async def expire(self, key: str, ttl: int) -> None:
        self._ttls[key] = float(ttl)

    async def ttl(self, key: str) -> int:
        return int(self._ttls.get(key, -1))

    async def delete(self, key: str) -> int:
        deleted = 0
        if key in self._strings:
            del self._strings[key]
            deleted += 1
        if key in self._sets:
            del self._sets[key]
            deleted += 1
        self._ttls.pop(key, None)
        return deleted

    async def exists(self, key: str) -> int:
        return 1 if key in self._strings or key in self._sets else 0

    async def sadd(self, key: str, *values: str) -> int:
        s = self._sets.setdefault(key, set())
        for v in values:
            s.add(v)
        return len(values)

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set()).copy()

    async def aclose(self) -> None:
        pass

    def reset(self) -> None:
        self._strings.clear()
        self._sets.clear()
        self._hashes.clear()
        self._zsets.clear()
        self._lists.clear()
        self._ttls.clear()

    def pipeline(self):
        """Return a mock pipeline for batch operations (L1DataBlockCache)."""
        return _FakePipeline(self)

    def scan_iter(self, match: str, count: int = 100):
        """Synchronous scan iterator (CacheInvalidator compatibility)."""
        import fnmatch

        for key in list(self._strings.keys()):
            if fnmatch.fnmatch(key, match.replace("*", "*")):
                yield key

    async def keys(self, pattern: str) -> list[str]:
        """Pattern-matching key search (CacheInvalidator compatibility)."""
        import fnmatch

        return [k for k in self._strings.keys() if fnmatch.fnmatch(k, pattern)]

    # -- hash operations --------------------------------------------------

    async def hset(
        self,
        key: str,
        *args: Any,
        mapping: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> int:
        """Set hash fields.

        支持三种形式（与 redis-py 一致）：
        - ``hset(key, mapping={...})``
        - ``hset(key, field=value, ...)``
        - ``hset(key, field, value)``  位置参数三元素
        """
        h = self._hashes.setdefault(key, {})
        fields: dict[str, Any] = {**(mapping or {}), **kwargs}
        # 位置参数形式：hset(key, field, value)
        if len(args) == 2:
            fields[args[0]] = args[1]
        elif len(args) == 1 and isinstance(args[0], dict):
            fields.update(args[0])
        elif args:
            raise TypeError(
                f"hset() takes 2 or 3 positional arguments but {len(args) + 1} were given"
            )
        for field, value in fields.items():
            h[field] = value if isinstance(value, str) else str(value)
        return len(fields)

    async def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        h = self._hashes.get(key, {})
        deleted = sum(1 for f in fields if h.pop(f, None) is not None)
        return deleted

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        h = self._hashes.setdefault(key, {})
        val = int(h.get(field, "0")) + amount
        h[field] = str(val)
        return val

    # -- sorted set operations -------------------------------------------

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        z = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in z:
                added += 1
            z[member] = float(score)
        return added

    async def zrange(self, key: str, start: int, stop: int) -> list[str]:
        z = self._zsets.get(key, {})
        members = sorted(z, key=lambda m: z[m])
        # Redis stop 是包含的，支持负索引
        if stop < 0:
            stop = len(members) + stop
        return members[start : stop + 1]

    async def zrem(self, key: str, *members: str) -> int:
        z = self._zsets.get(key, {})
        return sum(1 for m in members if z.pop(m, None) is not None)

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    # -- list operations --------------------------------------------------

    async def lpush(self, key: str, *values: str) -> int:
        lst = self._lists.setdefault(key, [])
        for v in values:
            lst.insert(0, v if isinstance(v, str) else str(v))
        return len(lst)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self._lists.get(key, [])
        if stop < 0:
            stop = len(lst) + stop
        return lst[start : stop + 1]

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        lst = self._lists.get(key, [])
        if stop < 0:
            stop = len(lst) + stop
        self._lists[key] = lst[start : stop + 1]

    # -- pub/sub & eval ---------------------------------------------------

    async def publish(self, channel: str, message: str) -> int:
        """Pub/sub no-op（测试环境不验证订阅端）."""
        return 0

    async def eval(self, script: str, numkeys: int, *args: Any) -> list[str]:
        """Lua eval 模拟：CAS 脚本（task_tracker / data_import）返回 UPDATED 成功。

        生产环境用 Lua 保证原子性；测试环境无需原子保证，统一返回成功让流程继续。
        需要验证 BLOCKED/MISSING 分支的测试应在函数级 mock _update_task_cas 等。
        """
        return ["UPDATED", ""]

    async def info(self, section: str | None = None) -> dict[str, Any]:
        """Redis INFO command mock (CacheStats compatibility)."""
        return {
            "memory": {"used_memory": 1024 * 100, "used_memory_human": "100K"},
            "stats": {"keyspace_hits": 10, "keyspace_misses": 5},
        }


class _FakePipeline:
    """Mock Redis pipeline for batch operations."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str, Any]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> _FakePipeline:
        self._ops.append(("set", key, {"value": value, "ex": ex}))
        return self

    def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> _FakePipeline:
        self._ops.append(("hset", key, {**(mapping or {}), **kwargs}))
        return self

    def expire(self, key: str, ttl: int) -> _FakePipeline:
        self._ops.append(("expire", key, {"ttl": ttl}))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> _FakePipeline:
        self._ops.append(("zadd", key, {"mapping": mapping}))
        return self

    def delete(self, key: str) -> _FakePipeline:
        self._ops.append(("delete", key, {}))
        return self

    def lpush(self, key: str, *values: str) -> _FakePipeline:
        self._ops.append(("lpush", key, {"values": values}))
        return self

    def ltrim(self, key: str, start: int, stop: int) -> _FakePipeline:
        self._ops.append(("ltrim", key, {"start": start, "stop": stop}))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, key, kwargs in self._ops:
            if op == "set":
                self._redis._strings[key] = kwargs["value"]
                if kwargs.get("ex"):
                    self._redis._ttls[key] = float(kwargs["ex"])
                results.append(True)
            elif op == "hset":
                h = self._redis._hashes.setdefault(key, {})
                for field, value in kwargs.items():
                    h[field] = value if isinstance(value, str) else str(value)
                results.append(True)
            elif op == "expire":
                self._redis._ttls[key] = float(kwargs["ttl"])
                results.append(True)
            elif op == "zadd":
                z = self._redis._zsets.setdefault(key, {})
                for member, score in kwargs["mapping"].items():
                    z[member] = float(score)
                results.append(True)
            elif op == "delete":
                deleted = 0
                if key in self._redis._strings:
                    del self._redis._strings[key]
                    deleted += 1
                results.append(deleted)
            elif op == "lpush":
                lst = self._redis._lists.setdefault(key, [])
                for v in kwargs["values"]:
                    lst.insert(0, v)
                results.append(len(lst))
            elif op == "ltrim":
                lst = self._redis._lists.setdefault(key, [])
                start = kwargs["start"]
                stop = kwargs["stop"]
                if stop < 0:
                    stop = len(lst) + stop + 1
                else:
                    stop += 1
                self._redis._lists[key] = lst[start:stop]
                results.append(True)
        self._ops.clear()
        return results

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Modules that do **module-level** `from app.core.redis import redis_client` —
# each must be patched individually because `from ... import` binds the name in
# the module's own namespace at import time; patching app.core.redis.redis_client
# alone does NOT affect them. 函数内懒导入的模块（loop_data / tags / tuning /
# kpi_calc）无需在此列出——它们在调用时从 app.core.redis 查找，已被
# patch("app.core.redis.redis_client", ...) 覆盖。
# 维护：grep -rn "^from app.core.redis import redis_client" app/ --include="*.py"
# ---------------------------------------------------------------------------
_REDIS_CLIENT_MODULES: list[str] = [
    "app.api.v1.endpoints.dataplanner",
    "app.api.v1.endpoints.health",
    "app.api.v1.endpoints.tasks",
    "app.api.v1.endpoints.ws_realtime",
    "app.middleware.idempotency",
    "app.middleware.rate_limit",
    "app.services.alert_rule_engine.cache",
    "app.services.alert_rule_engine.dispatcher",
    "app.services.alert_rule_engine.suppressor",
    "app.services.auth",
    "app.services.dashboard",
    "app.services.data_import",
    "app.services.data_source.realtime_subscriber",
    "app.services.diagnosis_rule",
    "app.services.loop",
    "app.services.performance",
    "app.services.task_tracker",
    "app.services.tuning_progress",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_universal_db_result() -> MagicMock:
    """构造通用 DB execute 结果，支持所有结果访问方式。

    用于 dashboard 并行查询的 mock session，返回空/零/None 默认值。
    各方法返回值：
    - scalars().all() → []（空列表，用于 _build_inefficient_loops / _build_pending_alerts）
    - scalar() → 0（用于 _build_pending_alerts diag count）
    - scalar_one_or_none() → None（用于 _get_plant_name）
    - one() → 空聚合行（cnt=0，所有字段 None，用于 _aggregate_kpi_cards_sql /
      _aggregate_counts_sql）
    - all() → []（空列表，用于 _aggregate_trend_summary_sql /
      _batch_query_diagnosis_labels）
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar.return_value = 0
    result.scalar_one_or_none.return_value = None
    # 空聚合行：所有 cur_/prev_ 字段为 None，cnt 为 0
    empty_row = MagicMock()
    empty_row.cur_cnt = 0
    empty_row.prev_cnt = 0
    empty_row.cur_score = None
    empty_row.prev_score = None
    empty_row.cur_auto_mode_rate = None
    empty_row.prev_auto_mode_rate = None
    empty_row.cur_steady_rate = None
    empty_row.prev_steady_rate = None
    empty_row.cur_good_value_rate = None
    empty_row.prev_good_value_rate = None
    result.one.return_value = empty_row
    result.all.return_value = []
    return result


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Provide a fresh FakeRedis instance."""
    return FakeRedis()


@pytest.fixture
def mock_db() -> AsyncMock:
    """Provide a mock async DB session."""
    db = AsyncMock()
    # Default: no user found. Individual tests override this.
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def mock_dashboard_session_local() -> AsyncMock:
    """Patch app.services.dashboard.AsyncSessionLocal for service-layer tests.

    Yields the mock parallel session whose execute returns a universal result.
    """
    universal_result = _make_universal_db_result()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=universal_result)
    with patch("app.services.dashboard.AsyncSessionLocal") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
        yield mock_session


@pytest.fixture
def client(fake_redis: FakeRedis, mock_db: AsyncMock) -> TestClient:
    """Provide a TestClient with DB and Redis mocked out.

    The mock Redis is installed at module level so all service/endpoint functions
    pick it up. The mock DB is injected via FastAPI dependency override.
    AsyncSessionLocal is patched so dashboard parallel queries use a mock session.
    """
    from app.core.db import get_db
    from app.main import app

    # 构造通用 DB 结果 mock（用于 dashboard 并行查询的独立 session）
    universal_result = _make_universal_db_result()
    mock_parallel_session = AsyncMock()
    mock_parallel_session.execute = AsyncMock(return_value=universal_result)

    # Patch redis_client 在 app.core.redis 及所有 `from app.core.redis import redis_client`
    # 的模块。`from ... import` 在导入时绑定名字到各自模块命名空间，必须逐个 patch。
    with ExitStack() as stack:
        stack.enter_context(patch("app.core.redis.redis_client", fake_redis))
        for mod in _REDIS_CLIENT_MODULES:
            stack.enter_context(patch(f"{mod}.redis_client", fake_redis))
        mock_session_local = stack.enter_context(patch("app.services.dashboard.AsyncSessionLocal"))

        # 配置 AsyncSessionLocal mock：每次 async with 返回 mock_parallel_session
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_parallel_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        # Override DB dependency to return our mock session.
        async def _override_db():
            yield mock_db

        app.dependency_overrides[get_db] = _override_db

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()


@pytest.fixture
def admin_user() -> MagicMock:
    """Return the admin test user."""
    return TEST_USERS["admin"]


@pytest.fixture
def ic_user() -> MagicMock:
    """Return the IC_ENGINEER test user."""
    return TEST_USERS["ic_engineer"]


def make_db_execute_return(user: MagicMock | None) -> MagicMock:
    """Helper: create a mock execute() return value that yields ``user``."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=user)
    return result


def login_as(client: TestClient, username: str = "admin", password: str = TEST_PASSWORD) -> dict:
    """Helper: login and return the response data."""
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    return resp.json()["data"]


@contextmanager
def mock_current_user(user: MagicMock):
    """Override ``get_current_user`` via FastAPI dependency_overrides.

    ``patch("app.api.deps.get_current_user", ...)`` does NOT work because
    ``Depends(get_current_user)`` captures the function object at route
    registration time. FastAPI's ``dependency_overrides`` is the correct
    mechanism — it also cascades into ``require_roles`` which internally
    depends on ``get_current_user``.
    """
    from app.api.deps import get_current_user
    from app.main import app

    async def _override() -> MagicMock:
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
