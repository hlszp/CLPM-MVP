"""gate_overview 服务测试：latest-per-loop 门禁聚合口径."""

from __future__ import annotations

from collections import namedtuple
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SVC = "app.services.gate_overview"

# 模拟 SQLAlchemy Row（可按位解包）
_Row = namedtuple("_Row", "loop_id ts_end status score fitness_level fitness_detail valid_rate")


@pytest.mark.asyncio
async def test_gate_overview_buckets_and_offenders():
    """断点比例分桶 + 门禁失败原因 + TOP 榜 + 覆盖统计."""
    from app.services.gate_overview import get_gate_overview

    now = datetime.now(UTC).replace(tzinfo=None)
    loops = [(f"loop-{i}", f"T-{i}") for i in range(5)]
    snap_rows = [
        # 回路0：门禁通过、断点 5%（0-10% 桶）
        _Row(
            "loop-0",
            now,
            "SUCCESS",
            90.0,
            "L4",
            {"gate": {"passed": True, "gapRatio": 0.05, "reason": None}},
            0.99,
        ),
        # 回路1：断点 35% 超门槛（30-50% 桶 + offenders）
        _Row(
            "loop-1",
            now,
            "INCONCLUSIVE",
            None,
            "L0",
            {"gate": {"passed": False, "gapRatio": 0.35, "reason": "断点比例 35% 超过 30% 门槛"}},
            0.65,
        ),
        # 回路2：断点 70%（50%+ 桶 + offenders 榜首）
        _Row(
            "loop-2",
            now,
            "INCONCLUSIVE",
            None,
            "L0",
            {"gate": {"passed": False, "gapRatio": 0.70, "reason": "断点比例 70% 超过 30% 门槛"}},
            0.30,
        ),
        # 回路3：有点数不足类失败（同 35% 断点）
        _Row(
            "loop-3",
            now,
            "INCONCLUSIVE",
            None,
            "L0",
            {
                "gate": {
                    "passed": False,
                    "gapRatio": 0.35,
                    "reason": "有效数据点 3 不足（门槛 32 点）",
                }
            },
            0.65,
        ),
        # 回路4：无快照（不在 rows 里）→ noSnapshot=1
    ]

    db = MagicMock()
    chainable = MagicMock()
    with patch(f"{_SVC}.select", return_value=chainable):
        # 两次 db.execute：①活跃回路清单 ②latest-per-loop 快照
        results = [MagicMock(), MagicMock()]
        results[0].all.return_value = loops
        results[1].all.return_value = snap_rows
        db.execute = AsyncMock(side_effect=results)
        out = await get_gate_overview(db)

    assert out["coverage"]["totalLoops"] == 5
    assert out["coverage"]["withSnapshot"] == 4
    assert out["coverage"]["noSnapshot"] == 1
    assert out["coverage"]["scored"] == 1
    assert out["coverage"]["inconclusive"] == 3

    assert out["gate"]["passed"] == 1
    assert out["gate"]["failed"] == 3
    assert out["gate"]["failReasons"]["断点比例 35% 超过 30% 门槛"] == 1
    assert out["gate"]["failReasons"]["有效数据点 3 不足（门槛 32 点）"] == 1

    buckets = {b["label"]: b["count"] for b in out["gapBuckets"]}
    assert buckets["0-10%"] == 1
    assert buckets["30-50%"] == 2
    assert buckets["50%+"] == 1

    top = out["topOffenders"]
    assert top[0]["loopTagName"] == "T-2" and top[0]["gapRatio"] == 0.70
    assert len(top) == 3

    assert out["threshold"]["maxGapRatio"] == 0.3
