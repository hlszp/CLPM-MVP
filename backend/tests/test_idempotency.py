"""写操作幂等性中间件测试 (S2-C6)。

测试覆盖：
- 相同 Idempotency-Key 的 POST 请求返回缓存响应，不重复创建
- 不同 Idempotency-Key 的请求各自独立执行
- 无 Idempotency-Key 的请求正常工作（向后兼容）
- GET 请求不受幂等性中间件影响
- Redis 不可用时降级为正常请求
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# POST 幂等性测试
# ---------------------------------------------------------------------------


class TestIdempotencyPost:
    """POST 请求幂等性测试。"""

    def test_same_key_returns_cached_response(self, client, mock_db, fake_redis) -> None:
        """相同 Idempotency-Key 的 POST 请求返回缓存响应，不重复创建。"""
        # 第一次请求：用户名不存在，可以创建
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        headers = {
            "Authorization": "Bearer fake-token",
            "Idempotency-Key": "test-key-001",
        }
        body = {
            "username": "idempotent_user",
            "password": "Pass@1234",
            "displayName": "幂等用户",
            "role": "IC_ENGINEER",
        }

        with mock_current_user(TEST_USERS["admin"]):
            # 第一次请求 — 正常创建
            resp1 = client.post("/api/v1/users", headers=headers, json=body)
            assert resp1.status_code == 201
            body1 = resp1.json()
            assert body1["code"] == "0"
            assert body1["data"]["username"] == "idempotent_user"

            # 第二次请求 — 相同 Idempotency-Key，应返回缓存响应
            resp2 = client.post("/api/v1/users", headers=headers, json=body)
            assert resp2.status_code == 201
            assert resp2.json() == body1

        # 验证用户只创建了一次（create_user 调用 db.add 两次：user + audit log）
        # 第二次请求应从缓存返回，不触发 db.add
        assert mock_db.add.call_count == 2
        assert mock_db.commit.call_count == 1

    def test_different_keys_execute_independently(self, client, mock_db, fake_redis) -> None:
        """不同 Idempotency-Key 的请求各自独立执行。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        body = {
            "username": "user_a",
            "password": "Pass@1234",
            "displayName": "用户A",
            "role": "IC_ENGINEER",
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp1 = client.post(
                "/api/v1/users",
                headers={
                    "Authorization": "Bearer fake-token",
                    "Idempotency-Key": "key-a",
                },
                json=body,
            )
            assert resp1.status_code == 201

            # 第二次请求用不同的 key 和不同的用户名
            body2 = dict(body, username="user_b", displayName="用户B")
            resp2 = client.post(
                "/api/v1/users",
                headers={
                    "Authorization": "Bearer fake-token",
                    "Idempotency-Key": "key-b",
                },
                json=body2,
            )
            assert resp2.status_code == 201
            assert resp2.json()["data"]["username"] == "user_b"

        # 两次请求都执行了创建
        assert mock_db.add.call_count == 4  # 2 次 user + 2 次 audit log
        assert mock_db.commit.call_count == 2

    def test_no_key_works_normally(self, client, mock_db, fake_redis) -> None:
        """无 Idempotency-Key 的请求正常工作（向后兼容）。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        headers = {"Authorization": "Bearer fake-token"}
        body = {
            "username": "no_key_user",
            "password": "Pass@1234",
            "displayName": "无Key用户",
            "role": "IC_ENGINEER",
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post("/api/v1/users", headers=headers, json=body)
            assert resp.status_code == 201
            assert resp.json()["data"]["username"] == "no_key_user"

        # 正常执行了一次创建
        assert mock_db.add.call_count == 2  # user + audit log
        assert mock_db.commit.call_count == 1


# ---------------------------------------------------------------------------
# GET 请求不受影响测试
# ---------------------------------------------------------------------------


class TestIdempotencyGet:
    """GET 请求不受幂等性中间件影响。"""

    def test_get_not_cached(self, client, mock_db, fake_redis) -> None:
        """GET 请求即使携带 Idempotency-Key 也不被缓存。"""
        from tests.test_users import _make_count_mock, _make_scalars_mock, _make_user

        users = [_make_user(username="user1"), _make_user(username="user2")]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                # Count query
                return _make_count_mock(2)
            return _make_scalars_mock(users)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        headers = {
            "Authorization": "Bearer fake-token",
            "Idempotency-Key": "get-key-001",
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp1 = client.get("/api/v1/users", headers=headers)
            assert resp1.status_code == 200

            resp2 = client.get("/api/v1/users", headers=headers)
            assert resp2.status_code == 200

        # 两次 GET 请求都执行了（未被缓存）
        assert call_count[0] == 4  # 2 次请求 × 2 次 execute（count + list）


# ---------------------------------------------------------------------------
# Redis 不可用降级测试
# ---------------------------------------------------------------------------


class TestIdempotencyDegrade:
    """Redis 不可用时降级为正常请求。"""

    def test_redis_get_failure_degrades(self, client, mock_db, fake_redis) -> None:
        """Redis GET 失败时降级为正常请求。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        # 让 Redis get 抛异常
        fake_redis.get = AsyncMock(side_effect=Exception("Redis connection refused"))
        # setex 也抛异常
        fake_redis.setex = AsyncMock(side_effect=Exception("Redis connection refused"))

        headers = {
            "Authorization": "Bearer fake-token",
            "Idempotency-Key": "redis-down-key",
        }
        body = {
            "username": "degrade_user",
            "password": "Pass@1234",
            "displayName": "降级用户",
            "role": "IC_ENGINEER",
        }

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post("/api/v1/users", headers=headers, json=body)
            assert resp.status_code == 201
            assert resp.json()["data"]["username"] == "degrade_user"

        # 请求正常执行
        assert mock_db.add.call_count == 2
        assert mock_db.commit.call_count == 1
