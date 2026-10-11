"""P2-03 计算上下文与历史复现（不可变输入包）单元测试.

覆盖《03-阶段任务计划》P2-03 验收口径 **C12b / C12b-ID** 的可单测部分：

C12b（04 号文 C12b 行 + 任务卡）：
- v1 复现一致：record 重放读原包（不重查源）重算，与 v1 metricsDetail
  全等；源数据迟到/覆写/不可用不影响 v1 包（读原包反例）；
- 按 v2 重评独立：同绑定改值 → 新包新 inputHash，v1 重放仍一致；
- 身份随内容变：相同绑定不同值/质量 mask/dtype → 不同 inputHash；
- 旧包旧构建复现：包构建 digest 在保留清单 → 放行；不在 → 显式拒绝；
- 损坏/过期/清理/缺文件/schema 主版本不匹配 → 显式"不可完整复现"；
- 固定引用（FROZEN）防 GC；清理规则 ①②③（残留文件/过期未引用/FROZEN）；
- NumPy 禁 pickle（object dtype 写入拒绝 / 读取 allow_pickle=False）；
- 原子写入（临时名+rename+核 hash，无残留）。

C12b-ID（07 号文 §5 + 冻结稿 §3）：
- 600 点安静段＋gap＋399 点激励段：完整输入保存（激励段不被丢弃）、
  时间轴 gap 显式保留（不拼 gap）、原包完整重放成功；
- 同绑定改 SP/MODE → 身份变化；
- 结构化预处理参数（DEC-10：三件套/multi-window 窗位从 reason 提升为
  结构化，随包 manifest.preprocess 保存，evidence 不破坏）。

行级落库/迁移/约束/SQL 过滤条件由 dev 库 upgrade + 临时库新装路径验证
（见交接文件），不在本文件 mock 层重复。
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import numpy as np
import pytest

from app.core.config import settings
from app.core.exceptions import BizError
from app.models.calc_dataset_snapshot import (
    KIND_KPI_GRID,
    RETENTION_FROZEN,
    RETENTION_NORMAL,
    CalculationDatasetSnapshot,
)
from app.services import calc_snapshot as cs

# ---------------------------------------------------------------------------
# 公共夹具与助手
# ---------------------------------------------------------------------------

_TS0 = datetime(2026, 10, 12, 8, 0, 0)
_TS1 = _TS0 + timedelta(hours=1)
_TEST_BUILD = "testbuild012345678"


@pytest.fixture()
def snap_dir(tmp_path, monkeypatch):
    """输入包根目录指向测试临时目录（可配置、随测试隔离）."""
    monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    return settings.CLPM_CALC_SNAPSHOT_DIR


@pytest.fixture()
def fixed_build(monkeypatch):
    """构建摘要固定（避免 git 环境差异；绕过 lru_cache）."""
    monkeypatch.setattr(cs, "executable_build_digest", lambda: _TEST_BUILD)
    return _TEST_BUILD


def _exec_result(*, scalar=None, scalars_list=None):
    """db.execute 返回值：scalar_one_or_none / scalars().all() 双口径."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = scalars_list or []
    return result


def _row_from_files(snapshot_id: str, **overrides) -> CalculationDatasetSnapshot:
    """按盘上实际包文件构造元数据行（input_hash 按包内容重算，口径一致）."""
    paths = cs.snapshot_file_paths(snapshot_id)
    npz = next(p for p in paths if p.suffix == ".npz")
    man = next(p for p in paths if p.name.endswith(".manifest.json"))
    manifest, arrays = cs._read_files(npz, man)
    row = CalculationDatasetSnapshot(
        id=snapshot_id,
        dataset_ref=manifest.get("binding", {}).get("datasetRef", "test:ref"),
        input_hash=cs.compute_input_hash(manifest, arrays),
        ts_start=_TS0,
        ts_end=_TS1,
        schema_version=manifest["schemaVersion"],
        kind=manifest["kind"],
        created_by_record_id=manifest.get("createdByRecordId"),
        retention_class=RETENTION_NORMAL,
        frozen_by_record_ids=[],
        expires_at=_TS0 + timedelta(days=90),
        size_bytes=sum(p.stat().st_size for p in (npz, man)),
        loop_id=manifest.get("loopId"),
        created_at=_TS0,
    )
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


class _SaveDB:
    """save_calculation_snapshot 专用假会话.

    execute 应答次序：①既有包 SELECT（None）→ ②INSERT RETURNING（按刚
    写出的包文件构造行：id=文件名、input_hash=按包内容重算）。
    """

    def __init__(self):
        self._inserted = False
        self.stmts: list = []
        db = MagicMock()
        self._db = db

        async def _exec(stmt):
            self.stmts.append(stmt)
            if not self._inserted:
                self._inserted = True
                return _exec_result(scalar=None)
            root = cs.snapshot_root()
            candidates = [
                (p.stat().st_mtime_ns, p.stem)
                for month in sorted(p for p in root.iterdir() if p.is_dir())
                for p in month.glob("*.npz")
                if not p.name.startswith(".")
            ]
            assert candidates, "save 未写出包文件"
            latest = max(candidates)[1]  # 刚写出的包（mtime 最新）
            return _exec_result(scalar=_row_from_files(latest))

        db.execute = AsyncMock(side_effect=_exec)

    async def save(self, **kwargs) -> CalculationDatasetSnapshot:
        return await cs.save_calculation_snapshot(self._db, **kwargs)


class _LoadDB:
    """load/replay 专用假会话：SELECT 返回指定行."""

    def __init__(self, row):
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[_exec_result(scalar=row)])
        self._db = db


def _kpi_manifest_extra(loop_id="loop-1", weights=None, **extra) -> dict:
    meta = {
        "loopId": loop_id,
        "window": {"tsStart": _TS0.isoformat(), "tsEnd": _TS1.isoformat()},
        "binding": {"datasetRef": f"test:loop:{loop_id}", "bindingVersion": None},
        "replayContext": {
            "loopId": loop_id,
            "controlType": "TC",
            "idealSettlingTime": None,
            "weights": weights,
            "opRange": [0.0, 100.0],
            "pvRange": [0.0, 100.0],
        },
        "source": {"algorithmVersion": "KPI_CALC_v4.0", "configRevision": "digest:abc"},
    }
    meta.update(extra)
    return meta


# ---------------------------------------------------------------------------
# A. 内容寻址身份（C12b：相同绑定不同值/质量/dtype → 不同 inputHash）
# ---------------------------------------------------------------------------


class TestInputHashIdentity:
    async def test_same_binding_different_values_different_hash(self, snap_dir, fixed_build):
        """C12b：相同绑定+窗口，改值 → 不同 inputHash / 不同包."""
        r1 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays={"sig": np.arange(10, dtype=np.float64)},
            manifest_extra=_kpi_manifest_extra(),
        )
        r2 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays={"sig": np.arange(10, dtype=np.float64) * 2.0},  # 改值
            manifest_extra=_kpi_manifest_extra(),
        )
        assert r1.input_hash != r2.input_hash
        assert str(r1.id) != str(r2.id)

    async def test_quality_mask_changes_identity(self, snap_dir, fixed_build):
        """C12b：同绑定同值，仅改质量 mask → 身份变化（质量入哈希）."""
        base = {"sig": np.ones(5, dtype=np.float64)}
        mask_off = {**base, "valid": np.ones(5, dtype=bool)}
        mask_on = {**base, "valid": np.array([1, 1, 0, 1, 1], dtype=bool)}
        r1 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L2",
            arrays=mask_off,
            manifest_extra=_kpi_manifest_extra(),
        )
        r2 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L2",
            arrays=mask_on,
            manifest_extra=_kpi_manifest_extra(),
        )
        assert r1.input_hash != r2.input_hash

    def test_dtype_changes_identity(self):
        """同值不同 dtype → 不同身份（复现必须同 dtype，dtype/shape 入哈希）."""
        assert cs.compute_input_hash(
            {"k": 1}, {"a": np.ones(3, dtype=np.float64)}
        ) != cs.compute_input_hash({"k": 1}, {"a": np.ones(3, dtype=np.float32)})
        assert cs.compute_input_hash({"k": 1}, {"a": np.ones(3)}) != cs.compute_input_hash(
            {"k": 1}, {"a": np.ones((3, 1))}
        )

    def test_hash_deterministic_volatile_excluded(self):
        """同内容同 hash（createdAt 等 volatile 字段排除在身份外）."""
        m1 = {"schemaVersion": "1.1", "createdAt": "2026-10-12T08:00:00"}
        m2 = {"schemaVersion": "1.1", "createdAt": "2027-01-01T00:00:00"}
        arrays = {"a": np.linspace(0, 1, 7)}
        assert cs.compute_input_hash(m1, arrays) == cs.compute_input_hash(m2, arrays)

    async def test_same_key_save_reuses_row(self, snap_dir, fixed_build):
        """幂等：同 (ref, hash, 窗口, 创建引用) 重复保存 → 复用既有行."""
        kwargs = {
            "kind": KIND_KPI_GRID,
            "ts_start": _TS0,
            "ts_end": _TS1,
            "dataset_ref": "test:loop:L3",
            "arrays": {"sig": np.arange(4, dtype=np.float64)},
            "manifest_extra": _kpi_manifest_extra(),
            "created_by_record_id": "rec-00000000-0000-0000-0000-000000000001",
        }
        r1 = await _SaveDB().save(**kwargs)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[_exec_result(scalar=r1)])
        r2 = await cs.save_calculation_snapshot(db, **kwargs)
        assert str(r1.id) == str(r2.id)


# ---------------------------------------------------------------------------
# B. 原子写入 / 禁 pickle（不可变包基础契约）
# ---------------------------------------------------------------------------


class TestImmutableWrite:
    async def test_files_written_and_hash_verified(self, snap_dir, fixed_build):
        """原子写：npz+manifest 落盘，读回重算 hash 与 DB 存值一致."""
        row = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L4",
            arrays={"sig": np.linspace(0.0, 9.0, 10)},
            manifest_extra=_kpi_manifest_extra(),
        )
        paths = cs.snapshot_file_paths(str(row.id))
        assert sorted(p.name.split(".", 1)[1] for p in paths) == ["manifest.json", "npz"]
        npz = next(p for p in paths if p.suffix == ".npz")
        man = next(p for p in paths if p.name.endswith(".manifest.json"))
        manifest = json.loads(man.read_text(encoding="utf-8"))
        with np.load(npz, allow_pickle=False) as z:  # 禁 pickle 读取
            arrays = {k: z[k] for k in z.files}
        assert cs.compute_input_hash(manifest, arrays) == row.input_hash
        assert manifest["source"]["executableBuildDigest"] == _TEST_BUILD

    async def test_no_temp_files_left(self, snap_dir, fixed_build):
        """写完成后无临时名残留（rename 成功才提交 DB 引用）."""
        await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L5",
            arrays={"sig": np.ones(3)},
            manifest_extra=_kpi_manifest_extra(),
        )
        root = cs.snapshot_root()
        leftovers = [
            p for month in root.iterdir() for p in month.iterdir() if p.name.startswith(".")
        ]
        assert leftovers == []

    async def test_object_dtype_rejected(self, snap_dir, fixed_build):
        """禁 pickle：object dtype 数组写入前拒绝."""
        with pytest.raises(ValueError, match="object dtype"):
            await cs.save_calculation_snapshot(
                MagicMock(),
                kind=KIND_KPI_GRID,
                ts_start=_TS0,
                ts_end=_TS1,
                dataset_ref="test:loop:L6",
                arrays={"bad": np.array([{"x": 1}], dtype=object)},
                manifest_extra=_kpi_manifest_extra(),
            )


# ---------------------------------------------------------------------------
# C. KPI 重放（C12b 核心：v1 复现一致 / 读原包不重查源 / v2 独立）
# ---------------------------------------------------------------------------


def _make_kpi_bundles(n_points=1200):
    """构造带真实信号的 BASE 块 bundle 集（确定性公式，无随机源）."""
    from app.contracts.data_types import (
        DataBlock,
        DataLineage,
        MetricDataBundle,
        QualitySummary,
    )

    ts = [_TS0 + timedelta(seconds=i) for i in range(n_points)]
    sp, pv, op = [], [], []
    for i in range(n_points):
        s = 50.0 + (10.0 if (i // 300) % 2 else -10.0) * min(1.0, i / 100.0)
        lag = max(0, i - 5)
        s_lag = 50.0 + (10.0 if (lag // 300) % 2 else -10.0) * min(1.0, lag / 100.0)
        p = s_lag + 2.0 * math.sin(i / 37.0)
        sp.append(s)
        pv.append(p)
        op.append(45.0 + 0.8 * (s - p) + 1.5 * math.sin(i / 53.0))
    n = n_points
    block = DataBlock(
        data_block_id="db_loop-1_BASE_1s",
        loop_id="loop-1",
        tag_group="BASE",
        sampling_freq="1s",
        timestamps=ts,
        signals={"pv": pv, "sp": sp, "op": op, "mode": [1] * n},
        validity={
            "pv_valid": [True] * n,
            "sp_valid": [True] * n,
            "op_valid": [True] * n,
            "mode_valid": [True] * n,
        },
        quality_summary=QualitySummary(total_count=n, valid_count=n, valid_rate=1.0),
        consecutive_segments=[(0, n - 1)],
        point_count=n,
        control_type="STABLE",
        loop_confidence_level="A",
        loop_valid_rate=1.0,
    )
    codes = [
        "accuracy_rate",
        "auto_mode_rate",
        "oscillation_rate",
        "saturation_rate",
        "settling_time",
        "pv_mean",
        "pv_std",
        "sp_mean",
        "sp_std",
        "op_mean",
        "op_std",
        "error_mean",
        "error_std",
    ]
    return [
        MetricDataBundle(
            metric_code=c,
            data_block=block,
            mask_expression="pv_valid",
            masked_indices=list(range(n)),
            lineage=DataLineage(sampling_freq="1s", tag_group="BASE"),
        )
        for c in codes
    ]


def _record_stub(record_id: str, snapshot_id: str, metrics_detail: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id,
        dataset_snapshot_id=snapshot_id,
        payload={"metricsDetail": metrics_detail},
    )


class TestKpiReplayC12b:
    async def test_v1_replay_identical_reads_original_package(self, snap_dir, fixed_build):
        """C12b：v1 record 重放=读原包重算，与 v1 全等；不重查当前源."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        v1_detail = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None)[0]
        )
        non_null = {k: v for k, v in v1_detail.items() if v.get("value") is not None}
        assert len(non_null) >= 5, f"测试数据应产出多个非空指标（实际 {len(non_null)}）"

        arrays, blocks_meta = cs.serialize_data_blocks(bundles)
        row = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays=arrays,
            manifest_extra=_kpi_manifest_extra(blocks=blocks_meta),
        )
        record = _record_stub("rec-1", str(row.id), v1_detail)

        with (
            patch(
                "app.services.result_ledger.get_record_by_id",
                new=AsyncMock(return_value=record),
            ),
            # 反例守卫：重放若试图重查当前源（DataPlanner）立即失败——
            # 证明重放只读原包，不重查 TDengine/当前值
            patch(
                "app.tasks.kpi_calc._build_data_planner",
                side_effect=AssertionError("重放不得重查当前数据源"),
            ),
        ):
            report = await cs.replay_result_record(_LoadDB(row)._db, "rec-1")

        assert report["identical"] is True, f"差异: {report['differences']}"
        assert report["snapshotId"] == str(row.id)
        assert report["inputHash"] == row.input_hash

    async def test_v2_reevaluation_independent_v1_replay_unaffected(self, snap_dir, fixed_build):
        """C12b：v2 重评（源值覆写/迟到后）保存新身份；v1 仍读 v1 原包一致."""
        from app.contracts.data_types import ControlType
        from app.tasks.kpi_calc import (
            _build_config_bundle,
            _compute_kpis_three_layer,
            _extract_metrics_detail,
        )

        bundles = _make_kpi_bundles()
        config_bundle = _build_config_bundle("loop-1", ControlType.TEMPERATURE, None)
        v1_detail = _extract_metrics_detail(
            _compute_kpis_three_layer(bundles, config_bundle, None)[0]
        )
        arrays1, meta1 = cs.serialize_data_blocks(bundles)
        row1 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays=arrays1,
            manifest_extra=_kpi_manifest_extra(blocks=meta1),
        )

        # v2 重评：源值变化（覆写场景）→ 不同数组 → 不同 inputHash（独立身份）
        mutated = _make_kpi_bundles()
        block = mutated[0].data_block
        block.signals = {
            k: ([v * 1.5 + 3.0 for v in vals] if k != "mode" else vals)
            for k, vals in block.signals.items()
        }
        arrays2, meta2 = cs.serialize_data_blocks(mutated)
        row2 = await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:L1",
            arrays=arrays2,
            manifest_extra=_kpi_manifest_extra(blocks=meta2),
        )
        assert row1.input_hash != row2.input_hash
        assert str(row1.id) != str(row2.id)

        record = _record_stub("rec-1", str(row1.id), v1_detail)
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            report = await cs.replay_result_record(_LoadDB(row1)._db, "rec-1")
        assert report["identical"] is True

    async def test_record_without_snapshot_rejected(self, snap_dir, fixed_build):
        """P1 历史记录/例行 KPI 按 DEC-10 不落包 → 显式拒绝（不伪称可复现）."""
        record = _record_stub("rec-old", None, {})
        with patch(
            "app.services.result_ledger.get_record_by_id",
            new=AsyncMock(return_value=record),
        ):
            with pytest.raises(BizError) as ei:
                await cs.replay_result_record(MagicMock(), "rec-old")
        assert ei.value.code == cs.ERR_SNAPSHOT_NOT_SAVED


# ---------------------------------------------------------------------------
# D. 显式拒绝（损坏/过期/清理/缺文件/缺构建/schema 不匹配）
# ---------------------------------------------------------------------------


class TestReplayRejections:
    async def _save_one(self, snap_dir, fixed_build) -> CalculationDatasetSnapshot:
        return await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:R",
            arrays={"sig": np.linspace(0.0, 5.0, 6)},
            manifest_extra=_kpi_manifest_extra(),
        )

    async def test_expired_rejected(self, snap_dir, fixed_build):
        """NORMAL 过期 → ERR_SNAPSHOT_EXPIRED（即使文件仍在）."""
        row = await self._save_one(snap_dir, fixed_build)
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        with pytest.raises(BizError) as ei:
            await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert ei.value.code == cs.ERR_SNAPSHOT_EXPIRED

    async def test_frozen_expired_still_loadable(self, snap_dir, fixed_build):
        """FROZEN 过期不受 expires_at 拒绝（固定引用包可复现）."""
        row = await self._save_one(snap_dir, fixed_build)
        row.retention_class = RETENTION_FROZEN
        row.frozen_reason = "审核证据"
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        loaded = await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert loaded.snapshot_id == str(row.id)

    async def test_cleaned_rejected(self, snap_dir, fixed_build):
        """已清理（cleaned_at）→ ERR_SNAPSHOT_CLEANED."""
        row = await self._save_one(snap_dir, fixed_build)
        row.cleaned_at = datetime.now(UTC).replace(tzinfo=None)
        with pytest.raises(BizError) as ei:
            await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert ei.value.code == cs.ERR_SNAPSHOT_CLEANED

    async def test_files_missing_rejected(self, snap_dir, fixed_build):
        """包文件缺失 → ERR_SNAPSHOT_FILES_MISSING."""
        row = await self._save_one(snap_dir, fixed_build)
        for p in cs.snapshot_file_paths(str(row.id)):
            p.unlink()
        with pytest.raises(BizError) as ei:
            await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert ei.value.code == cs.ERR_SNAPSHOT_FILES_MISSING

    async def test_corrupted_file_rejected(self, snap_dir, fixed_build):
        """NPZ 损坏/被篡改 → hash 不一致拒绝（不静默降级）."""
        row = await self._save_one(snap_dir, fixed_build)
        npz = next(p for p in cs.snapshot_file_paths(str(row.id)) if p.suffix == ".npz")
        raw = bytearray(npz.read_bytes())
        raw[-20] ^= 0xFF
        npz.write_bytes(bytes(raw))
        with pytest.raises(BizError) as ei:
            await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert ei.value.code == cs.ERR_SNAPSHOT_HASH_MISMATCH

    async def test_schema_versioned_extension_and_major_gate(self, snap_dir, fixed_build):
        """schemaVersion 策略：主版本一致（次版本升级+未知可选节）→ 接受；
        主版本不一致 → 显式拒绝."""
        row = await self._save_one(snap_dir, fixed_build)
        man = next(
            p for p in cs.snapshot_file_paths(str(row.id)) if p.name.endswith(".manifest.json")
        )
        npz = next(p for p in cs.snapshot_file_paths(str(row.id)) if p.suffix == ".npz")

        def _rewrite(schema_version: str):
            manifest = json.loads(man.read_text(encoding="utf-8"))
            manifest["schemaVersion"] = schema_version
            manifest["futureOptionalSection"] = None  # 未知可选节=显式 null
            man.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), "utf-8")
            _, arrays = cs._read_files(npz, man)
            row.input_hash = cs.compute_input_hash(manifest, arrays)

        _rewrite("1.9")  # 次版本升级（版本化扩展）
        loaded = await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert loaded.snapshot_id == str(row.id)

        _rewrite("2.0")  # 主版本升级
        with pytest.raises(BizError) as ei:
            await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert ei.value.code == cs.ERR_SNAPSHOT_SCHEMA_UNSUPPORTED

    async def test_build_unavailable_rejected_and_retained_old_build_ok(
        self, snap_dir, fixed_build
    ):
        """C12b 旧构建：包 digest 不在保留清单 → 拒绝；补入清单 → 旧包复现."""
        row = await self._save_one(snap_dir, fixed_build)
        with patch.object(cs, "executable_build_digest", lambda: "newbuild999"):
            with pytest.raises(BizError) as ei:
                await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert ei.value.code == cs.ERR_SNAPSHOT_BUILD_UNAVAILABLE

        # 环境 keeper 按 DEC-10 保留旧构建 → 旧包按旧构建复现
        retained = cs.snapshot_root() / "retained-builds.json"
        retained.write_text(json.dumps({"digests": [_TEST_BUILD]}), "utf-8")
        with patch.object(cs, "executable_build_digest", lambda: "newbuild999"):
            loaded = await cs.load_snapshot_for_replay(_LoadDB(row)._db, str(row.id))
        assert loaded.input_hash == row.input_hash


# ---------------------------------------------------------------------------
# E. FROZEN 固定引用与清理规则（C12b：固定引用包不能被 GC）
# ---------------------------------------------------------------------------


class TestRetentionAndCleanup:
    async def _save_one(self, snap_dir, fixed_build) -> CalculationDatasetSnapshot:
        return await _SaveDB().save(
            kind=KIND_KPI_GRID,
            ts_start=_TS0,
            ts_end=_TS1,
            dataset_ref="test:loop:C",
            arrays={"sig": np.arange(8, dtype=np.float64)},
            manifest_extra=_kpi_manifest_extra(),
        )

    def _cleanup_db(self, all_ids, expired_rows, frozen_rows):
        db = MagicMock()
        stmts: list = []

        async def _exec(stmt):
            stmts.append(stmt)
            if len(stmts) == 1:
                return _exec_result(scalars_list=all_ids)  # ① 全表 id
            if len(stmts) == 2:
                return _exec_result(scalars_list=expired_rows)  # ② 过期行
            return _exec_result(scalars_list=frozen_rows)  # ③ FROZEN 计数

        db.execute = AsyncMock(side_effect=_exec)
        db._stmts = stmts
        return db

    async def test_expired_select_filters_normal_and_not_cleaned(self, snap_dir, fixed_build):
        """清理 ② 的 SQL 条件：仅 retention_class=NORMAL 且未清理且已过期."""
        row = await self._save_one(snap_dir, fixed_build)
        db = self._cleanup_db([str(row.id)], [], [])
        with patch.object(cs, "find_referencing_records", new=AsyncMock(return_value={})):
            await cs.cleanup_expired_snapshots(db)
        expired_stmt = str(db._stmts[1])
        assert "retention_class" in expired_stmt
        assert "cleaned_at" in expired_stmt
        assert "expires_at" in expired_stmt

    async def test_freeze_prevents_gc(self, snap_dir, fixed_build):
        """C12b：被方案/审核证据引用（FROZEN）的包过期也不清理."""
        row = await self._save_one(snap_dir, fixed_build)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[_exec_result(scalar=row)])
        frozen = await cs.freeze_snapshot(
            db, str(row.id), reason="审核证据引用", record_ids=["ord-1"]
        )
        assert frozen.retention_class == RETENTION_FROZEN
        assert frozen.frozen_by_record_ids == ["ord-1"]

        # FROZEN 行不满足 ② 的 NORMAL 过滤（SQL 断言见上例）；此处验证
        # 即便混入过期列表，frozen_by_record_ids 非空也被跳过
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        db2 = self._cleanup_db([str(row.id)], [row], [row])
        with patch.object(cs, "find_referencing_records", new=AsyncMock(return_value={})):
            report = await cs.cleanup_expired_snapshots(db2)
        assert report["frozenSkipped"] == 1
        assert str(row.id) not in report["expiredCleaned"]
        assert cs.snapshot_file_paths(str(row.id))  # 文件仍在

    async def test_expired_unreferenced_cleaned(self, snap_dir, fixed_build):
        """过期 NORMAL 未被引用 → 先标 cleaned_at 再删文件（次序即规则②）."""
        row = await self._save_one(snap_dir, fixed_build)
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        with patch.object(cs, "find_referencing_records", new=AsyncMock(return_value={})):
            report = await cs.cleanup_expired_snapshots(self._cleanup_db([str(row.id)], [row], []))
        assert report["expiredCleaned"] == [str(row.id)]
        assert row.cleaned_at is not None
        assert cs.snapshot_file_paths(str(row.id)) == []

    async def test_expired_but_referenced_skipped(self, snap_dir, fixed_build):
        """过期但被 record/方案引用 → 跳过清理（referencedExpiredSkipped）."""
        row = await self._save_one(snap_dir, fixed_build)
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        with patch.object(
            cs,
            "find_referencing_records",
            new=AsyncMock(return_value={"tuning_record": ["tr-1"]}),
        ):
            report = await cs.cleanup_expired_snapshots(self._cleanup_db([str(row.id)], [row], []))
        assert report["referencedExpiredSkipped"] == [str(row.id)]
        assert row.cleaned_at is None
        assert cs.snapshot_file_paths(str(row.id))

    async def test_orphan_file_removed_after_grace(self, snap_dir, fixed_build):
        """规则①：无 DB 引用且超 24h 的残留文件删除；新鲜残留保留（宽限）."""
        row = await self._save_one(snap_dir, fixed_build)
        month = cs.snapshot_file_paths(str(row.id))[0].parent
        stale = month / "orphan-00000000-0000-0000-0000-000000000009.npz"
        stale.write_bytes(b"stale")
        fresh = month / "fresh-00000000-0000-0000-0000-000000000009.npz"
        fresh.write_bytes(b"fresh")
        old_ts = datetime.now().timestamp() - 25 * 3600
        os.utime(stale, (old_ts, old_ts))

        with patch.object(cs, "find_referencing_records", new=AsyncMock(return_value={})):
            report = await cs.cleanup_expired_snapshots(self._cleanup_db([str(row.id)], [], []))
        assert str(stale) in report["orphanFilesRemoved"]
        assert not stale.exists()
        assert fresh.exists()
        assert cs.snapshot_file_paths(str(row.id))  # DB 引用文件不受影响

    async def test_unfreeze_restores_normal(self, snap_dir, fixed_build):
        """解除引用：无剩余引用 → 降级 NORMAL（恢复保留期约束）."""
        row = await self._save_one(snap_dir, fixed_build)
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[_exec_result(scalar=row)])
        await cs.freeze_snapshot(db, str(row.id), reason="引用", record_ids=["r1"])
        db2 = MagicMock()
        db2.execute = AsyncMock(side_effect=[_exec_result(scalar=row)])
        await cs.unfreeze_snapshot(db2, str(row.id), record_ids=["r1"])
        assert row.retention_class == RETENTION_NORMAL
        assert row.frozen_by_record_ids == []
        assert row.frozen_reason is None


# ---------------------------------------------------------------------------
# F. C12b-ID：辨识输入包（600 安静 + gap + 399 激励；六层语义）
# ---------------------------------------------------------------------------


def _c12b_id_signals(gap_slots: int = 300):
    """600 点安静段 + gap + 399 点激励段（确定性 FOPDT 仿真，无随机源）.

    Returns:
        (ts_rel, op, pv, sp, mode)——ts_rel 在 gap 处真实跳变（不拼 gap）。
    """
    n_quiet, n_exc = 600, 399
    ts_s, op_s, pv_s, sp_s, mode_s = [], [], [], [], []
    t_abs = 0
    tau, theta, k = 20.0, 5.0, 0.5
    pv = 50.0
    for _ in range(n_quiet):  # 安静段：OP 缓慢漂移（无激励）
        t_abs += 1
        op = 50.0 + 0.2 * math.sin(t_abs / 400.0)
        pv += (-pv + 50.0 + k * (op - 50.0)) / tau
        ts_s.append(float(t_abs))
        op_s.append(op)
        pv_s.append(pv)
        sp_s.append(50.0)
        mode_s.append(1)
    t_abs += gap_slots  # gap：时间轴跳过 gap_slots 个采样槽（不删点、不拼接）
    for i in range(n_exc):  # 激励段：OP 方波
        t_abs += 1
        op = 50.0 + (12.0 if (i // 40) % 2 == 0 else -12.0)
        u_eff = (op - 50.0) if i >= theta else 0.0
        pv += (-pv + 50.0 + k * u_eff) / tau
        ts_s.append(float(t_abs))
        op_s.append(op)
        pv_s.append(pv)
        sp_s.append(50.0)
        mode_s.append(1)
    return ts_s, op_s, pv_s, sp_s, mode_s


async def _save_c12b_id_package(snap_dir, fixed_build, *, mutate=None, record_id="tr-1"):
    """按 C12b-ID 场景跑辨识并保存输入包，返回 (row, v1_result_dict)."""
    from app.services.tuning_identification.pipeline import identify_from_history

    ts_rel, op, pv, sp, mode = _c12b_id_signals()
    if mutate:
        ts_rel, op, pv, sp, mode = mutate(ts_rel, op, pv, sp, mode)
    v1 = identify_from_history(op=list(op), pv=list(pv), sp=list(sp), ts=1.0, mode=list(mode))
    v1_dict = v1.to_dict()
    s = _SaveDB()
    row = await cs.save_identification_snapshot(
        s._db,
        loop_id="loop-c12bid",
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
        tuning_record_id=record_id,
        dcs_template_revision="UNKNOWN_DCS_TEMPLATE",
        valid_rate=0.99,
    )
    return row, v1_dict


def _load_manifest(snapshot_id: str) -> tuple[dict, dict]:
    paths = cs.snapshot_file_paths(snapshot_id)
    npz = next(p for p in paths if p.suffix == ".npz")
    man = next(p for p in paths if p.name.endswith(".manifest.json"))
    manifest = json.loads(man.read_text(encoding="utf-8"))
    with np.load(npz, allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    return manifest, arrays


class TestIdentificationC12bId:
    async def test_full_input_saved_gap_preserved(self, snap_dir, fixed_build):
        """C12b-ID：全部输入保存（激励段不被丢弃）；gap 显式保留（不拼）."""
        row, _v1 = await _save_c12b_id_package(snap_dir, fixed_build)
        manifest, arrays = _load_manifest(str(row.id))

        # 完整分析输入：999 点全量（不只最终胜出片段）
        assert manifest["consumed"]["slotCount"] == 600 + 399
        assert len(arrays["consumed__sig_op"]) == 999
        assert len(arrays["consumed__sig_pv"]) == 999
        assert len(arrays["consumed__ts_rel"]) == 999
        # gap 不拼：时间轴在 gap 处真实跳变（≈300 槽），未被抹平为等距连续
        max_jump = float(np.max(np.diff(arrays["consumed__ts_rel"])))
        assert max_jump > 100.0, f"时间轴 gap 应显式保留（最大跳变 {max_jump}）"
        # 六层语义关键节存在（P2 载体；P3 未运行字段显式 null，不伪补）
        assert manifest["segments"]["available"] is False
        assert manifest["segments"]["regimeId"] is None  # 工况未知显式标记
        assert manifest["searchAudit"]["preprocessing"] is not None
        assert manifest["searchAudit"]["budgetTermination"] is None
        assert manifest["estimation"]["split"] is not None
        assert manifest["estimation"]["estimatorProfile"] is None
        assert manifest["resultEvidence"]["predictiveMetrics"] is None
        assert manifest["source"]["dcsTemplateRevision"] == "UNKNOWN_DCS_TEMPLATE"
        assert manifest["source"]["executableBuildDigest"] == _TEST_BUILD
        assert manifest["source"]["resultRecordId"] == "tr-1"

    async def test_identification_replay_reproduces_v1(self, snap_dir, fixed_build):
        """C12b-ID：原包完整重放（读原包重跑管线，结果与 v1 一致）."""
        row, v1_dict = await _save_c12b_id_package(snap_dir, fixed_build)
        replay = await cs.replay_identification_snapshot(_LoadDB(row)._db, str(row.id))
        assert replay["success"] == bool(v1_dict["success"])
        if v1_dict["success"]:
            assert replay["modelType"] == v1_dict["modelType"]
            assert replay["params"] == v1_dict["params"]
            assert replay["fittingScore"] == v1_dict["fittingScore"]

    async def test_winner_segment_change_keeps_original_input(self, snap_dir, fixed_build):
        """C12b-ID：模型胜出片段变化不丢原搜索输入（包恒为全量 999 点）."""
        row, _v1 = await _save_c12b_id_package(snap_dir, fixed_build)
        manifest, arrays = _load_manifest(str(row.id))
        assert manifest["consumed"]["slotCount"] == 999
        assert manifest["searchAudit"]["preprocessing"] is not None

    async def test_sp_or_mode_change_changes_identity(self, snap_dir, fixed_build):
        """C12b-ID：同绑定改 SP/MODE → inputHash 变化（身份随内容变）."""

        def _mutate_sp(ts_rel, op, pv, sp, mode):
            return ts_rel, op, pv, [v + 5.0 for v in sp], mode

        def _mutate_mode(ts_rel, op, pv, sp, mode):
            return ts_rel, op, pv, sp, [2 if i > 700 else 1 for i, _v in enumerate(mode)]

        row_v1, _ = await _save_c12b_id_package(snap_dir, fixed_build, record_id="tr-1")
        row_sp, _ = await _save_c12b_id_package(
            snap_dir, fixed_build, mutate=_mutate_sp, record_id="tr-2"
        )
        row_mode, _ = await _save_c12b_id_package(
            snap_dir, fixed_build, mutate=_mutate_mode, record_id="tr-3"
        )
        assert len({row_v1.input_hash, row_sp.input_hash, row_mode.input_hash}) == 3

    async def test_manifest_preprocess_structured(self, snap_dir, fixed_build):
        """DEC-10：结构化预处理参数随包保存（不再只有 reason 字符串）."""
        row, v1_dict = await _save_c12b_id_package(snap_dir, fixed_build)
        manifest, _arrays = _load_manifest(str(row.id))
        prep = manifest["preprocess"]
        assert prep is not None
        for key in ("cleaning", "autoSegment", "autoWindow", "detrend", "lowpass", "multiWindow"):
            assert key in prep, f"结构化预处理缺 {key}"
        assert prep["cleaning"]["originalPoints"] == 999
        # reason 字符串并存（evidence 不破坏）
        assert v1_dict.get("reason")


# ---------------------------------------------------------------------------
# G. 服务接线：identify_model_from_history 落包 + DEC-10 结构化透出
# ---------------------------------------------------------------------------


def _identify_signals_and_loop(gap_slots=10):
    ts_rel, op, pv, sp, mode = _c12b_id_signals(gap_slots=gap_slots)
    signals = {
        "pv": list(pv),
        "op": list(op),
        "sp": list(sp),
        "mode": list(mode),
        "timestamps": list(ts_rel),
        "valid_rate": 0.98,
        "sampling_freq": 1.0,
    }
    loop = SimpleNamespace(
        id=UUID(int=42),
        tag_name="TIC-101",
        loop_type="TEMPERATURE",
        control_type="TC",
        ideal_settling_time=None,
        dcs_model_id=None,
    )
    return signals, loop


class TestIdentifyServiceWiring:
    async def test_identify_model_from_history_saves_snapshot(self, snap_dir, fixed_build):
        """辨识服务保存输入包并透出 inputSnapshot / dcsTemplateRevision."""
        from app.services.tuning import identify_model_from_history

        signals, loop = _identify_signals_and_loop()
        save_db = _SaveDB()

        async def _fake_fetch(_db, _loop_id, _s, _e, _ct):
            return signals

        async def _fake_get_loop(_db, _loop_id):
            return loop

        with (
            patch("app.services.tuning._fetch_preprocessed_signals", new=_fake_fetch),
            patch("app.services.tuning._get_loop", new=_fake_get_loop),
        ):
            result = await identify_model_from_history(
                save_db._db,
                loop_id=str(loop.id),
                start_time="2026-10-12T08:00:00",
                end_time="2026-10-12T09:00:00",
                tuning_record_id="tr-00000000-0000-0000-0000-000000000001",
            )
        snap_info = result.get("inputSnapshot") or {}
        assert snap_info.get("status") == "SAVED"
        assert snap_info.get("snapshotId")
        assert snap_info.get("inputHash")
        assert result["dcsTemplateRevision"] == "UNKNOWN_DCS_TEMPLATE"
        if result["success"]:
            assert result["preprocessing"] is not None  # DEC-10 结构化透出

    async def test_identify_save_failure_degrades_explicitly(self, snap_dir, fixed_build):
        """保存失败 → 显式 SAVE_FAILED（不阻断辨识，不伪称可复现）."""
        from app.services.tuning import identify_model_from_history

        signals, loop = _identify_signals_and_loop()
        db = MagicMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))

        async def _fake_fetch(_db, _loop_id, _s, _e, _ct):
            return signals

        async def _fake_get_loop(_db, _loop_id):
            return loop

        with (
            patch("app.services.tuning._fetch_preprocessed_signals", new=_fake_fetch),
            patch("app.services.tuning._get_loop", new=_fake_get_loop),
        ):
            result = await identify_model_from_history(
                db,
                loop_id=str(loop.id),
                start_time="2026-10-12T08:00:00",
                end_time="2026-10-12T09:00:00",
                tuning_record_id="tr-9",
            )
        assert result["inputSnapshot"] == {"status": "SAVE_FAILED"}
        assert result.get("success") is not None  # 辨识主链未被阻断


# ---------------------------------------------------------------------------
# H. 清理任务注册（Beat 调度；DEC-10 隔离清理）
# ---------------------------------------------------------------------------


class TestCleanupTaskRegistration:
    def test_beat_schedule_registered(self):
        """清理任务按 Beat 每日 03:40 注册（复用 lifespan Beat，无新进程）."""
        from app.tasks.celery_app import celery_app

        assert "calc-snapshot-cleanup-daily-0340" in celery_app.conf.beat_schedule
        entry = celery_app.conf.beat_schedule["calc-snapshot-cleanup-daily-0340"]
        assert entry["task"] == "app.tasks.calc_snapshot_cleanup.cleanup_calc_snapshots"

    def test_cleanup_task_failure_returns_error_dict(self):
        """任务失败 → 显式 error 字典（不中断其他任务）."""
        from app.tasks import calc_snapshot_cleanup as mod

        with patch("app.core.db.AsyncSessionLocal", MagicMock(side_effect=RuntimeError("no"))):
            out = mod.cleanup_calc_snapshots.run(mod.cleanup_calc_snapshots)
        assert out == {"error": "cleanup_failed"}
