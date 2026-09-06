"""数据链路整改 S2 行为测试（R02/R05/R08，角色 A 第二轮）.

对齐审查报告 §7 测试盲区三行：
- 行时间与 last-known：PV 不变而 SP/OP/MODE 变化、重复/乱序 collectTime、
  旧重连快照不回退、同 tick 多次更新、不修改旧历史状态；
- gap backfill：同代第二/三次重连、部分分片长时故障、实时成功不覆盖旧失败
  gap、空返回计数、重启保留待补窗口、开关关闭只登记不调远端；
- 缓存容量：高速率+重复 ts 不越每回路上限、全局预算触达后新回路不再建键、
  TTL+上限保证有界。

全部测试调用真实实现（仅 fake Redis/WS/DB），不在测试体复制实现逻辑。
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source.realtime_subscriber import (
    _GAP_LOOP_WM_KEY,
    _GAP_PENDING_KEY,
    _HISTORY_KEY_PREFIX,
    _REDIS_KEY_PREFIX,
    RealtimeSubscriber,
    _ShardState,
)
from tests.test_data_source.test_realtime_subscriber import (
    _FakeRedis,
    _gap_settings,
)
from tests.test_data_source.test_realtime_subscriber_s1 import _RecordingRedis

_SUB = "app.services.data_source.realtime_subscriber"
_TZ8 = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 共享 helper
# ---------------------------------------------------------------------------


def _r02_settings(mock_s, **overrides) -> None:
    """R02 缓存容量测试 settings（int 型才会被 _history_limits 采信）."""
    mock_s.REALTIME_HISTORY_MAX_POINTS_PER_LOOP = 1200
    mock_s.REALTIME_HISTORY_GLOBAL_BUDGET_BYTES = 64 * 1024 * 1024
    mock_s.REALTIME_WRITEBACK_ENABLED = False
    for k, v in overrides.items():
        setattr(mock_s, k, v)


def _hist_entry(loop_part: str, ts: str, pv: float = 1.0) -> tuple[str, str, str]:
    """构造 (key, row_json, row_ts) 历史缓存条目（与 _flush_buffer 输出同构）."""
    row = {
        "ts": ts,
        "pv": pv,
        "sp": None,
        "op": None,
        "mode": None,
        "pid_p": None,
        "pid_i": None,
        "pid_d": None,
        "pv_quality": 1,
        "roleTs": {"PV": ts},
        "roleQuality": {"PV": 1},
    }
    return f"{_HISTORY_KEY_PREFIX}{loop_part}", json.dumps(row), ts


def _mock_gap_db(rows: list[tuple[str, str]]) -> AsyncMock:
    """构造 _run_gap_backfill 的 mock AsyncSessionLocal（(loop_id, tag_name) 行）."""
    result = MagicMock()
    result.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _mapped_loop_data(loop_ids: list[str]) -> dict[str, dict]:
    return {
        lid: {
            "role_tag_map": {"PV": f"{lid}.PV"},
            "unit_id": "unit-1",
            "subtable": f"t_{lid}",
            "loop_part": lid,
        }
        for lid in loop_ids
    }


async def _cancel_retry(sub: RealtimeSubscriber) -> None:
    task = sub._backfill_retry_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# R05：行时间 = max(角色 sourceTime)；乱序拒绝；无 ts 丢弃；自描述行
# ---------------------------------------------------------------------------


async def test_r05_sp_change_gets_new_row_time_and_old_row_preserved():
    """验收用例：10:00:00 PV=5/SP=6 落行后，10:00:10 仅 SP=9 → 新行 ts=10:00:10
    （PV 取 last-known 5），旧时刻行 SP 仍为 6——TD/Redis 两行独立存在。"""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()

    async def _feed(tag: str, value: str, collect_time: str) -> None:
        accepted = await sub._cache_value(
            {"tagCode": tag, "value": value, "quality": 1, "collectTime": collect_time}
        )
        assert accepted is True

    insert = AsyncMock(return_value=1)

    async def _flush():
        with (
            patch(f"{_SUB}.settings") as mock_s,
            patch(f"{_SUB}.batch_insert_multi", new=insert),
            patch.object(sub, "_get_loop_meta_map", new=AsyncMock(return_value={})),
        ):
            mock_s.REALTIME_WRITEBACK_ENABLED = True
            await sub._flush_buffer()

    with patch(f"{_SUB}.redis_client", fake):
        # tick1：PV/SP 同时刻到达
        await _feed("LIC.PV", "5", "2026-09-06 10:00:00")
        await _feed("LIC.SP", "6", "2026-09-06 10:00:00")
        await _flush()

        # tick2：仅 SP 在 10:00:10 变化（PV 无新测量）
        await _feed("LIC.SP", "9", "2026-09-06 10:00:10")
        await _flush()

    # TD 两行独立存在：旧行 ts=10:00:00（sp=6），新行 ts=10:00:10（sp=9, pv=5）
    td_rows = [t["rows"][0] for call in insert.await_args_list for t in call.args[0]]
    assert [r[0] for r in td_rows] == ["2026-09-06 10:00:00.000", "2026-09-06 10:00:10.000"]
    assert td_rows[0][2] == 6.0 and td_rows[0][1] == 5.0
    assert td_rows[1][2] == 9.0
    assert td_rows[1][1] == 5.0, "PV 取 last-known 5，不因 SP 事件伪造新 PV"

    # Redis 历史：旧时刻行未被改写（sp 仍 6），两行独立存在
    hist = fake._lists[f"{_HISTORY_KEY_PREFIX}LIC"]
    assert len(hist) == 2
    rows = [json.loads(r) for r in hist]
    by_ts = {r["ts"]: r for r in rows}
    assert by_ts["2026-09-06 10:00:00.000"]["sp"] == 6.0, "旧时刻 SP 不被新值改写"
    assert by_ts["2026-09-06 10:00:10.000"]["sp"] == 9.0
    assert by_ts["2026-09-06 10:00:10.000"]["pv"] == 5.0


async def test_r05_history_row_self_describing_rolets_and_rolequality():
    """自描述行：roleTs 标注各角色 sourceTime——下游可区分"新测量"与"携带的
    last-known"（roleTs == 行 ts 为新测量，< 行 ts 为携带值）；roleQuality 保留."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()

    with patch(f"{_SUB}.redis_client", fake):
        await sub._cache_value(
            {"tagCode": "LIC.PV", "value": "5", "quality": 1, "collectTime": "2026-09-06 10:00:00"}
        )
        await sub._cache_value(
            {"tagCode": "LIC.SP", "value": "6", "quality": 1, "collectTime": "2026-09-06 10:00:00"}
        )
        await sub._cache_value(
            {"tagCode": "LIC.SP", "value": "9", "quality": 1, "collectTime": "2026-09-06 10:00:10"}
        )
        with patch(f"{_SUB}.settings") as mock_s:
            mock_s.REALTIME_WRITEBACK_ENABLED = False
            await sub._flush_buffer()

    hist = fake._lists[f"{_HISTORY_KEY_PREFIX}LIC"]
    newest = json.loads(hist[0])  # LPUSH 头部 = 最新行
    assert newest["ts"] == "2026-09-06 10:00:10.000"
    assert newest["roleTs"]["SP"] == "2026-09-06 10:00:10.000", "SP 为驱动本行的新测量"
    assert newest["roleTs"]["PV"] == "2026-09-06 10:00:00.000", "PV 为携带的 last-known"
    assert newest["roleQuality"] == {"PV": 1, "SP": 1}
    assert newest["pv_quality"] == 1  # 既有列保持


async def test_r05_late_snapshot_does_not_rollback_current_value():
    """乱序/迟到拒绝：旧快照不回退当前值（late_rejected 计数、已存状态不动）."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()

    with patch(f"{_SUB}.redis_client", fake):
        await sub._cache_value(
            {"tagCode": "LIC.PV", "value": "7", "quality": 1, "collectTime": "2026-09-06 10:00:30"}
        )
        assert sub._last_known["LIC"]["PV"]["ts"] == "2026-09-06 10:00:30"

        # 迟到旧快照（sourceTime 更早，即使到达更晚）→ 拒绝，不回退
        await sub._cache_value(
            {"tagCode": "LIC.PV", "value": "3", "quality": 1, "collectTime": "2026-09-06 10:00:20"}
        )
        assert sub._metrics["late_rejected"] == 1
        entry = sub._last_known["LIC"]["PV"]
        assert entry["ts"] == "2026-09-06 10:00:30"
        assert entry["value"] == "7"
        assert sub._buffer["LIC"]["PV"]["ts"] == "2026-09-06 10:00:30", "buffer 不被旧快照覆盖"

        # 无 sourceTime 的更新不得回退已知时间
        await sub._cache_value({"tagCode": "LIC.PV", "value": "8", "quality": 0, "collectTime": ""})
        assert sub._metrics["late_rejected"] == 2
        assert sub._last_known["LIC"]["PV"]["ts"] == "2026-09-06 10:00:30"

        # 同 ts 同值幂等接受（recvAt 更新）
        await sub._cache_value(
            {"tagCode": "LIC.PV", "value": "7", "quality": 1, "collectTime": "2026-09-06 10:00:30"}
        )
        assert sub._metrics["late_rejected"] == 2, "同 ts 幂等接受不计拒绝"


def test_r05_update_is_newer_rule_matrix():
    """逐角色确定性接受规则的矩阵验证（>、==且recvAt≥、==且recvAt<、未知组合）."""
    base = {"ts": "2026-09-06 10:00:30", "recvAt": 1000.0}
    newer_ts = {"ts": "2026-09-06 10:00:31", "recvAt": 999.0}
    older_ts = {"ts": "2026-09-06 10:00:20", "recvAt": 2000.0}
    same_ts_newer_recv = {"ts": "2026-09-06 10:00:30", "recvAt": 1001.0}
    same_ts_older_recv = {"ts": "2026-09-06 10:00:30", "recvAt": 999.0}
    unknown_ts = {"ts": "", "recvAt": 3000.0}
    assert RealtimeSubscriber._update_is_newer(base, newer_ts) is True
    assert RealtimeSubscriber._update_is_newer(base, older_ts) is False
    assert RealtimeSubscriber._update_is_newer(base, same_ts_newer_recv) is True
    assert RealtimeSubscriber._update_is_newer(base, same_ts_older_recv) is False
    # 未知新 sourceTime：不得回退已知 sourceTime
    assert RealtimeSubscriber._update_is_newer(base, unknown_ts) is False
    # 已存未知、新已知 → 接受（从未知改善为已知）
    assert RealtimeSubscriber._update_is_newer(unknown_ts, newer_ts) is True
    # 两者均未知 → recvAt 仲裁
    assert RealtimeSubscriber._update_is_newer(unknown_ts, {"ts": "", "recvAt": 3001.0}) is True
    assert RealtimeSubscriber._update_is_newer(unknown_ts, {"ts": "", "recvAt": 2999.0}) is False
    # 时区口径：同一时刻的 Z 与 +08 表示应判定相等（recvAt 决定）
    tz_same = {"ts": "2026-09-06T02:00:30Z", "recvAt": 1001.0}
    assert RealtimeSubscriber._update_is_newer(base, tz_same) is True


async def test_r05_row_without_any_ts_dropped_and_counted():
    """整行无任何已知 sourceTime → 不落 TD/历史缓存，计 rows_dropped_no_ts."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()
    insert = AsyncMock(return_value=1)

    with patch(f"{_SUB}.redis_client", fake):
        await sub._cache_value({"tagCode": "LIC.PV", "value": "5", "collectTime": ""})
        await sub._cache_value({"tagCode": "LIC.SP", "value": "6", "collectTime": ""})
        with (
            patch(f"{_SUB}.settings") as mock_s,
            patch(f"{_SUB}.batch_insert_multi", new=insert),
        ):
            mock_s.REALTIME_WRITEBACK_ENABLED = True
            await sub._flush_buffer()

    assert sub._metrics["rows_dropped_no_ts"] == 1
    insert.assert_not_awaited(), "无 ts 行不得写 TD"
    assert fake._lists.get(f"{_HISTORY_KEY_PREFIX}LIC") in (None, []), "无 ts 行不得入历史缓存"


async def test_r05_get_history_values_sorted_and_deduped():
    """返回前按 ts 排序 + 同 ts 去重（保留后写值），不再只 reverse 到达顺序."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    key = f"{_HISTORY_KEY_PREFIX}LIC"

    def _row(ts: str, pv: float) -> str:
        return json.dumps({"ts": ts, "pv": pv, "sp": None, "pv_quality": 1})

    # 模拟乱序写入 + 同 ts 重复（后写值 pv=2 应胜出）
    for raw in (_row("2026-09-06 10:00:20.000", 1), _row("2026-09-06 10:00:10.000", 5)):
        await fake.lpush(key, raw)
    await fake.lpush(key, _row("2026-09-06 10:00:20.000", 2))
    await fake.lpush(key, _row("not-a-ts", 9))

    with patch(f"{_SUB}.redis_client", fake):
        rows = await sub.get_history_values("LIC")

    assert [r["ts"] for r in rows[:2]] == [
        "2026-09-06 10:00:10.000",
        "2026-09-06 10:00:20.000",
    ], "乱序输入须按 ts 升序返回"
    assert rows[1]["pv"] == 2, "同 ts 去重保留后写值"
    assert len(rows) == 3
    assert rows[-1]["ts"] == "not-a-ts", "不可解析 ts 排末尾（不丢弃不炸序）"


# ---------------------------------------------------------------------------
# R08：分片重连 gap（per-loop 水位 + 持久待补列表）
# ---------------------------------------------------------------------------


async def test_r08_failing_shard_reconnects_still_produce_gap_windows():
    """A 片健康推进水位，B 片断开超阈值——B 片第二/第三次重连（同代池内）
    仍产生正确缺口窗口；健康回路 A 不被误伤。"""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()  # 点号风格 tag 兜底（免 DB）
    now = time.time()
    # A 刚成功落库（水位新鲜）；B 一小时前最后一次成功行（水位陈旧）
    sub._loop_watermarks = {"LOOPA": now, "LOOPB": now - 3600}
    sub._loop_wm_loaded = True
    state_b = _ShardState(index=1, total=2, tags=["LOOPB.PV"])

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
        patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_bf,
    ):
        _gap_settings(mock_s)  # ENABLED=True, MIN_GAP=60
        # 健康片 A 的建连检查：不产生 A 的缺口窗口
        state_a = _ShardState(index=0, total=2, tags=["LOOPA.PV"])
        await sub._maybe_trigger_gap_backfill(state_a)
        entries = await sub._load_pending_gaps()
        assert not any(e.get("loops") and "LOOPA" in e["loops"] for e in entries), (
            "新鲜水位回路不得进缺口窗口"
        )

        # B 片同代池内连续三次重连，每次都核对水位并产生窗口
        for i in range(3):
            await sub._maybe_trigger_gap_backfill(state_b)
            assert sub._backfill_task is not None, f"第 {i + 1} 次重连须触发补数"
            await sub._backfill_task

    assert mock_bf.await_count == 3, "第二/第三次重连不得被一次性标记吞掉"
    for call in mock_bf.await_args_list:
        assert call.args[0] == pytest.approx(now - 3600, abs=5), "窗口起点 = B 片最小水位"
        assert call.kwargs["loop_parts"] == ["LOOPB"], "仅覆盖 B 片故障回路（不无边界全量）"
        assert call.args[1] > now - 3600


async def test_r08_reshard_keeps_loop_identity():
    """分片重建（reshard）后身份不串位：缺口按 loop_part 识别，与分片编号无关."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()
    now = time.time()
    sub._loop_watermarks = {"LOOPB": now - 7200}
    sub._loop_wm_loaded = True

    # reshard：LOOPB.PV 从原分片 2 迁入新分片 0（与 LOOPA.PV 同片）
    new_shard = _ShardState(index=0, total=1, tags=["LOOPA.PV", "LOOPB.PV"])
    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
        patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_bf,
    ):
        _gap_settings(mock_s)
        await sub._maybe_trigger_gap_backfill(new_shard)
        await sub._backfill_task

    assert mock_bf.await_count == 1
    assert mock_bf.await_args.kwargs["loop_parts"] == ["LOOPB"]
    assert mock_bf.await_args.args[0] == pytest.approx(now - 7200, abs=5)


async def test_r08_pending_window_survives_restart():
    """补数失败/进程重启后待补窗口仍可见：新实例从 Redis 恢复水位并消费."""
    fake = _FakeRedis()
    old_dt = datetime.now(_TZ8).replace(microsecond=0) - timedelta(hours=2)
    collect_time = old_dt.strftime("%Y-%m-%d %H:%M:%S")
    sub1 = RealtimeSubscriber()
    sub1._refresh_loop_meta_cache = AsyncMock()

    # 第一实例：B 片正常落库一次（老时间戳）→ 水位持久化到 Redis hash
    with patch(f"{_SUB}.redis_client", fake):
        await sub1._cache_value(
            {"tagCode": "LOOPB.PV", "value": "1", "quality": 1, "collectTime": collect_time}
        )
        with patch(f"{_SUB}.settings") as mock_s:
            mock_s.REALTIME_WRITEBACK_ENABLED = False
            await sub1._flush_buffer()
        await sub1._maybe_save_loop_watermarks(force=True)
    wm_persisted = sub1._loop_watermarks["LOOPB"]
    assert wm_persisted == pytest.approx(old_dt.timestamp(), abs=1)

    # 开关关闭期间登记缺口（只登记不调远端）——模拟补数不可用/失败场景
    state_b = _ShardState(index=0, total=1, tags=["LOOPB.PV"])
    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _gap_settings(mock_s)
        mock_s.GAP_BACKFILL_ENABLED = False
        await sub1._maybe_trigger_gap_backfill(state_b)
    assert len(fake._lists.get(_GAP_PENDING_KEY, [])) == 1

    # 进程重启：新实例共享同一 Redis，恢复水位后消费待补窗口
    sub2 = RealtimeSubscriber()
    sub2._refresh_loop_meta_cache = AsyncMock()
    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
        patch.object(sub2, "_run_gap_backfill", new=AsyncMock()) as mock_bf,
    ):
        _gap_settings(mock_s)
        await sub2._load_checkpoint()
        assert sub2._loop_watermarks["LOOPB"] == pytest.approx(wm_persisted, abs=0.01)
        await sub2._maybe_trigger_gap_backfill(state_b)
        assert sub2._backfill_task is not None
        await sub2._backfill_task

    mock_bf.assert_awaited_once()
    assert mock_bf.await_args.args[0] == pytest.approx(wm_persisted, abs=1), "窗口起点=持久化水位"
    assert mock_bf.await_args.kwargs["loop_parts"] == ["LOOPB"]


async def test_r08_disabled_switch_registers_without_remote_call():
    """开关关闭：只登记缺口不调用远端（import_history_data 零调用 + 计数）."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()
    now = time.time()
    sub._loop_watermarks = {"LOOPB": now - 3600}
    sub._loop_wm_loaded = True
    state_b = _ShardState(index=0, total=1, tags=["LOOPB.PV"])

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
        patch("app.services.data_import.import_history_data", new=AsyncMock()) as mock_import,
        patch.object(sub, "_run_gap_backfill", new=AsyncMock()),
    ):
        _gap_settings(mock_s)
        mock_s.GAP_BACKFILL_ENABLED = False
        await sub._maybe_trigger_gap_backfill(state_b)

    assert sub._backfill_task is None, "开关关闭不得创建补数任务"
    mock_import.assert_not_awaited(), "开关关闭不得调用远端历史接口"
    entries = [json.loads(r) for r in fake._lists.get(_GAP_PENDING_KEY, [])]
    assert len(entries) == 1
    assert entries[0]["loops"] == ["LOOPB"]
    assert sub._metrics["gap_windows_registered"] == 1


async def test_r08_per_loop_backfill_success_and_failure_lifecycle():
    """per-loop 补数：成功→水位推进+条目出队+空窗口计数；失败→条目保留+重试安排."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    now = time.time()
    gap_start, gap_end = now - 3600, now - 2

    async def _register_entry() -> dict:
        await sub._register_gap_window(["LOOPB"], gap_start, now)
        entries = await sub._load_pending_gaps()
        assert len(entries) == 1
        return entries[0]

    def _common_patches():
        return [
            patch(f"{_SUB}.redis_client", fake),
            patch(f"{_SUB}.AsyncSessionLocal", return_value=_mock_gap_db([("loop-1", "LOOPB")])),
            patch(
                "app.services.data_import._batch_get_loop_data",
                new=AsyncMock(return_value=_mapped_loop_data(["loop-1"])),
            ),
            patch("app.services.task_tracker.create_task", new=AsyncMock(return_value="task-1")),
            patch("app.services.task_tracker.update_status", new=AsyncMock()),
            patch("app.services.alerting.send_alert", new=AsyncMock()),
        ]

    # --- 失败：failed=1 → 条目保留、水位不推进、安排重试 ---
    with ExitStack() as stack:
        for p in _common_patches():
            stack.enter_context(p)
        mock_s = stack.enter_context(patch(f"{_SUB}.settings"))
        stack.enter_context(
            patch(
                "app.services.data_import.import_history_data",
                new=AsyncMock(
                    return_value={"total": 1, "succeeded": 0, "failed": 1, "errors": ["504"]}
                ),
            )
        )
        _gap_settings(mock_s)
        entry = await _register_entry()
        await sub._run_gap_backfill(gap_start, gap_end, loop_parts=["LOOPB"], pending_entry=entry)
        failure_pending = await sub._load_pending_gaps()
        await _cancel_retry(sub)

    assert sub._last_flushed_at is None, "per-loop 失败不推进全局落库点"
    assert len(failure_pending) == 1, "失败条目保留（重启仍可见）"
    assert "LOOPB" not in sub._loop_watermarks

    # --- 成功但空窗口（远端确无数据，failed==0）→ 水位推进 + 空窗口计数 + 出队 ---
    with ExitStack() as stack:
        for p in _common_patches():
            stack.enter_context(p)
        mock_s = stack.enter_context(patch(f"{_SUB}.settings"))
        stack.enter_context(
            patch(
                "app.services.data_import.import_history_data",
                new=AsyncMock(
                    return_value={
                        "total": 1,
                        "succeeded": 1,
                        "failed": 0,
                        "errors": [],
                        "loopCoverage": [
                            {"loopId": "loop-1", "importedPoints": 0, "coverage": 0.0}
                        ],
                    }
                ),
            )
        )
        _gap_settings(mock_s)
        entry = await _register_entry()
        await sub._run_gap_backfill(gap_start, gap_end, loop_parts=["LOOPB"], pending_entry=entry)
        success_pending = await sub._load_pending_gaps()

    assert sub._loop_watermarks.get("LOOPB") == pytest.approx(gap_end, abs=0.01), (
        "空窗口按 failed==0 推进（口径已登记）"
    )
    assert sub._metrics["backfill_empty_windows"] == 1, "空返回≠完整，计数供观测"
    assert success_pending == [], "成功条目出队"
    assert sub._last_flushed_at is None, "per-loop 成功不推进全局落库点（其他回路口径不变）"
    # 水位已持久化（重启后不再重复检测该窗口）
    assert fake._hashes.get(_GAP_LOOP_WM_KEY, {}).get("LOOPB") == f"{gap_end:.3f}"


async def test_r08_watermark_advance_only_on_persisted_success():
    """flush 部分失败（TD 失败）回路不推水位；窗口重试确认后才推进（行 ts 口径）."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = 900.0
    ts = "2026-09-06 10:00:00"
    sub._buffer = {
        "LOOPA": {
            "PV": {
                "value": "1.0",
                "quality": 1,
                "ts": ts,
                "recvAt": 1000.0,
                "tag": "A.PV",
                "epoch": 0,
            }
        },
        "LOOPB": {
            "PV": {
                "value": "2.0",
                "quality": 1,
                "ts": ts,
                "recvAt": 1000.0,
                "tag": "B.PV",
                "epoch": 0,
            }
        },
    }
    ts_epoch = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ8).timestamp()

    calls = iter([Exception("TD down")] * 3 + [1, 1])  # A/B 首批失败；重试成功
    insert = AsyncMock(side_effect=lambda tables: next(calls))

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
        patch(f"{_SUB}.batch_insert_multi", new=insert),
        patch.object(sub, "_get_loop_meta_map", new=AsyncMock(return_value={})),
    ):
        mock_s.REALTIME_WRITEBACK_ENABLED = True
        await sub._flush_buffer()
        assert sub._loop_watermarks == {}, "失败批不推 per-loop 水位"

        await sub._flush_buffer()  # 空批：仅重试未确认窗口
        assert sub._loop_watermarks == {
            "LOOPA": pytest.approx(ts_epoch, abs=0.01),
            "LOOPB": pytest.approx(ts_epoch, abs=0.01),
        }, "窗口确认成功后按行 ts 推进"


# ---------------------------------------------------------------------------
# R02：历史缓存三重限制（每回路上限 / 写入前去重 / 全局字节预算）
# ---------------------------------------------------------------------------


async def test_r02_per_loop_cap_and_duplicate_ts_dedup():
    """高速率 + 重复 ts 写入不越每回路上限；重复 ts 行跳过并计数."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    key = f"{_HISTORY_KEY_PREFIX}LIC"

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _r02_settings(mock_s, REALTIME_HISTORY_MAX_POINTS_PER_LOOP=5)
        # 高速率：8 个不同 ts 的行（每拍一次 flush 等价）
        for i in range(8):
            ts = f"2026-09-06 10:{i:02d}:00.000"
            await sub._push_history_entries([_hist_entry("LIC", ts, pv=float(i))])
        # 重复 ts：最新行 ts 再推一次（R05 修复后的兜底路径）
        await sub._push_history_entries([_hist_entry("LIC", "2026-09-06 10:07:00.000", pv=99.0)])
        await sub._push_history_entries([_hist_entry("LIC", "2026-09-06 10:03:00.000", pv=77.0)])

    lst = fake._lists[key]
    assert len(lst) == 5, "每回路上限 5 生效（LTRIM 收敛）"
    assert sub._metrics["history_dup_dropped"] == 2, "同 ts/旧 ts 行跳过计数"
    newest = json.loads(lst[0])
    assert newest["pv"] == 7.0, "同 ts 重复行不覆盖（后写同 ts 被去重兜底拦下）"
    oldest = json.loads(lst[-1])
    assert oldest["ts"] == "2026-09-06 10:03:00.000", "上限裁掉最老行（10:00~10:02）"


async def test_r02_global_budget_blocks_new_loop_keys_only():
    """触达全局预算后：新回路不再建 history 键并计数；已活跃键与 TD 写回不受影响."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    row_len = len(_hist_entry("X", "2026-09-06 10:00:00.000")[1])

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _r02_settings(
            mock_s,
            REALTIME_WRITEBACK_ENABLED=True,
            REALTIME_HISTORY_GLOBAL_BUDGET_BYTES=row_len,  # 仅容一个键的预算
        )
        # 活跃键 X 正常建立
        await sub._push_history_entries([_hist_entry("X", "2026-09-06 10:00:00.000")])
        assert f"{_HISTORY_KEY_PREFIX}X" in fake._lists

        # 新回路 Y：超预算 → 不建键 + 计数；TD 写回不受影响（经 flush 验证）
        sub._buffer = {
            "Y": {
                "PV": {
                    "value": "2.0",
                    "quality": 1,
                    "ts": "2026-09-06 10:00:01",
                    "recvAt": 1000.0,
                    "tag": "Y.PV",
                    "epoch": 0,
                }
            },
        }
        insert = AsyncMock(return_value=1)
        with (
            patch(f"{_SUB}.batch_insert_multi", new=insert),
            patch.object(sub, "_get_loop_meta_map", new=AsyncMock(return_value={})),
        ):
            await sub._flush_buffer()

        assert f"{_HISTORY_KEY_PREFIX}Y" not in fake._lists, "超预算的新回路不得建历史键"
        assert sub._metrics["history_budget_exceeded"] == 1
        insert.assert_awaited_once(), "TD 写回不受历史缓存预算影响"
        assert sub._metrics["rows_written"] == 1

        # 已活跃键 X 继续正常写入（预算门只挡新键）
        await sub._push_history_entries([_hist_entry("X", "2026-09-06 10:00:02.000")])
        assert len(fake._lists[f"{_HISTORY_KEY_PREFIX}X"]) == 2
        # 最新值显示缓存（realtime:{tag}）不受影响：经 _cache_value + 显示批量发送验证
        sub2 = RealtimeSubscriber()
        sub2._refresh_loop_meta_cache = AsyncMock()
        await sub2._cache_value(
            {"tagCode": "Y.PV", "value": "2.0", "quality": 1, "collectTime": "2026-09-06 10:00:01"}
        )
        await sub2._flush_display_pending()
        assert f"{_REDIS_KEY_PREFIX}Y.PV" in fake._data, "最新值缓存不受预算影响"


async def test_r02_ttl_expiry_sweep_keeps_tracking_bounded():
    """低速率长期写入：TTL 过期后跟踪状态收敛（键回到"尚无"状态，预算重新适用）."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _r02_settings(mock_s)
        entry1 = _hist_entry("LIC", "2026-09-06 10:00:00.000")
        await sub._push_history_entries([entry1])
        assert sub._history_bytes_total == len(entry1[1])
        assert "LIC" in sub._history_key_bytes

        # 模拟整键 TTL 过期（内存过期模型）：置过期时刻为过去，越过清扫节流
        sub._history_key_expire_at["LIC"] = time.time() - 1.0
        sub._last_history_sweep = 0.0

        entry2 = _hist_entry("LIC", "2026-09-06 11:00:00.000", pv=2.0)
        await sub._push_history_entries([entry2])

    assert "LIC" in sub._history_key_bytes, "过期后新写入重建跟踪"
    assert sub._history_bytes_total == len(entry2[1]), "过期键字节已扣减（近似有界）"
    assert sub._history_key_rows["LIC"] == 1
    # TTL+上限联合保证有界：单键 ≤ 上限行数，全局 ≤ 预算（近似模型）
    assert sub._history_bytes_total <= 64 * 1024 * 1024
