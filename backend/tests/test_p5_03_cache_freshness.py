"""P5-03 预计算与缓存真实新鲜度（03 号文 §8 卡）单测。

覆盖六项：
① 小时可合并统计复用——HourPartials 加法合并 ≡ aggregate_rows 逐行聚合
   （等价 + 抽 3 块手算对齐 + build_and_upsert 单次批量装载）；
② latest 口径查询收口——统一查询 SQL 形状/分支接线 + R02"最新代表后置
   筛选"scope 级语义（最新 NULL 行不被旧 SUCCESS 行顶替，真库用例见
   TestRealPg 类）；
③ 缓存绝对有效期——命中不延寿（时间可控单测）+ Redis setex ceil(ttl)
   + 晋升携带剩余 TTL（双层一致性）；
④ single-flight——20 并发冷请求只重建一次 + 失败不缓存 + 跨进程
   Redis 锁/轮询合并证据（FakeTTLRedis 模拟他 worker）；
⑤ 三源失效——generation_suffix（policy/data/config）推进 → 缓存 key
   整体更换（数据新窗/配置更新两反例；权限源 α 已有测试）；
⑥ 分块状态与局部重试——失败块 UNAVAILABLE 其余块 OK；失败块不缓存
   （下次请求重建失败块、成功块命中缓存=重试只重失败块）+ HTTP 证据。
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.agg_cache as agg_cache
from app.services.agg_cache import cached_agg, prime_agg
from app.services.workbench_precalc import (
    HourPartials,
    aggregate_rows,
    build_and_upsert,
    build_trend_points,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTTLRedis:
    """带 TTL 语义的内存 Redis（get/setex/set(nx/px)/ttl/delete/scan_iter）.

    setex 按 wall-clock 记到期时刻；ttl() 返回剩余秒（-2=不存在）——
    用于"临到期命中不延寿/晋升携带剩余 TTL"与跨进程合并的可控验证。
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expire_at: dict[str, float] = {}
        self._now: float = 0.0
        self.setex_calls: list[tuple[str, int]] = []

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def _alive(self, key: str) -> bool:
        exp = self._expire_at.get(key)
        return key in self._store and (exp is None or self._now < exp)

    async def get(self, key: str) -> str | None:
        return self._store.get(key) if self._alive(key) else None

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value
        self._expire_at[key] = self._now + ttl
        self.setex_calls.append((key, ttl))

    async def set(self, key: str, value: str, **kwargs: Any) -> bool:
        if kwargs.get("nx") and self._alive(key):
            return False
        self._store[key] = value
        if kwargs.get("px") is not None:
            self._expire_at[key] = self._now + kwargs["px"] / 1000
        else:
            self._expire_at.pop(key, None)
        return True

    async def ttl(self, key: str) -> int:
        if not self._alive(key):
            return -2
        exp = self._expire_at.get(key)
        if exp is None:
            return -1
        import math

        return max(0, math.ceil(exp - self._now))

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                self._expire_at.pop(k, None)
                n += 1
        return n

    async def scan_iter(self, match: str = "*", count: int = 100):  # noqa: ARG002
        for k in list(self._store):
            import fnmatch

            if fnmatch.fnmatch(k, match):
                yield k


class _FakeClock:
    """可控单调时钟（进程层绝对到期验证）."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _reset_agg_cache_state() -> None:
    agg_cache._cache.clear()
    agg_cache._inflight.clear()


@pytest.fixture(autouse=True)
def _isolated_agg_cache():
    """每用例隔离 agg_cache 进程层状态（Redis 层默认关闭，与 conftest 一致）."""
    _reset_agg_cache_state()
    yield
    _reset_agg_cache_state()


# ---------------------------------------------------------------------------
# ③ 缓存绝对有效期（命中不延寿；双层一致性）
# ---------------------------------------------------------------------------


class TestAbsoluteExpiry:
    @pytest.mark.asyncio
    async def test_进程层命中不延寿(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """t0 写入 ttl=100：t0+99 命中**不重写到期时刻**，t0+100 即过期重建."""
        clock = _FakeClock()
        monkeypatch.setattr(agg_cache, "_clock", clock)
        calls = []

        async def builder() -> dict:
            calls.append(clock.t)
            return {"v": len(calls)}

        assert await cached_agg("k", builder, ttl=100) == {"v": 1}
        clock.advance(99)
        assert await cached_agg("k", builder, ttl=100) == {"v": 1}  # 命中
        assert calls == [1000.0]
        clock.advance(1)  # t0+100：绝对到期（命中不曾延寿）
        assert await cached_agg("k", builder, ttl=100) == {"v": 2}
        assert calls == [1000.0, 1100.0]

    @pytest.mark.asyncio
    async def test_redis_setex用ceil无加成(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Redis 层 setex 用 ceil(ttl)——原 int(ttl)+5 的 5s 延寿窗已移除."""
        fake = FakeTTLRedis()
        monkeypatch.setattr(agg_cache, "REDIS_LAYER_ENABLED", True)

        async def _get_client():
            return fake

        monkeypatch.setattr("app.core.redis.redis_client", fake)
        # agg_cache 函数内 import redis_client——直接 patch 模块属性即可
        clock = _FakeClock()
        monkeypatch.setattr(agg_cache, "_clock", clock)

        async def builder() -> str:
            return "x"

        await cached_agg("k1", builder, ttl=240.0)
        await cached_agg("k2", builder, ttl=100.5)
        ttls = {k.split("agg_cache:")[-1]: ttl for k, ttl in fake.setex_calls}
        assert ttls["k1"] == 240  # 不再是 245
        assert ttls["k2"] == 101  # ceil

    @pytest.mark.asyncio
    async def test_redis晋升携带剩余ttl_双层一致(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Redis 命中晋升进程层按剩余 TTL 到期——不晚于 Redis 层，不延寿."""
        fake = FakeTTLRedis()
        monkeypatch.setattr(agg_cache, "REDIS_LAYER_ENABLED", True)
        monkeypatch.setattr("app.core.redis.redis_client", fake)
        clock = _FakeClock()
        monkeypatch.setattr(agg_cache, "_clock", clock)

        calls = []

        async def builder() -> dict:
            calls.append(1)
            return {"v": "data"}

        await cached_agg("k", builder, ttl=100)  # worker A 写入 Redis
        assert len(calls) == 1
        # 模拟 worker B：进程层清空（另一 worker 无本地缓存）
        agg_cache._cache.clear()
        agg_cache._inflight.clear()
        fake.advance(99)  # Redis 剩余 1s
        assert await cached_agg("k", builder, ttl=100) == {"v": "data"}
        assert calls == [1]  # Redis 命中，未重建
        # 晋升条目到期时刻 = 晋升时刻 + 1s（Redis 剩余），不按本地 ttl=100 重算
        fake.advance(2)
        clock.advance(2)
        assert await cached_agg("k", builder, ttl=100) == {"v": "data"}
        assert len(calls) == 2  # 进程层按剩余 TTL 到期后重建（不延寿）

    @pytest.mark.asyncio
    async def test_redis临到期条目不可用(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Redis 层到期后 GET 返回 None（FakeTTLRedis 按墙钟清除）——重建."""
        fake = FakeTTLRedis()
        monkeypatch.setattr(agg_cache, "REDIS_LAYER_ENABLED", True)
        monkeypatch.setattr("app.core.redis.redis_client", fake)

        calls = []

        async def builder() -> int:
            calls.append(1)
            return len(calls)

        await cached_agg("k", builder, ttl=50)
        fake.advance(51)  # 超过 ceil(50)=50s：Redis 到期
        agg_cache._cache.clear()
        assert await cached_agg("k", builder, ttl=50) == 2
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# ④ single-flight
# ---------------------------------------------------------------------------


class TestSingleFlight:
    @pytest.mark.asyncio
    async def test_20并发冷请求只重建一次(self) -> None:
        """同 key 20 并发冷请求：builder 只执行 1 次，全部拿到同一结果."""
        calls = []

        async def builder() -> dict:
            calls.append(1)
            await asyncio.sleep(0.05)  # 模拟 5~9s 冷算（合并窗口）
            return {"v": 42}

        results = await asyncio.gather(*[cached_agg("sf-key", builder) for _ in range(20)])
        assert len(calls) == 1
        assert all(r == {"v": 42} for r in results)

    @pytest.mark.asyncio
    async def test_失败不缓存_异常传播_下次重试(self) -> None:
        """builder 失败：异常传播给全部合并等待者，两层不缓存，下次重建."""
        calls = []

        async def bad_builder() -> Any:
            calls.append(1)
            await asyncio.sleep(0.02)
            raise RuntimeError("boom")

        results = await asyncio.gather(
            *[cached_agg("sf-fail", bad_builder) for _ in range(20)], return_exceptions=True
        )
        assert len(calls) == 1  # 合并：失败也只算一次
        assert all(isinstance(r, RuntimeError) for r in results)
        # 失败未缓存（进程层/合并表均空）
        assert "sf-fail" not in agg_cache._cache
        assert not agg_cache._inflight

        async def good_builder() -> str:
            calls.append(1)
            return "recovered"

        assert await cached_agg("sf-fail", good_builder) == "recovered"
        assert len(calls) == 2  # 下次请求自然重试

    @pytest.mark.asyncio
    async def test_跨进程合并_他worker在建时等待其结果(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """分布式合并证据：Redis 锁被他 worker 持有 → 本地轮询等值，不本地重建."""
        fake = FakeTTLRedis()
        monkeypatch.setattr(agg_cache, "REDIS_LAYER_ENABLED", True)
        monkeypatch.setattr("app.core.redis.redis_client", fake)
        monkeypatch.setattr(agg_cache, "_FLIGHT_POLL_INTERVAL", 0.01)
        monkeypatch.setattr(agg_cache, "_FLIGHT_POLL_MAX_WAIT", 2.0)

        # 模拟另一 worker：先持有 agg_flight 锁（SET NX 成功）
        assert await fake.set("agg_flight:agg_cache:x-key", "peer-token", nx=True, px=15000)

        calls = []

        async def builder() -> str:
            calls.append(1)
            return "local-rebuild"

        task = asyncio.create_task(cached_agg("x-key", builder, ttl=60))
        await asyncio.sleep(0.05)
        assert calls == []  # 未本地重建，处于等待
        # 他 worker 完成：写值 + 释放锁
        await prime_agg("x-key", "peer-result", ttl=60)
        await fake.delete("agg_flight:agg_cache:x-key")
        assert await task == "peer-result"
        assert calls == []  # 本地 builder 全程未执行（合并到他 worker 结果）

    @pytest.mark.asyncio
    async def test_跨进程合并_等待超时兜底自建(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """锁持有方异常消失（无值写入）：等待超时后本地自建（正确性兜底）."""
        fake = FakeTTLRedis()
        monkeypatch.setattr(agg_cache, "REDIS_LAYER_ENABLED", True)
        monkeypatch.setattr("app.core.redis.redis_client", fake)
        monkeypatch.setattr(agg_cache, "_FLIGHT_POLL_INTERVAL", 0.01)
        monkeypatch.setattr(agg_cache, "_FLIGHT_POLL_MAX_WAIT", 0.1)

        await fake.set("agg_flight:agg_cache:y-key", "dead-peer", nx=True, px=15000)
        calls = []

        async def builder() -> str:
            calls.append(1)
            return "self-rebuilt"

        assert await cached_agg("y-key", builder, ttl=60) == "self-rebuilt"
        assert calls == [1]


# ---------------------------------------------------------------------------
# ⑤ 三源失效（generation_suffix）
# ---------------------------------------------------------------------------


def _scalar_db(value: Any) -> AsyncMock:
    """执行 max() 标量查询的 mock 会话（scalar → value）."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = value
    db.execute = AsyncMock(return_value=result)
    return db


class TestGenerationSuffix:
    @pytest.mark.asyncio
    async def test_数据源代数_新窗落库推进(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """unit_kpi_summary max(snapshot_time) 前进 → data 代数变 → key 变."""
        monkeypatch.delenv("CLPM_TEST_MODE", raising=False)
        from app.services.agg_cache import data_revision_suffix

        t1 = datetime(2026, 10, 13, 10, 0)
        t2 = t1 + timedelta(hours=1)  # 新小时窗落库
        assert await data_revision_suffix(_scalar_db(t1)) != await data_revision_suffix(
            _scalar_db(t2)
        )
        assert (await data_revision_suffix(_scalar_db(t1))).startswith("d")
        # 异常/不可解析 → d0（TTL 兜底）
        assert await data_revision_suffix(_scalar_db("garbage")) == "d0"
        assert await data_revision_suffix(AsyncMock(side_effect=RuntimeError)) == "d0"

    @pytest.mark.asyncio
    async def test_配置源代数_sys_config更新推进(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """sys_config max(updated_at) 前进 → config 代数变 → key 变."""
        monkeypatch.delenv("CLPM_TEST_MODE", raising=False)
        from app.services.agg_cache import config_revision_suffix

        t1 = datetime(2026, 10, 13, 8, 0)
        t2 = t1 + timedelta(minutes=1)  # 阈值/等级线等配置更新
        assert await config_revision_suffix(_scalar_db(t1)) != await config_revision_suffix(
            _scalar_db(t2)
        )
        assert (await config_revision_suffix(_scalar_db(t1))).startswith("c")

    @pytest.mark.asyncio
    async def test_generation合体含三段(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLPM_TEST_MODE", raising=False)
        from app.services.agg_cache import generation_suffix

        gen = await generation_suffix(_scalar_db(datetime(2026, 10, 13, 10, 0)))
        # r{policy}:d{data}:c{config} 三段（policy 读取失败回退 r0）
        parts = gen.split(":")
        assert len(parts) == 3
        assert parts[0].startswith("r") and parts[1].startswith("d") and parts[2].startswith("c")

    @pytest.mark.asyncio
    async def test_数据代数推进_聚合缓存整体换key重建(self) -> None:
        """数据源失效反例（服务级）：新窗落库 → 同参请求重建而非命中旧缓存.

        generation_suffix 直接按调用序列推进（g1/g1/g2），验证 gen 进入全部
        块 key 且推进后整组换 key 重建（suffix 本身的推进语义由前两例覆盖）。
        """
        from app.services.cockpit_overview import build_overview
        from tests.test_cockpit import _overview_patches

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=3)
        gen_calls = ["g1", "g1", "g2"]  # build×3：同代数命中 → 代数推进重建

        async def _gen(_db: Any) -> str:
            return gen_calls.pop(0)

        built = []  # 只记**实际执行**的 builder（缓存命中不记——重建才算）
        from app.services.agg_cache import cached_agg as _orig_cached

        async def _track(key: str, builder: Any, ttl: float = 60.0) -> Any:
            async def _counting() -> Any:
                built.append(key)
                return await builder()

            return await _orig_cached(key, _counting, ttl=ttl)  # 保留缓存语义

        with (
            patch("app.services.agg_cache.generation_suffix", _gen),
            patch("app.services.agg_cache.cached_agg", _track),
            patch.multiple("app.services.cockpit_overview", **_overview_patches()),
            patch(
                "app.services.performance.get_grade_distribution",
                AsyncMock(return_value={}),
            ),
        ):
            await build_overview(db, window="24h")
            n1 = len(built)
            await build_overview(db, window="24h")  # 同代数：全部块命中，无重建
            assert len(built) == n1
            await build_overview(db, window="24h")  # 代数推进：全块重建
            assert len(built) == 2 * n1
        # 两次代数实际重建的块 key 互不相同（整组换 key）
        first, second = set(built[:n1]), set(built[n1:])
        assert first and second and not (first & second)


# ---------------------------------------------------------------------------
# ① 小时可合并统计复用（等价守护）
# ---------------------------------------------------------------------------


def _mk_row(
    node_id: str,
    hour: datetime,
    w: int,
    score: Decimal | float | None,
    **metrics: Decimal | float | None,
) -> SimpleNamespace:
    """构造 _RowLike（UnitKpiSummary 轻量行替身）."""
    base_metrics = {
        "auto_mode_rate": None,
        "effective_auto_rate": None,
        "stability_rate": None,
        "accuracy_rate": None,
        "fast_rate": None,
        "good_value_rate": None,
        "oscillation_rate": None,
        "saturation_rate": None,
        "instrument_fault_rate": None,
    }
    base_metrics.update(metrics)
    return SimpleNamespace(
        node_id=node_id,
        snapshot_time=hour,
        evaluated_loops=w,
        avg_score=score,
        **base_metrics,
    )


class TestHourPartialsEquivalence:
    def test_随机行集_部分和合并等价逐行聚合(self) -> None:
        """同 行集：window_agg ≡ aggregate_rows / window_trend ≡ build_trend_points."""
        random.seed(7)
        base = datetime(2026, 10, 1)
        combos: set[tuple[str, int]] = set()
        while len(combos) < 200:  # (unit, hour) 唯一（表唯一约束前提）
            combos.add((f"u{random.randint(0, 4)}", random.randint(0, 71)))
        rows = [
            _mk_row(
                u,
                base + timedelta(hours=h),
                random.choice([0, 3, 10, 25]),
                None if random.random() < 0.2 else Decimal(str(round(random.uniform(40, 100), 2))),
                **{
                    k: None
                    if random.random() < 0.2
                    else Decimal(str(round(random.uniform(40, 100), 2)))
                    for k in (
                        "auto_mode_rate",
                        "effective_auto_rate",
                        "stability_rate",
                        "accuracy_rate",
                        "fast_rate",
                        "good_value_rate",
                        "oscillation_rate",
                        "saturation_rate",
                        "instrument_fault_rate",
                    )
                },
            )
            for u, h in sorted(combos)
        ]
        units = [f"u{i}" for i in range(5)]
        hp = HourPartials(rows)
        for start_h, end_h in [(0, 24), (0, 72), (10, 50)]:
            s, e = base + timedelta(hours=start_h), base + timedelta(hours=end_h)
            ref = aggregate_rows([r for r in rows if s <= r.snapshot_time < e])
            got = hp.window_agg(units, s, e)
            assert got["loop_count"] == ref["loop_count"]
            if ref["score"] is None:
                assert got["score"] is None
            else:
                assert got["score"] == pytest.approx(ref["score"], abs=0.001)
            for k in ref["rates"]:
                g, r_ = got["rates"][k], ref["rates"][k]
                assert (g is None and r_ is None) or (
                    g is not None and r_ is not None and abs(g - r_) <= 0.001
                )
        for window in ("24h", "7d", "30d"):
            ref = build_trend_points(rows, window)
            got = hp.window_trend(units, window, base, base + timedelta(hours=72))
            assert [p["t"] for p in ref] == [p["t"] for p in got]
            for a, b in zip(ref, got, strict=False):
                assert abs(a["v"] - b["v"]) <= 0.001  # 仅 .xxx5 舍入刀口可差末位

    def test_子集scope合并等价(self) -> None:
        """scope=成员 UNIT 子集：合并 ≡ 对该子集行集逐行聚合（加法可分性）."""
        base = datetime(2026, 10, 1)
        rows = [
            _mk_row("u1", base, 10, Decimal("80"), stability_rate=Decimal("80")),
            _mk_row("u2", base, 30, Decimal("90")),
            _mk_row("u3", base, 5, Decimal("60"), good_value_rate=Decimal("95")),
        ]
        hp = HourPartials(rows)
        sub = ["u1", "u3"]
        ref = aggregate_rows([r for r in rows if r.node_id in sub])
        assert hp.window_agg(sub, base, base + timedelta(hours=1)) == ref

    def test_手算块1_双单元加权(self) -> None:
        """手算：u1(score80,w10)+u2(score90,w30) → score 85.0 / loop_count 40."""
        base = datetime(2026, 10, 1)
        rows = [
            _mk_row("u1", base, 10, Decimal("80"), stability_rate=Decimal("80")),
            _mk_row("u2", base, 30, Decimal("90")),
        ]
        agg = HourPartials(rows).window_agg(["u1", "u2"], base, base + timedelta(hours=1))
        assert agg["score"] == 87.5  # (80×10+90×30)/40
        assert agg["loop_count"] == 40
        assert agg["rates"]["steady_rate"] == 0.8  # 独立分母：仅 u1 有平稳率
        assert agg["rates"]["auto_mode_rate"] is None

    def test_手算块2_NULL指标独立分母(self) -> None:
        """手算：good_value u1=90(w10) u2=None(w30) → 90 而非 22.5（NULL 不拉低）."""
        base = datetime(2026, 10, 1)
        rows = [
            _mk_row("u1", base, 10, Decimal("70"), good_value_rate=Decimal("90")),
            _mk_row("u2", base, 30, Decimal("70"), good_value_rate=None),
        ]
        agg = HourPartials(rows).window_agg(["u1", "u2"], base, base + timedelta(hours=1))
        assert agg["rates"]["good_value_rate"] == 0.9
        assert agg["loop_count"] == 40

    def test_手算块3_零权重与NULL评分(self) -> None:
        """手算：w=0 行无贡献；NULL 评分行不进 score 分母（loop_count 只计有效）."""
        base = datetime(2026, 10, 1)
        rows = [
            _mk_row("u1", base, 0, Decimal("95")),  # 零权重：分子分母均无贡献
            _mk_row("u2", base, 5, Decimal("60")),
            _mk_row("u3", base, 7, None),  # NULL 评分：不进 score 分母
        ]
        agg = HourPartials(rows).window_agg(["u1", "u2", "u3"], base, base + timedelta(hours=1))
        assert agg["score"] == 60.0
        assert agg["loop_count"] == 5  # 95×0+60×5 → 分母 5（u3 的 7 不计）
        # 空集 → 未计算（CAL-07：None 而非 0）
        empty = HourPartials([]).window_agg(["u1"], base, base + timedelta(hours=1))
        assert empty["score"] is None and empty["loop_count"] == 0

    @pytest.mark.asyncio
    async def test_build_and_upsert单次批量装载多scope复用(self) -> None:
        """① 复用主张：整轮 3 窗口 × N scope 只 1 次 unit_kpi_summary 批量查询."""
        base = datetime(2026, 10, 13, 0, 0)
        rows = [
            _mk_row("u1", base, 10, Decimal("80"), stability_rate=Decimal("80")),
            _mk_row("u2", base, 30, Decimal("90")),
        ]
        scopes = {
            "GLOBAL:0": (0, ["u1", "u2"]),
            "UNIT:101": (101, ["u1"]),
            "UNIT:102": (102, ["u2"]),
        }
        bulk_calls: list[Any] = []
        upserts: list[Any] = []

        async def _bulk(db, unit_ids, start, end):
            bulk_calls.append((tuple(unit_ids), start, end))
            return [r for r in rows if r.node_id in unit_ids and start <= r.snapshot_time < end]

        db = AsyncMock()
        # get_grade_distribution 为 build_and_upsert 函数内 import——patch 源模块
        with (
            patch("app.services.workbench_precalc._query_rows_bulk", _bulk),
            patch("app.services.workbench_precalc._load_scope_map", AsyncMock(return_value=scopes)),
            patch("app.services.performance.get_grade_distribution", AsyncMock(return_value={})),
            patch(
                "app.services.workbench_precalc._query_mode_distribution",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.workbench_precalc._upsert_row",
                AsyncMock(side_effect=lambda db, **kw: upserts.append(kw) or True),
            ),
            patch("app.services.workbench_precalc._prune_rows", AsyncMock(return_value=0)),
        ):
            stats = await build_and_upsert(db)

        assert stats["written"] == 3 * 3  # 3 scope × 3 窗口
        assert len(bulk_calls) == 1  # 全轮仅 1 次批量装载（旧路径为 scope×窗×2 次）
        # 写入行的聚合值来自部分和合并（GLOBAL=u1+u2）
        global_rows = [u for u in upserts if u["scope_type"] == "GLOBAL"]
        assert any(u["agg"]["score"] == 87.5 for u in global_rows)  # (80×10+90×30)/40


# ---------------------------------------------------------------------------
# ② latest 口径查询收口
# ---------------------------------------------------------------------------


class TestLatestScopeQuery:
    async def _capture_stmt(self, **kwargs) -> tuple[str, list]:
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result)
        from app.services.workbench_overview import query_latest_scope_rows

        await query_latest_scope_rows(db, **kwargs)
        stmt = db.execute.call_args[0][0]
        from sqlalchemy.dialects import postgresql

        return (
            str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})),
            db.execute.call_args,
        )

    @pytest.mark.asyncio
    async def test_统一查询SQL形状_distinct_on加窗口终点倒序(self) -> None:
        sql, _ = await self._capture_stmt(scope_type="AREA", window="24h", scope_ids=[10, 20])
        assert "DISTINCT ON (workbench_window_summary.scope_id)" in sql
        assert (
            "ORDER BY workbench_window_summary.scope_id, workbench_window_summary.window_end DESC"
            in sql
        )
        assert 'window_w."window" =' in sql or "window_w = " in sql or '"window" =' in sql
        assert "scope_id IN" in sql
        # 不预筛 score/status（最新代表后置筛选——R02 语义）：WHERE 段无该谓词
        assert "score IS NOT NULL" not in sql
        assert "status =" not in sql and "status IN" not in sql

    @pytest.mark.asyncio
    async def test_空ids显式空结果不触库(self) -> None:
        from app.services.workbench_overview import query_latest_scope_rows

        db = AsyncMock()
        assert await query_latest_scope_rows(db, "UNIT", "24h", []) == []
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_None_ids查该层级全部(self) -> None:
        sql, _ = await self._capture_stmt(scope_type="UNIT", window="7d", scope_ids=None)
        assert "scope_id IN" not in sql

    @pytest.mark.asyncio
    async def test_overview子树分支接线统一查询(self) -> None:
        """plants/units 的 .in_() 分支走统一 latest 查询（G15 漏改分支收口）."""
        from app.services import workbench_overview as wo

        spy = AsyncMock(return_value=[])
        with (
            patch.object(wo, "query_latest_scope_rows", spy),
            patch.object(
                wo, "_get_child_ids_for_plants", AsyncMock(return_value=("AREA", [11, 12]))
            ),
            patch.object(wo, "_get_descendant_unit_ids", AsyncMock(return_value=[21, 22])),
            patch.object(wo, "_load_plant_hierarchy", AsyncMock(return_value=_hierarchy_stub())),
            patch.object(wo, "_query_alarm_per_unit", AsyncMock(return_value={})),
            patch.object(wo, "_query_overdue_per_unit", AsyncMock(return_value={})),
        ):
            await wo._plants_block(AsyncMock(), "FACTORY", 5, "24h", _hierarchy_stub(), 0.9, None)
            await wo._units_block(AsyncMock(), "FACTORY", 5, "24h", _hierarchy_stub(), None)
        assert spy.call_args_list[0].args[1:] == ("AREA", "24h", [11, 12])
        assert spy.call_args_list[1].args[1:] == ("UNIT", "24h", [21, 22])

    def test_最新代表后置筛选_python级语义(self) -> None:
        """R02 语义 scope 级：最新行 score=None 时读方判未计算，不被旧 95 顶替."""
        from app.services.workbench_assessment import _row_score

        latest = SimpleNamespace(loop_count=5, score=None)  # 最新行：未计算
        older = SimpleNamespace(loop_count=5, score=95.0)
        assert _row_score(latest) is None  # 后置筛选：shaper 判 NOT_COMPUTED
        assert _row_score(older) == 95.0


# ---------------------------------------------------------------------------
# ⑥ 分块状态与局部重试
# ---------------------------------------------------------------------------


def _hierarchy_stub() -> dict:
    return {
        "areas": [],
        "by_id": {},
        "factories": [],
        "name_by_source_id": {},
        "unit_to_area": {},
        "unit_to_factory": {},
    }


def _win_row(window: str, score: float, rates: dict | None = None) -> SimpleNamespace:
    rates = rates or {}
    return SimpleNamespace(
        window_w=window,
        score=score,
        status="GOOD",
        loop_count=10,
        score_trend=[],
        flags=[],
        snapshot_at=None,
        **{
            k: rates.get(k, 0.9)
            for k in (
                "good_value_rate",
                "auto_mode_rate",
                "effective_auto_rate",
                "steady_rate",
                "accuracy_rate",
                "fast_rate",
            )
        },
    )


class TestBlockStates:
    @pytest.mark.asyncio
    async def test_工作台总览_失败块UNAVAILABLE其余块OK(self) -> None:
        """故障块显式 UNAVAILABLE，其余块照常出数（不以空数据伪装可用）."""
        from app.services import workbench_overview as wo

        db = AsyncMock()
        with (
            patch.object(wo, "_query_windows", AsyncMock(side_effect=RuntimeError("windows 挂了"))),
            patch.object(wo, "_load_plant_hierarchy", AsyncMock(return_value={})),
            patch.object(wo, "_get_child_ids_for_plants", AsyncMock(return_value=("AREA", []))),
            patch.object(wo, "_query_scope_rows", AsyncMock(return_value=[])),
            patch.object(wo, "_query_alarm_per_unit", AsyncMock(return_value={})),
            patch.object(wo, "_query_overdue_per_unit", AsyncMock(return_value={})),
            patch.object(
                wo, "_query_pareto", AsyncMock(return_value=[{"root_cause": "X", "tag_count": 1}])
            ),
            patch.object(wo, "_query_roots", AsyncMock(return_value=[])),
            patch.object(wo, "_query_alert_events", AsyncMock(return_value=[])),
            patch.object(wo, "_query_funnel", AsyncMock(return_value={"pending_count": 1})),
            patch.object(wo, "_get_descendant_unit_ids", AsyncMock(return_value=[])),
        ):
            data = await wo.build_overview(db, scope_type="GLOBAL", window="24h")

        assert data["block_states"]["windows"] == "UNAVAILABLE"
        assert data["windows"] == {"24h": None, "7d": None, "30d": None}  # 兜底形状稳定
        assert data["block_states"]["pareto"] == "OK"
        assert data["pareto"][0]["root_cause"] == "X"
        assert data["block_states"]["funnel"] == "OK"
        assert data["funnel"]["pending"] == 1

    @pytest.mark.asyncio
    async def test_重试只重失败块_成功块不再重建(self) -> None:
        """失败块不缓存：第二次请求重建失败块；成功块命中缓存不再执行 builder."""
        from app.services import workbench_overview as wo

        db = AsyncMock()
        windows_mock = AsyncMock(side_effect=[RuntimeError("第一次挂"), [_win_row("24h", 82.0)]])
        pareto_mock = AsyncMock(return_value=[{"root_cause": "X", "tag_count": 1}])
        with (
            patch.object(wo, "_query_windows", windows_mock),
            patch.object(wo, "_load_plant_hierarchy", AsyncMock(return_value={})),
            patch.object(wo, "_get_child_ids_for_plants", AsyncMock(return_value=("AREA", []))),
            patch.object(wo, "_query_scope_rows", AsyncMock(return_value=[])),
            patch.object(wo, "_query_alarm_per_unit", AsyncMock(return_value={})),
            patch.object(wo, "_query_overdue_per_unit", AsyncMock(return_value={})),
            patch.object(wo, "_query_pareto", pareto_mock),
            patch.object(wo, "_query_roots", AsyncMock(return_value=[])),
            patch.object(wo, "_query_alert_events", AsyncMock(return_value=[])),
            patch.object(wo, "_query_funnel", AsyncMock(return_value=None)),
            patch.object(wo, "_get_descendant_unit_ids", AsyncMock(return_value=[])),
        ):
            first = await wo.build_overview(db, scope_type="GLOBAL", window="24h")
            second = await wo.build_overview(db, scope_type="GLOBAL", window="24h")

        assert first["block_states"]["windows"] == "UNAVAILABLE"
        assert second["block_states"]["windows"] == "OK"  # 失败块重试恢复
        assert second["windows"]["24h"]["score"] == 82.0
        # 成功块只构建一次（第二次命中缓存）：pareto 底层查询只执行 1 次
        assert pareto_mock.call_count == 1
        assert windows_mock.call_count == 2  # 失败块重试（失败不缓存）

    @pytest.mark.asyncio
    async def test_评估块_失败块UNAVAILABLE其余可用(self) -> None:
        """ranking 失败 → UNAVAILABLE；heatmap/summary/trend 不受阻断."""
        from app.services import workbench_assessment as wa

        db = AsyncMock()
        heat_spy = MagicMock(return_value={"metrics": ["m"], "units": [{"id": 1}]})
        with (
            patch.object(
                wa, "_query_scope_rows", AsyncMock(side_effect=RuntimeError("ranking 挂了"))
            ),
            patch.object(wa, "_query_scope_row", AsyncMock(return_value=None)),
            patch.object(wa, "_load_plant_hierarchy", AsyncMock(return_value=_hierarchy_stub())),
            patch.object(wa, "_get_descendant_unit_ids", AsyncMock(return_value=[1])),
            patch.object(wa, "_query_scope_rows_in_ids", AsyncMock(return_value=[])),
            patch.object(wa, "_query_prev_window_row", AsyncMock(return_value=None)),
            patch.object(wa, "_count_evaluable_loops", AsyncMock(return_value=12)),
            patch.object(wa, "_get_lose_threshold", AsyncMock(return_value=0.9)),
            patch.object(wa, "_query_alarm_per_unit", AsyncMock(return_value={})),
            patch.object(wa, "_query_overdue_per_unit", AsyncMock(return_value={})),
            patch.object(wa, "_get_child_ids_for_plants", AsyncMock(return_value=("AREA", []))),
            patch.object(wa, "_query_prev_rows_for", AsyncMock(return_value={})),
            patch.object(wa, "shape_heatmap", heat_spy),
        ):
            data = await wa.build_assessment(db, scope_type="GLOBAL", window="24h", view="plant")

        assert data["block_states"]["ranking"] == "UNAVAILABLE"
        assert data["ranking"] == []
        assert data["block_states"]["heatmap"] == "OK"
        assert data["heatmap"]["units"]
        assert data["block_states"]["summary"] == "OK"
        assert data["summary"]["participation"]["total"] == 12

    @pytest.mark.asyncio
    async def test_cockpit_部分块UNAVAILABLE_其余块出数(self) -> None:
        """驾驶舱：待办块故障 → todo=UNAVAILABLE + 兜底 0；等级分布/漏斗照常."""
        from app.services import cockpit_overview as co
        from tests.test_cockpit import _overview_patches

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=7)
        with (
            patch.multiple(
                "app.services.cockpit_overview",
                **_overview_patches(
                    _query_todo_counts=AsyncMock(side_effect=RuntimeError("todo 挂"))
                ),
            ),
            patch(
                "app.services.performance.get_grade_distribution",
                AsyncMock(return_value={"WARNING": 2, "POOR": 1}),
            ),
        ):
            data = await co.build_overview(db, window="24h")

        states = data["block_states"]
        assert states["todo"] == "UNAVAILABLE"
        assert data["kpi"]["todoPending"] == 0  # 兜底形状稳定
        assert states["gradeDist"] == "OK"
        assert data["kpi"]["degradedCount"] == 3
        assert states["funnelCounts"] == "OK"
        assert data["funnel"]["closed"] == 6

    def test_http_工作台总览_block_states随响应透出(self, p503_auth_client: object) -> None:
        """HTTP 证据：GET /api/v1/workbench/overview 响应携带 block_states，
        故障块 UNAVAILABLE、其余块 OK（部分可用语义过真实路由透出）."""
        from app.services import workbench_overview as wo

        with (
            patch.object(wo, "_query_windows", AsyncMock(side_effect=RuntimeError("windows 挂了"))),
            patch.object(wo, "_load_plant_hierarchy", AsyncMock(return_value={})),
            patch.object(wo, "_get_child_ids_for_plants", AsyncMock(return_value=("AREA", []))),
            patch.object(wo, "_query_scope_rows", AsyncMock(return_value=[])),
            patch.object(wo, "_query_alarm_per_unit", AsyncMock(return_value={})),
            patch.object(wo, "_query_overdue_per_unit", AsyncMock(return_value={})),
            patch.object(wo, "_query_pareto", AsyncMock(return_value=[])),
            patch.object(wo, "_query_roots", AsyncMock(return_value=[])),
            patch.object(wo, "_query_alert_events", AsyncMock(return_value=[])),
            patch.object(wo, "_query_funnel", AsyncMock(return_value={"pending_count": 2})),
            patch.object(wo, "_get_descendant_unit_ids", AsyncMock(return_value=[])),
        ):
            resp = p503_auth_client.get("/api/v1/workbench/overview?scopeType=GLOBAL&window=24h")

        assert resp.status_code == 200
        body = resp.json()
        assert str(body["code"]) == "0"
        data = body["data"]
        assert data["block_states"]["windows"] == "UNAVAILABLE"
        assert data["block_states"]["funnel"] == "OK"
        assert data["funnel"]["pending"] == 2


# ---------------------------------------------------------------------------
# 真库证据（PG 不可达即 skip；只读查询 + 故障注入）
# ---------------------------------------------------------------------------


def _probe_pg_or_skip() -> None:
    def _ping() -> None:
        async def _p() -> None:
            from sqlalchemy import text

            from app.core.db import AsyncSessionLocal

            async with AsyncSessionLocal() as s:
                await s.execute(text("SELECT 1"))

        asyncio.run(_p())

    try:
        _ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可达，跳过真库断言: {type(exc).__name__}")


class TestRealPgEvidence:
    """真库（只读）证据：① 等价对账 + ② latest 去重 + ⑥ 部分可用."""

    def test_真库_部分和合并对账逐行聚合(self) -> None:
        """同一真实窗口：_query_rows_bulk+HourPartials ≡ _query_window_rows+
        aggregate_rows（空数据集两侧同为空也成立）."""
        _probe_pg_or_skip()

        async def _body() -> None:
            from sqlalchemy import select

            from app.core.db import AsyncSessionLocal
            from app.models.plant_node import PlantNode
            from app.models.unit_kpi_summary import UnitKpiSummary
            from app.services.workbench_precalc import _query_window_rows

            async with AsyncSessionLocal() as db:
                unit_rows = (
                    await db.execute(select(PlantNode.id).where(PlantNode.type == "UNIT").limit(5))
                ).all()
                unit_ids = [str(r[0]) for r in unit_rows]
                if not unit_ids:
                    pytest.skip("真库无 UNIT 节点，空集对账无意义")
                newest = (
                    await db.execute(
                        select(UnitKpiSummary.snapshot_time)
                        .order_by(UnitKpiSummary.snapshot_time.desc())
                        .limit(1)
                    )
                ).scalar()
                if newest is None:
                    pytest.skip("真库 unit_kpi_summary 无数据")
                end = newest.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                start = end - timedelta(hours=24)

                from app.services.workbench_precalc import _query_rows_bulk

                ref_rows = await _query_window_rows(db, unit_ids, start, end)
                bulk = await _query_rows_bulk(db, unit_ids, start, end)
                hp = HourPartials(bulk)
                ref = aggregate_rows(ref_rows)
                got = hp.window_agg(unit_ids, start, end)
                assert got["loop_count"] == ref["loop_count"]
                if ref["score"] is None:
                    assert got["score"] is None
                else:
                    assert abs(got["score"] - ref["score"]) <= 0.001

        asyncio.run(_body())

    def test_真库_latest查询无重复scope(self) -> None:
        _probe_pg_or_skip()

        async def _body() -> None:
            from app.core.db import AsyncSessionLocal
            from app.services.workbench_overview import query_latest_scope_rows

            async with AsyncSessionLocal() as db:
                rows = await query_latest_scope_rows(db, "UNIT", "24h", None)
                ids = [r.scope_id for r in rows]
                assert len(ids) == len(set(ids))  # 每 scope 恰一行（含历史快照时）

        asyncio.run(_body())

    def test_真库_故障块UNAVAILABLE其余块真实出数(self) -> None:
        """⑥ 真库证据：funnel 块注入故障 → UNAVAILABLE；windows/plants 等块
        走真实查询出数（真库部分可用）。"""
        _probe_pg_or_skip()

        async def _body() -> None:
            from app.core.db import AsyncSessionLocal
            from app.services import workbench_overview as wo

            async with AsyncSessionLocal() as db:
                with patch.object(
                    wo, "_query_funnel", AsyncMock(side_effect=RuntimeError("MV 注入故障"))
                ):
                    data = await wo.build_overview(db, scope_type="GLOBAL", window="24h")
            assert data["block_states"]["funnel"] == "UNAVAILABLE"
            assert data["funnel"] is None
            # 其余块：真库查询成功（状态 OK；数据可为空但状态非 UNAVAILABLE）
            for block in ("windows", "plants", "units", "pareto", "roots", "alert_events"):
                assert data["block_states"][block] == "OK", block

        asyncio.run(_body())


@pytest.fixture
def p503_auth_client(client: object):
    """本文件专用：conftest client + get_current_user override（对齐
    test_api_workbench.auth_client 模式）。"""
    from app.api.deps import get_current_user
    from app.main import app

    user = MagicMock()
    user.id = 1
    user.role = "ADMIN"
    user.display_name = "测试管理员"

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    yield client
    app.dependency_overrides.pop(get_current_user, None)
