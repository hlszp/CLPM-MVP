"""G2 增量批（C12b 两缺口）专项测试：G2-INC-a 完整旧参数固定 + G2-INC-b 真实旧构建执行.

G2-INC-a（11§2"KPI 完整旧参数固定"）：
- 任务边界 pin 完整五层快照（非仅 revision 号）；算子（fast_rate/stability 及
  同模式 9 文件）消费任务固定快照，不读当前缓存；
- 运行中任务不换参（发布 v2 只改当前缓存，在跑任务恒 v1）；新任务取 v2；
  旧包重放取包内 v1 有效值——两组互不污染（INT-01 前半语义）；
- 输入包 manifest.source.effectiveParams 携带五层展开值+逐参来源+作用域，
  参数变化改变包身份（inputHash）。

G2-INC-b（11§2"真实旧构建执行"）：
- 构造两套**真实受控构建**（backend 源码树副本 + 有意公式差分补丁 + 独立
  构建身份 env），对同一旧包按其构建子进程执行，数值级复现 v1；
- 缺执行配方（只在 digests 登记、无 builds 条目）/构建路径失效 → 显式拒绝
  ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE（登记 digest ≠ 执行能力）；
- 门禁层 digests ∪ builds 键收口（有可执行配方视为已保留）。

真实链说明：受控构建测试用**真实子进程**跑真实代码副本（非 mock）；
构建内 CLI 自报 digest（digest 公式单源）。真实 PG 集成链见
tests/integration/test_g2_increment_real_pg.py（验收时显式运行）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.core.config import settings
from app.core.exceptions import BizError
from app.models.calc_dataset_snapshot import KIND_KPI_GRID
from app.services import calc_snapshot as cs
from app.services import controlled_build as cb
from tests.test_calc_snapshot import (
    _TS0,
    _TS1,
    _c12b_id_signals,
    _kpi_manifest_extra,
    _LoadDB,
    _make_kpi_bundles,
    _record_stub,
    _SaveDB,
)

# ---------------------------------------------------------------------------
# 本文件夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def snap_dir(tmp_path, monkeypatch):
    """输入包根目录指向测试临时目录（与 test_calc_snapshot 同款）."""
    monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", str(tmp_path / "g2-snaps"))
    return settings.CLPM_CALC_SNAPSHOT_DIR


@contextmanager
def _metric_threshold_override(metric_code: str, params: dict):
    """临时把 metric_config.threshold 层覆盖为指定参数（模拟发布 v2 改当前缓存）."""
    from app.services import algorithm_config as ac

    saved = dict(ac._merged_cache)
    ac._merged_cache = ac._rebuild_merged({}, {metric_code: dict(params)})
    try:
        yield
    finally:
        ac._merged_cache = saved


def _full_default_params() -> dict[str, dict[str, dict]]:
    """注册表全量默认参数（构造 pin 快照 params 节用）."""
    from app.services import algorithm_config as ac

    return {f"{m}|{ct}": dict(p) for m, cts in ac._DEFAULTS.items() for ct, p in cts.items()}


def _pinned_snapshot(overrides: dict | None = None, revision: int = 7) -> dict:
    """构造 pin_config_snapshot 形态的完整快照（DEFAULT 合并值 + 覆盖层）."""
    return {
        "schemaVersion": "1",
        "configRevision": revision,
        "pinnedAt": "2026-10-11T00:00:00",
        "params": _full_default_params(),
        "overrides": overrides or {},
        "hostname": "test",
        "pid": 1,
    }


def _g2_kpi_bundles():
    """KPI bundle 集 + steady_rate（Layer2 稳定率可计算；decay 参数敏感）."""
    from app.contracts.data_types import DataLineage, MetricDataBundle

    base = _make_kpi_bundles()
    block = base[0].data_block
    return base + [
        MetricDataBundle(
            metric_code="steady_rate",
            data_block=block,
            mask_expression="pv_valid",
            masked_indices=list(range(block.point_count)),
            lineage=DataLineage(sampling_freq="1s", tag_group="BASE"),
        )
    ]


# ---------------------------------------------------------------------------
# I. G2-INC-a：算子消费任务固定快照（不读当前缓存；运行中不换参）
# ---------------------------------------------------------------------------


class TestG2IncParamPinning:
    def test_effective_params_pinned_overrides_cache(self):
        """注入固定参数后算子入口只消费固定值（同值改缓存无影响）."""
        from app.services.metric_calculator.stability import StabilityRateCalculator

        bundle = _make_kpi_bundles(10)[0]
        calc = StabilityRateCalculator().with_pinned_params(
            {("stability_rate", "STABLE"): {"decay_ratio": 0.1, "band_ratio": 0.01}}
        )
        with _metric_threshold_override("stability_rate", {"decay_ratio": 0.2}):
            assert calc._effective_params("stability_rate", bundle) == {
                "decay_ratio": 0.1,
                "band_ratio": 0.01,
            }

    def test_effective_params_missing_entry_falls_to_registry_defaults(self):
        """固定表未收录的指标按注册表默认值（同样不读当前缓存）."""
        from app.services import algorithm_config as ac
        from app.services.metric_calculator.stability import StabilityRateCalculator

        bundle = _make_kpi_bundles(10)[0]
        calc = StabilityRateCalculator().with_pinned_params({})
        with _metric_threshold_override("stability_rate", {"decay_ratio": 0.2}):
            assert calc._effective_params("stability_rate", bundle) == dict(
                ac._DEFAULTS["stability_rate"]["STABLE"]
            )

    def test_unpinned_calculator_reads_current_cache(self):
        """未固定（None）→ 既有配置链缓存口径不变（回填/单回路路径）."""
        from app.services.metric_calculator.stability import StabilityRateCalculator

        bundle = _make_kpi_bundles(10)[0]
        calc = StabilityRateCalculator()
        with _metric_threshold_override("stability_rate", {"decay_ratio": 0.2}):
            params = calc._effective_params("stability_rate", bundle)
        assert params["decay_ratio"] == 0.2  # 阈值层覆盖生效（其余键为注册表默认）

    async def test_running_task_not_switching_params_on_publish_v2(self):
        """核验 Q1/Q4：任务固定 v1 后发布 v2（当前缓存改）——在跑任务恒 v1，
        新任务（不 pin 或重新 pin）取 v2；两组互不污染."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _g2_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        pinned_v1 = {
            ("stability_rate", "STABLE"): dict(
                _full_default_params()["stability_rate|STABLE"], decay_ratio=0.05
            )
        }

        r_task = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None, pinned_params=pinned_v1)[0]
        )

        with _metric_threshold_override("stability_rate", {"decay_ratio": 0.2}):
            # 在跑任务：同一固定 map 重算 → 结果不变（不换参）
            r_task_again = _extract_metrics_detail(
                _compute_kpis_three_layer(bundles, config_bundle, None, pinned_params=pinned_v1)[0]
            )
            # 新任务：不 pin → 读当前缓存（v2 生效）
            r_new_task = _extract_metrics_detail(
                _compute_kpis_three_layer(bundles, config_bundle, None)[0]
            )

        assert r_task == r_task_again, "运行中任务不得换参"
        v1 = r_task["steady_rate"]["value"]
        v2 = r_new_task["steady_rate"]["value"]
        assert v1 is not None and v2 is not None and v1 != v2, "decay 0.05 vs 0.2 应产生不同稳定率"

    def test_resolve_loop_params_map_five_layer_shadowing(self):
        """五层解析：DEFAULT < NODE < LOOP 遮盖关系与逐参来源正确."""
        from app.services.config_publish import resolve_loop_params_map

        snapshot = _pinned_snapshot(
            overrides={
                "NODE|node-1|stability_rate": {"decay_ratio": 0.07},
                "LOOP|loop-1|stability_rate": {"decay_ratio": 0.09},
            }
        )
        both = resolve_loop_params_map(snapshot, node_id="node-1", loop_id="loop-1")
        assert both["params"][("stability_rate", "STABLE")]["decay_ratio"] == 0.09
        assert both["sources"][("stability_rate", "STABLE")]["decay_ratio"] == "LOOP"
        # band_ratio 未被覆盖 → DEFAULT 来源
        assert both["sources"][("stability_rate", "STABLE")]["band_ratio"] == "DEFAULT"

        only_node = resolve_loop_params_map(snapshot, node_id="node-1", loop_id="loop-2")
        assert only_node["params"][("stability_rate", "STABLE")]["decay_ratio"] == 0.07
        assert only_node["sources"][("stability_rate", "STABLE")]["decay_ratio"] == "NODE"

        no_scope = resolve_loop_params_map(snapshot)
        assert no_scope["params"][("stability_rate", "STABLE")]["decay_ratio"] == 0.05
        assert no_scope["sources"][("stability_rate", "STABLE")]["decay_ratio"] == "DEFAULT"

    async def test_pin_task_config_wrap_and_fallback(self):
        """_pin_task_config 复用 config_publish.pin_config_snapshot；失败回落 None."""
        from app.tasks.kpi_calc import _pin_task_config

        snapshot = _pinned_snapshot(revision=9)
        with patch(
            "app.services.config_publish.pin_config_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            assert await _pin_task_config(MagicMock()) is snapshot

        async def _boom(_db):
            raise RuntimeError("db down")

        with patch("app.services.config_publish.pin_config_snapshot", new=_boom):
            assert await _pin_task_config(MagicMock()) is None


# ---------------------------------------------------------------------------
# II. G2-INC-a：输入包 effectiveParams 完整化（经 _calculate_loop_kpi 真实链）
# ---------------------------------------------------------------------------


def _loop_stub():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000201",
        tag_name="101-FC-1023",
        loop_type="FLOW",
        unit_id="00000000-0000-0000-0000-000000000111",
        ideal_settling_time=None,
        engineering_template_id=None,
    )


def _mock_db():
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        )
    )
    db.add = MagicMock()
    return db


class TestG2IncManifestEffectiveParams:
    async def test_manifest_carries_full_five_layer_params(self, snap_dir):
        """落包 effectiveParams：五层展开值+来源+作用域+PINNED；算子消费固定值."""
        from app.tasks.kpi_calc import _calculate_loop_kpi

        bundles = _make_kpi_bundles()
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        captured: dict = {}
        persist_mock = AsyncMock(return_value={"status": "SUCCESS"})

        async def _fake_save(_db, **kwargs):
            captured.update(kwargs["manifest_extra"])
            return SimpleNamespace(id="snap-1", input_hash="h1")

        snapshot = _pinned_snapshot(
            overrides={
                "NODE|00000000-0000-0000-0000-000000000111|stability_rate": {"decay_ratio": 0.09}
            }
        )
        planner = SimpleNamespace(request_bundles=AsyncMock(return_value=bundles))
        with (
            patch("app.tasks.kpi_calc._persist_snapshot", new=persist_mock),
            patch("app.services.calc_snapshot.save_calculation_snapshot", new=_fake_save),
        ):
            result = await _calculate_loop_kpi(
                db=_mock_db(),
                loop=_loop_stub(),
                metric_configs={},
                ts_start=_TS0,
                ts_end=_TS1,
                data_planner=planner,
                save_input_snapshot=True,
                pinned_config=snapshot,
            )
        assert result["status"] == "SUCCESS"
        eff = captured["source"]["effectiveParams"]
        assert eff["typeWeights"] == {} and "fitness" in eff  # 1.1 既有键保留
        assert eff["status"] == "PINNED"
        assert eff["configRevision"] == 7
        assert eff["scope"]["nodeId"] == "00000000-0000-0000-0000-000000000111"
        assert eff["scope"]["loopId"] == _loop_stub().id
        assert eff["params"]["stability_rate|STABLE"]["decay_ratio"] == 0.09
        assert eff["sources"]["stability_rate|STABLE"]["decay_ratio"] == "NODE"
        # 注入算子的固定值生效：metrics_detail 的稳定率与固定值口径一致
        # （NODE 0.09 不同于默认 0.05——由下例身份测试与重放测试交叉证明）

    async def test_manifest_unpinned_status_when_pin_failed(self, snap_dir):
        """pin 失败（pinned_config=None）落包 → status=UNPINNED，params 显式 null."""
        from app.tasks.kpi_calc import _calculate_loop_kpi

        bundles = _make_kpi_bundles()
        captured: dict = {}

        async def _fake_save(_db, **kwargs):
            captured.update(kwargs["manifest_extra"])
            return SimpleNamespace(id="snap-2", input_hash="h2")

        planner = SimpleNamespace(request_bundles=AsyncMock(return_value=bundles))
        with (
            patch(
                "app.tasks.kpi_calc._persist_snapshot",
                new=AsyncMock(return_value={"status": "SUCCESS"}),
            ),
            patch("app.services.calc_snapshot.save_calculation_snapshot", new=_fake_save),
        ):
            await _calculate_loop_kpi(
                db=_mock_db(),
                loop=_loop_stub(),
                metric_configs={},
                ts_start=_TS0,
                ts_end=_TS1,
                data_planner=planner,
                save_input_snapshot=True,
                pinned_config=None,
            )
        eff = captured["source"]["effectiveParams"]
        assert eff["status"] == "UNPINNED"
        assert eff["params"] is None and eff["sources"] is None

    async def test_params_change_changes_package_identity(self, snap_dir):
        """同数组不同五层有效参数 → inputHash 不同（参数属实际计算上下文身份）."""
        arrs = {"sig": np.linspace(0.0, 9.0, 10)}
        p1 = _pinned_snapshot()
        p2 = _pinned_snapshot(overrides={"LOOP|loop-x|stability_rate": {"decay_ratio": 0.2}})

        def _eff(snapshot):
            from app.services.config_publish import resolve_loop_params_map

            resolved = resolve_loop_params_map(snapshot, loop_id="loop-x")
            return {
                "status": "PINNED",
                "configRevision": snapshot["configRevision"],
                "scope": {"templateKey": None, "nodeId": None, "loopId": "loop-x"},
                "params": {f"{m}|{ct}": p for (m, ct), p in sorted(resolved["params"].items())},
                "sources": {f"{m}|{ct}": s for (m, ct), s in sorted(resolved["sources"].items())},
            }

        row1 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:g2:ident",
            arrays=arrs,
            manifest_extra=_kpi_manifest_extra(effective_params=_eff(p1)),
        )
        row2 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:g2:ident",
            arrays=arrs,
            manifest_extra=_kpi_manifest_extra(effective_params=_eff(p2)),
        )
        assert row1.input_hash != row2.input_hash


# ---------------------------------------------------------------------------
# III. G2-INC-a：重放消费包内参数（旧包重放取 v1，不读当前配置）
# ---------------------------------------------------------------------------


class TestG2IncReplayParams:
    async def test_replay_uses_package_params_not_current_cache(self, snap_dir):
        """包内 effectiveParams.params=v1（decay 0.10）→ 当前缓存改 0.2 后重放
        仍按 v1 固定计算，identical=True，paramsSource=package."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _g2_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        defaults = _full_default_params()
        pinned_v1 = {
            ("stability_rate", "STABLE"): dict(defaults["stability_rate|STABLE"], decay_ratio=0.10)
        }
        v1 = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None, pinned_params=pinned_v1)[0]
        )
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        manifest_extra = _kpi_manifest_extra(
            blocks=blocks_meta,
            effective_params={
                "status": "PINNED",
                "configRevision": 3,
                "scope": {"templateKey": None, "nodeId": None, "loopId": "loop-1"},
                "params": {"stability_rate|STABLE": pinned_v1[("stability_rate", "STABLE")]},
                "sources": {"stability_rate|STABLE": {"decay_ratio": "DEFAULT"}},
            },
        )
        row = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:g2:replay",
            arrays=arrays,
            manifest_extra=manifest_extra,
        )
        record = _record_stub("rec-g2a", str(row.id), v1)
        with (
            patch(
                "app.services.result_ledger.get_record_by_id",
                new=AsyncMock(return_value=record),
            ),
            _metric_threshold_override("stability_rate", {"decay_ratio": 0.2}),
        ):
            report = await cs.replay_result_record(_LoadDB(row)._db, "rec-g2a")

        assert report["paramsSource"] == "package"
        assert report["executionMode"] == "in-process"
        assert report["identical"] is True, f"差异: {report['differences']}"
        # 反证：0.2 缓存口径的稳定率确实不同于 v1（重放若读了当前缓存必不一致）
        with _metric_threshold_override("stability_rate", {"decay_ratio": 0.2}):
            v2 = _extract_metrics_detail(_compute_kpis_three_layer(bundles, config_bundle, None)[0])
        assert v1["steady_rate"]["value"] != v2["steady_rate"]["value"]

    async def test_replay_legacy_package_flags_unpinned(self, snap_dir):
        """1.1/1.2 旧包（无 effectiveParams.params）→ legacy-unpinned 显式标记
        （非静默；同构建既有口径计算）."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        v1 = _extract_metrics_detail(_compute_kpis_three_layer(bundles, config_bundle, None)[0])
        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        row = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:g2:legacy",
            arrays=arrays,
            manifest_extra=_kpi_manifest_extra(blocks=blocks_meta),  # 无 effectiveParams
        )
        record = _record_stub("rec-legacy", str(row.id), v1)
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.replay_result_record(_LoadDB(row)._db, "rec-legacy")
        assert report["paramsSource"] == "legacy-unpinned"
        assert report["identical"] is True

    async def test_series_context_roundtrip_preserves_gap_guard(self, snap_dir):
        """G2 真实链暴露的 P2-03 重放保真缺口回归：point 网格包重放须还原
        series_context（否则 gap 守卫退化为 legacy 放行，v1 拒绝的指标重放
        算出数值，同构建全等被破坏）."""
        from app.contracts.data_types import (
            DataBlock,
            DataLineage,
            MetricDataBundle,
            QualitySummary,
        )
        from app.contracts.series_context import SeriesContext
        from app.services.preprocessing.input_guards import gap_guard_verdict

        # point 网格块：masked_indices 含跳变（[0,1,60,61] 型）
        n = 64
        block = DataBlock(
            data_block_id="db-g2-ctx",
            loop_id="loop-ctx",
            tag_group="BASE",
            sampling_freq="1s",
            timestamps=[_TS0 + timedelta(seconds=i) for i in range(n)],
            signals={"pv": [50.0 + 0.1 * i for i in range(n)]},
            validity={},
            quality_summary=QualitySummary(total_count=n, valid_count=4, valid_rate=4 / n),
            consecutive_segments=[],
            point_count=n,
            control_type="STABLE",
            series_context=SeriesContext(
                layout="point",
                grid_start=_TS0,
                grid_end=_TS0 + timedelta(seconds=n),
                grid_period_s=1.0,
                expected_slots=n,
                dataset_ref="test:grid",
            ),
        )
        masked = [0, 1, 60, 61]
        bundle = MetricDataBundle(
            metric_code="settling_time",
            data_block=block,
            mask_expression="pv_valid",
            masked_indices=masked,
            lineage=DataLineage(sampling_freq="1s", tag_group="BASE"),
        )
        # v1 侧（live）：守卫拒绝
        allowed_live, gap_live = gap_guard_verdict("settling_time", bundle)
        assert allowed_live is False and gap_live > 1

        # 落包 → 重放侧反序列化：守卫行为必须一致（series_context 已还原）
        arrays, blocks_meta = cs.serialize_data_blocks([bundle])
        import json as _json

        _json.dumps(blocks_meta)  # manifest 可序列化（gridStart/gridEnd 已 isoformat）
        replay_bundles, _codes = cs.deserialize_data_blocks(arrays, blocks_meta)
        allowed_replay, gap_replay = gap_guard_verdict("settling_time", replay_bundles[0])
        assert allowed_replay is False, "重放侧不得让 gap 守卫退化为 legacy 放行"
        assert gap_replay == gap_live


# ---------------------------------------------------------------------------
# IV. G2-INC-b：受控旧构建（真实子进程执行 + 两套构建互证 + 拒绝分支）
# ---------------------------------------------------------------------------

#: 当前代码 stability.py 指数公式行（受控构建打"有意公式差分"补丁的锚点）
_FORMULA_LINE = "        stability = math.exp(-normalized_std) * osc_factor * 100.0\n"

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _make_controlled_build(root: Path, marker: str, factor: float) -> dict:
    """构造一套真实受控构建：backend 源码树副本 + 稳定率公式差分 + 构建身份 env.

    公式差分（有意、确定性）：S = e^(−σ/(d·U)·factor)——构建 A factor=0.5、
    构建 B factor=2.0，与当前代码（无 factor）三者两两不同。
    digest 由构建自己的 executable_build_digest()（单源公式）计算。
    """
    build_dir = root / f"build-{marker}"
    shutil.copytree(
        _BACKEND_ROOT / "app",
        build_dir / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    shutil.copy2(_BACKEND_ROOT / "uv.lock", build_dir / "uv.lock")
    stab = build_dir / "app" / "services" / "metric_calculator" / "stability.py"
    text = stab.read_text(encoding="utf-8")
    assert text.count(_FORMULA_LINE) == 1, "公式锚点漂移：受控构建补丁需同步更新"
    patched = _FORMULA_LINE.replace(
        "math.exp(-normalized_std)", f"math.exp(-normalized_std * {factor})"
    )
    stab.write_text(text.replace(_FORMULA_LINE, patched), encoding="utf-8")
    env_sha = hashlib.sha256(f"g2inc-build-{marker}".encode()).hexdigest()
    recipe = {"kind": "source", "path": str(build_dir), "env": {"CLPM_BUILD_CODE_SHA": env_sha}}
    digest = cb.query_build_digest(recipe)
    return {"marker": marker, "recipe": recipe, "digest": digest, "factor": factor}


@pytest.fixture(scope="session")
def controlled_builds(tmp_path_factory):
    """两套真实受控构建（session 级：源码树副本只复制一次）."""
    root = tmp_path_factory.mktemp("g2inc-controlled-builds")
    return {
        "A": _make_controlled_build(root, "A", 0.5),
        "B": _make_controlled_build(root, "B", 2.0),
    }


def _write_retained(snap_root: Path, digests: list, builds: dict | None = None) -> None:
    data: dict = {"digests": digests}
    if builds is not None:
        data["builds"] = builds
    root = Path(snap_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "retained-builds.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def _package_paths(snapshot_id: str) -> tuple[Path, Path]:
    paths = cs.snapshot_file_paths(snapshot_id)
    npz = next(p for p in paths if p.suffix == ".npz")
    man = next(p for p in paths if p.name.endswith(".manifest.json"))
    return npz, man


async def _save_package_in_build_era(build: dict, blocks_meta: list, manifest_extra: dict):
    """在"构建 X 时代"保存包：manifest stamp=该构建 digest（模拟当年由该构建落包）."""
    with patch.object(cs, "executable_build_digest", lambda: build["digest"]):
        return await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref=f"test:g2:build-{build['marker']}",
            arrays=_G2_ARRAYS[0],
            manifest_extra=manifest_extra,
        )


#: 模块级缓存：同组数组/manifest 只序列化一次（两套构建共用同一"旧包"内容）
_G2_ARRAYS: list = []
_G2_BLOCKS_META: list = []


def _g2_arrays_and_meta() -> tuple[dict, list]:
    if not _G2_ARRAYS:
        arrays, blocks_meta = cs.serialize_data_blocks(_g2_kpi_bundles())
        _G2_ARRAYS.append(arrays)
        _G2_BLOCKS_META.append(blocks_meta)
    return _G2_ARRAYS[0], _G2_BLOCKS_META[0]


def _g2_kpi_manifest_extra() -> dict:
    """包 manifest：含 G2-INC-a 完整 effectiveParams（默认五层展开值）."""
    snapshot = _pinned_snapshot(revision=3)
    from app.services.config_publish import resolve_loop_params_map

    _, blocks_meta = _g2_arrays_and_meta()
    resolved = resolve_loop_params_map(snapshot, loop_id="loop-1")
    return _kpi_manifest_extra(
        blocks=blocks_meta,
        effective_params={
            "status": "PINNED",
            "configRevision": 3,
            "scope": {"templateKey": None, "nodeId": None, "loopId": "loop-1"},
            "params": {f"{m}|{ct}": p for (m, ct), p in sorted(resolved["params"].items())},
            "sources": {f"{m}|{ct}": s for (m, ct), s in sorted(resolved["sources"].items())},
        },
    )


class TestG2IncOldBuildExecution:
    @pytest.mark.timeout(600)
    async def test_two_controlled_builds_reproduce_their_own_v1(self, snap_dir, controlled_builds):
        """G2-INC-b 阳性证据：两套真实受控构建对自己的旧包数值级复现 v1.

        链路：包在构建 X 时代保存（stamp=digest_X）→ v1 指标由构建 X 的
        真实子进程按其公式差分算出 → 当前时代（当前构建 digest 不同）重放
        路由到构建 X 子进程执行 → identical=True 且回传其 digest。
        """
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _g2_kpi_bundles()
        arrays, blocks_meta = _g2_arrays_and_meta()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        current_metrics = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None)[0]
        )
        # 当前构建 digest 必须不同于两套受控构建（否则路由不到子进程）
        assert cs.executable_build_digest() not in {
            controlled_builds[k]["digest"] for k in controlled_builds
        }

        _write_retained(
            cs.snapshot_root(),
            [controlled_builds[k]["digest"] for k in controlled_builds],
            {
                controlled_builds[k]["digest"]: controlled_builds[k]["recipe"]
                for k in controlled_builds
            },
        )

        # 每套构建：其时代落包 → 其子进程算 v1 → 当前时代重放复现
        for key, build in controlled_builds.items():
            manifest_extra = _g2_kpi_manifest_extra()
            row = await _save_package_in_build_era(build, blocks_meta, manifest_extra)
            npz, man = _package_paths(str(row.id))

            # v1 指标 = 构建 X 自己的代码算出（公式差分生效）
            out = cb.run_build_cli(
                build["digest"], ["kpi", "--npz", str(npz), "--manifest", str(man)]
            )
            assert out["ok"] is True and out["buildDigest"] == build["digest"]
            v1 = out["metrics"]
            assert v1["steady_rate"]["value"] is not None

            record = _record_stub(f"rec-{key}", str(row.id), v1)
            with patch(
                "app.services.result_ledger.get_record_by_id",
                new=AsyncMock(return_value=record),
            ):
                report = await cs.replay_result_record(_LoadDB(row)._db, f"rec-{key}")

            assert report["executionMode"] == "controlled-build"
            assert report["executedBuildDigest"] == build["digest"]
            assert report["paramsSource"] == "package"
            assert report["identical"] is True, f"[{key}] 差异: {report['differences']}"

            # 同包再重放一次：确定性（同构建同包恒一致）
            with patch(
                "app.services.result_ledger.get_record_by_id",
                new=AsyncMock(return_value=record),
            ):
                again = await cs.replay_result_record(_LoadDB(row)._db, f"rec-{key}")
            assert again["identical"] is True
            build["_v1_stability"] = v1["steady_rate"]["value"]
            build["_row_id"] = str(row.id)

        # 两套构建与当前代码三方公式两两不同 → 证明子进程确实执行了旧构建
        va = controlled_builds["A"]["_v1_stability"]
        vb = controlled_builds["B"]["_v1_stability"]
        vc = current_metrics["steady_rate"]["value"]
        assert len({va, vb, vc}) == 3, f"A={va} B={vb} current={vc} 应两两不同"

    @pytest.mark.timeout(300)
    async def test_registered_digest_without_recipe_rejected(self, snap_dir, controlled_builds):
        """拒绝分支：digest 在 digests 清单（门禁放行）但无 builds 执行配方
        → ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE（登记 ≠ 执行能力）."""
        build = controlled_builds["A"]
        arrays, blocks_meta = _g2_arrays_and_meta()
        row = await _save_package_in_build_era(build, blocks_meta, _g2_kpi_manifest_extra())
        _write_retained(cs.snapshot_root(), [build["digest"]])  # 只登记，无配方
        record = _record_stub("rec-norecipe", str(row.id), {})
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            with pytest.raises(BizError) as ei:
                await cs.replay_result_record(_LoadDB(row)._db, "rec-norecipe")
        assert ei.value.code == cs.ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE

    @pytest.mark.timeout(300)
    async def test_broken_recipe_path_rejected(self, snap_dir, controlled_builds):
        """拒绝分支：builds 配方路径失效 → 同错误码（附原因，不静默用当前构建）."""
        build = controlled_builds["A"]
        arrays, blocks_meta = _g2_arrays_and_meta()
        row = await _save_package_in_build_era(build, blocks_meta, _g2_kpi_manifest_extra())
        broken = {**build["recipe"], "path": "/nonexistent/g2inc/build"}
        _write_retained(cs.snapshot_root(), [build["digest"]], {build["digest"]: broken})
        record = _record_stub("rec-broken", str(row.id), {})
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            with pytest.raises(BizError) as ei:
                await cs.replay_result_record(_LoadDB(row)._db, "rec-broken")
        assert ei.value.code == cs.ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE

    async def test_retained_digests_union_builds_keys(self, snap_dir, controlled_builds):
        """门禁收口：builds 键并入保留清单（有可执行配方视为已保留）."""
        build = controlled_builds["B"]
        _write_retained(cs.snapshot_root(), [], {build["digest"]: build["recipe"]})
        assert cs.retained_build_digests() == {build["digest"]}
        assert cb.retained_build_recipes().keys() == {build["digest"]}

    @pytest.mark.timeout(600)
    async def test_identification_replay_via_controlled_build(self, snap_dir, controlled_builds):
        """辨识链同路由：旧构建时代包 → 受控构建子进程重放（真实管线执行）."""
        from app.services.tuning_identification.pipeline import identify_from_history

        build = controlled_builds["B"]
        _write_retained(cs.snapshot_root(), [build["digest"]], {build["digest"]: build["recipe"]})
        ts_rel, op, pv, sp, mode = _c12b_id_signals()
        v1 = identify_from_history(op=list(op), pv=list(pv), sp=list(sp), ts=1.0, mode=list(mode))
        v1_dict = v1.to_dict()
        s = _SaveDB()
        with patch.object(cs, "executable_build_digest", lambda: build["digest"]):
            row = await cs.save_identification_snapshot(
                s._db,
                loop_id="loop-g2b",
                ts_start=_TS0,
                ts_end=_TS1,
                timestamps_rel=list(ts_rel),
                op=list(op),
                pv=list(pv),
                sp=list(sp),
                mode=list(mode),
                sampling_period_seconds=1.0,
                theta_estimate=None,
                candidate_model_types=["FOPDT", "SOPDT", "IPDT"],
                result_dict=v1_dict,
                tuning_record_id="tr-g2b",
                dcs_template_revision="UNKNOWN_DCS_TEMPLATE",
                valid_rate=0.99,
            )
        report = await cs.replay_identification_snapshot(_LoadDB(row)._db, str(row.id))
        assert report["executionMode"] == "controlled-build"
        assert report["executedBuildDigest"] == build["digest"]
        assert report["success"] == bool(v1_dict["success"])
        if v1_dict["success"]:
            assert report["modelType"] == v1_dict["modelType"]
            assert report["params"] == v1_dict["params"]
            assert report["fittingScore"] == v1_dict["fittingScore"]
