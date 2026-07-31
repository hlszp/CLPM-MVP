"""V62-P3-005 模型版本迁移服务测试.

验证"一次性回填 → 影子读比对 → 切换读取 → 停止旧参数新写"四步迁移策略：

1. ``backfill_model_versions_from_tuning_records``: 为遗留 tuning_record 创建
   process_model_version CANDIDATE 并回填 FK；
2. ``shadow_read_compare``: 双源读取 model_params 比对一致性；
3. ``get_effective_model_params``: 读路径切换——优先版本参数，回退旧字段；
4. ``count_records_without_version``: 回填进度监控。

测试为纯逻辑测试（mock DB），不依赖真实 PostgreSQL；并发一致性的运行时
验证由 ``tests/integration/test_process_model_version_concurrency.py`` 覆盖。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.process_model_migration import (
    _compare_params,
    _extract_theta_source,
    backfill_model_versions_from_tuning_records,
    count_records_without_version,
    get_effective_model_params,
    shadow_read_compare,
)

_DEFAULT_PARAMS = {"K": 1.0, "tau": 10.0, "theta": 2.0}
_SENTINEL = object()


def _make_record(
    *,
    record_id: str | None = None,
    loop_id: str = "loop-1",
    model_type: str = "FOPDT",
    model_params: object = _SENTINEL,
    process_model_version_id: str | None = None,
    identify_method: str | None = "HISTORICAL_ARX",
    confidence_level: str | None = "A",
    confidence_reason: str | None = "拟合通过",
    fitting_score: float | None = 95.5,
    excitation_score: float | None = 0.9,
    residual_test_passed: bool | None = True,
    created_by: str | None = "test",
    time_window_start=None,
    time_window_end=None,
):
    """构造 mock TuningRecord.

    ``model_params`` 使用哨兵区分"未提供"（用默认值）和"显式 None"（空参数）。
    """
    return MagicMock(
        id=record_id or str(uuid4()),
        loop_id=loop_id,
        model_type=model_type,
        model_params=_DEFAULT_PARAMS if model_params is _SENTINEL else model_params,
        process_model_version_id=process_model_version_id,
        identify_method=identify_method,
        confidence_level=confidence_level,
        confidence_reason=confidence_reason,
        fitting_score=fitting_score,
        excitation_score=excitation_score,
        residual_test_passed=residual_test_passed,
        created_by=created_by,
        time_window_start=time_window_start,
        time_window_end=time_window_end,
    )


# ---------------------------------------------------------------------------
# _extract_theta_source
# ---------------------------------------------------------------------------


def test_extract_theta_source_heuristic():
    """从 confidence_reason 提取 HEURISTIC_2TS 标记."""
    assert _extract_theta_source("拟合通过; THETA_SOURCE=HEURISTIC_2TS") == "HEURISTIC_2TS"


def test_extract_theta_source_explicit():
    """从 confidence_reason 提取 EXPLICIT 标记."""
    assert _extract_theta_source("THETA_SOURCE=EXPLICIT; 拟合通过") == "EXPLICIT"


def test_extract_theta_source_none():
    """无 theta_source 标记时返回 None."""
    assert _extract_theta_source("拟合通过") is None
    assert _extract_theta_source(None) is None


# ---------------------------------------------------------------------------
# _compare_params
# ---------------------------------------------------------------------------


def test_compare_params_identical():
    """完全相同的参数应判定一致."""
    params = {"K": 1.0, "tau": 10.0, "theta": 2.0}
    match, mismatch = _compare_params(params, dict(params))
    assert match is True
    assert mismatch == []


def test_compare_params_float_tolerance():
    """浮点数在容差内应判定一致."""
    old = {"K": 1.0, "tau": 10.0}
    new = {"K": 1.0 + 1e-15, "tau": 10.0 - 1e-15}
    match, mismatch = _compare_params(old, new)
    assert match is True


def test_compare_params_mismatch():
    """参数值不一致应报不匹配字段."""
    old = {"K": 1.0, "tau": 10.0}
    new = {"K": 1.5, "tau": 10.0}
    match, mismatch = _compare_params(old, new)
    assert match is False
    assert "K" in mismatch


def test_compare_params_missing_key():
    """一方缺字段应报不匹配."""
    old = {"K": 1.0, "tau": 10.0}
    new = {"K": 1.0}
    match, mismatch = _compare_params(old, new)
    assert match is False
    assert "tau" in mismatch


# ---------------------------------------------------------------------------
# backfill_model_versions_from_tuning_records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_creates_versions_for_legacy_records():
    """回填应为有 model_params 且无版本引用的记录创建 CANDIDATE."""
    record1 = _make_record(model_params={"K": 1.0, "tau": 10.0})
    record2 = _make_record(model_params={"K": 2.0, "tau": 20.0})

    # mock db.execute 第一次返回记录列表，第二次返回空（分批结束）
    def _mock_scalars(records):
        return MagicMock(
            all=MagicMock(return_value=records),
        )

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalars=MagicMock(return_value=_mock_scalars([record1, record2]))),
            MagicMock(scalars=MagicMock(return_value=_mock_scalars([]))),
        ]
    )
    db.flush = AsyncMock()

    mock_version = MagicMock(id="version-new")
    with patch(
        "app.services.process_model_migration.create_candidate_version",
        AsyncMock(return_value=mock_version),
    ):
        result = await backfill_model_versions_from_tuning_records(db)

    assert result["backfilled"] == 2
    assert result["total_scanned"] == 2
    # 两条记录的 FK 都被设置
    assert record1.process_model_version_id == "version-new"
    assert record2.process_model_version_id == "version-new"


@pytest.mark.asyncio
async def test_backfill_skips_records_with_existing_version():
    """已关联版本的记录应跳过（幂等）."""
    # 查询条件是 model_params IS NOT NULL AND process_model_version_id IS NULL
    # 已有版本的记录不会出现在查询结果中
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )
    db.flush = AsyncMock()

    result = await backfill_model_versions_from_tuning_records(db)
    assert result["backfilled"] == 0
    assert result["total_scanned"] == 0


@pytest.mark.asyncio
async def test_backfill_skips_non_dict_model_params():
    """model_params 非 dict（异常数据）应跳过."""
    record_bad = _make_record(model_params="not-a-dict")

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[record_bad])))
            ),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]
    )
    db.flush = AsyncMock()

    with patch(
        "app.services.process_model_migration.create_candidate_version",
        AsyncMock(),
    ) as mock_create:
        result = await backfill_model_versions_from_tuning_records(db)

    assert result["backfilled"] == 0
    assert result["skipped"] == 1
    mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# shadow_read_compare
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_read_compare_match():
    """双源参数一致时 match=True."""
    record = _make_record(
        model_params={"K": 1.0, "tau": 10.0},
        process_model_version_id="version-1",
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value={"K": 1.0, "tau": 10.0}))
    )

    result = await shadow_read_compare(db, record)
    assert result["match"] is True
    assert result["mismatch_keys"] == []
    assert result["version_id"] == "version-1"


@pytest.mark.asyncio
async def test_shadow_read_compare_mismatch():
    """双源参数不一致时 match=False 且报不匹配字段."""
    record = _make_record(
        model_params={"K": 1.0, "tau": 10.0},
        process_model_version_id="version-1",
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value={"K": 2.0, "tau": 10.0}))
    )

    result = await shadow_read_compare(db, record)
    assert result["match"] is False
    assert "K" in result["mismatch_keys"]


@pytest.mark.asyncio
async def test_shadow_read_compare_no_version():
    """无版本引用时 old_params 有值、new_params 为 None，match=False."""
    record = _make_record(
        model_params={"K": 1.0},
        process_model_version_id=None,
    )
    db = AsyncMock()

    result = await shadow_read_compare(db, record)
    assert result["match"] is False
    assert result["new_params"] is None
    assert result["old_params"] == {"K": 1.0}


# ---------------------------------------------------------------------------
# get_effective_model_params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_effective_params_prefers_version():
    """有版本引用时应从 process_model_version 读取."""
    record = _make_record(
        model_params={"K": 1.0, "tau": 10.0},  # 旧字段
        process_model_version_id="version-1",
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(
                return_value={"K": 1.1, "tau": 10.1}  # 版本中的新值
            )
        )
    )

    params = await get_effective_model_params(db, record)
    assert params == {"K": 1.1, "tau": 10.1}


@pytest.mark.asyncio
async def test_get_effective_params_falls_back_to_record():
    """无版本引用时应回退到 tuning_record.model_params."""
    record = _make_record(
        model_params={"K": 1.0, "tau": 10.0},
        process_model_version_id=None,
    )
    db = AsyncMock()

    params = await get_effective_model_params(db, record)
    assert params == {"K": 1.0, "tau": 10.0}


@pytest.mark.asyncio
async def test_get_effective_params_falls_back_when_version_deleted():
    """版本被删除（返回 None）时应回退到旧字段并告警."""
    record = _make_record(
        model_params={"K": 1.0},
        process_model_version_id="deleted-version",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    params = await get_effective_model_params(db, record)
    assert params == {"K": 1.0}


@pytest.mark.asyncio
async def test_get_effective_params_returns_none_for_empty_record():
    """无版本且无旧参数时返回 None."""
    record = _make_record(model_params=None, process_model_version_id=None)
    db = AsyncMock()

    params = await get_effective_model_params(db, record)
    assert params is None


# ---------------------------------------------------------------------------
# count_records_without_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_records_without_version():
    """统计未关联版本的遗留记录数."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=42)))

    count = await count_records_without_version(db)
    assert count == 42
