"""历史导入窗口背压校验（整改 G13）。

背景
----
AGENTS.md 要求「手工导入 overwrite 强制 tsEnd ≤ now-5min」，但该约束在代码中
**从未实现**：端点只校验 tsStart < tsEnd，schema 只把时间当裸字符串，服务层
签名也没有上界参数（三层皆可绕过）。任务重算侧却有同类防护
（tasks.py 的 ERR_BACKFILL_WINDOW_IN_FUTURE），唯独导入侧缺失——
导致**最活跃的时间窗反而无保护**。

危害链（见整改方案 G11）：导入以 UPSERT 直写点表 → 远端网格值覆盖正在写入的
实时值 → 随后到达的同 ts 实时事件因 payload_hash 不一致被判冲突并静默丢弃。

本文件守护两层防护：端点层（面向用户）与服务层（防绕过 API 直投 Celery）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_import import IMPORT_MIN_END_LAG_MINUTES, import_history_data
from tests.conftest import TEST_USERS, mock_current_user


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _window(minutes_ago: int, span_minutes: int = 60) -> dict[str, object]:
    """构造结束时间距现在 minutes_ago 分钟的导入请求体。"""
    end = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    start = end - timedelta(minutes=span_minutes)
    return {"tsStart": _iso(start), "tsEnd": _iso(end), "loopIds": ["loop-1"]}


# ---------------------------------------------------------------------------
# 一、端点层
# ---------------------------------------------------------------------------


class TestImportWindowEndpointGuard:
    """POST /loops/data-import/start 的窗口背压。"""

    def test_recent_window_rejected(self, client, mock_db, fake_redis) -> None:
        """tsEnd = 现在 → 400 ERR_IMPORT_WINDOW_TOO_RECENT。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops/data-import/start",
                headers={"Authorization": "Bearer fake-token"},
                json=_window(minutes_ago=0),
            )
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "ERR_IMPORT_WINDOW_TOO_RECENT"

    def test_window_inside_grace_rejected(self, client, mock_db, fake_redis) -> None:
        """tsEnd = 3 分钟前（落在 5 分钟宽限内）→ 同样拒绝。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops/data-import/start",
                headers={"Authorization": "Bearer fake-token"},
                json=_window(minutes_ago=IMPORT_MIN_END_LAG_MINUTES - 2),
            )
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "ERR_IMPORT_WINDOW_TOO_RECENT"

    def test_sufficiently_old_window_passes_this_guard(self, client, mock_db, fake_redis) -> None:
        """tsEnd 早于宽限 → 不再因该规则被拒（会走到后续的远端配置校验）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops/data-import/start",
                headers={"Authorization": "Bearer fake-token"},
                json=_window(minutes_ago=IMPORT_MIN_END_LAG_MINUTES + 30),
            )
        # 本机可能未配置 HISTORY_DATA_API_URL，故不断言成功；
        # 只断言"不是被窗口规则挡下的"——这正是本用例要证明的边界。
        assert resp.json().get("code") != "ERR_IMPORT_WINDOW_TOO_RECENT", resp.text

    def test_invalid_range_still_checked_first(self, client, mock_db, fake_redis) -> None:
        """起止颠倒仍走原有 ERR_INVALID_TIME_RANGE（不得被新规则抢答）。"""
        body = _window(minutes_ago=60)
        body["tsStart"], body["tsEnd"] = body["tsEnd"], body["tsStart"]
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops/data-import/start",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )
        assert resp.json()["code"] == "ERR_INVALID_TIME_RANGE"


# ---------------------------------------------------------------------------
# 二、服务层兜底（防绕过 API 直投 Celery）
# ---------------------------------------------------------------------------


class TestImportWindowServiceGuard:
    """import_history_data 自身的窗口断言。"""

    def test_service_rejects_recent_window(self) -> None:
        """绕过端点直接调用服务 → 仍拒绝（ValueError）。"""
        window = _window(minutes_ago=0)
        with pytest.raises(ValueError, match="早于当前时间"):
            asyncio.run(
                import_history_data(
                    loop_ids=["loop-1"],
                    ts_start=str(window["tsStart"]),
                    ts_end=str(window["tsEnd"]),
                )
            )

    def test_service_allows_old_window_to_proceed(self) -> None:
        """足够旧的窗口不会被窗口规则挡下（后续失败属环境原因，非本规则）。"""
        window = _window(minutes_ago=IMPORT_MIN_END_LAG_MINUTES + 30)
        try:
            asyncio.run(
                import_history_data(
                    loop_ids=["loop-1"],
                    ts_start=str(window["tsStart"]),
                    ts_end=str(window["tsEnd"]),
                )
            )
        except ValueError as exc:
            assert "早于当前时间" not in str(exc), f"足够旧的窗口被窗口规则误拒：{exc}"
        except Exception:
            # 无远端配置/无 TDengine 等环境原因导致的失败与本规则无关
            pass
