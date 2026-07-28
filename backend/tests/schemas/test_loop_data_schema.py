"""loop_data Schema 校验测试.

覆盖：
- ImportRequest overwrite 策略 tsEnd 实时边缘防护（P1：先 DELETE 再拉远端，
  tsEnd 贴近实时边缘会误删远端尚未归档的实时行，造成永久缺口）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.loop_data import ConflictStrategy, ImportRequest


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class TestImportRequestOverwriteTsEndMargin:
    """overwrite 策略强制 tsEnd ≤ now−5min（schema 层校验，FastAPI 映射为 422）."""

    def test_overwrite_ts_end_near_now_rejected(self):
        """overwrite + tsEnd 贴实时边缘（now）→ ValidationError（422）."""
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="overwrite"):
            ImportRequest(
                loopIds=["loop-1"],
                tsStart=_iso(now - timedelta(hours=1)),
                tsEnd=_iso(now),
                conflictStrategy=ConflictStrategy.OVERWRITE,
            )

    def test_overwrite_ts_end_within_margin_rejected(self):
        """overwrite + tsEnd 在 5 分钟余量内（now−2min）→ ValidationError."""
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="skip"):
            ImportRequest(
                loopIds=["loop-1"],
                tsStart=_iso(now - timedelta(hours=1)),
                tsEnd=_iso(now - timedelta(minutes=2)),
                conflictStrategy=ConflictStrategy.OVERWRITE,
            )

    def test_overwrite_ts_end_beyond_margin_accepted(self):
        """overwrite + tsEnd 超出余量（now−1h）→ 通过."""
        now = datetime.now(UTC)
        req = ImportRequest(
            loopIds=["loop-1"],
            tsStart=_iso(now - timedelta(hours=2)),
            tsEnd=_iso(now - timedelta(hours=1)),
            conflictStrategy=ConflictStrategy.OVERWRITE,
        )
        assert req.conflictStrategy == ConflictStrategy.OVERWRITE

    def test_skip_strategy_ts_end_near_now_accepted(self):
        """skip 策略无 DELETE，tsEnd 贴实时边缘不拦截."""
        now = datetime.now(UTC)
        req = ImportRequest(
            loopIds=["loop-1"],
            tsStart=_iso(now - timedelta(hours=1)),
            tsEnd=_iso(now),
            conflictStrategy=ConflictStrategy.SKIP,
        )
        assert req.conflictStrategy == ConflictStrategy.SKIP

    def test_default_strategy_is_overwrite_and_guarded(self):
        """schema 默认仍为 overwrite（兼容），但默认值同样受余量校验拦截."""
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            ImportRequest(
                loopIds=["loop-1"],
                tsStart=_iso(now - timedelta(hours=1)),
                tsEnd=_iso(now),
            )

    def test_invalid_ts_end_format_deferred_to_endpoint(self):
        """tsEnd 格式非法时校验器不重复报错（交由端点层 400 处理）."""
        now = datetime.now(UTC)
        req = ImportRequest(
            loopIds=["loop-1"],
            tsStart=_iso(now - timedelta(hours=1)),
            tsEnd="not-a-timestamp",
            conflictStrategy=ConflictStrategy.OVERWRITE,
        )
        assert req.tsEnd == "not-a-timestamp"
