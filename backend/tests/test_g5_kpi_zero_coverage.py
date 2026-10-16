"""A computed zero coverage must not fall back to the retained numeric values."""

from types import SimpleNamespace

from app.services.preprocessing.pipeline import PREPROCESS_VERSION
from app.tasks.kpi_calc import _compute_loop_valid_rate_from_bundles


def test_computed_zero_preserves_zero_but_legacy_default_still_falls_back():
    def bundle(version):
        block = SimpleNamespace(
            tag_group="BASE",
            loop_valid_rate=0.0,
            preprocess_version=version,
            validity={"pv_valid": [True] * 10},
            point_count=10,
        )
        return SimpleNamespace(data_block=block)

    assert _compute_loop_valid_rate_from_bundles([bundle(PREPROCESS_VERSION)]) == 0.0
    assert _compute_loop_valid_rate_from_bundles([bundle("pre_v1")]) == 1.0
