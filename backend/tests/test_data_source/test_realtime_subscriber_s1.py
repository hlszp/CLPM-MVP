"""数据链路整改 S1 行为测试（R03/R04/R06/R07/R09/R10/R11）.

对齐审查报告 §7 测试盲区：持续 PV/仅 Pong/空推送、写 A 期间注入 B、
四 worker Redis 断网接管、握手/首响应永不返回、改绑竞态、NaN/Infinity 批隔离。
全部测试调用真实实现（仅 fake Redis/WS/DB），不在测试体复制实现逻辑。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.data_source.realtime_subscriber as rts_mod
from app.services.data_source.realtime_subscriber import (
    _REDIS_KEY_PREFIX,
    _SUBSCRIBER_LEADER_LOCK_KEY,
    RealtimeSubscriber,
    _ShardState,
)
from tests.test_data_source.test_realtime_subscriber import _FakeRedis, _leader_settings

_SUB = "app.services.data_source.realtime_subscriber"


# ---------------------------------------------------------------------------
# 共享 fake / helper
# ---------------------------------------------------------------------------


class _FlakyRedis(_FakeRedis):
    """支持故障注入的 FakeRedis：setex 按 key 失败；set/eval 可整体失败."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_all = False
        self.fail_keys: set[str] = set()

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self.fail_all or key in self.fail_keys:
            raise ConnectionError("redis down (setex)")
        await super().setex(key, ttl, value)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if self.fail_all:
            raise ConnectionError("redis down (set)")
        return await super().set(key, value, nx=nx, ex=ex)

    async def eval(self, script: str, numkeys: int, *args: str) -> Any:
        if self.fail_all:
            raise ConnectionError("redis down (eval)")
        return await super().eval(script, numkeys, *args)


class _RecordingRedis(_FakeRedis):
    """记录创建过的 pipeline（供断言 flush 写入的历史行 JSON）."""

    def __init__(self) -> None:
        super().__init__()
        self.pipelines: list[Any] = []

    def pipeline(self):
        pipe = super().pipeline()
        self.pipelines.append(pipe)
        return pipe


def _entry(value: Any, ts: str, tag: str, recv_at: float, quality: int = 1) -> dict:
    """构造 S0 契约 §4.1 口径的缓冲条目（含 recvAt/tag/epoch）."""
    return {
        "value": value,
        "quality": quality,
        "ts": ts,
        "recvAt": recv_at,
        "tag": tag,
        "epoch": 0,
    }


def _make_ws(recv=None, sent: list[str] | None = None):
    """构造 mock WebSocket：send 记录到 sent，recv 用可调用/预置序列."""
    ws = MagicMock()
    ws.send = AsyncMock(side_effect=lambda m: sent.append(m) if sent is not None else None)
    if recv is not None:
        ws.recv = recv
    ws.close = AsyncMock()
    return ws


def _shard_settings(mock_s, **overrides) -> None:
    """补齐分片连接路径所需 settings（可按测试覆盖）."""
    mock_s.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
    mock_s.SIGNALR_STALL_TIMEOUT_SECONDS = 300
    mock_s.SIGNALR_RESUBSCRIBE_INTERVAL = 1800
    mock_s.SIGNALR_OPEN_TIMEOUT = 15
    mock_s.SIGNALR_PING_INTERVAL = 0
    mock_s.SIGNALR_PING_TIMEOUT = 60
    for k, v in overrides.items():
        setattr(mock_s, k, v)


class _NegotiateUnavailable:
    """negotiate HTTP 客户端桩：构造即抛连接错误（走回退裸连路径，零等待）."""

    def __init__(self, **_kw):
        raise ConnectionError("negotiate unreachable (test stub)")


async def _idle() -> None:
    await asyncio.sleep(100)


def _stub_all_tasks(sub: RealtimeSubscriber) -> None:
    """把 Leader 启动的全部后台任务替换为空转协程（避免真实连接）."""
    sub._run = _idle
    sub._flush_loop = _idle
    sub._refresh_loop = _idle
    sub._control_loop = _idle
    sub._display_flush_loop = _idle


def _mock_meta_db(loops: list[tuple], mappings: list[tuple]) -> AsyncMock:
    """构造 _refresh_loop_meta_cache 的 mock AsyncSessionLocal 上下文.

    loops: [(loop_id, loop_tag_name, unit_id)]；mappings: [(loop_id, role, tag_name)]
    """
    loops_result = MagicMock()
    loops_result.all.return_value = loops
    mapping_result = MagicMock()
    mapping_result.all.return_value = mappings
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[loops_result, mapping_result])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


# ---------------------------------------------------------------------------
# R03：Redis 故障不阻断采集缓冲 + 显示快照批量发送
# ---------------------------------------------------------------------------


async def test_r03_redis_failure_does_not_block_history_buffer():
    """SETEX/PUBLISH 全失败：数据仍入历史缓冲，_cache_value 不抛异常，失败计数."""
    fake = _FlakyRedis()
    fake.fail_all = True
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()

    with patch(f"{_SUB}.redis_client", fake):
        accepted = await sub._cache_value(
            {
                "tagCode": "LIC-101.PV",
                "value": "50.5",
                "quality": 1,
                "collectTime": "2026-09-06T02:00:00Z",
            }
        )
        assert accepted is True
        # 历史缓冲与显示待发均已就绪（不依赖 Redis）
        assert "LIC-101" in sub._buffer
        assert "LIC-101.PV" in sub._display_pending
        # 批量发送失败被隔离计数，不抛出、不阻断
        await sub._flush_display_pending()
        assert sub._metrics["cache_write_failed"] >= 1
        assert sub._buffer != {}  # 历史缓冲不受影响


async def test_r03_display_pending_bounded_latest_per_tag():
    """待发字典按 tag 合并最新值（有界 ≤ 活跃 tag 数，无界任务为零）."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()

    with patch(f"{_SUB}.redis_client", fake):
        for i in range(100):
            await sub._cache_value({"tagCode": "T1", "value": str(i), "collectTime": "t"})

    assert len(sub._display_pending) == 1
    payload = json.loads(sub._display_pending["T1"][1])
    assert payload["value"] == "99"  # 仅保留最新值
    assert sub._bg_tasks == set()  # 非 MODE 消息不创建后台任务


async def test_r03_display_flush_per_item_isolation_and_recovery():
    """pipeline 失败时逐项隔离：健康项送达、失败项计数并回并，恢复后收敛."""
    fake = _FlakyRedis()
    fake.fail_keys = {f"{_REDIS_KEY_PREFIX}BAD"}
    sub = RealtimeSubscriber()

    def _payload(tag: str) -> tuple[str, str]:
        return f"{_REDIS_KEY_PREFIX}{tag}", json.dumps({"tagCode": tag})

    sub._display_pending = {
        "GOOD1": _payload("GOOD1"),
        "GOOD2": _payload("GOOD2"),
        "BAD": _payload("BAD"),
    }
    with patch(f"{_SUB}.redis_client", fake):
        await sub._flush_display_pending()

    assert f"{_REDIS_KEY_PREFIX}GOOD1" in fake._data
    assert f"{_REDIS_KEY_PREFIX}GOOD2" in fake._data
    assert f"{_REDIS_KEY_PREFIX}BAD" not in fake._data
    assert sub._metrics["cache_write_failed"] == 1
    assert set(sub._display_pending) == {"BAD"}  # 失败项回并（待恢复收敛）

    # 故障恢复后收敛（60s 故障窗口内待发字典有界，恢复后由重试送达）
    fake.fail_keys.clear()
    with patch(f"{_SUB}.redis_client", fake):
        await sub._flush_display_pending()
    assert f"{_REDIS_KEY_PREFIX}BAD" in fake._data
    assert sub._display_pending == {}
    assert sub._metrics["cache_write_failed"] == 1  # 恢复后不再累计


async def test_r03_item_loop_single_failure_does_not_interrupt_batch():
    """Completion/推送的 item 循环逐项隔离：第 2 项抛错，第 1/3 项仍处理."""
    sub = RealtimeSubscriber()
    seen: list[str] = []

    async def _flaky(item):
        seen.append(item["tagCode"])
        if item["tagCode"] == "T2":
            raise RuntimeError("boom")
        return True

    with patch.object(sub, "_cache_value", new=AsyncMock(side_effect=_flaky)):
        accepted = await sub._handle_signalr_message(
            {
                "target": "updateRealValues",
                "data": [{"tagCode": "T1"}, {"tagCode": "T2"}, {"tagCode": "T3"}],
            }
        )

    assert seen == ["T1", "T2", "T3"], "单项失败不得中断后续项"
    assert accepted == 2


# ---------------------------------------------------------------------------
# R04：Leader 锁三态语义（断网不 fail-open、租约到期退位、唯一接管）
# ---------------------------------------------------------------------------


async def test_r04_renew_exception_keeps_leadership_until_lease_expiry():
    """续租异常：租约未过期保持现状；过期仍无法确认 → 退位并登记窗口."""
    fake = _FlakyRedis()
    fake._data[_SUBSCRIBER_LEADER_LOCK_KEY] = "tok"
    sub = RealtimeSubscriber()
    sub._leader_token = "tok"
    sub._running = True
    _stub_all_tasks(sub)

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _leader_settings(mock_s)  # TTL=1s
        await sub._become_leader()
        assert sub._is_leader is True
        assert sub._lease_expires_at is not None and sub._lease_expires_at > time.time()

        # Redis 断网：续租异常 → 租约未过期，保持 Leader
        fake.fail_all = True
        await sub._maintain_leadership()
        assert sub._is_leader is True

        # 租约到期仍无法确认 → 退位停止接收/写回 + 登记控制面故障窗口
        sub._lease_expires_at = time.time() - 0.1
        await sub._maintain_leadership()
        assert sub._is_leader is False
        assert sub._metrics["lease_lost_windows"] == 1
        assert sub._task is None and sub._flush_task is None
        assert sub._display_flush_task is None

        # 恢复且锁 TTL 过期（模拟删除）→ 本进程重新接管，epoch 递增
        fake.fail_all = False
        del fake._data[_SUBSCRIBER_LEADER_LOCK_KEY]
        await sub._maintain_leadership()
        assert sub._is_leader is True
        assert sub._leader_epoch == 2
        assert sub._metrics["leader_epoch"] == 2
        await sub._resign_leader()


async def test_r04_four_worker_outage_single_leader_and_unique_takeover():
    """四 worker 同时 Redis 断网：无新增 Leader、原 Leader 到期退位、恢复后唯一接管."""
    fake = _FlakyRedis()
    workers: list[RealtimeSubscriber] = []
    for i in range(4):
        w = RealtimeSubscriber()
        w._leader_token = f"host{i}:1:1"
        w._running = True
        _stub_all_tasks(w)
        workers.append(w)

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _leader_settings(mock_s)
        # 正常启动：恰一个 Leader
        for w in workers:
            if await w._acquire_leader_lock():
                await w._become_leader()
        assert sum(w._is_leader for w in workers) == 1
        leader = next(w for w in workers if w._is_leader)

        # Redis 断网：待命者抢锁异常 → 全部保持待命（不再 fail-open）
        fake.fail_all = True
        for w in workers:
            await w._maintain_leadership()
        assert sum(w._is_leader for w in workers) == 1
        assert leader._is_leader is True  # 租约未过期，保持现状

        # 原 Leader 租约到期仍无法续租 → 退位登记窗口；断网期间无人采集
        leader._lease_expires_at = time.time() - 1
        await leader._maintain_leadership()
        assert leader._is_leader is False
        assert leader._metrics["lease_lost_windows"] == 1
        assert sum(w._is_leader for w in workers) == 0

        # 恢复 + 锁 TTL 过期：恰一个接管，锁内 token 与之一致
        fake.fail_all = False
        fake._data.pop(_SUBSCRIBER_LEADER_LOCK_KEY, None)
        for w in workers:
            await w._maintain_leadership()
        leaders = [w for w in workers if w._is_leader]
        assert len(leaders) == 1
        assert fake._data[_SUBSCRIBER_LEADER_LOCK_KEY] == leaders[0]._leader_token

        for w in leaders:
            await w._resign_leader()


# ---------------------------------------------------------------------------
# R06：非有限值与质量处理（共享数值契约 + 批隔离 + SQL 出口守卫）
# ---------------------------------------------------------------------------


def test_r06_build_row_invalid_values_to_none():
    """NaN/Infinity/1e999/空串 → None；科学计数法照常解析；MODE 溢出不再炸批."""
    sub = RealtimeSubscriber()
    ts = "2026-07-15T10:00:00Z"
    row = sub._build_row(
        {
            "PV": {"value": "nan", "quality": 1, "ts": ts},
            "SP": {"value": "Infinity", "quality": 1, "ts": ts},
            "OP": {"value": "", "quality": 1, "ts": ts},
            "MODE": {"value": "Infinity", "quality": 1, "ts": ts},
            "PID_P": {"value": "1.5E3", "quality": 1, "ts": ts},
            "PID_I": {"value": "1e999", "quality": 1, "ts": ts},
            "PID_D": {"value": "-1.#QNAN0", "quality": 1, "ts": ts},
        }
    )
    assert row[1] is None  # PV nan
    assert row[2] is None  # SP Infinity
    assert row[3] is None  # OP 空串
    assert row[4] is None  # MODE Infinity（原实现 int(float("Infinity")) OverflowError）
    assert row[5] == 1500.0  # PID_P 1.5E3 合法科学计数法
    assert row[6] is None  # 1e999 溢出
    assert row[7] is None  # 工业异常字面量
    # 质量列同样经 parse_mode_int（无效 → None，不抛异常）
    row2 = sub._build_row({"PV": {"value": "1.0", "quality": "Infinity", "ts": ts}})
    assert row2[8] is None


async def test_r06_invalid_value_keeps_quality_and_collect_time():
    """无效值不丢消息：value 置 None、quality/collectTime 照常记录并计数."""
    fake = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()

    with patch(f"{_SUB}.redis_client", fake):
        accepted = await sub._cache_value(
            {
                "tagCode": "LIC-101.PV",
                "value": "-1.#QNAN0",
                "quality": 0,
                "collectTime": "2026-07-15T10:00:00Z",
            }
        )
        assert accepted is True

    entry = sub._buffer["LIC-101"]["PV"]
    assert entry["value"] is None
    assert entry["quality"] == 0
    assert entry["ts"] == "2026-07-15T10:00:00Z"
    assert sub._metrics["points_invalid"] == 1
    # 显示载荷：原样字面量 + valueValid=False（消费侧显式显示无效）
    payload = json.loads(sub._display_pending["LIC-101.PV"][1])
    assert payload["value"] == "-1.#QNAN0"
    assert payload["valueValid"] is False
    assert payload["quality"] == 0


async def test_r06_flush_batch_with_bad_loop_isolated():
    """含 MODE=Infinity 回路的批次：健康回路照常写出，坏回路该列 NULL 不炸批.

    宽表退役（2026-09-25）后落库形态是测点点表，本用例改为验证：
    ① 批次不被坏值炸掉（Redis 历史两回路都写出）；② MODE=Infinity → NULL 不折算 0。
    """
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    ts = "2026-07-15T10:00:00Z"
    sub._buffer = {
        "HEALTHY": {"PV": _entry("50.5", ts, "H.PV", 1000.0)},
        "BAD": {
            "PV": _entry("12.3", ts, "B.PV", 1000.0),
            "MODE": _entry("Infinity", ts, "B.MODE", 1000.0),
        },
    }

    with patch(f"{_SUB}.redis_client", fake):
        await sub._flush_buffer()

    assert fake.pipelines and fake.pipelines[0]._ops, "健康与坏回路的历史缓存行均应写入"
    assert sub._metrics["rows_dropped_no_ts"] == 0, "坏值不得导致整批丢弃"

    # MODE=Infinity → NULL（不折算 0），PV 照常解析
    bad_row = sub._build_row(
        {
            "PV": _entry("12.3", ts, "B.PV", 1000.0),
            "MODE": _entry("Infinity", ts, "B.MODE", 1000.0),
        }
    )
    assert bad_row is not None
    assert bad_row[4] is None
    assert bad_row[1] == 12.3


# ---------------------------------------------------------------------------
# R07：flush 原子截取 / 边界推进 / 分块 / 失败窗口重试缓冲
# ---------------------------------------------------------------------------


async def test_r07_flush_during_await_new_data_goes_to_next_batch():
    """写 A 期间注入 B：A 成功只确认 A 的边界；B 进入下一批，checkpoint 不越过 B."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = 900.0
    sub._buffer = {"A": {"PV": _entry("50.5", "2026-07-15T10:00:00Z", "A.PV", recv_at=1000.0)}}

    pushed: list[list] = []

    async def _push_and_inject(entries):
        # 模拟 await 落缓存期间并发接收 B（recvAt=1001 晚于 A）
        pushed.append(entries)
        sub._buffer["B"] = {"MODE": _entry("1", "2026-07-15T10:00:01Z", "B.MODE", recv_at=1001.0)}

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch.object(sub, "_push_history_entries", side_effect=_push_and_inject),
    ):
        await sub._flush_buffer()

    # A 的批只含 A 一行（原子截取，B 不得混入本批）
    assert len(pushed) == 1
    rows = [json.loads(e[1]) for e in pushed[0]]
    assert len(rows) == 1
    assert rows[0]["mode"] is None
    # checkpoint 推进到 A 的 batch_boundary（max recvAt=1000），不含 B
    assert sub._last_flushed_at == 1000.0
    # B 留在下一批缓冲
    assert "B" in sub._buffer


async def test_r07_failed_batch_window_survives_later_success():
    """A 失败 → B 成功不得抹去 A；A 重试成功后窗口清除、水位推进."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = 900.0
    ts = "2026-07-15T10:00:00Z"
    sub._buffer = {"A": {"PV": _entry("5.0", ts, "A.PV", recv_at=1000.0)}}

    calls = iter([Exception("redis down"), Exception("redis down"), None, None])

    async def _push(entries):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch.object(sub, "_push_history_entries", side_effect=_push),
    ):
        # flush1：A 批落缓存失败 → 登记未确认窗口，checkpoint 不推进
        await sub._flush_buffer()
        assert len(sub._unconfirmed_windows) == 1
        assert sub._metrics["unconfirmed_windows"] == 1
        assert sub._last_flushed_at == 900.0

        # flush2：A 窗口重试仍失败 + 新批 B 成功 —— B 的成功不得确认/抹去 A
        sub._buffer = {"B": {"PV": _entry("6.0", ts, "B.PV", recv_at=1001.0)}}
        await sub._flush_buffer()
        assert len(sub._unconfirmed_windows) == 1, "后续批成功不得擦掉旧失败窗口"
        assert sub._last_flushed_at == 900.0, "checkpoint 不得越过未确认的 A"

        # flush3：A 窗口重试成功 → 窗口清除，水位推进到已确认边界（B=1001）
        await sub._flush_buffer()
        assert sub._unconfirmed_windows == []
        assert sub._last_flushed_at == 1001.0


async def test_r07_single_row_build_failure_isolated():
    """单行构造失败（_build_row 抛错）不影响其他行."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    ts = "2026-07-15T10:00:00Z"
    sub._buffer = {
        "GOOD": {"PV": _entry("1.0", ts, "G.PV", 1000.0)},
        "BAD": {"PV": _entry("2.0", ts, "B.PV", 1000.0)},
    }
    real_build = sub._build_row

    def _build(roles):
        if roles.get("PV", {}).get("tag") == "B.PV":
            raise ValueError("bad row")
        return real_build(roles)

    pushed: list[list] = []

    async def _capture(entries):
        pushed.append(entries)

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch.object(sub, "_push_history_entries", side_effect=_capture),
        patch.object(sub, "_build_row", side_effect=_build),
    ):
        await sub._flush_buffer()

    assert pushed, "GOOD 回路的历史缓存行应写出"
    rows = [json.loads(entry[1]) for batch in pushed for entry in batch]
    assert len(rows) == 1, "BAD 行被隔离，仅 GOOD 落缓存"
    # 行 ts 经归一化到目标时区（Asia/Shanghai，UTC+8）
    assert rows[0]["ts"] == "2026-07-15 18:00:00.000"


# ---------------------------------------------------------------------------
# R09：deadline 驱动维护 + 片级接收点只由本片数据推进
# ---------------------------------------------------------------------------


async def test_r09_resubscribe_executes_under_continuous_traffic():
    """持续 PV 流量（recv 永不超时）下，保鲜订阅仍到点执行（不再被饿死）."""
    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])
    sent: list[str] = []

    async def _recv():
        await asyncio.sleep(0.02)
        return (
            '{"target": "updateRealValues", '
            '"data": [{"tagCode": "LIC-101.PV", "value": "5", "quality": 1}]}\x1e'
        )

    ws = _make_ws(recv=_recv, sent=sent)

    with (
        patch(f"{_SUB}.websockets.connect", new=AsyncMock(return_value=ws)),
        patch(f"{_SUB}.httpx.AsyncClient", new=_NegotiateUnavailable),
        patch(f"{_SUB}.settings") as mock_s,
        patch.object(sub, "_cache_value", new=AsyncMock(return_value=True)),
        patch.object(sub, "_maybe_trigger_gap_backfill", new=AsyncMock()),
    ):
        _shard_settings(mock_s, SIGNALR_RESUBSCRIBE_INTERVAL=0.2)
        task = asyncio.create_task(sub._connect_and_subscribe(state))
        await asyncio.sleep(0.55)
        sub._running = False
        await asyncio.wait_for(task, timeout=5)

    refresh_sends = [s for s in sent if "refresh_" in s]
    assert refresh_sends, "持续流量下保鲜订阅必须到点执行"
    assert state.last_data_at is not None  # 本片接纳了数据


async def test_r09_pong_only_not_business_data_and_keeps_alive():
    """仅 Pong 的分片：连接保持（不误杀），但片级接收点不推进（不算业务数据）."""
    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])

    async def _recv():
        await asyncio.sleep(0.02)
        return '{"type": 6}\x1e'

    ws = _make_ws(recv=_recv)

    with (
        patch(f"{_SUB}.websockets.connect", new=AsyncMock(return_value=ws)),
        patch(f"{_SUB}.settings") as mock_s,
        patch.object(sub, "_maybe_trigger_gap_backfill", new=AsyncMock()),
    ):
        _shard_settings(mock_s)
        task = asyncio.create_task(sub._connect_and_subscribe(state))
        await asyncio.sleep(0.4)
        sub._running = False
        await asyncio.wait_for(task, timeout=5)

    assert state.last_data_at is None, "Pong 不算业务数据"
    assert state.ws is not None, "Pong 应答正常的连接不应被看门狗误杀"
    ws.close.assert_not_called()


async def test_r09_empty_push_and_other_shard_data_do_not_advance_shard_time():
    """空推送不推进片级接收点；也不借用其他片（全局接收点）的时间."""
    sub = RealtimeSubscriber()
    st = _ShardState(index=1, total=2, tags=[])
    sub._last_data_at = time.time()  # 其他分片刚刚收到数据

    await sub._process_shard_message(st, {"target": "updateRealValues", "data": []})
    assert st.last_data_at is None

    await sub._process_shard_message(st, {"type": 3, "result": {"code": 200, "data": []}})
    assert st.last_data_at is None

    await sub._process_shard_message(st, {"type": 6})  # Pong
    assert st.last_data_at is None

    # 本片真正接纳数据后才推进
    with patch.object(sub, "_cache_value", new=AsyncMock(return_value=True)):
        await sub._process_shard_message(
            st,
            {"target": "updateRealValues", "data": [{"tagCode": "T1", "value": "1"}]},
        )
    assert st.last_data_at is not None


async def test_r09_cancel_during_recv_leaves_no_task_residue():
    """接收等待中取消分片任务：干净退出，无任务/计时器残留."""
    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])

    async def _recv():
        await asyncio.sleep(30)

    ws = _make_ws(recv=_recv)

    with (
        patch(f"{_SUB}.websockets.connect", new=AsyncMock(return_value=ws)),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _shard_settings(mock_s, SIGNALR_OPEN_TIMEOUT=5)
        task = asyncio.create_task(sub._connect_and_subscribe(state))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    residue = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert residue == [], f"取消后不得残留任务: {residue}"


# ---------------------------------------------------------------------------
# R10：握手/首响应超时 + 同帧多消息分发
# ---------------------------------------------------------------------------


async def test_r10_handshake_timeout_goes_through_shard_backoff_reconnect():
    """握手永不响应：限时退出并走既有片级退避重连（非永久等待）."""
    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])

    async def _recv():
        await asyncio.sleep(30)  # 永不回握手

    ws = _make_ws(recv=_recv)

    with (
        patch(f"{_SUB}.websockets.connect", new=AsyncMock(return_value=ws)),
        patch(f"{_SUB}.httpx.AsyncClient", new=_NegotiateUnavailable),
        patch(f"{_SUB}.settings") as mock_s,
    ):
        _shard_settings(
            mock_s,
            SIGNALR_OPEN_TIMEOUT=0.15,
            SIGNALR_RECONNECT_INTERVAL=0.05,
            SIGNALR_RECONNECT_MAX_INTERVAL=0.1,
        )
        task = asyncio.create_task(sub._shard_loop(state))
        await asyncio.sleep(0.5)  # 至少两次建连尝试
        sub._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert ws.send.call_count >= 2, "超时后应经退避重连重发握手"
    assert ws.close.call_count >= 1, "失败后应关闭旧连接"


async def test_r10_first_response_timeout_raises():
    """握手成功但首响应（快照）永不返回：在 _FIRST_RESPONSE_TIMEOUT 内退出."""
    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])
    sent: list[str] = []
    calls = 0

    async def _recv():
        nonlocal calls
        calls += 1
        if calls == 1:
            return "{}\x1e"  # 握手成功
        await asyncio.sleep(30)  # 首响应永不返回

    ws = _make_ws(recv=_recv, sent=sent)

    with (
        patch(f"{_SUB}.websockets.connect", new=AsyncMock(return_value=ws)),
        patch(f"{_SUB}.settings") as mock_s,
        patch(f"{_SUB}.redis_client", new=_FakeRedis()),
        patch.object(rts_mod, "_FIRST_RESPONSE_TIMEOUT", 0.2),
    ):
        _shard_settings(mock_s)
        with pytest.raises(TimeoutError):
            await sub._connect_and_subscribe(state)

    assert any("SubscribeAsync" in s for s in sent), "订阅 invocation 已发出"


async def test_r10_same_frame_handshake_pong_and_completion_push():
    """同帧多消息：握手+Pong、Completion+Pong 均正确分发处理."""
    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])
    calls = 0

    async def _recv():
        nonlocal calls
        calls += 1
        if calls == 1:
            # 握手 + Pong 同帧
            return '{}\x1e{"type": 6}\x1e'
        if calls == 2:
            # 初始响应：Completion（带数据）+ Pong 同帧
            return (
                '{"type": 3, "result": {"code": 200, "data": '
                '[{"tagCode": "LIC-101.PV", "value": "5", "quality": 1, '
                '"collectTime": "2026-07-15T10:00:00Z"}]}}\x1e{"type": 6}\x1e'
            )
        await asyncio.sleep(30)

    ws = _make_ws(recv=_recv)

    with (
        patch(f"{_SUB}.websockets.connect", new=AsyncMock(return_value=ws)),
        patch(f"{_SUB}.httpx.AsyncClient", new=_NegotiateUnavailable),
        patch(f"{_SUB}.settings") as mock_s,
        patch.object(sub, "_cache_value", new=AsyncMock(return_value=True)) as mock_cache,
        patch.object(sub, "_maybe_trigger_gap_backfill", new=AsyncMock()),
    ):
        _shard_settings(mock_s, SIGNALR_OPEN_TIMEOUT=1)
        task = asyncio.create_task(sub._connect_and_subscribe(state))
        await asyncio.sleep(0.3)
        sub._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    mock_cache.assert_awaited_once()
    assert mock_cache.await_args.args[0]["tagCode"] == "LIC-101.PV"
    assert state.last_data_at is not None


# ---------------------------------------------------------------------------
# R11：绑定代次（改绑/解绑/删除后旧来源不再入库）
# ---------------------------------------------------------------------------


async def test_r11_rebind_clears_old_source_and_new_role_is_null():
    """OLD_SP 改绑尚无值的 NEW_SP：旧条目清除、旧来源消息丢弃计数、后续行 SP=NULL."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    ts = "2026-07-15T10:00:00Z"

    v1_loops = [("loop-1", "41LIC30044", "unit-1")]
    v1_map = [("loop-1", "PV", "41LIC30044_PV"), ("loop-1", "SP", "OLD_SP")]
    v2_map = [("loop-1", "PV", "41LIC30044_PV"), ("loop-1", "SP", "NEW_SP")]

    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.AsyncSessionLocal", return_value=_mock_meta_db(v1_loops, v1_map)),
    ):
        await sub._refresh_loop_meta_cache()
        assert sub._loop_role_tags["41LIC30044"]["SP"] == "OLD_SP"
        assert sub._loop_epochs == {"41LIC30044": 1}

        # OLD_SP 有值入库（v1 绑定下合法）
        await sub._cache_value(
            {"tagCode": "OLD_SP", "value": "6.0", "quality": 1, "collectTime": ts}
        )
        await sub._cache_value(
            {"tagCode": "41LIC30044_PV", "value": "5.0", "quality": 1, "collectTime": ts}
        )
        assert sub._last_known["41LIC30044"]["SP"]["tag"] == "OLD_SP"

    # 改绑：SP → NEW_SP（尚无值）
    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.AsyncSessionLocal", return_value=_mock_meta_db(v1_loops, v2_map)),
    ):
        await sub._refresh_loop_meta_cache()
        assert sub._loop_epochs["41LIC30044"] == 2, "绑定变化 epoch+1"
        assert "SP" not in sub._last_known["41LIC30044"], "旧来源 last_known 必须清除"
        assert "SP" not in sub._buffer["41LIC30044"], "旧来源 buffer 必须清除"

        # 旧代次在途消息：丢弃计数，不入历史缓冲（显示缓存不受影响）
        await sub._cache_value(
            {"tagCode": "OLD_SP", "value": "7.0", "quality": 1, "collectTime": ts}
        )
        assert sub._metrics["unbound_tag_msgs"] == 1
        assert "SP" not in sub._buffer["41LIC30044"]

        # flush：新绑定无值 → SP 列为 NULL（不写 0、不沿用旧值 6.0）
        with patch(f"{_SUB}.settings") as mock_s:
            mock_s.REALTIME_WRITEBACK_ENABLED = False
            await sub._flush_buffer()

    history_ops = fake.pipelines[-1]._ops
    row_json = next(v for op, _k, v in [(o[0], o[1], o[2]) for o in history_ops] if op == "lpush")
    row = json.loads(row_json)
    assert row["pv"] == 5.0
    assert row["sp"] is None, "新绑定尚无值必须为 NULL，不得沿用 OLD_SP 的 6.0"


async def test_r11_deleted_tag_and_loop_memory_convergence():
    """删除 tag/整回路后旧来源值不再入库，内存结构随活跃集合收敛."""
    fake = _RecordingRedis()
    sub = RealtimeSubscriber()
    ts = "2026-07-15T10:00:00Z"

    v1_loops = [("loop-1", "LOOP1", "u1"), ("loop-2", "LOOP2", "u2")]
    v1_map = [
        ("loop-1", "PV", "L1_PV"),
        ("loop-2", "PV", "L2_PV"),
        ("loop-2", "SP", "L2_SP"),
    ]
    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.AsyncSessionLocal", return_value=_mock_meta_db(v1_loops, v1_map)),
    ):
        await sub._refresh_loop_meta_cache()
        await sub._cache_value(
            {"tagCode": "L1_PV", "value": "1.0", "quality": 1, "collectTime": ts}
        )
        await sub._cache_value(
            {"tagCode": "L2_PV", "value": "2.0", "quality": 1, "collectTime": ts}
        )
        await sub._cache_value(
            {"tagCode": "L2_SP", "value": "9.0", "quality": 1, "collectTime": ts}
        )
        assert "LOOP2" in sub._last_known and "LOOP2" in sub._buffer

    # v2：整回路 loop-2 删除；相同绑定刷新不改 epoch
    v2_loops = [("loop-1", "LOOP1", "u1")]
    v2_map = [("loop-1", "PV", "L1_PV")]
    with (
        patch(f"{_SUB}.redis_client", fake),
        patch(f"{_SUB}.AsyncSessionLocal", return_value=_mock_meta_db(v2_loops, v2_map)),
    ):
        await sub._refresh_loop_meta_cache()

    # 内存结构收敛：删除回路的状态全部清出
    assert "LOOP2" not in sub._last_known
    assert "LOOP2" not in sub._buffer
    assert "LOOP2" not in sub._loop_epochs
    assert "LOOP2" not in sub._loop_role_tags
    assert "LOOP2" not in sub._loop_meta_cache

    # 已删除回路的旧来源消息：丢弃计数，不入库
    await sub._cache_value({"tagCode": "L2_SP", "value": "9.5", "quality": 1, "collectTime": ts})
    assert sub._metrics["unbound_tag_msgs"] == 1
    assert "LOOP2" not in sub._buffer

    # 绑定未变化（LOOP1）的 epoch 不推进
    assert sub._loop_epochs == {"LOOP1": 1}
