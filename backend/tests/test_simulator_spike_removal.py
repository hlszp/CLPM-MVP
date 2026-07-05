"""Verify simulators no longer produce PV spike glitches.

Tests:
    1. RealtimeAnomalyInjector (realtime_simulator.py) drops spike scheduling
    2. AnomalyInjector (simulate_unit_loops.py) drops spike_events
    3. LoopSimulator long-run PV sequence contains no spike pattern

Spike detection reuses the same dual-neighbour-jump algorithm as
clean_tdengine_spikes.py: a point i is a spike iff both
|pv[i]-pv[i-1]| and |pv[i+1]-pv[i]| exceed spike_threshold.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"


def _load_script_module(module_name: str):
    module_path = SCRIPTS_DIR / f"{module_name}.py"
    if not module_path.exists():
        pytest.skip(f"script {module_path} not found", allow_module_level=True)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        pytest.skip(f"cannot load {module_path}", allow_module_level=True)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


realtime_simulator = _load_script_module("realtime_simulator")
simulate_unit_loops = _load_script_module("simulate_unit_loops")


def _detect_spike_points(pv_values: list[float], spike_threshold: float) -> list[int]:
    """Detect single-point spikes (same algorithm as clean_tdengine_spikes.py)."""
    spikes: list[int] = []
    n = len(pv_values)
    if n < 3:
        return spikes
    for i in range(1, n - 1):
        prev_diff = abs(pv_values[i] - pv_values[i - 1])
        next_diff = abs(pv_values[i + 1] - pv_values[i])
        if prev_diff > spike_threshold and next_diff > spike_threshold:
            spikes.append(i)
    return spikes


class TestRealtimeAnomalyInjectorNoSpike:
    """RealtimeAnomalyInjector must have spike logic fully removed."""

    def test_next_schedule_no_spike_key(self):
        injector = realtime_simulator.RealtimeAnomalyInjector(
            pv_range=100.0, base_sp=50.0, range_min=0.0, range_max=100.0, enabled=True
        )
        assert "spike" not in injector._next_schedule
        assert set(injector._next_schedule.keys()) == {
            "flatline",
            "out_of_range",
            "bad_quality",
        }

    def test_trigger_event_spike_not_supported(self):
        injector = realtime_simulator.RealtimeAnomalyInjector(
            pv_range=100.0, base_sp=50.0, range_min=0.0, range_max=100.0, enabled=True
        )
        initial_active = dict(injector._active)
        injector._trigger_event("spike")
        assert injector._active == initial_active

    def test_apply_no_spike_pattern_long_run(self):
        pv_range = 100.0
        base_sp = 50.0
        spike_threshold = pv_range * 0.5
        injector = realtime_simulator.RealtimeAnomalyInjector(
            pv_range=pv_range,
            base_sp=base_sp,
            range_min=0.0,
            range_max=100.0,
            enabled=True,
        )
        for key in list(injector._next_schedule.keys()):
            injector._next_schedule[key] = 0.0
        pv_values: list[float] = []
        pv = base_sp
        for _ in range(200):
            pv, _q = injector.apply(pv)
            pv_values.append(pv)
        spikes = _detect_spike_points(pv_values, spike_threshold)
        assert spikes == [], (
            f"apply() produced {len(spikes)} spikes at indices {spikes[:10]}"
        )

    def test_apply_out_of_range_does_not_create_spike(self):
        injector = realtime_simulator.RealtimeAnomalyInjector(
            pv_range=100.0, base_sp=50.0, range_min=0.0, range_max=100.0, enabled=True
        )
        injector._next_schedule["out_of_range"] = 0.0
        pv_values: list[float] = []
        pv = 50.0
        for _ in range(30):
            pv, _q = injector.apply(pv)
            pv_values.append(pv)
        assert all(0 <= v <= 200 for v in pv_values)


class TestAnomalyInjectorNoSpike:
    """AnomalyInjector must have spike logic fully removed."""

    def test_no_spike_events_attribute(self):
        cfg = {
            "range_min": 0.0,
            "range_max": 100.0,
            "pv_range": 100.0,
            "base_sp": 50.0,
        }
        injector = simulate_unit_loops.AnomalyInjector(cfg, n_points=1000)
        assert not hasattr(injector, "spike_events")
        assert hasattr(injector, "flatline_events")
        assert hasattr(injector, "bad_clusters")
        assert hasattr(injector, "uncertain_indices")

    def test_apply_no_spike_pattern(self):
        pv_range = 100.0
        base_sp = 50.0
        spike_threshold = pv_range * 0.5
        cfg = {
            "range_min": 0.0,
            "range_max": 100.0,
            "pv_range": pv_range,
            "base_sp": base_sp,
        }
        injector = simulate_unit_loops.AnomalyInjector(cfg, n_points=1000)
        pv_values: list[float] = []
        for idx in range(1000):
            pv, _q = injector.apply(idx, base_sp)
            pv_values.append(pv)
        spikes = _detect_spike_points(pv_values, spike_threshold)
        assert spikes == [], f"apply() produced {len(spikes)} spikes at {spikes[:10]}"

    def test_flatline_value_within_range(self):
        cfg = {
            "range_min": 10.0,
            "range_max": 90.0,
            "pv_range": 80.0,
            "base_sp": 50.0,
        }
        injector = simulate_unit_loops.AnomalyInjector(cfg, n_points=6000)
        for _start, _dur, val in injector.flatline_events:
            assert cfg["range_min"] <= val <= cfg["range_max"]


class TestLoopSimulatorNoSpikeIntegration:
    """Integration test: LoopSimulator long-run PV sequence contains no spike."""

    @pytest.mark.parametrize(
        "control_type,scenario,spike_threshold_pct",
        [
            ("FLOW", "normal", 0.5),
            ("LEVEL", "normal", 0.2),
            ("PRESSURE", "normal", 0.3),
            ("TEMPERATURE", "normal", 0.2),
            ("FLOW", "oscillation", 0.5),
            ("FLOW", "valve_stiction", 0.5),
            ("FLOW", "op_saturation", 0.5),
            ("LEVEL", "manual", 0.2),
        ],
    )
    def test_no_spike_in_generated_timeseries(
        self, control_type: str, scenario: str, spike_threshold_pct: float
    ):
        type_params = simulate_unit_loops.TYPE_PARAMS[control_type]
        type_pid = simulate_unit_loops.TYPE_PID[control_type]
        range_min = 0.0
        range_max = 100.0
        pv_range = range_max - range_min
        cfg = {
            "id": "test-loop-id",
            "tag_name": f"TEST-{control_type}-001",
            "description": f"test {control_type} {scenario}",
            "unit_id": "test-unit-id",
            "unit_name": "test-unit",
            "control_type": control_type,
            "scenario": scenario,
            "tau": type_params["tau"],
            "theta": type_params["theta"],
            "model_type": type_params["model"],
            "noise_pct": type_params["noise_pct"],
            "range_min": range_min,
            "range_max": range_max,
            "pv_range": pv_range,
            "pv_unit": "",
            "base_sp": 50.0,
            "base_pv": 50.0,
            "base_op": 50.0,
            "pid_p": type_pid[0],
            "pid_i": type_pid[1],
            "pid_d": type_pid[2],
        }
        start = datetime(2026, 7, 5, 0, 0, 0)
        end = start + timedelta(hours=1)
        points = simulate_unit_loops.generate_timeseries(cfg, start, end)
        pv_values = [p[1] for p in points]
        assert len(pv_values) >= 3600
        spike_threshold = pv_range * spike_threshold_pct
        spikes = _detect_spike_points(pv_values, spike_threshold)
        assert spikes == [], (
            f"{control_type}/{scenario}: {len(spikes)} spikes at {spikes[:10]}, "
            f"threshold={spike_threshold}"
        )

    def test_detect_algorithm_identifies_known_spike(self):
        """Sanity check: detect algorithm itself works."""
        pv_values = [50.0, 50.0, 95.0, 50.0, 50.0]
        spikes = _detect_spike_points(pv_values, 20.0)
        assert spikes == [2]

    def test_detect_algorithm_no_false_positive_on_constant_pv(self):
        pv_values = [50.0] * 100
        spikes = _detect_spike_points(pv_values, 20.0)
        assert spikes == []


class TestRealtimeInjectorEventBehavior:
    """Verify each event type's PV behavior matches expectations."""

    def test_flatline_event_locks_pv(self):
        injector = realtime_simulator.RealtimeAnomalyInjector(
            pv_range=100.0, base_sp=50.0, range_min=0.0, range_max=100.0, enabled=True
        )
        injector._next_schedule["flatline"] = 0.0
        pv1, _q1 = injector.apply(50.0)
        pv2, _q2 = injector.apply(pv1)
        pv3, _q3 = injector.apply(pv2)
        assert abs(pv1 - pv2) < 1.0
        assert abs(pv2 - pv3) < 1.0

    def test_bad_quality_does_not_change_pv(self):
        injector = realtime_simulator.RealtimeAnomalyInjector(
            pv_range=100.0, base_sp=50.0, range_min=0.0, range_max=100.0, enabled=True
        )
        injector._next_schedule["bad_quality"] = 0.0
        input_pv = 73.5
        pv, q = injector.apply(input_pv)
        assert pv == input_pv
        assert q == 0

    def test_disabled_injector_returns_input(self):
        injector = realtime_simulator.RealtimeAnomalyInjector(
            pv_range=100.0, base_sp=50.0, range_min=0.0, range_max=100.0, enabled=False
        )
        pv, q = injector.apply(73.5)
        assert pv == 73.5
        assert q == 1
