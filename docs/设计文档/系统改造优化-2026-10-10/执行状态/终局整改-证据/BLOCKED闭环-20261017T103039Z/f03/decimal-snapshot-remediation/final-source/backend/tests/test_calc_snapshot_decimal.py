"""Numeric 权重的输入包边界：生产者 → JSON 数值 → hash/重放一致。"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models.calc_dataset_snapshot import KIND_KPI_GRID
from app.models.loop_config import LoopTypeWeight
from app.services import calc_snapshot as cs
from app.services.loop_config import get_loop_type_weights_map
from app.services.result_ledger import canonical_value
from tests.test_calc_snapshot import (
    _TS0,
    _TS1,
    _exec_result,
    _kpi_manifest_extra,
    _LoadDB,
    _make_kpi_bundles,
    _record_stub,
    _SaveDB,
)
from tests.test_calc_snapshot import (
    fixed_build as fixed_build,
)
from tests.test_calc_snapshot import (
    snap_dir as snap_dir,
)


async def _producer_weights():
    # 原始独立测试库 input-policy-before 的四组 Numeric(3,2) 配置。
    rows = [
        LoopTypeWeight(
            loop_type=kind,
            weight_a=Decimal(a),
            weight_f=Decimal(f),
            weight_s=Decimal(s),
        )
        for kind, a, f, s in (
            ("STABLE", "0.20", "0.30", "0.50"),
            ("SLOW", "0.30", "0.10", "0.60"),
            ("FAST", "0.20", "0.50", "0.30"),
            ("LOGIC", "0.00", "0.50", "0.60"),
        )
    ]
    db = MagicMock()
    db.execute = AsyncMock(return_value=_exec_result(scalars_list=rows))
    weights = await get_loop_type_weights_map(db)
    assert all(isinstance(v, Decimal) for w in weights.values() for v in w.values())
    return weights


async def test_decimal_weight_producer_round_trip(snap_dir, fixed_build):
    """真实 ORM/生产者 Decimal 全字典落盘、读回及既有 KPI 重放；无外部 IO。"""
    from app.contracts.data_types import ControlType
    from app.tasks.kpi_calc import (
        _build_config_bundle,
        _build_weights_map,
        _compute_kpis_three_layer,
        _extract_metrics_detail,
    )

    type_weights = await _producer_weights()
    weights = _build_weights_map(type_weights, "STABLE")
    bundles = _make_kpi_bundles()
    detail = _extract_metrics_detail(
        _compute_kpis_three_layer(
            bundles, _build_config_bundle("loop-1", ControlType.TEMPERATURE, None), weights
        )[0]
    )
    arrays, blocks = cs.serialize_data_blocks(bundles)
    extra = _kpi_manifest_extra(
        blocks=blocks, weights=weights, effective_params={"typeWeights": type_weights}
    )
    row = await _SaveDB().save(
        kind=KIND_KPI_GRID,
        ts_start=_TS0,
        ts_end=_TS1,
        dataset_ref="test:decimal:weights",
        arrays=arrays,
        manifest_extra=extra,
    )
    loaded = await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
    assert loaded.manifest["replayContext"] == canonical_value(extra["replayContext"])
    expected = canonical_value(extra["source"]["effectiveParams"])
    assert loaded.manifest["source"]["effectiveParams"] == expected
    actual_weights = loaded.manifest["source"]["effectiveParams"]["typeWeights"]
    assert all(type(v) is float for w in actual_weights.values() for v in w.values())
    assert cs.compute_input_hash(loaded.manifest, loaded.arrays) == row.input_hash
    assert (
        cs.compute_input_hash(
            {
                **loaded.manifest,
                "source": {
                    **loaded.manifest["source"],
                    "effectiveParams": {
                        "typeWeights": type_weights,
                        "fitness": {},
                    },
                },
            },
            loaded.arrays,
        )
        == row.input_hash
    )
    # 规范化不得就地改写调用方仍在消费的权重配置。
    assert all(isinstance(v, Decimal) for w in type_weights.values() for v in w.values())

    with (
        patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=_record_stub("rec-decimal", str(row.id), detail)),
        ),
        patch(
            "app.tasks.kpi_calc._build_data_planner",
            side_effect=AssertionError("重放不得查询当前源"),
        ),
        patch(
            "app.tasks.kpi_calc._compute_kpis_three_layer", wraps=_compute_kpis_three_layer
        ) as run,
    ):
        report = await cs.replay_result_record(_LoadDB(row)._db, "rec-decimal")
    assert run.call_args.args[2] == weights
    assert report["identical"] is True
    assert report["replayedMetrics"] == detail


def test_decimal_numbers_share_json_numeric_identity():
    arrays = {"sig": np.array([0.0, 1.0])}
    decimal_manifest = {"nested": [{"weight": Decimal("0.20")}], "zero": Decimal("-0.00")}
    numbers = {"nested": [{"weight": 0.2}], "zero": -0.0}
    assert cs.compute_input_hash(decimal_manifest, arrays) == cs.compute_input_hash(numbers, arrays)
    assert cs.compute_input_hash(decimal_manifest, arrays) != cs.compute_input_hash(
        {"nested": [{"weight": "0.20"}], "zero": "-0.00"}, arrays
    )
    assert cs.compute_input_hash(decimal_manifest, arrays) != cs.compute_input_hash(
        {"nested": [{"weight": Decimal("0.21")}], "zero": Decimal("-0.00")}, arrays
    )


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "1e9999"])
async def test_invalid_decimal_rejected_before_io(snap_dir, fixed_build, value):
    manifest = _kpi_manifest_extra(effective_params={"thresholds": [Decimal(value)]})
    db = MagicMock()
    db.execute = AsyncMock()
    with pytest.raises(ValueError, match="finite"):
        cs.compute_input_hash(manifest, {})
    with pytest.raises(ValueError, match="finite"):
        await cs.save_calculation_snapshot(
            db,
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:invalid:decimal",
            arrays={"sig": np.ones(2)},
            manifest_extra=manifest,
        )
    db.execute.assert_not_called()
    assert not cs.snapshot_root().exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), object()])
async def test_json_validation_precedes_npz(snap_dir, fixed_build, value):
    db = MagicMock()
    db.execute = AsyncMock()
    with pytest.raises((ValueError, TypeError)):
        await cs.save_calculation_snapshot(
            db,
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:invalid:manifest",
            arrays={"sig": np.ones(2)},
            manifest_extra=_kpi_manifest_extra(extra=value),
        )
    db.execute.assert_not_called()
    assert not cs.snapshot_root().exists()


def test_existing_json_native_canonical_identity():
    """修前冻结的合法 JSON 原生输入字节/hash（包含 -0、Unicode、大整数）。"""
    manifest = {
        "a": [None, True, False, 0, 0.0, -0.0, 1e-100, 9007199254740993, "中文"],
        "b": {"x": [1, 2], "empty": {}},
    }
    assert cs._canonical_json(manifest) == (
        '{"a":[null,true,false,0,0.0,-0.0,1e-100,9007199254740993,"中文"],'
        '"b":{"empty":{},"x":[1,2]}}'
    )
    assert cs.compute_input_hash(manifest, {}) == (
        "bea616322bfb04e14073acacc0cd30106fecb4b9f75a5e886d84df6e8a8676b1"
    )
