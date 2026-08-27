"""指标算法参数配置服务测试（P0-B A7）.

覆盖三层配置合并链（算法默认 → algorithm_parameter 表 → metric_config.threshold）、
进程内缓存读写、预载、API 合并视图生成。

设计依据：app/services/algorithm_config.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import algorithm_config as ac

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_cache():
    """每个测试前后清空进程内缓存，避免相互污染。"""
    saved = dict(ac._merged_cache)
    ac._merged_cache = {}
    yield
    ac._merged_cache = saved


def _make_db_with_rows(table_rows: list, threshold_rows: list | None = None) -> AsyncMock:
    """构造 mock AsyncSession，分别响应 load_stored_config / load_metric_thresholds 查询。

    Args:
        table_rows: AlgorithmParameter 行列表（对象，含 metric_code/control_type/params 属性）
        threshold_rows: Row-like 对象列表（含 .metric_code / .threshold 属性）
    """
    db = AsyncMock()

    table_result = MagicMock()
    table_result.scalars.return_value.all.return_value = table_rows
    threshold_result = MagicMock()
    threshold_result.all.return_value = threshold_rows or []

    # load_stored_config 先执行；load_metric_thresholds 后执行
    db.execute = AsyncMock(side_effect=[table_result, threshold_result])
    return db


def _make_threshold_row(metric_code: str, threshold: dict) -> MagicMock:
    """构造 metric_config 查询返回的 Row-like 对象（对齐 select(col, col) 属性访问）。"""
    row = MagicMock()
    row.metric_code = metric_code
    row.threshold = threshold
    return row


def _make_param_row(metric_code: str, control_type: str, params: dict) -> MagicMock:
    row = MagicMock()
    row.metric_code = metric_code
    row.control_type = control_type
    row.params = params
    return row


# ---------------------------------------------------------------------------
# get_default_params / get_algorithm_params
# ---------------------------------------------------------------------------


def test_get_default_params_returns_known_defaults(reset_cache):
    defaults = ac.get_default_params("oscillation_rate", "STABLE")
    assert defaults == {
        "similarity_threshold": 0.4,
        "min_ratio": 0.05,
        "max_ratio": 15.0,
        "min_zero_crossings": 4,
        "min_half_period_samples": 8,
        "min_amplitude_ratio": 0.002,
        "sp_step_exclusion_enabled": False,
        "sp_step_sigma": 3.0,
        "sp_tracking_window": 60,
    }


def test_get_default_params_unknown_returns_empty(reset_cache):
    assert ac.get_default_params("unknown_metric", "STABLE") == {}
    assert ac.get_default_params("oscillation_rate", "UNKNOWN") == {}


def test_get_algorithm_params_empty_cache_returns_empty(reset_cache):
    """缓存未预载时返回空字典（计算器回落各自硬编码默认值）。"""
    assert ac.get_algorithm_params("oscillation_rate", "STABLE") == {}


def test_get_algorithm_params_unknown_control_type_falls_back_stable(reset_cache):
    """未知/None 控制类型回落 STABLE。"""
    ac.apply_runtime({})  # 仅用算法默认值填充缓存
    stable = ac.get_algorithm_params("oscillation_rate", "STABLE")
    none_ct = ac.get_algorithm_params("oscillation_rate", None)
    unknown_ct = ac.get_algorithm_params("oscillation_rate", "BOGUS")
    assert none_ct == stable
    assert unknown_ct == stable
    assert stable == {
        "similarity_threshold": 0.4,
        "min_ratio": 0.05,
        "max_ratio": 15.0,
        "min_zero_crossings": 4,
        "min_half_period_samples": 8,
        "min_amplitude_ratio": 0.002,
        "sp_step_exclusion_enabled": False,
        "sp_step_sigma": 3.0,
        "sp_tracking_window": 60,
    }


def test_get_algorithm_params_returns_copy_not_reference(reset_cache):
    ac.apply_runtime({})
    params = ac.get_algorithm_params("oscillation_rate", "STABLE")
    params["similarity_threshold"] = 999
    # 修改返回值不应影响缓存
    again = ac.get_algorithm_params("oscillation_rate", "STABLE")
    assert again["similarity_threshold"] == 0.4


# ---------------------------------------------------------------------------
# _rebuild_merged 三层合并
# ---------------------------------------------------------------------------


def test_rebuild_merged_defaults_only(reset_cache):
    merged = ac._rebuild_merged({}, {})
    assert merged[("oscillation_rate", "STABLE")] == {
        "similarity_threshold": 0.4,
        "min_ratio": 0.05,
        "max_ratio": 15.0,
        "min_zero_crossings": 4,
        "min_half_period_samples": 8,
        "min_amplitude_ratio": 0.002,
        "sp_step_exclusion_enabled": False,
        "sp_step_sigma": 3.0,
        "sp_tracking_window": 60,
    }
    # 7 指标 × 4 控制类型 = 28 组合（2026-08-27 新增 stability_rate 配置化）
    assert len(merged) == 28


def test_rebuild_merged_table_override(reset_cache):
    """Layer 2: algorithm_parameter 表覆盖默认值。"""
    table_overrides = {"oscillation_rate": {"STABLE": {"similarity_threshold": 0.6}}}
    merged = ac._rebuild_merged(table_overrides, {})
    assert merged[("oscillation_rate", "STABLE")]["similarity_threshold"] == 0.6
    # 未覆盖的键保持默认
    assert merged[("oscillation_rate", "STABLE")]["min_ratio"] == 0.05
    # 其他控制类型不受影响
    assert merged[("oscillation_rate", "FAST")]["similarity_threshold"] == 0.4


def test_rebuild_merged_metric_threshold_override(reset_cache):
    """Layer 3: metric_config.threshold 指标级覆盖（不区分控制类型）。"""
    metric_thresholds = {"fast_rate": {"settling_tolerance": 0.05}}
    merged = ac._rebuild_merged({}, metric_thresholds)
    for ct in ("STABLE", "SLOW", "FAST", "LOGIC"):
        assert merged[("fast_rate", ct)]["settling_tolerance"] == 0.05
        assert merged[("fast_rate", ct)]["ideal_settling_ratio"] == 1.0


def test_rebuild_merged_layer_precedence(reset_cache):
    """Layer 3 优先级 > Layer 2 > Layer 1。"""
    table_overrides = {"accuracy_rate": {"STABLE": {"e_max_percentile": 90}}}
    metric_thresholds = {"accuracy_rate": {"e_max_percentile": 80}}
    merged = ac._rebuild_merged(table_overrides, metric_thresholds)
    assert merged[("accuracy_rate", "STABLE")]["e_max_percentile"] == 80


# ---------------------------------------------------------------------------
# apply_runtime / build_merged_view
# ---------------------------------------------------------------------------


def test_apply_runtime_refreshes_cache(reset_cache):
    ac.apply_runtime({})
    assert ac.get_algorithm_params("fast_rate", "STABLE") == {
        "ideal_settling_ratio": 1.0,
        "settling_tolerance": 0.0,
        "anti_disturbance_enabled": False,
        "disturbance_band_sigma": 2.0,
        "recovery_persistence": 5,
        "min_disturbance_duration": 3.0,
        "sp_step_sigma": 3.0,
    }
    ac.apply_runtime({"fast_rate": {"STABLE": {"settling_tolerance": 0.1}}})
    assert ac.get_algorithm_params("fast_rate", "STABLE")["settling_tolerance"] == 0.1


def test_fast_rate_anti_disturbance_defaults(reset_cache):
    """P2: fast_rate 4 控制类型均含抗扰参数且默认关闭（零回归）。"""
    ac.apply_runtime({})
    for ct in ("STABLE", "SLOW", "FAST", "LOGIC"):
        params = ac.get_algorithm_params("fast_rate", ct)
        assert params["anti_disturbance_enabled"] is False
        assert params["disturbance_band_sigma"] == 2.0
        assert params["recovery_persistence"] == 5
        assert params["min_disturbance_duration"] == 3.0
        assert params["sp_step_sigma"] == 3.0


def test_build_merged_view_marks_overridden(reset_cache):
    ac.apply_runtime({"oscillation_rate": {"STABLE": {"similarity_threshold": 0.7}}})
    view = ac.build_merged_view()

    stable = view["oscillation_rate"]["STABLE"]
    assert stable["overridden"] is True
    assert stable["params"]["similarity_threshold"] == 0.7
    assert stable["defaults"]["similarity_threshold"] == 0.4

    fast = view["oscillation_rate"]["FAST"]
    assert fast["overridden"] is False
    assert fast["params"] == fast["defaults"]


def test_build_merged_view_covers_all_metrics(reset_cache):
    ac.apply_runtime({})
    view = ac.build_merged_view()
    assert set(view.keys()) == {
        "oscillation_rate",
        "fast_rate",
        "accuracy_rate",
        "settling_time",
        "effective_auto_rate",
        "output_trip_index",
        "stability_rate",
    }
    for metric in view.values():
        assert set(metric.keys()) == {"STABLE", "SLOW", "FAST", "LOGIC"}


# ---------------------------------------------------------------------------
# preload_algorithm_params（DB 加载）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preload_algorithm_params_loads_from_db(reset_cache):
    table_rows = [
        _make_param_row("oscillation_rate", "STABLE", {"similarity_threshold": 0.55}),
        _make_param_row("fast_rate", "FAST", {"settling_tolerance": 0.03}),
    ]
    threshold_rows = [_make_threshold_row("accuracy_rate", {"e_max_percentile": 95})]
    db = _make_db_with_rows(table_rows, threshold_rows)

    await ac.preload_algorithm_params(db)

    # table override 生效
    assert ac.get_algorithm_params("oscillation_rate", "STABLE")["similarity_threshold"] == 0.55
    # 未覆盖的控制类型保持默认
    assert ac.get_algorithm_params("oscillation_rate", "FAST")["similarity_threshold"] == 0.4
    # metric threshold 覆盖（不区分控制类型）
    assert ac.get_algorithm_params("accuracy_rate", "STABLE")["e_max_percentile"] == 95
    assert ac.get_algorithm_params("accuracy_rate", "LOGIC")["e_max_percentile"] == 95
    # fast_rate FAST 同时受 table + 默认 ideal_settling_ratio
    fast_params = ac.get_algorithm_params("fast_rate", "FAST")
    assert fast_params["settling_tolerance"] == 0.03
    assert fast_params["ideal_settling_ratio"] == 1.0


@pytest.mark.asyncio
async def test_preload_algorithm_params_empty_db_uses_defaults(reset_cache):
    db = _make_db_with_rows([], [])
    await ac.preload_algorithm_params(db)
    assert ac.get_algorithm_params("oscillation_rate", "STABLE") == {
        "similarity_threshold": 0.4,
        "min_ratio": 0.05,
        "max_ratio": 15.0,
        "min_zero_crossings": 4,
        "min_half_period_samples": 8,
        "min_amplitude_ratio": 0.002,
        "sp_step_exclusion_enabled": False,
        "sp_step_sigma": 3.0,
        "sp_tracking_window": 60,
    }
