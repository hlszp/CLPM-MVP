"""P1-1 真实 TDengine 行为验证（测点子表，双目标版本 3.3.6.0 / 3.3.6.6）.

运行（隔离环境 deploy/docker/docker-compose.refactor.yml）：

    cd backend && uv run pytest tests/integration/test_refactor_point_store.py -m integration -s

验证清单（设计 §4.1 / 计划 P1-1）：
1. DDL 幂等：ensure_schema 重复执行不报错；
2. DOUBLE/NULL：NULL 值写入读回为 None；double 精度保持（15-16 位）；
3. 毫秒精度：ts 保留毫秒，不截秒；
4. 同 ts 重复写（同 payload）：UPSERT 语义，行数不增；
5. 同 ts 更正（不同 payload）：TDengine 默认覆盖——证实"静默覆盖"存在，
   仓储层必须先读后写分流（write_events 的冲突路径以本测试为依据）；
6. LAST_ROW 不忽略 NULL（窗口前最后状态含 BAD/NULL，V04 关键前提）；
7. 稳定表 point_id IN (...) + GROUP BY 批量查询；
8. 跨子表部分成功：一条多子表 INSERT 中第二子表语法错——验证第一条子表
   数据是否留存（TDengine 语句级原子性边界）；
9. BINARY(64) payload_hash 往返。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.core.tdengine_native as native
from app.core.config import settings
from app.services.data_source import point_history_repository as repo

pytestmark = pytest.mark.integration

# 双目标版本：隔离 compose 的 td360（默认 settings 指向）与 td366
_TD_TARGETS = [
    pytest.param({"host": "localhost", "port": 17204, "label": "3.3.6.0"}, id="td360"),
    pytest.param({"host": "localhost", "port": 17214, "label": "3.3.6.6"}, id="td366"),
]


@pytest.fixture(params=_TD_TARGETS)
def td_target(request, monkeypatch) -> str:
    """切换 tdengine_native 连接到目标实例（REST 端口=原生+11）."""
    target = request.param
    monkeypatch.setattr(settings, "TDENGINE_HOST", target["host"])
    monkeypatch.setattr(settings, "TDENGINE_PORT", target["port"])
    monkeypatch.setattr(native, "_TD_REST_PORT", target["port"] + 11)
    native.TDengineConnectionPool.close_all()
    yield target["label"]
    native.TDengineConnectionPool.close_all()


def _pid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"clpm-ref/{seed}"))


def _ev(
    point_id: str,
    ts: datetime,
    value: float | None,
    quality_class: int = repo.QC_GOOD,
    quality_raw: int | None = 1,
    source_kind: int = repo.SOURCE_KIND_COV,
) -> repo.PointEvent:
    return repo.PointEvent(
        point_id=point_id,
        ts=ts,
        value=value,
        quality_class=quality_class,
        quality_raw=quality_raw,
        quality_schema=repo.QSCHEMA_AAS,
        source_kind=source_kind,
        received_at=ts,
        source_id="verify",
    )


BASE = datetime(2026, 9, 8, tzinfo=UTC)


class TestPointStoreBehavior:
    @pytest.fixture(autouse=True)
    async def _schema(self, td_target):
        await repo.ensure_schema()
        yield

    async def test_ddl_idempotent(self, td_target):
        await repo.ensure_schema()
        await repo.ensure_schema()

    async def test_double_null_and_precision(self, td_target):
        pid = _pid(f"prec-{td_target}")
        ts = BASE.replace(microsecond=123000)
        precise = 3.141592653589793
        await repo.write_events([_ev(pid, ts, precise), _ev(_pid(f"nul-{td_target}"), ts, None)])
        rows = await repo.read_events(
            [pid], BASE - timedelta(seconds=1), BASE + timedelta(seconds=1)
        )
        assert rows[pid][0]["value"] == precise  # DOUBLE 全精度往返
        pid2 = _pid(f"nul-{td_target}")
        rows2 = await repo.read_events(
            [pid2], BASE - timedelta(seconds=1), BASE + timedelta(seconds=1)
        )
        assert rows2[pid2][0]["value"] is None
        assert rows2[pid2][0]["quality_class"] == repo.QC_GOOD

    async def test_millisecond_precision(self, td_target):
        """库精度 PRECISION 'ms'：毫秒完整往返（亚毫秒输入不承诺，设计 §4.1）."""
        pid = _pid(f"ms-{td_target}")
        ts = BASE.replace(microsecond=7_000)
        await repo.write_events([_ev(pid, ts, 1.0)])
        rows = await repo.read_events(
            [pid], BASE - timedelta(seconds=1), BASE + timedelta(seconds=1)
        )
        assert rows[pid][0]["ts"].microsecond == 7_000

    async def test_same_ts_same_payload_idempotent(self, td_target):
        # 每次运行用新点（重跑不与历史数据耦合）
        pid = str(uuid.uuid4())
        ts = BASE + timedelta(seconds=10)
        r1 = await repo.write_events([_ev(pid, ts, 5.0)])
        assert r1.inserted == 1
        r2 = await repo.write_events([_ev(pid, ts, 5.0)])
        assert r2.inserted == 0
        assert r2.identical_skipped == 1
        assert r2.conflicts == []
        rows = await repo.read_events([pid], BASE, BASE + timedelta(seconds=20))
        assert len(rows[pid]) == 1

    async def test_same_ts_different_payload_semantics(self, td_target):
        """TDengine 同 ts 不同 payload 的裸 INSERT 行为 = 覆盖（更正语义）.

        先证实裸 INSERT 覆盖（这是仓储层必须读比分流的原因），再验证
        write_events 默认 skip 保留既有事实。
        """
        pid = _pid(f"conflict-{td_target}")
        ts = BASE + timedelta(seconds=20)
        # 裸 INSERT 覆盖证实
        sql_first = repo._build_insert_sql([(_ev(pid, ts, 1.0), "h1")])
        sql_second = repo._build_insert_sql([(_ev(pid, ts, 2.0), "h2")])
        await native.execute_native(sql_first)
        await native.execute_native(sql_second)
        rows = await repo.read_events([pid], BASE, BASE + timedelta(seconds=30))
        assert len(rows[pid]) == 1
        assert rows[pid][0]["value"] == 2.0  # 裸写覆盖（TDengine UPSERT）
        assert rows[pid][0]["payload_hash"] == "h2"
        # 仓储层：不同 payload 默认 skip，保留既有 + 冲突计数
        r = await repo.write_events([_ev(pid, ts, 3.0)])
        assert r.inserted == 0
        assert len(r.conflicts) == 1
        assert r.conflicts[0]["resolution"] == "skip"
        rows2 = await repo.read_events([pid], BASE, BASE + timedelta(seconds=30))
        assert rows2[pid][0]["value"] == 2.0  # 既有事实未被覆盖
        # 授权 overwrite 路径
        r2 = await repo.write_events([_ev(pid, ts, 3.0)], conflict_policy="overwrite_authorized")
        assert r2.inserted == 1
        rows3 = await repo.read_events([pid], BASE, BASE + timedelta(seconds=30))
        assert rows3[pid][0]["value"] == 3.0

    async def test_last_row_keeps_null_state(self, td_target):
        """LAST_ROW 窗口前最后状态不忽略 NULL/BAD（V04 前提）."""
        pid = _pid(f"lastrow-{td_target}")
        t1 = BASE + timedelta(seconds=100)
        t2 = BASE + timedelta(seconds=200)
        await repo.write_events(
            [
                _ev(pid, t1, 7.0, repo.QC_GOOD, 1),
                # 最后状态：值 NULL + BAD（不得被跳过沿用 t1 的 Good）
                _ev(pid, t2, None, repo.QC_BAD, 0),
            ]
        )
        states = await repo.read_last_states_before([pid], BASE + timedelta(seconds=300))
        st = states[pid]
        assert st is not None
        assert st["ts"] == t2
        assert st["value"] is None
        assert st["quality_class"] == repo.QC_BAD

    async def test_stable_query_batch_by_point_ids(self, td_target):
        pids = [_pid(f"batch-{td_target}-{i}") for i in range(5)]
        ts = BASE + timedelta(seconds=400)
        await repo.write_events(
            [_ev(pid, ts + timedelta(seconds=i), float(i)) for i, pid in enumerate(pids)]
        )
        rows = await repo.read_events(
            pids, BASE + timedelta(seconds=399), BASE + timedelta(seconds=410)
        )
        assert set(rows) == set(pids)
        assert all(len(v) == 1 for v in rows.values())
        # 未订阅的点不出现
        assert _pid(f"other-{td_target}") not in rows

    async def test_multi_subtable_partial_success(self, td_target):
        """一条多子表 INSERT 中第二子表非法（表名注入探针）→ 语句整体失败与否.

        用「第二个子表行带语法错误」的 SQL 验证 TDengine 语句级原子性：
        结果登记在基线报告中（部分成功/整体回滚二选一），P2 批次恢复设计
        以此为依据。
        """
        pid_a = _pid(f"partA-{td_target}")
        ts = BASE + timedelta(seconds=500)
        good = repo._build_insert_sql([(_ev(pid_a, ts, 1.0), "ha")])
        # 拼一条第二子表带非法字符的 SQL（表名含空格 → 语法错）
        bad_sql = (
            good + f", {settings.TDENGINE_DB}.bad table VALUES ('{repo.format_ts_utc(ts)}', 1)"
        )
        with pytest.raises(Exception):  # noqa: B017, PT011 — TDengine 错误类型不限定
            await native.execute_native(bad_sql)
        rows = await repo.read_events(
            [pid_a], BASE + timedelta(seconds=499), BASE + timedelta(seconds=501)
        )
        # 登记实测行为：整语句失败时第一子表是否已落（当前断言=不落，即语句级原子；
        # 若 TDengine 版本行为不同，本断言失败即需要按实际行为调整 P2 批次边界）
        assert len(rows[pid_a]) == 0

    async def test_payload_hash_roundtrip(self, td_target):
        pid = _pid(f"hash-{td_target}")
        ts = BASE + timedelta(seconds=600)
        ev = _ev(pid, ts, 42.5)
        ph = ev.payload_hash()
        assert len(ph) == 64
        await repo.write_events([ev])
        rows = await repo.read_events(
            [pid], BASE + timedelta(seconds=599), BASE + timedelta(seconds=601)
        )
        assert rows[pid][0]["payload_hash"] == ph
        # 同一事实不同 source_kind → 同 hash（语义去重，设计 §4.1）
        ev_snapshot = _ev(pid, ts, 42.5, source_kind=repo.SOURCE_KIND_SNAPSHOT)
        assert ev_snapshot.payload_hash() == ph
        # 值不同 → 不同 hash
        assert _ev(pid, ts, 42.6).payload_hash() != ph
