"""Shared pytest fixtures for auth tests.

Uses ``unittest.mock`` to stub the async DB session and Redis client so tests
run without external dependencies (no PostgreSQL/Redis required).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

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
    """Minimal in-memory async Redis mock for auth tests."""

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}
        self._ttls: dict[str, float] = {}

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
        self._ttls.clear()


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

    The mock Redis is installed at module level so all auth service functions
    pick it up. The mock DB is injected via FastAPI dependency override.
    AsyncSessionLocal is patched so dashboard parallel queries use a mock session.
    """
    from app.core.db import get_db
    from app.main import app

    # 构造通用 DB 结果 mock（用于 dashboard 并行查询的独立 session）
    universal_result = _make_universal_db_result()
    mock_parallel_session = AsyncMock()
    mock_parallel_session.execute = AsyncMock(return_value=universal_result)

    # Patch the redis_client used by the auth/dashboard services and rate limit middleware.
    with (
        patch("app.core.redis.redis_client", fake_redis),
        patch("app.services.auth.redis_client", fake_redis),
        patch("app.services.dashboard.redis_client", fake_redis),
        patch("app.middleware.rate_limit.redis_client", fake_redis),
        patch("app.services.dashboard.AsyncSessionLocal") as mock_session_local,
    ):
        # 配置 AsyncSessionLocal mock：每次 async with 返回 mock_parallel_session
        mock_session_local.return_value.__aenter__ = AsyncMock(
            return_value=mock_parallel_session
        )
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
