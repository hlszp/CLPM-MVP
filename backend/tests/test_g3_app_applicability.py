"""G3-APP 算法适用性核查测试（执行包「系统改造优化-2026-10-10」G3 前纠偏）.

正本：14-G3纠偏任务与执行提示词-2026-10-12.md §5.2（低 SNR 标注全路径）
+ G3纠偏-任务书-20261012.md G3-APP 节【2】1。修补对象 = P3-S1 落地交接
已知局限①：多窗编排外层早退分支（joint 胜出）不经单窗路径的估计/标注
代码——共同估计胜者无 LOW_SNR 标注、外层 preprocessing.lowSnr 缺失。

本文件覆盖（四象限真实序列化反例 + 聚合估计器独立重算 oracle）：
- 单窗成功（低 SNR）/单窗失败（深低 SNR）：S1 已有行为回归锚定 + 序列化
  （to_dict evidence/preprocessing 键在场）；
- 多窗 joint 胜出成功（低 SNR）：修补本体——evidence.lowSnr（聚合 ΣS/ΣN
  口径）+ reason code + 候选 reason 段 + 外层 preprocessing.lowSnr；
- 多窗全失败 fall-through（深低 SNR）：失败结果携带标注（诚实化原则）；
- 保存/重读：save_identification_snapshot → manifest（searchAudit.
  preprocessing.lowSnr + resultEvidence.lowSnr）读回保持。

工程事实登记（探针实证，~150 组合）：joint 候选胜出集中在低 SNR/低拟合
区（高 SNR 下逐窗候选稳定占优——3 噪声档 × 4 段长 × 6~7 种子 × 2/3 段
构造零 joint 胜出）；即漏标注缺口恰好落在标注最需要的区域。防误报（高
SNR joint 不标注）由此构造不可达，改由聚合估计器独立重算 oracle 锚定
（低/高 SNR 段双档）+ S1 既有噪声less 锚定测试承担。

oracle 独立性（冻结草案 §0.2）：生成器独立于被测估计器（FOPDT 一阶差分
状态递推；SOPDT 双一阶级联状态递推——非 ARX 回归）；聚合 SNR 独立重算
按 S1 落地交接登记的估计器口径（Welch PSD 上 1/3 频带中位噪底）从文档
公式独立编写，非复制实现代码。标注断言只针对存在性与口径（S1-b 裁决：
不承诺档不进参数级门禁，参数数值不作断言）。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
from scipy import signal as sps

from app.services.calc_snapshot import save_identification_snapshot
from app.services.tuning_identification.pipeline import (
    _LOW_SNR_ANNOTATION_DB,
    _estimate_snr_db_segments,
    identify_from_history,
)
from app.services.tuning_identification.types import ModelType

LOW_SNR_TIER_DB = _LOW_SNR_ANNOTATION_DB  # S1-b 承诺下界 20dB（登记实施参数）


# ---------------------------------------------------------------------------
# 独立生成器（oracle 独立性：状态递推，非被测回归）
# ---------------------------------------------------------------------------


def _seg_fopdt(
    bias: float,
    seg_n: int,
    noise_std: float,
    seed_u: int,
    K: float = 1.5,
    tau: float = 20.0,
    theta: float = 4.0,
    burn: int = 400,
) -> tuple[np.ndarray, np.ndarray]:
    """白输入 FOPDT 段（burn-in 丢弃——段起始即稳态，无段首瞬态）."""
    rng = np.random.default_rng(seed_u)
    u_full = bias + rng.normal(0, 5.0, seg_n + burn)
    a = math.exp(-1.0 / tau)
    b = K * (1.0 - a)
    d = max(0, round(theta))
    y_full = np.zeros(seg_n + burn)
    for k in range(d, seg_n + burn):
        y_full[k] = a * y_full[k - 1] + b * u_full[k - d]
    y = y_full[burn:] + np.random.default_rng(seed_u + 1000).normal(0, noise_std, seg_n)
    return u_full[burn:], y


def _seg_sopdt_slow(
    seg_n: int,
    noise_std: float,
    seed_u: int,
    K: float = 1.0,
    T1: float = 40.0,
    T2: float = 10.0,
    theta: float = 6.0,
) -> tuple[np.ndarray, np.ndarray]:
    """零均值白输入慢极点 SOPDT 段（S1 慢极点真值族；双一阶级联状态递推）."""
    rng = np.random.default_rng(seed_u)
    u = rng.normal(0, 5.0, seg_n)
    a1, a2 = math.exp(-1.0 / T1), math.exp(-1.0 / T2)
    d = max(0, round(theta))
    x1 = np.zeros(seg_n)
    y = np.zeros(seg_n)
    for k in range(d, seg_n):
        x1[k] = a1 * x1[k - 1] + (1.0 - a1) * u[k - d]
        y[k] = a2 * y[k - 1] + K * (1.0 - a2) * x1[k]
    y = y + np.random.default_rng(seed_u + 2000).normal(0, noise_std, seg_n)
    return u, y


def _two_segments_fopdt(noise_std: float, seg_n: int, seed_u: int, gap: int = 200):
    """两段白输入 FOPDT + NaN 缺口（多窗/joint 触发构造；段间物理不连续）."""
    u_a, y_a = _seg_fopdt(40.0, seg_n, noise_std, seed_u)
    u_b, y_b = _seg_fopdt(55.0, seg_n, noise_std, seed_u + 50)
    na = np.full(gap, np.nan)
    return np.concatenate([u_a, na, u_b]), np.concatenate([y_a, na, y_b])


def _two_segments_sopdt_slow(snr_db: float, seg_n: int, seed_u: int, gap: int = 200):
    """两段慢极点 SOPDT（按信号 σ 折算白噪 std 达目标 SNR）+ NaN 缺口."""
    u_a, ya0 = _seg_sopdt_slow(seg_n, 0.0, seed_u)
    u_b, yb0 = _seg_sopdt_slow(seg_n, 0.0, seed_u + 50)
    ns = float(np.std(ya0 - np.mean(ya0))) / (10.0 ** (snr_db / 20.0))
    y_a = ya0 + np.random.default_rng(seed_u + 2001).normal(0, ns, seg_n)
    y_b = yb0 + np.random.default_rng(seed_u + 2051).normal(0, ns, seg_n)
    na = np.full(gap, np.nan)
    return np.concatenate([u_a, na, u_b]), np.concatenate([y_a, na, y_b])


def _independent_joint_snr_db(ys: list[np.ndarray], ts: float) -> float | None:
    """测试侧独立重算：按 S1 落地交接登记口径从文档公式独立编写.

    口径（S1 交接"实施参数登记"§2 + 本批修补登记）：Welch（nperseg=
    min(n,256)）y 谱上 1/3 高频带中位 PSD 为噪底；噪声全带功率=噪底×
    奈奎斯特带宽；信号功率=总功率−噪声功率；多段按 ΣS/ΣN 聚合（不跨
    gap 拼接谱估计）。与实现对照容差 ±0.2dB（浮点聚合顺序差）。
    """
    tot_s = 0.0
    tot_n = 0.0
    estimated = False
    for y in ys:
        n = len(y)
        nperseg = min(n, 256)
        if nperseg < 32:
            continue
        freqs, pyy = sps.welch(y - float(np.mean(y)), fs=1.0 / ts, nperseg=nperseg)
        if len(freqs) < 12:
            continue
        floor = float(np.median(pyy[2 * len(freqs) // 3 :]))
        if floor <= 0.0:
            continue
        nyq = 0.5 / ts
        noise = floor * nyq
        signal = float(np.mean(pyy)) * nyq - noise
        tot_s += max(signal, 0.0)
        tot_n += noise
        estimated = True
    if not estimated or tot_n <= 0.0:
        return None
    if tot_s <= 0.0:
        return -30.0
    return 10.0 * math.log10(tot_s / tot_n)


# ---------------------------------------------------------------------------
# 聚合 SNR 估计器独立重算 oracle（防误报锚定：高 SNR 段不触发）
# ---------------------------------------------------------------------------


class TestJointSnrAggregateEstimator:
    def test_low_snr_segments_below_threshold_and_match_recompute(self):
        """低 SNR 段聚合 <20dB 且与独立重算一致（±0.2dB）."""
        segs = [_seg_fopdt(40.0, 900, 2.0, 301)[1], _seg_fopdt(55.0, 900, 2.0, 351)[1]]
        got = _estimate_snr_db_segments(segs, 1.0)
        ref = _independent_joint_snr_db(segs, 1.0)
        assert got is not None and ref is not None
        assert got < LOW_SNR_TIER_DB
        assert abs(got - ref) <= 0.2, f"聚合 SNR {got:.2f} vs 独立重算 {ref:.2f}"

    def test_high_snr_segments_above_threshold(self):
        """噪声less 段聚合 ≥20dB（防误报：高 SNR 不触发不承诺标注）.

        构造用 S1 噪声less 锚定同族（慢极点 SOPDT——S1 实测估计 25~41dB；
        估计器对宽带白输入驱动的快过程高频直通分量保守，FOPDT τ=20 短窗
        会低估——登记见交接"估计器口径"，非缺陷）。
        """
        segs = [
            _seg_sopdt_slow(2000, 0.0, 701)[1],
            _seg_sopdt_slow(2000, 0.0, 751)[1],
        ]
        got = _estimate_snr_db_segments(segs, 1.0)
        assert got is not None
        assert got >= LOW_SNR_TIER_DB, f"噪声less 聚合 SNR {got:.1f}dB 应 ≥20dB"

    def test_identical_segments_aggregate_equals_single(self):
        """同分布等长段聚合 = 单段 dB（ΣS/ΣN 比值不变性）."""
        seg = _seg_fopdt(40.0, 900, 1.0, 501)[1]
        single = _estimate_snr_db_segments([seg], 1.0)
        double = _estimate_snr_db_segments([seg, seg.copy()], 1.0)
        assert single is not None and double is not None
        assert abs(single - double) <= 0.2

    def test_too_short_segments_return_none(self):
        """全部段过短（<32 点）→ None 不标注（数据过短不可估计）."""
        assert _estimate_snr_db_segments([np.ones(10)], 1.0) is None
        assert _estimate_snr_db_segments([], 1.0) is None

    def test_mixed_lengths_use_estimable_segments(self):
        """一段过短一段可估 → 可估段承载（不因短段整体放弃）."""
        long_seg = _seg_fopdt(40.0, 900, 1.0, 601)[1]
        got = _estimate_snr_db_segments([np.full(10, 3.0), long_seg], 1.0)
        ref = _independent_joint_snr_db([long_seg], 1.0)
        assert got is not None and ref is not None
        assert abs(got - ref) <= 0.2


# ---------------------------------------------------------------------------
# 多窗 joint 胜出（修补本体）
# ---------------------------------------------------------------------------


class TestJointWinnerLowSnrAnnotated:
    """joint 早退分支补标注（S1 已知局限①修补，14 号文 §5.2）.

    构造（探针实证 deterministic）：两段 700 点 FOPDT（K=1.5/τ=20/θ=4，
    偏置 40/55，噪声 std=1.4 → 聚合实测 ≈−3dB）seed 521/571——joint 候选
    胜出（低 SNR 区 joint 数据量优势占优，与探针登记工程事实一致）。
    """

    NOISE = 1.4
    SEED = 521

    def _run(self):
        op, pv = _two_segments_fopdt(self.NOISE, 700, self.SEED)
        return identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)

    def test_joint_winner_annotated_not_silent(self):
        result = self._run()
        assert result.success, result.reason
        bm = result.best_model
        ev = bm.evidence
        assert ev is not None
        assert ev.joint_estimate is not None, "本构造应触发 joint 候选胜出"
        assert "LOW_SNR_PARAM_UNCOMMITTED" in ev.reason_codes
        assert ev.low_snr is not None
        assert ev.low_snr["mode"] == "PARAM_ACCURACY_UNCOMMITTED"
        assert ev.low_snr["snrDb"] < LOW_SNR_TIER_DB
        assert ev.low_snr["thresholdDb"] == LOW_SNR_TIER_DB
        assert ev.low_snr["basis"] == "WELCH_PSD_NOISE_FLOOR_JOINT_AGGREGATE"
        assert "LOW_SNR_PARAM_UNCOMMITTED" in (bm.reason or "")
        assert (result.preprocessing or {}).get("lowSnr") is not None

    def test_joint_annotation_snr_matches_independent_recompute(self):
        """记录的 snrDb 与测试侧独立重算一致（±0.2dB；口径=ΣS/ΣN 聚合）."""
        result = self._run()
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.low_snr is not None
        mw = (result.preprocessing or {}).get("multiWindow") or {}
        segs = (mw.get("jointEstimate") or {}).get("segments") or []
        assert segs, "joint 胜出应记录段块信息"
        # 用快照保存前的全量输入重切段（构造确定性——同种子重生成）
        op, pv = _two_segments_fopdt(self.NOISE, 700, self.SEED)
        ref = _independent_joint_snr_db([pv[s["start"] : s["end"]] for s in segs], 1.0)
        assert ref is not None
        assert abs(ev.low_snr["snrDb"] - ref) <= 0.2, (
            f"记录 {ev.low_snr['snrDb']} vs 独立重算 {ref:.2f}"
        )

    def test_annotation_survives_to_dict(self):
        """序列化：to_dict 的 evidence.lowSnr 与 preprocessing.lowSnr 在场."""
        d = self._run().to_dict()
        assert (d.get("evidence") or {}).get("lowSnr") is not None
        assert (d.get("preprocessing") or {}).get("lowSnr") is not None


class TestMultiWindowPerWindowWinnerRegression:
    """多窗逐窗胜出（joint 未胜出）回归锚定：内层单窗调用标注不回退."""

    def test_per_window_winner_keeps_inner_annotation(self):
        op, pv = _two_segments_fopdt(1.2, 1800, 301)  # 探针实证：joint 未胜出
        result = identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
        assert result.success, result.reason
        bm = result.best_model
        ev = bm.evidence
        assert ev is not None
        assert ev.joint_estimate is None, "本构造逐窗胜者应胜出（回归锚定）"
        # 噪声 1.2 → 实测 SNR ~4dB：内层单窗路径标注在场
        assert ev.low_snr is not None
        assert ev.low_snr["basis"] == "WELCH_PSD_NOISE_FLOOR"
        assert "LOW_SNR_PARAM_UNCOMMITTED" in ev.reason_codes
        assert (result.preprocessing or {}).get("lowSnr") is not None
        # joint 候选并入候选池（P-22 并列输出）但不携带胜者标注
        joint_pool = [c for c in result.candidates if c.evidence and c.evidence.joint_estimate]
        assert joint_pool
        assert all(c.evidence.low_snr is None for c in joint_pool)


class TestMultiWindowDeepLowSnrFailure:
    """多窗全失败 fall-through：失败结果携带显式不承诺标注（非裸失败）.

    构造：两段 2500 点慢极点 SOPDT（S1 深低 SNR 失败构造的多窗化），
    5dB——各窗 SOPDT 转换失败 → 落回整窗 → 整窗亦失败。
    """

    def test_all_windows_fail_annotated_not_silent(self):
        op, pv = _two_segments_sopdt_slow(5.0, 2500, 621)
        result = identify_from_history(
            op=op.tolist(), pv=pv.tolist(), ts=1.0, candidate_models=[ModelType.SOPDT]
        )
        assert not result.success
        assert "低信噪比" in (result.reason or "")
        low = (result.preprocessing or {}).get("lowSnr")
        assert low is not None
        assert low["mode"] == "PARAM_ACCURACY_UNCOMMITTED"
        assert low["snrDb"] < LOW_SNR_TIER_DB

    def test_failure_serialization_keeps_annotation(self):
        op, pv = _two_segments_sopdt_slow(5.0, 2500, 621)
        d = identify_from_history(
            op=op.tolist(), pv=pv.tolist(), ts=1.0, candidate_models=[ModelType.SOPDT]
        ).to_dict()
        assert d["success"] is False
        assert (d.get("preprocessing") or {}).get("lowSnr") is not None


# ---------------------------------------------------------------------------
# 单窗四象限之成功/失败锚定（S1 行为）+ 序列化
# ---------------------------------------------------------------------------


class TestSingleWindowQuadrantSerialization:
    def test_single_window_success_annotated(self):
        """单窗成功低 SNR（S1 7dB 边缘成功例同构造族）：标注+序列化在场."""
        rng_u = np.random.default_rng(611)
        u = rng_u.normal(0, 5.0, 5000)
        a1, a2 = math.exp(-1.0 / 8.0), math.exp(-1.0 / 3.0)
        x1 = np.zeros(5000)
        y0 = np.zeros(5000)
        for k in range(2, 5000):
            x1[k] = a1 * x1[k - 1] + (1 - a1) * u[k - 2]
            y0[k] = a2 * y0[k - 1] + 1.0 * (1 - a2) * x1[k]
        ns = float(np.std(y0)) / (10.0 ** (7.0 / 20.0))
        y = y0 + np.random.default_rng(612).normal(0, ns, 5000)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.SOPDT]
        )
        assert result.success, result.reason
        d = result.to_dict()
        assert (d.get("evidence") or {}).get("lowSnr") is not None
        assert (d.get("preprocessing") or {}).get("lowSnr") is not None

    def test_single_window_failure_annotated(self):
        """单窗失败深低 SNR（S1 5dB 深噪声例同构造族）：失败+标注在场."""
        op_in, ya0 = _seg_sopdt_slow(5000, 0.0, 621)
        ns = float(np.std(ya0)) / (10.0 ** (5.0 / 20.0))
        rng2 = np.random.default_rng(622)
        y = ya0 + rng2.normal(0, ns, 5000)
        result = identify_from_history(
            op=op_in.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.SOPDT]
        )
        assert not result.success
        assert "低信噪比" in (result.reason or "")
        d = result.to_dict()
        assert d["success"] is False
        assert (d.get("preprocessing") or {}).get("lowSnr") is not None


# ---------------------------------------------------------------------------
# 保存/重读（快照 round trip；假会话复用 test_calc_snapshot 的 _SaveDB 程式）
# ---------------------------------------------------------------------------


async def _save_joint_snapshot(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import calc_snapshot as cs
    from tests.test_calc_snapshot import _SaveDB

    monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    monkeypatch.setattr(cs, "executable_build_digest", lambda: "g3appbuild01")

    op, pv = _two_segments_fopdt(
        TestJointWinnerLowSnrAnnotated.NOISE, 700, TestJointWinnerLowSnrAnnotated.SEED
    )
    result = identify_from_history(op=op.tolist(), pv=pv.tolist(), ts=1.0)
    assert result.success and result.best_model.evidence.joint_estimate is not None
    assert result.best_model.evidence.low_snr is not None
    result_dict = result.to_dict()

    n = len(op)
    s = _SaveDB()
    await save_identification_snapshot(
        s._db,
        loop_id="loop-g3app",
        ts_start=datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC),
        ts_end=datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC) + timedelta(seconds=n),
        timestamps_rel=[float(i) for i in range(n)],
        op=list(op),
        pv=list(pv),
        sp=None,
        mode=None,
        sampling_period_seconds=1.0,
        theta_estimate=None,
        candidate_model_types=["FOPDT", "SOPDT", "IPDT"],
        result_dict=result_dict,
        tuning_record_id="tr-g3app-1",
        dcs_template_revision="UNKNOWN_DCS_TEMPLATE",
        valid_rate=0.99,
    )
    return result_dict


class TestSnapshotRoundTrip:
    async def test_joint_low_snr_saved_and_reloaded(self, tmp_path, monkeypatch):
        """保存/重读：joint 聚合标注在 manifest（preprocessing+resultEvidence）保持."""
        import json

        from app.services import calc_snapshot as cs

        result_dict = await _save_joint_snapshot(tmp_path, monkeypatch)
        snap_dir = next((tmp_path / "snaps").rglob("*.manifest.json"))
        manifest = json.loads(snap_dir.read_text(encoding="utf-8"))
        npz = snap_dir.with_name(snap_dir.name.replace(".manifest.json", ".npz"))
        with np.load(npz, allow_pickle=False) as z:
            assert "consumed__sig_op" in z.files

        # 层 3：preprocessing 整包保存（含 lowSnr）
        prep = manifest["searchAudit"]["preprocessing"]
        assert (prep or {}).get("lowSnr") is not None
        assert prep["lowSnr"]["basis"] == "WELCH_PSD_NOISE_FLOOR_JOINT_AGGREGATE"
        assert prep["lowSnr"]["snrDb"] == result_dict["evidence"]["lowSnr"]["snrDb"]
        # 层 5：resultEvidence.lowSnr 加性键（G3-APP 消费链透出）
        low = manifest["resultEvidence"]["lowSnr"]
        assert low is not None
        assert low["mode"] == "PARAM_ACCURACY_UNCOMMITTED"
        assert low["snrDb"] < LOW_SNR_TIER_DB
        # joint 段块记录在包内（P-22 证据随包）
        mw = (manifest["searchAudit"].get("preprocessing") or {}).get("multiWindow") or {}
        assert (mw.get("jointEstimate") or {}).get("ran") is True

        # 读回路径（_read_files 重读——hash 完整性与 JSON 往返）
        manifest2, _arrays = cs._read_files(npz, snap_dir)
        low2 = manifest2["resultEvidence"]["lowSnr"]
        assert low2 == low
