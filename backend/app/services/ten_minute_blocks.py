"""影子路径的原始事件块；正式算法仍对完整窗口预处理。

新鲜基线提供内容指纹 -> 命中同内容块 / 独立回源重建 -> 完整窗复核。
源数据变更会改变寻址键；缓存只是一种候选输入，绝不充当源版本真相。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import zlib
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.services.data_source import point_history_repository as repo
from app.services.result_ledger import canonical_json

BLOCK = timedelta(minutes=10)
TTL = 6 * 3600
MAX_ROWS = 50_000
MAX_BYTES = 8 * 1024 * 1024
MAX_PACKED = 128 * 1024
MAX_BLOCKS = 256  # 含 base64 的值合计至多 32 MiB；过期索引也有界。
_PUBLISH = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2], 'NX')
redis.call('ZADD', KEYS[2], ARGV[3], KEYS[1])
while redis.call('ZCARD', KEYS[2]) > tonumber(ARGV[4]) do
  local old = redis.call('ZPOPMIN', KEYS[2], 1)
  redis.call('DEL', old[1])
end
return 1
"""


class SourceChanged(RuntimeError):
    """权威基线与块独立读取之间发生变化，不能判定算法不一致。"""


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def floor_block(value: datetime) -> datetime:
    value = utc(value)
    return value.replace(minute=value.minute // 10 * 10, second=0, microsecond=0)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def event_digest(rows: list[dict]) -> str:
    return digest([{**row, "ts": utc(row["ts"]).isoformat()} for row in rows])


class ReferenceEvents:
    """记录基线真正读取的事件；不从缓存取数，不给候选块提供数据。"""

    def __init__(self, reader=None):
        self.reader = reader or repo.read_events
        self.reads: list[tuple[datetime, datetime, dict]] = []
        self.slots = asyncio.Semaphore(1)

    async def __call__(self, points, start, end, **kwargs):
        async with self.slots:
            result = await self.reader(points, start, end, **kwargs)
        self.reads.append((utc(start), utc(end), result))
        return result

    def expected(self, point, start, end) -> str:
        for left, right, result in self.reads:
            if point in result and left <= start and right >= end:
                rows = [r for r in result[point] if start <= utc(r["ts"]) < end]
                return event_digest(rows)
        raise SourceChanged("基线未覆盖完整块，拒绝复用")


class TenMinuteBlocks:
    def __init__(self, redis, reference: ReferenceEvents | None = None, reader=None):
        self.redis = redis
        self.reference = reference
        self.reader = reader or repo.read_events
        self.stats: Counter = Counter()
        self._inflight: dict[tuple, asyncio.Task] = {}
        self.slots = asyncio.Semaphore(1)

    @staticmethod
    def prefix(point: str, start: datetime) -> str:
        # UUID 校验沿用仓储，禁止任意 key 身份或 SQL 表名。
        point = repo.point_subtable(point)
        return f"shadow10:events:v1:{point}:{utc(start).isoformat()}"

    async def prepare(self, point: str, start: datetime) -> str:
        start = floor_block(start)
        rows = await self._fetch(point, start, start + BLOCK, half_open=True)
        revision = event_digest(rows)
        await self._put(point, start, revision, rows)
        return revision

    async def _fetch(self, point, start, end, *, half_open=False):
        async with self.slots:
            self.stats["sourceQueries"] += 1
            result = await self.reader([point], start, end, full_columns=False)
        rows = result.get(point, [])
        if half_open:
            rows = [r for r in rows if utc(r["ts"]) < end]
        if len(rows) > MAX_ROWS:
            raise ValueError("影子块超过行数预算，未截断输入")
        return rows

    async def _put(self, point, start, revision, rows):
        payload = canonical_json([{**r, "ts": utc(r["ts"]).isoformat()} for r in rows]).encode()
        if len(payload) > MAX_BYTES:
            raise ValueError("影子块超过字节预算，未截断输入")
        prefix = self.prefix(point, start)
        # 共用 Redis 客户端启用 decode_responses，不能直接写压缩二进制。
        packed = base64.b64encode(zlib.compress(payload)).decode("ascii")
        if len(packed) > MAX_PACKED:
            raise ValueError("影子块超过压缩预算，未截断输入")
        await self.redis.eval(
            _PUBLISH,
            2,
            f"{prefix}:{revision}",
            "shadow10:block-index",
            packed,
            TTL,
            utc(start).timestamp(),
            MAX_BLOCKS,
        )
        # head 只用于统计旧版本失效；读取候选键始终由新鲜基线内容决定。
        await self.redis.set(f"{prefix}:head", revision, ex=TTL)
        self.stats["prepared"] += 1

    async def _block(self, point, start):
        end = start + BLOCK
        revision = self.reference.expected(point, start, end)
        prefix = self.prefix(point, start)
        key = f"{prefix}:{revision}"
        packed = await self.redis.get(key)
        if packed is not None:
            try:
                if len(packed) > MAX_PACKED:
                    raise ValueError("invalid packed size")
                packed = base64.b64decode(packed, validate=True)
                decoder = zlib.decompressobj()
                payload = decoder.decompress(packed, MAX_BYTES + 1)
                if len(payload) > MAX_BYTES or not decoder.eof or decoder.unused_data:
                    raise ValueError("invalid block size")
                rows = json.loads(payload)
                for row in rows:
                    row["ts"] = utc(datetime.fromisoformat(row["ts"]))
                if len(rows) > MAX_ROWS or event_digest(rows) != revision:
                    raise ValueError("block content mismatch")
                if any(not start <= r["ts"] < end for r in rows):
                    raise ValueError("block boundary mismatch")
                self.stats["hits"] += 1
                return rows
            except (ValueError, TypeError, KeyError, zlib.error):
                self.stats["corrupt"] += 1
                await self.redis.delete(key)
        head = await self.redis.get(f"{prefix}:head")
        if isinstance(head, bytes):
            head = head.decode()
        if head is not None and head != revision:
            self.stats["invalidated"] += 1
        self.stats["misses"] += 1
        rows = await self._fetch(point, start, end, half_open=True)
        if event_digest(rows) != revision:
            raise SourceChanged("块独立回源与基线内容不同")
        await self._put(point, start, revision, rows)
        return rows

    async def _point(self, point, start, end):
        rows = []
        cursor = start
        while cursor <= end:
            boundary = floor_block(cursor)
            stop = boundary + BLOCK
            if cursor == boundary and stop <= end:
                key = (point, boundary)
                if key not in self._inflight:
                    self._inflight[key] = asyncio.create_task(self._block(point, boundary))
                rows.extend(await asyncio.shield(self._inflight[key]))
                cursor = stop
            else:
                # 任意手动窗口的首尾，以及含端点的最后一个点，精确回源。
                right = min(stop, end)
                part = await self._fetch(point, cursor, right)
                rows.extend(r for r in part if utc(r["ts"]) <= end and utc(r["ts"]) < stop)
                if right == end and right < stop:
                    break
                cursor = stop
        return rows

    async def __call__(self, points, start, end, **kwargs):
        if kwargs.get("full_columns", True):
            raise ValueError("影子块仅支持算法最小事件列")
        return {p: await self._point(p, utc(start), utc(end)) for p in points}

    async def finish(self):
        await asyncio.gather(*self._inflight.values(), return_exceptions=True)
        self._inflight.clear()
