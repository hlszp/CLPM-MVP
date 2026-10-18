"""不可变计算输入包（NPZ + JSON manifest）存取/校验/保留服务（P2-03）.

契约来源：《01-系统改造优化方案》§3.2 + 《P0-02-数值语义冻结-v2》§3
（DEC-10 已裁决按推荐）+ 《P2-03-辨识证据契约冻结.md》（07 号文六层语义）。

核心不变式（冻结稿）：
1. ``datasetRef`` 只是绑定身份；数值输入身份 = ``inputHash``（NPZ 数组 +
   manifest 全部内容的 SHA256，volatile 字段除外）——相同绑定但值/质量/
   mask 改变必须得到不同 inputHash；相同内容必然同 hash（确定性）。
2. 写入次序：**先原子写文件（同目录临时名 + fsync + rename，rename 前
   核 hash）再提交 DB 引用**；崩溃留下的未引用文件由隔离清理任务按
   契约删除（>24h 无 DB 行）。
3. 重放**读原包**，不重查当前源数据冒充原输入；损坏/过期/缺构建/
   schema 主版本不匹配 → 显式拒绝"不可完整复现"，绝不静默降级。
4. 保留契约（DEC-10）：NORMAL 默认 90 天；被结果 record/方案/审核证据
   引用的包 FROZEN 永不清理；清理先标 ``cleaned_at`` 再删文件。
5. NumPy 禁 pickle：NPZ 只写数值/布尔定长 dtype（object dtype 写入前
   拒绝），读取恒 ``allow_pickle=False``。

schemaVersion 策略（P2-03 Owner 定稿，见交接文档）：manifest 主版本冻结
``1``，次版本随六层语义**版本化扩展**递增（1.1 = P2-03 初版；1.2 = P2-04
增补 engineeringFacts 工程事实节）。读取端按主版本门禁：主版本一致即接受，
未知**可选**节按显式 null/"未计算"处理；主版本不一致显式拒绝。禁止在次
版本内改字段语义（只加不改）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BizError
from app.core.timeparse import to_naive_utc
from app.models.calc_dataset_snapshot import (
    KIND_DIAGNOSIS_RAW,
    KIND_IDENTIFICATION,
    KIND_KPI_GRID,
    KIND_KPI_ORIGINAL_EVENTS,
    RETENTION_FROZEN,
    RETENTION_NORMAL,
    CalculationDatasetSnapshot,
)

logger = logging.getLogger(__name__)

# manifest 结构版本（主版本 1 = 01§3.2 冻结的 CalculationContext schema 系；
# 次版本 1 = P2-03 初版含 07 号文六层可选节；
# 次版本 2 = P2-04 增补 engineeringFacts 节——回路工程事实：控制作用方向/
# 实际 OP 限幅/PV 量程与单位/PID 表示与时间单位（未知态显式 UNKNOWN）/
# DCS 与工程模板 revisionRef。只加不改：1.1 包缺本节仍按主版本门禁可读，
# 读取端按节缺失=当时未记录处理，不伪补）
# 次版本 3 = P3-08a（R17 冻结：只加不改）——发布上下文节
# （``source.assessmentProfile/releaseGeneration/dataPolicyVersion``，上线
# 清单六要素之三，另三要素 algorithmVersion/configRevision/executableBuildDigest
# 1.2 已有）＋派生网格身份节（``derivation``：原事件包 id/hash+派生方法）＋
# 新包种类 ``KPI_ORIGINAL_EVENTS``（原事件包，10§3.5 两类对照输入区分）。
# 旧 1.1/1.2 包读取行为完全不变（缺节=当时未记录）。
SNAPSHOT_SCHEMA_VERSION = "1.3"
_SUPPORTED_SCHEMA_MAJOR = 1

# DEC-10：NORMAL 包默认保留 90 天
RETENTION_DAYS_DEFAULT = 90
# 冻结稿 §3.3 清理规则①：未引用残留文件存活下限（写入中/写入失败的
# 事务窗口），超过才可按残留清理
ORPHAN_FILE_GRACE_HOURS = 24

UQ_SNAPSHOT = "uq_calc_dataset_snapshot_content_ref"

# inputHash 计算域中排除的 volatile manifest 字段（不属于输入身份：
# 创建时刻与创建引用随运行实例变化，不随内容变化）
_VOLATILE_MANIFEST_KEYS = frozenset({"createdAt", "createdByRecordId"})

# 显式拒绝错误码（统一 409：目标存在但按契约不可完整复现）
ERR_SNAPSHOT_NOT_SAVED = "ERR_SNAPSHOT_NOT_SAVED"
ERR_SNAPSHOT_CLEANED = "ERR_SNAPSHOT_CLEANED"
ERR_SNAPSHOT_FILES_MISSING = "ERR_SNAPSHOT_FILES_MISSING"
ERR_SNAPSHOT_HASH_MISMATCH = "ERR_SNAPSHOT_HASH_MISMATCH"
ERR_SNAPSHOT_EXPIRED = "ERR_SNAPSHOT_EXPIRED"
ERR_SNAPSHOT_BUILD_UNAVAILABLE = "ERR_SNAPSHOT_BUILD_UNAVAILABLE"
ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE = "ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE"
ERR_SNAPSHOT_SCHEMA_UNSUPPORTED = "ERR_SNAPSHOT_SCHEMA_UNSUPPORTED"

# 构建摘要不可用时的显式标记（不伪补版本）
UNVERSIONED_BUILD = "UNVERSIONED_BUILD"


# ---------------------------------------------------------------------------
# 存储目录与受控执行构建摘要
# ---------------------------------------------------------------------------


def snapshot_root() -> Path:
    """包文件根目录（本地持久化共享目录；禁止静态目录对外暴露）.

    优先 ``CLPM_CALC_SNAPSHOT_DIR``（指向共享卷，部署时配置）；未配置时
    落 ``data/calc-snapshots``（开发默认）。目录结构 ``{root}/{yyyymm}/``。
    """
    configured = getattr(settings, "CLPM_CALC_SNAPSHOT_DIR", "") or ""
    root = Path(configured) if configured else Path("data") / "calc-snapshots"
    return root


def _month_dir(ts: datetime) -> Path:
    return snapshot_root() / f"{ts:%Y%m}"


def snapshot_file_paths(snapshot_id: str, ts_hint: datetime | None = None) -> list[Path]:
    """snapshotId 的候选文件路径（npz + manifest）。

    目录按创建月份分层；清理/校验场景未知月份时扫全部月份目录。
    """
    if ts_hint is not None:
        d = _month_dir(ts_hint)
        return [d / f"{snapshot_id}.npz", d / f"{snapshot_id}.manifest.json"]
    root = snapshot_root()
    if not root.exists():
        return []
    hits: list[Path] = []
    for month in sorted(p for p in root.iterdir() if p.is_dir()):
        for suffix in (".npz", ".manifest.json"):
            p = month / f"{snapshot_id}{suffix}"
            if p.exists():
                hits.append(p)
    return hits


def _code_sha() -> str | None:
    """受控代码 SHA：环境注入（CI 构建）优先，其次 git HEAD，均无则 None."""
    env_sha = os.environ.get("CLPM_BUILD_CODE_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        sha = out.stdout.strip()
        return sha or None
    except Exception:  # noqa: BLE001
        return None


def _lock_sha() -> str | None:
    """依赖锁摘要：backend uv.lock 的 SHA256（前 16 位）。"""
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    try:
        return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


@lru_cache(maxsize=1)
def executable_build_digest() -> str:
    """受控执行构建摘要（代码 SHA + 依赖锁摘要）.

    口径：``sha256("clpm-build-v1|" + code_sha + "|" + lock_sha)[:16]``。
    - code_sha：环境变量 ``CLPM_BUILD_CODE_SHA``（CI 注入）> git HEAD；
    - lock_sha：``backend/uv.lock`` SHA256 前 16 位；
    - 两者均不可得（如剥离 git 的运行环境且未注入）→ ``UNVERSIONED_BUILD``
      显式标记（此时新包不可主张受构建复现，重放按"缺构建"口径裁决）。
    结果进程内缓存（无 asyncio 原语；fork worker 各自计算一次）。
    """
    code = _code_sha()
    lock = _lock_sha()
    if not code and not lock:
        return UNVERSIONED_BUILD
    return hashlib.sha256(f"clpm-build-v1|{code or '-'}|{lock or '-'}".encode()).hexdigest()[:16]


def _read_retained_builds_file() -> dict[str, Any]:
    """读取 retained-builds.json（缺失/损坏返回空 dict，诚实口径不默认放行）."""
    path = snapshot_root() / "retained-builds.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def retained_build_digests() -> set[str]:
    """环境 keeper 保留的旧构建摘要清单（DEC-10 保留责任主体维护）.

    文件 ``{snapshot_root}/retained-builds.json``：``{"digests": [...],
    "builds": {"<digest>": {执行配方}}}``（builds 为 G2-INC-b 增补，只加
    不改）。重放门禁：当前构建 digest 与包 digest 不一致且不在保留清单 →
    显式拒绝。**具备可执行配方（builds 键）视为已保留**；文件缺失 = 仅
    当前构建可用。

    门禁只回答"构建是否保留"；执行能力路由（登记 ≠ 可执行）见
    ``app.services.controlled_build``。
    """
    data = _read_retained_builds_file()
    digests = {str(d) for d in data.get("digests", [])}
    builds = data.get("builds")
    if isinstance(builds, dict):
        digests |= {str(k) for k in builds}
    return digests


# ---------------------------------------------------------------------------
# inputHash（内容寻址）
# ---------------------------------------------------------------------------


def _canonical_json(value: Any) -> str:
    """确定性 JSON（键排序、紧凑分隔符；非 JSON 原生类型先字符串化）。"""

    def _norm(v: Any) -> Any:
        if isinstance(v, dict):
            return {str(k): _norm(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_norm(x) for x in v]
        if isinstance(v, (bool, int, float, str)) or v is None:
            return v
        return str(v)

    return json.dumps(_norm(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_input_hash(manifest: dict[str, Any], arrays: dict[str, np.ndarray]) -> str:
    """内容 SHA256：manifest（volatile 字段除外）+ 全部 NPZ 数组字节.

    数组按 key 排序逐个入哈希（key + dtype + shape + tobytes），dtype/shape
    显式入哈希——同值不同 dtype 视为不同输入身份（复现必须同 dtype）。
    """
    core = {k: v for k, v in manifest.items() if k not in _VOLATILE_MANIFEST_KEYS}
    h = hashlib.sha256()
    h.update(b"clpm-calc-snapshot-v1\x00")
    h.update(_canonical_json(core).encode())
    for key in sorted(arrays):
        arr = np.ascontiguousarray(arrays[key])
        h.update(b"\x00" + key.encode("utf-8"))
        h.update(f"|{arr.dtype.str}|{arr.shape}|".encode())
        h.update(arr.tobytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 原子写入
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, writer) -> None:
    """同目录临时名写入 + fsync + rename（rename 成功即对外可见）."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _write_npz(arrays: dict[str, np.ndarray]):
    """NPZ 写入闭包（禁 pickle：object dtype 写入前拒绝）."""
    import io

    def _write(f) -> None:
        buf = io.BytesIO()
        np.savez_compressed(buf, **arrays)
        f.write(buf.getvalue())

    return _write


def _check_arrays_pickle_free(arrays: dict[str, np.ndarray]) -> None:
    for key, arr in arrays.items():
        if arr.dtype == np.dtype("O") or arr.dtype.hasobject:
            raise ValueError(f"输入包数组 {key} 为 object dtype（禁 pickle，拒绝写入）")


def _describe_arrays(arrays: dict[str, np.ndarray]) -> dict[str, dict[str, Any]]:
    return {
        key: {"dtype": str(arr.dtype.str), "shape": list(arr.shape)}
        for key, arr in sorted(arrays.items())
    }


# ---------------------------------------------------------------------------
# 保存（原子文件 → DB 引用）
# ---------------------------------------------------------------------------


async def save_calculation_snapshot(
    db: AsyncSession,
    *,
    kind: str,
    ts_start: datetime,
    ts_end: datetime,
    dataset_ref: str,
    arrays: dict[str, np.ndarray],
    manifest_extra: dict[str, Any],
    created_by_record_id: str | None = None,
    loop_id: str | None = None,
    retention_days: int = RETENTION_DAYS_DEFAULT,
    schema_version: str = SNAPSHOT_SCHEMA_VERSION,
) -> CalculationDatasetSnapshot | None:
    """保存不可变输入包：先原子写文件（核 hash）再提交 DB 引用.

    幂等：同 (dataset_ref, input_hash, 窗口, created_by_record_id) 已存在
    时复用既有行（同任务重试不重复建包）；显式重评=新创建引用 → 新行。
    文件写入失败抛异常（调用方决定降级口径）；DB 冲突复用后，若本轮已
    写出同 snapshotId 文件则保留（内容寻址，同 id 必同内容）。
    """
    _check_arrays_pickle_free(arrays)
    now = datetime.now(UTC).replace(tzinfo=None)

    manifest: dict[str, Any] = {
        "schemaVersion": schema_version,
        "kind": kind,
        "createdAt": now.isoformat(),
        "createdByRecordId": created_by_record_id,
        **manifest_extra,
    }
    manifest["arrays"] = _describe_arrays(arrays)
    manifest.setdefault("source", {})["executableBuildDigest"] = executable_build_digest()

    input_hash = compute_input_hash(manifest, arrays)

    key = {
        "dataset_ref": str(dataset_ref)[:64],
        "input_hash": input_hash,
        "ts_start": to_naive_utc(ts_start),
        "ts_end": to_naive_utc(ts_end),
        "created_by_record_id": created_by_record_id,
    }
    existing = (
        await db.execute(
            select(CalculationDatasetSnapshot).where(
                CalculationDatasetSnapshot.dataset_ref == key["dataset_ref"],
                CalculationDatasetSnapshot.input_hash == input_hash,
                CalculationDatasetSnapshot.ts_start == key["ts_start"],
                CalculationDatasetSnapshot.ts_end == key["ts_end"],
                CalculationDatasetSnapshot.created_by_record_id.is_(key["created_by_record_id"])
                if created_by_record_id is None
                else CalculationDatasetSnapshot.created_by_record_id == created_by_record_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    snapshot_id = str(uuid4())
    month = _month_dir(now)
    npz_path = month / f"{snapshot_id}.npz"
    manifest_path = month / f"{snapshot_id}.manifest.json"

    _atomic_write(npz_path, _write_npz(arrays))
    _atomic_write(
        manifest_path,
        lambda f: f.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()),
    )

    # rename 后核 hash（读回重算，防写入损坏）
    r_manifest, r_arrays = _read_files(npz_path, manifest_path)
    if compute_input_hash(r_manifest, r_arrays) != input_hash:
        npz_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise BizError(
            code=ERR_SNAPSHOT_HASH_MISMATCH,
            message="输入包写入后校验失败（hash 不一致），已丢弃残留文件",
            status_code=500,
        )

    size_bytes = npz_path.stat().st_size + manifest_path.stat().st_size
    expires_at = now + timedelta(days=retention_days)
    stmt = (
        pg_insert(CalculationDatasetSnapshot)
        .values(
            id=snapshot_id,
            dataset_ref=key["dataset_ref"],
            input_hash=input_hash,
            ts_start=key["ts_start"],
            ts_end=key["ts_end"],
            schema_version=schema_version,
            kind=kind,
            created_by_record_id=created_by_record_id,
            retention_class=RETENTION_NORMAL,
            frozen_by_record_ids=[],
            expires_at=expires_at,
            size_bytes=size_bytes,
            loop_id=loop_id,
        )
        .on_conflict_do_nothing(constraint=UQ_SNAPSHOT)
        .returning(CalculationDatasetSnapshot)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        # 并发竞态：他人已插入同键包 → 复用（并清理本轮文件残留）
        row = (
            await db.execute(
                select(CalculationDatasetSnapshot).where(
                    CalculationDatasetSnapshot.dataset_ref == key["dataset_ref"],
                    CalculationDatasetSnapshot.input_hash == input_hash,
                    CalculationDatasetSnapshot.ts_start == key["ts_start"],
                    CalculationDatasetSnapshot.ts_end == key["ts_end"],
                )
            )
        ).scalar_one_or_none()
        if row is not None and str(row.id) != snapshot_id:
            npz_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
    logger.info(
        "输入包已保存 snapshot=%s kind=%s loop=%s 窗口=%s~%s size=%dB",
        snapshot_id,
        kind,
        loop_id,
        ts_start,
        ts_end,
        size_bytes,
    )
    return row


# ---------------------------------------------------------------------------
# 读取（重放读原包，不重查源）
# ---------------------------------------------------------------------------


def _read_files(
    npz_path: Path, manifest_path: Path
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    if not npz_path.exists() or not manifest_path.exists():
        raise BizError(
            code=ERR_SNAPSHOT_FILES_MISSING,
            message=f"输入包文件缺失（{npz_path.name}）",
            status_code=409,
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BizError(
            code=ERR_SNAPSHOT_HASH_MISMATCH,
            message=f"输入包 manifest 损坏：{exc}",
            status_code=409,
        ) from exc
    try:
        with np.load(npz_path, allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
    except Exception as exc:  # noqa: BLE001
        raise BizError(
            code=ERR_SNAPSHOT_HASH_MISMATCH,
            message=f"输入包 NPZ 损坏（禁 pickle 读取）：{exc}",
            status_code=409,
        ) from exc
    return manifest, arrays


class LoadedSnapshot:
    """通过全部复现前置检查的输入包（重放就绪）.

    ``npz_path``/``manifest_path`` 为已定位的包文件路径（G2-INC-b：受控
    构建子进程按路径读原包执行）。
    """

    def __init__(
        self,
        row: CalculationDatasetSnapshot,
        manifest: dict,
        arrays: dict,
        npz_path: Path,
        manifest_path: Path,
    ):
        self.snapshot_id = str(row.id)
        self.row = row
        self.manifest = manifest
        self.arrays = arrays
        self.input_hash = row.input_hash
        self.npz_path = npz_path
        self.manifest_path = manifest_path


async def load_snapshot_for_replay(
    db: AsyncSession,
    snapshot_id: str,
    *,
    now: datetime | None = None,
) -> LoadedSnapshot:
    """按保留/构建/schema 契约加载输入包；任一不满足 → 显式拒绝.

    检查次序（全部显式 BizError，不静默降级）：
    1. DB 行不存在 / 已清理（cleaned_at）；
    2. retention=NORMAL 且 expires_at 已过（即使文件仍在，过期即拒绝完整复现）；
    3. 文件缺失 / manifest/NPZ 损坏；
    4. 内容 hash 与 DB 存值不一致（损坏/篡改）；
    5. schema 主版本不支持；
    6. 受控构建不可用（当前 digest ≠ 包 digest 且不在保留清单）。
    """
    row = (
        await db.execute(
            select(CalculationDatasetSnapshot).where(
                CalculationDatasetSnapshot.id == str(snapshot_id)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise BizError(
            code=ERR_SNAPSHOT_FILES_MISSING,
            message=f"输入包元数据不存在（snapshotId={snapshot_id}）",
            status_code=409,
        )
    if row.cleaned_at is not None:
        raise BizError(
            code=ERR_SNAPSHOT_CLEANED,
            message=f"输入包已按保留契约清理（cleanedAt={row.cleaned_at.isoformat()}），不可完整复现",
            status_code=409,
        )
    now_naive = to_naive_utc(now) if now is not None else datetime.now(UTC).replace(tzinfo=None)
    if row.retention_class == RETENTION_NORMAL and row.expires_at is not None:
        if now_naive > row.expires_at:
            raise BizError(
                code=ERR_SNAPSHOT_EXPIRED,
                message=(
                    f"输入包已过保留期（expiresAt={row.expires_at.isoformat()}），"
                    "不可完整复现；如需固定保留请升级 FROZEN"
                ),
                status_code=409,
            )

    npz_path, manifest_path = _locate_files(row)
    manifest, arrays = _read_files(npz_path, manifest_path)

    if compute_input_hash(manifest, arrays) != row.input_hash:
        raise BizError(
            code=ERR_SNAPSHOT_HASH_MISMATCH,
            message="输入包内容 hash 与元数据不一致（损坏或被篡改），不可完整复现",
            status_code=409,
        )

    schema_major = str(manifest.get("schemaVersion", "1")).split(".")[0]
    if schema_major != str(_SUPPORTED_SCHEMA_MAJOR):
        raise BizError(
            code=ERR_SNAPSHOT_SCHEMA_UNSUPPORTED,
            message=(
                f"输入包 schema 主版本 {schema_major} 不受当前读取端支持"
                f"（支持 {_SUPPORTED_SCHEMA_MAJOR}.x），不可完整复现"
            ),
            status_code=409,
        )

    pkg_digest = (manifest.get("source") or {}).get("executableBuildDigest")
    cur_digest = executable_build_digest()
    if pkg_digest != cur_digest and pkg_digest not in retained_build_digests():
        raise BizError(
            code=ERR_SNAPSHOT_BUILD_UNAVAILABLE,
            message=(
                f"包绑定的执行构建 {pkg_digest} 未保留（当前 {cur_digest}，"
                "保留清单不含该版本），不可完整复现；由环境 keeper 按 DEC-10 保留责任补齐"
            ),
            status_code=409,
        )
    return LoadedSnapshot(row, manifest, arrays, npz_path, manifest_path)


def _locate_files(row: CalculationDatasetSnapshot) -> tuple[Path, Path]:
    paths = snapshot_file_paths(str(row.id), ts_hint=row.created_at or row.ts_start)
    npz = [p for p in paths if p.suffix == ".npz"]
    man = [p for p in paths if p.name.endswith(".manifest.json")]
    month = _month_dir(row.created_at or row.ts_start)
    npz_path = npz[0] if npz else month / f"{row.id}.npz"
    manifest_path = man[0] if man else month / f"{row.id}.manifest.json"
    return npz_path, manifest_path


# ---------------------------------------------------------------------------
# 固定引用（FROZEN）与解除
# ---------------------------------------------------------------------------


async def freeze_snapshot(
    db: AsyncSession,
    snapshot_id: str,
    *,
    reason: str,
    record_ids: list[str],
) -> CalculationDatasetSnapshot:
    """升级 FROZEN：被方案/实施/审核证据引用的包固定保留（永不清理）."""
    row = (
        await db.execute(
            select(CalculationDatasetSnapshot)
            .where(CalculationDatasetSnapshot.id == str(snapshot_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise BizError(
            code=ERR_SNAPSHOT_FILES_MISSING,
            message=f"输入包不存在（snapshotId={snapshot_id}）",
            status_code=404,
        )
    existing = list(row.frozen_by_record_ids or [])
    merged = list(dict.fromkeys(existing + [str(r) for r in record_ids]))
    row.retention_class = RETENTION_FROZEN
    row.frozen_reason = reason[:200]
    row.frozen_by_record_ids = merged
    return row


async def unfreeze_snapshot(
    db: AsyncSession,
    snapshot_id: str,
    *,
    record_ids: list[str],
) -> CalculationDatasetSnapshot:
    """解除部分固定引用；无剩余引用且已过期时降级 NORMAL（仍受 expires_at 保护）."""
    row = (
        await db.execute(
            select(CalculationDatasetSnapshot)
            .where(CalculationDatasetSnapshot.id == str(snapshot_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise BizError(
            code=ERR_SNAPSHOT_FILES_MISSING,
            message=f"输入包不存在（snapshotId={snapshot_id}）",
            status_code=404,
        )
    removed = {str(x) for x in record_ids}
    remaining = [r for r in (row.frozen_by_record_ids or []) if r not in removed]
    row.frozen_by_record_ids = remaining
    if not remaining:
        row.retention_class = RETENTION_NORMAL
        row.frozen_reason = None
        if row.expires_at is None:
            base = row.created_at or datetime.now(UTC).replace(tzinfo=None)
            row.expires_at = base + timedelta(days=RETENTION_DAYS_DEFAULT)
    return row


# ---------------------------------------------------------------------------
# 引用核查与清理（隔离清理任务；单 worker 串行）
# ---------------------------------------------------------------------------


async def find_referencing_records(db: AsyncSession, snapshot_id: str) -> dict[str, list[str]]:
    """跨表核查包引用（结果账本 / tuning_record / diagnosis_run / 模型版本）."""
    from app.models.calculation_result import CalculationResultRecord
    from app.models.diagnosis_run import DiagnosisRun
    from app.models.process_model_version import ProcessModelVersion
    from app.models.tuning import TuningRecord

    sid = str(snapshot_id)
    refs: dict[str, list[str]] = {}
    result = await db.execute(
        select(CalculationResultRecord.id).where(CalculationResultRecord.dataset_snapshot_id == sid)
    )
    refs["calculation_result_record"] = [str(r) for r in result.scalars().all()]
    result = await db.execute(
        select(TuningRecord.id).where(TuningRecord.dataset_snapshot_id == sid)
    )
    refs["tuning_record"] = [str(r) for r in result.scalars().all()]
    result = await db.execute(
        select(DiagnosisRun.id).where(DiagnosisRun.dataset_snapshot_id == sid)
    )
    refs["diagnosis_run"] = [str(r) for r in result.scalars().all()]
    result = await db.execute(
        select(ProcessModelVersion.id).where(ProcessModelVersion.dataset_snapshot_id == sid)
    )
    refs["process_model_version"] = [str(r) for r in result.scalars().all()]
    return refs


async def cleanup_expired_snapshots(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按保留契约清理（冻结稿 §3.3 有序规则；返回报告供任务落账）.

    规则序：
    ① 文件存在但无 DB 引用且创建超过 24h → 删文件（写入失败/崩溃残留）；
    ② NORMAL 且 expires_at 已过且**未被任何 record/模型版本引用**且
       frozen_by_record_ids 为空 → 先标 cleaned_at → 删文件；
    ③ FROZEN 永不清理（解除引用 + 过期后先降级 NORMAL 才进 ②）。
    """
    now_naive = to_naive_utc(now) if now is not None else datetime.now(UTC).replace(tzinfo=None)
    report: dict[str, Any] = {
        "ranAt": now_naive.isoformat(),
        "dryRun": dry_run,
        "orphanFilesRemoved": [],
        "expiredCleaned": [],
        "frozenSkipped": 0,
        "referencedExpiredSkipped": [],
    }

    # ① 未引用残留文件
    db_ids: set[str] = {
        str(r) for r in (await db.execute(select(CalculationDatasetSnapshot.id))).scalars().all()
    }
    grace = timedelta(hours=ORPHAN_FILE_GRACE_HOURS)
    now_epoch = time.time()  # 真实 epoch（naive datetime .timestamp() 会按本地时区偏移）
    root = snapshot_root()
    if root.exists():
        for month_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for f in month_dir.iterdir():
                if not f.is_file() or f.name.startswith("."):
                    continue
                stem = f.stem if f.name.endswith(".manifest.json") else f.stem
                sid = stem.split(".")[0]
                if sid in db_ids or sid in {"retained-builds"}:
                    continue
                if now_epoch - f.stat().st_mtime > grace.total_seconds():
                    report["orphanFilesRemoved"].append(str(f))
                    if not dry_run:
                        f.unlink(missing_ok=True)

    # ② NORMAL 过期未引用 → 标记 + 删文件；③ FROZEN 跳过
    expired = (
        (
            await db.execute(
                select(CalculationDatasetSnapshot).where(
                    CalculationDatasetSnapshot.retention_class == RETENTION_NORMAL,
                    CalculationDatasetSnapshot.cleaned_at.is_(None),
                    CalculationDatasetSnapshot.expires_at.is_not(None),
                    CalculationDatasetSnapshot.expires_at < now_naive,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in expired:
        if row.frozen_by_record_ids:
            report["referencedExpiredSkipped"].append(str(row.id))
            continue
        refs = await find_referencing_records(db, str(row.id))
        if any(refs[t] for t in refs):
            report["referencedExpiredSkipped"].append(str(row.id))
            continue
        if not dry_run:
            row.cleaned_at = now_naive
        npz_path, manifest_path = _locate_files(row)
        report["expiredCleaned"].append(str(row.id))
        if not dry_run:
            npz_path.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)

    frozen_rows = (
        (
            await db.execute(
                select(CalculationDatasetSnapshot.id).where(
                    CalculationDatasetSnapshot.retention_class == RETENTION_FROZEN
                )
            )
        )
        .scalars()
        .all()
    )
    report["frozenSkipped"] = len(frozen_rows)
    return report


# ---------------------------------------------------------------------------
# KPI_GRID 序列化（DataBlock ↔ NPZ 数组 + manifest 块描述）
# ---------------------------------------------------------------------------


def _ns_int(datetimes: list[datetime]) -> np.ndarray:
    """datetime 列表 → epoch 纳秒 int64（naive 视为 UTC；无逐点 .timestamp 慢路径）."""
    base = np.datetime64("1970-01-01T00:00:00", "ns")
    out = np.empty(len(datetimes), dtype=np.int64)
    for i, dt in enumerate(datetimes):
        naive = to_naive_utc(dt) if dt.tzinfo else dt
        out[i] = (np.datetime64(naive.isoformat(), "ns") - base).astype(np.int64)
    return out


def _ns_to_datetimes(ns: np.ndarray) -> list[datetime]:
    """epoch 纳秒 int64 → naive UTC datetime 列表（微秒精度整数运算）."""
    epoch = datetime(1970, 1, 1)
    return [epoch + timedelta(microseconds=int(v) // 1000) for v in ns]


def serialize_signal(values: list[Any], key: str, arrays: dict[str, np.ndarray]) -> None:
    """信号列 → float64 + nullmask（None→NaN；显式 null 位置单独 mask）."""
    n = len(values)
    nullmask = np.zeros(n, dtype=bool)
    arr = np.full(n, np.nan, dtype=np.float64)
    for i, v in enumerate(values):
        if v is None:
            nullmask[i] = True
        else:
            arr[i] = float(v)
    arrays[key] = arr
    arrays[key.replace("__sig_", "__null_")] = nullmask


def serialize_data_blocks(bundles: list[Any]) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    """MetricDataBundle 列表 →（NPZ 数组 + manifest 块描述）.

    每块：``blk{i}__ts``（epoch ns）、``blk{i}__sig_{name}``（float64，
    None→NaN）、``blk{i}__null_{name}``、``blk{i}__valid_{name}``（bool，
    有则）。outlier_reasons / quality_summary / consecutive_segments 等
    非数组元数据进 manifest（不截断、不静默丢弃）。
    """
    import dataclasses

    arrays: dict[str, np.ndarray] = {}
    blocks_meta: list[dict[str, Any]] = []
    for i, bundle in enumerate(bundles):
        block = bundle.data_block
        prefix = f"blk{i}"
        arrays[f"{prefix}__ts"] = _ns_int(list(block.timestamps))
        for name, values in block.signals.items():
            serialize_signal(list(values), f"{prefix}__sig_{name}", arrays)
        for name, flags in (block.validity or {}).items():
            if len(flags) == len(block.timestamps):
                arrays[f"{prefix}__valid_{name}"] = np.asarray(flags, dtype=bool)
        outlier = {}
        for name, reasons in (block.outlier_reasons or {}).items():
            outlier[name] = [list(r) for r in reasons] if reasons else []
        meta: dict[str, Any] = {
            "metricCode": getattr(bundle, "metric_code", None),
            "tagGroup": block.tag_group,
            "samplingFreq": block.sampling_freq,
            "configVersion": block.config_version,
            "preprocessVersion": block.preprocess_version,
            "pointCount": int(block.point_count),
            "controlType": block.control_type,
            "loopConfidenceLevel": block.loop_confidence_level,
            "loopValidRate": float(block.loop_valid_rate),
            "consecutiveSegments": [[int(a), int(b)] for a, b in block.consecutive_segments],
            "outlierReasons": outlier,
        }
        if getattr(block, "series_context", None) is not None:
            sc = block.series_context

            def _iso(v: Any) -> str | None:
                # G2 增量真实链修复：gridStart/gridEnd 为 datetime，直接入
                # manifest 会 json.dumps 崩溃（P2-03 单测未覆盖 point 网格包）
                return v.isoformat() if hasattr(v, "isoformat") else v

            meta["seriesContext"] = {
                "layout": getattr(sc, "layout", None),
                "gridStart": _iso(getattr(sc, "grid_start", None)),
                "gridEnd": _iso(getattr(sc, "grid_end", None)),
                "gridPeriodS": getattr(sc, "grid_period_s", None),
                "expectedSlots": getattr(sc, "expected_slots", None),
                "datasetRef": getattr(sc, "dataset_ref", None),
                "bindingVersion": getattr(sc, "binding_version", None),
                "coverageRevision": getattr(sc, "coverage_revision", None),
            }
        try:
            meta["qualitySummary"] = dataclasses.asdict(block.quality_summary)
        except Exception:  # noqa: BLE001
            meta["qualitySummary"] = None
        # bundle 级元数据（重放重建 MetricDataBundle 必需；不截断）
        meta["maskExpression"] = getattr(bundle, "mask_expression", None)
        meta["maskedIndices"] = list(getattr(bundle, "masked_indices", None) or [])
        try:
            meta["lineage"] = dataclasses.asdict(bundle.lineage)
        except Exception:  # noqa: BLE001
            meta["lineage"] = None
        blocks_meta.append(meta)
    return arrays, blocks_meta


def _restore_series_context(sc_meta: Any):
    """manifest.seriesContext → SeriesContext（重放保真，G2 增量真实链修复）.

    P2-03 缺口：deserialize 不还原 series_context → gap 守卫（AD03/I04，
    依赖 ``is_point``）在重放侧退化为 legacy 放行——点网格包 v1 被
    GAP_SENSITIVE 拒绝的指标在重放侧算出数值，"同构建同包全等"被破坏。
    覆盖/边界等高级字段不落包（保持既有 manifest 形态），仅还原能使
    守卫与网格口径一致的骨架字段；缺字段/损坏 → None（legacy 行为）。
    """
    if not isinstance(sc_meta, dict):
        return None
    try:
        from app.contracts.series_context import SeriesContext

        return SeriesContext(
            layout=str(sc_meta.get("layout") or "legacy"),
            grid_start=datetime.fromisoformat(str(sc_meta["gridStart"])),
            grid_end=datetime.fromisoformat(str(sc_meta["gridEnd"])),
            grid_period_s=float(sc_meta.get("gridPeriodS") or 1.0),
            expected_slots=int(sc_meta.get("expectedSlots") or 0),
            dataset_ref=str(sc_meta.get("datasetRef") or ""),
            binding_version=sc_meta.get("bindingVersion"),
            coverage_revision=sc_meta.get("coverageRevision"),
        )
    except (KeyError, ValueError, TypeError):
        return None


def deserialize_data_blocks(arrays: dict[str, np.ndarray], blocks_meta: list[dict[str, Any]]):
    """NPZ 数组 + manifest 块描述 → (DataBlock 列表, metric_code 列表)."""
    from app.contracts.data_types import (
        DataBlock,
        DataLineage,
        MetricDataBundle,
        QualitySummary,
    )

    bundles = []
    codes: list[str | None] = []
    for i, meta in enumerate(blocks_meta):
        prefix = f"blk{i}"
        ts = _ns_to_datetimes(arrays[f"{prefix}__ts"])
        signals: dict[str, list[Any]] = {}
        for key in arrays:
            if key.startswith(f"{prefix}__sig_"):
                name = key.split("__sig_", 1)[1]
                nullmask = arrays.get(f"{prefix}__null_{name}")
                vals: list[Any] = []
                arr = arrays[key]
                for j in range(len(arr)):
                    if nullmask is not None and bool(nullmask[j]):
                        vals.append(None)
                    elif bool(np.isnan(arr[j])):
                        vals.append(None)
                    else:
                        vals.append(float(arr[j]))
                signals[name] = vals
        validity = {
            key.split("__valid_", 1)[1]: [bool(v) for v in arrays[key]]
            for key in arrays
            if key.startswith(f"{prefix}__valid_")
        }
        outlier_reasons = {
            name: [list(r) for r in reasons]
            for name, reasons in (meta.get("outlierReasons") or {}).items()
        }
        quality = meta.get("qualitySummary")
        block = DataBlock(
            data_block_id=f"{prefix}-replay",
            loop_id=meta.get("loopId") or "replay",
            tag_group=meta.get("tagGroup") or "",
            sampling_freq=meta.get("samplingFreq") or "1s",
            timestamps=ts,
            signals=signals,
            validity=validity,
            outlier_reasons=outlier_reasons,
            quality_summary=QualitySummary(**quality) if quality else QualitySummary(),
            consecutive_segments=[(int(a), int(b)) for a, b in meta.get("consecutiveSegments", [])],
            config_version=meta.get("configVersion") or "v1",
            preprocess_version=meta.get("preprocessVersion") or "pre_v1",
            point_count=int(meta.get("pointCount") or len(ts)),
            control_type=meta.get("controlType"),
            loop_confidence_level=meta.get("loopConfidenceLevel") or "E",
            loop_valid_rate=float(meta.get("loopValidRate") or 0.0),
            series_context=_restore_series_context(meta.get("seriesContext")),
        )
        code = meta.get("metricCode")
        lineage_dict = meta.get("lineage")
        bundles.append(
            MetricDataBundle(
                metric_code=code,
                data_block=block,
                mask_expression=meta.get("maskExpression") or "pv_valid",
                masked_indices=[int(i) for i in meta.get("maskedIndices") or []],
                lineage=DataLineage(**lineage_dict) if lineage_dict else DataLineage(),
            )
        )
        codes.append(code)
    return bundles, codes


def serialize_grid_signals(
    *,
    timestamps: list[datetime],
    signals: dict[str, list[Any]],
    quality_codes: dict[str, list[int]] | None = None,
    validity: dict[str, list[bool]] | None = None,
    prefix: str = "grid",
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """完整网格信号（诊断/辨识的分析输入全集）→ NPZ 数组 + manifest 描述.

    缺口以逐槽 null/valid mask 显式标记（KEEP_ALL_WITH_VALIDITY），不删点、
    不跨 gap 拼接——网格缺口本身即时间轴的一部分。
    """
    arrays: dict[str, np.ndarray] = {f"{prefix}__ts": _ns_int(list(timestamps))}
    for name, values in signals.items():
        serialize_signal(list(values), f"{prefix}__sig_{name}", arrays)
    if quality_codes:
        for name, codes in quality_codes.items():
            arrays[f"{prefix}__qual_{name}"] = np.asarray(codes, dtype=np.int32)
    if validity:
        for name, flags in validity.items():
            if len(flags) == len(timestamps):
                arrays[f"{prefix}__valid_{name}"] = np.asarray(flags, dtype=bool)
    desc = {
        "gridPeriodS": None,
        "slotCount": len(timestamps),
        "signalNames": sorted(signals.keys()),
        "qualityNames": sorted((quality_codes or {}).keys()),
        "validityNames": sorted((validity or {}).keys()),
    }
    return arrays, desc


def deserialize_grid_signals(
    arrays: dict[str, np.ndarray], desc: dict[str, Any], prefix: str = "grid"
) -> tuple[list[datetime], dict[str, list[Any]], dict[str, list[int]], dict[str, list[bool]]]:
    """NPZ 数组 + manifest 描述 → (timestamps, signals, quality, validity)."""
    timestamps = _ns_to_datetimes(arrays[f"{prefix}__ts"])
    signals: dict[str, list[Any]] = {}
    for name in desc.get("signalNames", []):
        key = f"{prefix}__sig_{name}"
        nullmask = arrays.get(f"{prefix}__null_{name}")
        vals: list[Any] = []
        arr = arrays[key]
        for j in range(len(arr)):
            if nullmask is not None and bool(nullmask[j]):
                vals.append(None)
            elif bool(np.isnan(arr[j])):
                vals.append(None)
            else:
                vals.append(float(arr[j]))
        signals[name] = vals
    quality = {
        name: [int(v) for v in arrays[f"{prefix}__qual_{name}"]]
        for name in desc.get("qualityNames", [])
        if f"{prefix}__qual_{name}" in arrays
    }
    validity = {
        name: [bool(v) for v in arrays[f"{prefix}__valid_{name}"]]
        for name in desc.get("validityNames", [])
        if f"{prefix}__valid_{name}" in arrays
    }
    return timestamps, signals, quality, validity


# ---------------------------------------------------------------------------
# 辨识输入包（IDENTIFICATION：07 号文六层语义；P2 保存载体与真实输入身份，
# 具体估计器输出由 P3 填入，未运行字段显式 null/None，不伪补）
# ---------------------------------------------------------------------------


def _identification_segments_section(preprocessing: dict[str, Any] | None) -> dict[str, Any]:
    """P3-06b（R2/P-14）：manifest 第 2 层"段与事件目录"从 preprocessing 填充.

    - 有 segmentCatalog（v1.6 起外层辨识必产）：逐段记录 segmentId/tsStart/
      tsEnd/mode/regimeId/pidVersion/sampleInterval/排除原因/qualifyTier——
      regimeId/pidVersion 恒 None（工况与 PID 版本未知显式标记，不自动伪填
      工况身份，07§3.2）；abs tsStart/tsEnd 相对输入窗（绝对时间由 window+
      consumed__ts_rel 承载）；
    - 无该键（旧版本结果/内层窗切片）：保持 P2 占位语义（available=False
      显式未知）——旧包读取行为不变。
    """
    catalog = (preprocessing or {}).get("segmentCatalog")
    if not catalog:
        return {
            "available": False,
            "regimeId": None,
            "pidVersion": None,
            "note": "该结果未运行结构化片段编目（P3-06b 前版本或内层窗切片）；"
            "MODE 序列与时间轴见 consumed 数组，缺口以 NaN 保留不拼接",
        }
    return {
        "available": True,
        "regimeId": None,  # 目录层级工况组未知（段级 regimeId 亦恒 None，不伪填）
        "pidVersion": None,
        "catalog": catalog,
        "note": "P3-06b R2 全保留段目录（tier1=P-12 物理最小点数档；"
        "tier2=50 点下限降级档；exclusionReason=None 为合格段）",
    }


def _f64_with_nan(values: list[Any] | None) -> np.ndarray | None:
    """信号列 → float64（None→NaN；辨识管线按原样接收并清洗 NaN）."""
    if values is None:
        return None
    arr = np.full(len(values), np.nan, dtype=np.float64)
    for i, v in enumerate(values):
        if v is not None:
            arr[i] = float(v)
    return arr


async def save_identification_snapshot(
    db: AsyncSession,
    *,
    loop_id: str,
    ts_start: datetime,
    ts_end: datetime,
    timestamps_rel: list[float],
    op: list[Any],
    pv: list[Any],
    sp: list[Any] | None,
    mode: list[Any] | None,
    sampling_period_seconds: float,
    theta_estimate: float | None,
    candidate_model_types: list[str] | None,
    result_dict: dict[str, Any],
    tuning_record_id: str | None,
    dcs_template_revision: str | None,
    dataset_ref: str | None = None,
    valid_rate: float | None = None,
    point_ctx: Any = None,
    engineering_facts: dict[str, Any] | None = None,
    retention_days: int = RETENTION_DAYS_DEFAULT,
) -> CalculationDatasetSnapshot | None:
    """保存辨识任务不可变输入包（完整分析输入，不只胜出片段）.

    六层语义（07 号文 §3.2 / 冻结稿；NPZ 数组 + manifest 各层）：
    1. 完整分析输入：``consumed__*`` 数组=进入算法栈的整窗输入（OP/PV/SP/
       MODE + 相对时间轴），缺口以 NaN 显式保留（管线按 P2-019 清洗规则
       确定性处理，不删点不跨 gap 拼接）；
    2. 段与事件目录：``segments``——P3-06b（R2/P-14）起从 preprocessing.
       segmentCatalog 实填（全部连续段含排除原因与 qualifyTier；
       regimeId/pidVersion 未知显式 None 不伪填）；旧版本结果保持占位
       （available=False）；
    3. 搜索审计：``searchAudit``=autoSegment/autoWindow/multiWindow 结构化
       窗位与理由 + P3-06b ``windowSearch`` 四类清单（被检/选中/拒/未搜索）
       与 B-05 预算终态（B-03 墙钟预算归 P3-04/P5-02，未执行显式 null）；
    4. 估计与分割身份：``estimation``=候选结构/估计方法/时滞来源与搜索轨迹/
       train-val-test 分割（evidence 摘要级）；
    5. 结果证据：``resultEvidence``=当前可得摘要（方法/拟合度/可信度），
       预测指标与自由仿真指标细分由 P3 填入（显式 null）；
    6. 来源链：``source``=algorithmVersion/resultRecordId(tuning_record)/
       executableBuildDigest/dcsTemplateRevision（modelVersionId 在版本
       创建后写入 tuning_record.calc_context，包 manifest 不回填）。

    DEC-10：预处理参数以 ``preprocess`` 结构化记录（三件套开关与实际生效
    参数 + multi-window 窗位），同 reason 字符串并存（evidence 不破坏）。

    schemaVersion 1.2（P2-04，只加不改）：``engineeringFacts`` 节从回路工程
    事实填充（``engineering_facts`` 参数，None=当时未记录，显式 null 不伪补）——
    控制作用方向 / 实际 OP 限幅（含来源）/ PV 量程与单位 / PID 表示与
    时间单位（未知态 status=UNKNOWN，绝不默认标准 kp/ti/td）/ DCS 与工程模板
    revisionRef。转换与算法消费归 P3-03。

    schemaVersion 1.3 承接（P3-06a R17，只加不改）：估计身份与终验节从
    ``result_dict.evidence`` 填充——``estimation.estimatorProfile``
    （估计器 profile：结构/估计方法/阶次/时滞样本数）与
    ``resultEvidence.finalTestAvailable``（R4/R5 终验四态记录
    {status, r2, n, ptpRange}，None=短数据 70/30 分割无终验段——显式
    null 不伪补）；``resultEvidence.predictiveMetrics`` 填训练集一步
    预测指标（r2Train）。旧 1.1/1.2 包无这些键=当时未记录，读取不变。

    schemaVersion 1.3 内加性扩展（P3-06c，只加不改）：``resultEvidence``
    增 ``spExogeneity``（P-21 三条件判定）/``finalEventTest``（P-24 独立
    事件终验）/``reselectUncertainty``（P-26 重选不确定性）三键；共同
    估计与跨窗稳定性经 ``preprocessing.multiWindow``（已整包保存）承载。
    旧包无这些键=当时未记录，读取不变。

    schemaVersion 1.3 内加性扩展（P3-03 终验节点，只加不改）：
    ``resultEvidence.finalVerification``（模型级终验结论四态+依据链——
    06a finalTest + 06c finalEventTest/跨窗稳定性/重选不确定性的模型级
    汇总；可整定判定=终验通过+结构支持）。旧包无该键=当时未记录，
    读取不变。
    """
    arrays: dict[str, np.ndarray] = {
        "consumed__ts_rel": np.asarray(timestamps_rel, dtype=np.float64),
        "consumed__sig_op": _f64_with_nan(op),
        "consumed__sig_pv": _f64_with_nan(pv),
    }
    sp_arr = _f64_with_nan(sp)
    if sp_arr is not None:
        arrays["consumed__sig_sp"] = sp_arr
    mode_arr = _f64_with_nan(mode)
    if mode_arr is not None:
        arrays["consumed__sig_mode"] = mode_arr

    preprocessing = result_dict.get("preprocessing")
    evidence = result_dict.get("evidence") or {}
    manifest_extra: dict[str, Any] = {
        "loopId": str(loop_id),
        "window": {"tsStart": ts_start.isoformat(), "tsEnd": ts_end.isoformat()},
        "consumed": {
            "samplingPeriodSeconds": float(sampling_period_seconds),
            "slotCount": len(timestamps_rel),
            "signalNames": [
                n for n in ("op", "pv", "sp", "mode") if f"consumed__sig_{n}" in arrays
            ],
            "modeProvided": mode_arr is not None,
            "spProvided": sp_arr is not None,
        },
        # 层 1 补充：绑定/量程解释上下文（point 路径；legacy 路径显式 null）
        "binding": {
            "datasetRef": dataset_ref
            or getattr(point_ctx, "dataset_ref", None)
            or f"loop:{loop_id}",
            "bindingVersion": getattr(point_ctx, "binding_version", None),
            "coverageRevision": getattr(point_ctx, "coverage_revision", None),
        },
        "validRate": valid_rate,
        # 层 2：段与事件目录（P3-06b R2/P-14 起实填——preprocessing.segmentCatalog
        # 存在时逐段记录；regimeId/pidVersion 工况与 PID 版本未知显式 None 不伪填；
        # 旧结果无该键=当时未运行，保持占位语义显式 available=False）
        "segments": _identification_segments_section(preprocessing),
        # 层 3：搜索审计（multiWindow/autoSegment/autoWindow 结构化窗位 +
        # P3-06b windowSearch 四类清单与 B-05 预算终态）
        "searchAudit": {
            "preprocessing": preprocessing,
            "windowSearch": (preprocessing or {}).get("windowSearch"),
            # B-04/B-08：预算终态——B-05 候选窗数预算生效时为结构化记录；
            # B-03 墙钟预算归 P3-04/P5-02 承接，未执行时 None 不伪补
            "budgetTermination": (
                ((preprocessing or {}).get("windowSearch") or {}).get("budgetTermination")
            ),
        },
        # 层 4：估计与分割身份（evidence 摘要级；P3-06a R17：estimatorProfile 已填）
        "estimation": {
            "candidateStructures": list(candidate_model_types or []),
            "thetaEstimateSeconds": theta_estimate,
            "thetaSource": result_dict.get("thetaSource"),
            "identifyMethod": result_dict.get("identifyMethod"),
            "delaySearchTrace": evidence.get("delaySearchTrace"),
            "split": evidence.get("split"),
            # P3-06a（R17）：估计器 profile（结构/估计方法/阶次/时滞样本数；
            # evidence 无该键=旧结果未记录，显式 null 不伪补）
            "estimatorProfile": evidence.get("estimatorProfile"),
        },
        # 层 5：结果证据（P3-06a R17：终验/预测指标已填）
        "resultEvidence": {
            "success": bool(result_dict.get("success")),
            "fittingScore": result_dict.get("fittingScore"),
            "confidenceLevel": result_dict.get("confidenceLevel"),
            # P3-06a（R4/R5）：训练集一步预测指标（预测/自由仿真指标分别存储）
            "predictiveMetrics": {"r2Train": evidence.get("r2Train")} if evidence else None,
            "freeSimulationMetrics": evidence.get("nrmseVal"),
            # P3-06a（R4/R5）：终验四态记录（None=无终验段，不伪补）
            "finalTestAvailable": evidence.get("finalTest"),
            "uncertainty": evidence.get("parameterUncertainty"),
            # P3-06c（P-21/P-24/P-26）加性键（None=当时未记录，不伪补）：
            # SP 外生性三条件记录 / 独立事件终验 / 重选时滞-结构不确定性
            "spExogeneity": evidence.get("spExogeneity"),
            "finalEventTest": evidence.get("finalEventTest"),
            "reselectUncertainty": evidence.get("reselectUncertainty"),
            # G3-APP（14 号文 §5，S1-b 消费链）：低 SNR 显式不承诺标注
            # （含 joint 胜出路径的聚合 SNR 记录；None=当时未触发不伪补）
            "lowSnr": evidence.get("lowSnr"),
            # P3-03 终验节点加性键（模型级终验结论四态+依据链；
            # None=未运行终验装配/旧结果，不伪补）
            "finalVerification": evidence.get("finalVerification"),
        },
        "replayContext": {
            "loopId": str(loop_id),
            "thetaEstimate": theta_estimate,
            "candidateModels": list(candidate_model_types or []),
            "opLimits": (0.0, 100.0),
        },
        "preprocess": preprocessing,
        # schemaVersion 1.2（P2-04）：回路工程事实（None=当时未记录，不伪补）
        "engineeringFacts": engineering_facts,
        "source": {
            "algorithmVersion": result_dict.get("algorithmVersion"),
            "configRevision": None,  # 辨识链不消费全局配置发布（显式 null）
            "resultRecordId": tuning_record_id,
            "dcsTemplateRevision": dcs_template_revision,
        },
    }
    return await save_calculation_snapshot(
        db,
        kind=KIND_IDENTIFICATION,
        ts_start=ts_start,
        ts_end=ts_end,
        dataset_ref=manifest_extra["binding"]["datasetRef"],
        arrays=arrays,
        manifest_extra=manifest_extra,
        created_by_record_id=tuning_record_id,
        loop_id=str(loop_id),
        retention_days=retention_days,
    )


# ---------------------------------------------------------------------------
# 原事件包（KPI_ORIGINAL_EVENTS：10§3.5 两类对照输入；P3-08a schema 1.3）
# ---------------------------------------------------------------------------


async def save_original_events_snapshot(
    db: AsyncSession,
    *,
    loop_id: str,
    ts_start: datetime,
    ts_end: datetime,
    dataset_ref: str,
    arrays: dict[str, np.ndarray],
    binding: dict[str, Any],
    events: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    initial_state: dict[str, Any] | None = None,
    derivation_plan: dict[str, Any] | None = None,
    quality_codes: dict[str, Any] | None = None,
    created_by_record_id: str | None = None,
    retention_days: int = RETENTION_DAYS_DEFAULT,
) -> CalculationDatasetSnapshot | None:
    """保存原事件包（同一时点冻结的原始事件/质量/缺口覆盖/窗口前初始
    状态/绑定与单位解释；HF 与数据策略对照的原点，10§3.5）.

    manifest 节（schema 1.3，08b 填真实数据；本函数定义结构契约）：
    - ``events``：结构化事件目录（SP/MODE/PID/已知工况切换；无法确认
      工况时显式 unknown，不伪造稳定工况——契约 §2.7）；
    - ``coverage``：逐角色缺口覆盖（半开区间游程编码，对齐
      ``contracts.series_context.RoleCoverage``）；
    - ``initialState``：窗口前初始状态（COV 初值等）；
    - ``binding``：绑定与单位解释（datasetRef/bindingVersion/
      interpretationVersion/单位量程）；
    - ``derivationPlan``：计划派生的网格策略清单（旧/新网格 policy 描述）；
    - ``qualityCodes``：质量码说明（逐信号）。

    派生网格包（KPI_GRID）经 manifest ``derivation`` 节引用本包（见
    ``derivation_section``）——原事件包 hash 与两个派生网格包 hash 三者
    可复核；缺原事件的旧粗网格包只能做公式重放，不可伪称已验证取数修复。
    """
    manifest_extra: dict[str, Any] = {
        "loopId": str(loop_id),
        "window": {"tsStart": ts_start.isoformat(), "tsEnd": ts_end.isoformat()},
        "binding": binding,
        "events": events,
        "coverage": coverage,
        "initialState": initial_state,
        "derivationPlan": derivation_plan,
        "qualityCodes": quality_codes,
    }
    return await save_calculation_snapshot(
        db,
        kind=KIND_KPI_ORIGINAL_EVENTS,
        ts_start=ts_start,
        ts_end=ts_end,
        dataset_ref=dataset_ref,
        arrays=arrays,
        manifest_extra=manifest_extra,
        created_by_record_id=created_by_record_id,
        loop_id=str(loop_id),
        retention_days=retention_days,
    )


def derivation_section(
    source: CalculationDatasetSnapshot,
    *,
    derivation_method: str,
    grid_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """派生网格包（KPI_GRID）manifest.derivation 节——原事件身份引用.

    记录原事件包 id/inputHash + 派生方法 + 网格策略；同一原事件按不同
    策略派生的旧/新网格包各自 inputHash 可与原事件包 hash 三方对账
    （"原事件→旧网格→新网格"差异与"同网格→新公式"差异分开报告的前提）。
    """
    return {
        "sourceSnapshotId": str(source.id),
        "sourceInputHash": source.input_hash,
        "derivationMethod": derivation_method,
        "gridPolicy": grid_policy,
    }


# ---------------------------------------------------------------------------
# 完整语义比较（P3-08a：replay 从"只比 value"扩展到完整语义视图）
# ---------------------------------------------------------------------------

#: 语义视图中排除的非语义字段（生成时间/耗时等，不参与差异判定，10§3.5.3）
_SEMANTIC_EXCLUDED_KEYS = frozenset(
    {"replayedAt", "computedAt", "generatedAt", "elapsedMs", "snapshotId"}
)


def _prune_semantic(value: Any) -> Any:
    """递归剔除非语义键（数值取 round 12 位消浮点尾差；其余原样）."""
    if isinstance(value, dict):
        return {
            str(k): _prune_semantic(v) for k, v in value.items() if k not in _SEMANTIC_EXCLUDED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_prune_semantic(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 12)
    return value


def extract_semantic_view(
    *,
    metrics_semantic: dict[str, Any] | None,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造比较用语义视图：指标明细（含 ``__composite__`` 综合评分条目）＋
    方法/参数身份.

    比较域（10§3.5.3）：指标集合、null/数值、状态、原因、分母、覆盖、
    方法/参数身份、参考分/参考政策/统计构成、P 与等级；只排除生成时间
    等非语义字段。composite（P/等级）以 ``metrics_semantic["__composite__"]``
    条目参与（``_extract_metrics_semantic`` 产物）。
    """
    return _prune_semantic(
        {
            "metrics": metrics_semantic or {},
            "identity": identity or {},
        }
    )


def diff_semantic_views(v1: dict[str, Any], replay: dict[str, Any]) -> dict[str, list[Any]]:
    """递归 diff 两棵语义视图；差异完整列出（路径 → [v1, replay]）.

    缺键与显式 null **分开**（null=已计算不可判定；缺键=未记录）；
    数值按 round(12) 容差。返回空 dict 即语义全等。
    """
    diffs: dict[str, list[Any]] = {}

    def _walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                child = key if not path else f"{path}.{key}"
                if key not in a:
                    diffs[child] = ["<absent>", b[key]]
                elif key not in b:
                    diffs[child] = [a[key], "<absent>"]
                else:
                    _walk(a[key], b[key], child)
            return
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                diffs[path] = [a, b]
                return
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                _walk(x, y, f"{path}[{i}]")
            return
        if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
            if a is not b:
                diffs[path] = [a, b]
            return
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if round(float(a), 12) != round(float(b), 12):
                diffs[path] = [a, b]
            return
        if a != b:
            diffs[path] = [a, b]

    _walk(v1, replay, "")
    return diffs


# ---------------------------------------------------------------------------
# 结果账本 record 重放（C12b：读原包按旧构建复现；G2-INC-a/b 增量）
# ---------------------------------------------------------------------------


def _pinned_params_from_manifest(manifest: dict[str, Any]) -> tuple[dict | None, str]:
    """从包 manifest 重建五层固定参数（G2-INC-a：重放不读当前配置缓存）.

    Returns:
        (pinned_map, source_label)：
        - 新包（G2 增量起保存，``source.effectiveParams.params`` 存在）：
          ``({(metric, ct): params}, "package")``——重放按包内 v1 有效值固定；
        - 旧包（1.1/1.2，无该节或 status=UNPINNED）：``(None,
          "legacy-unpinned")``——显式标记后按既有同构建口径计算，非静默。
    """
    eff = (manifest.get("source") or {}).get("effectiveParams")
    raw = eff.get("params") if isinstance(eff, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None, "legacy-unpinned"
    pinned: dict[tuple[str, str], dict[str, Any]] = {}
    for key, params in raw.items():
        if not isinstance(key, str) or "|" not in key or not isinstance(params, dict):
            continue
        metric, ct = key.rsplit("|", 1)
        pinned[(metric, ct)] = dict(params)
    if not pinned:
        return None, "legacy-unpinned"
    return pinned, "package"


def _resolve_replay_execution(loaded: LoadedSnapshot) -> tuple[str, str]:
    """裁决重放执行方式（G2-INC-b：digest 校验通过后的执行侧路由）.

    Returns:
        (mode, pkg_digest)：mode 为 ``in-process``（当前构建即包构建，进程内
        执行既有代码）或 ``controlled-build``（包构建 ≠ 当前构建——由调用方
        路由到 ``controlled_build`` 子进程执行，缺配方时由其显式拒绝）。
    """
    pkg_digest = (loaded.manifest.get("source") or {}).get("executableBuildDigest")
    cur_digest = executable_build_digest()
    if pkg_digest is not None and pkg_digest != cur_digest:
        return "controlled-build", str(pkg_digest)
    return "in-process", str(cur_digest)


#: 语义比较 identity 域（REWORK-D2 定稿：两侧投影到同一键域——写侧
#: assessmentContext 为 resolve_task_release_context 全量 12+2 键，重放侧
#: 从包 manifest source 只能取到方法/参数身份 6 键；比较域取交集 6 键，
#: v1 侧多出的登记元信息（buildDigest/scope/rollbackProfile 等）不属
#: "方法/参数身份"漂移判定域，投影丢弃；键缺=当时未记录（不伪补））
_SEMANTIC_IDENTITY_KEYS = (
    "assessmentProfile",
    "releaseGeneration",
    "dataPolicyVersion",
    "algorithmVersion",
    "configRevision",
    "paramsSource",
)


def _identity_projection(ctx: dict[str, Any] | None) -> dict[str, Any]:
    """任意 identity 上下文 → 语义比较 6 键域投影（缺键=None）."""
    src = ctx if isinstance(ctx, dict) else {}
    return {k: src.get(k) for k in _SEMANTIC_IDENTITY_KEYS}


def _semantic_view_from_manifest_source(manifest: dict[str, Any]) -> dict[str, Any]:
    """包内方法/参数身份（语义视图 identity 域；schema 1.2 起可得）.

    configRevision 统一取 ``source.configRevision``（"rev:{n}:{digest16}" /
    "digest:..." 字符串口径——与 writer assessmentContext 回填值同源同构；
    effectiveParams.configRevision 的 int 表达不进比较域，防同 revision 双
    表达伪差异）。paramsSource 由包内参数节推导（10§3.5.3 方法/参数身份）。
    """
    src = manifest.get("source") or {}
    eff = src.get("effectiveParams") or {}
    return _identity_projection(
        {
            "assessmentProfile": src.get("assessmentProfile"),
            "releaseGeneration": src.get("releaseGeneration"),
            "dataPolicyVersion": src.get("dataPolicyVersion"),
            "algorithmVersion": src.get("algorithmVersion"),
            "configRevision": src.get("configRevision"),
            "paramsSource": (
                "package"
                if isinstance(eff.get("params"), dict) and eff.get("params")
                else "legacy-unpinned"
            ),
        }
    )


async def replay_result_record(
    db: AsyncSession,
    record_id: str,
    *,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """按结果账本 record 重放 KPI 计算：读原包（不重查源）按包构建重算并比对 v1.

    G2-INC-a：算子参数从包 ``source.effectiveParams.params`` 固定（不读当前
    配置缓存）；旧版包缺该节 → 报告 ``paramsSource="legacy-unpinned"`` 显式
    标记（非静默）。

    G2-INC-b：包构建 digest ≠ 当前构建 → 经 ``controlled_build`` 在该 digest
    对应的受控构建内子进程执行（缺执行配方/构建失效 → 显式拒绝
    ``ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE``，绝不静默用当前构建冒充）。

    P3-08a 完整语义比较（10§3.5.3）：v1 record 含 ``metricsSemantic`` 时，
    比较扩展到指标集合/null/数值/状态/原因/分母/覆盖/参考分/综合评分(P/
    等级)/方法参数身份（``semanticDifferences`` 完整列出差异路径）；旧
    record（P3-08a 前仅 metricsDetail.value）→ ``v1SemanticAvailable=false``
    退回值级比较（显式标记，非静默）。

    P3-08a profile 对照（``profile_id``）：None=复现模式（按包身份重算，
    受控构建路由照旧）；指定 profile → **新 profile 对照**（离线验收）：
    新算法只在当前构建 → 强制 in-process（executionMode=
    ``in-process-profile-contrast``），包内参数照固定，差异归因 profile。
    profile 必须在服务端策略表登记（未注册/未接入显式拒绝）。

    Returns:
        ``{"recordId", "snapshotId", "inputHash", "replayedAt",
           "identical", "differences", "semanticDifferences",
           "v1SemanticAvailable", "comparisonKind", "targetAssessmentProfile",
           "replayedMetrics", "paramsSource", "executionMode",
           "executedBuildDigest"}``

    Raises:
        BizError: ERR_SNAPSHOT_* —— 未保存包/过期/损坏/缺构建/缺执行配方均
            显式拒绝"不可完整复现"，绝不重查当前源数据冒充原输入；
            ERR_ASSESSMENT_PROFILE_*（profile 对照模式）。
    """
    from app.services.assessment_profile import (
        AssessmentProfileId,
        resolve_assessment_profile,
    )
    from app.services.result_ledger import get_record_by_id

    record = await get_record_by_id(db, record_id)
    if record is None:
        raise BizError(
            code="ERR_RESULT_RECORD_NOT_FOUND",
            message=f"结果记录不存在（{record_id}）",
            status_code=404,
        )
    if not record.dataset_snapshot_id:
        raise BizError(
            code=ERR_SNAPSHOT_NOT_SAVED,
            message=("该记录未保存输入包（P1 历史记录/例行 KPI 按 DEC-10 不落包），不可完整复现"),
            status_code=409,
        )
    loaded = await load_snapshot_for_replay(db, str(record.dataset_snapshot_id))
    if loaded.manifest.get("kind") != KIND_KPI_GRID:
        raise BizError(
            code=ERR_SNAPSHOT_SCHEMA_UNSUPPORTED,
            message=f"包种类 {loaded.manifest.get('kind')} 不是 KPI_GRID，不能按 KPI 链重放",
            status_code=409,
        )

    pkg_profile = (loaded.manifest.get("source") or {}).get("assessmentProfile") or (
        AssessmentProfileId.LEGACY_V2  # 1.2 旧包=未记录 profile（legacy 隐式）
    )
    # profile 对照模式：显式解析（未注册/未接入拒绝）；目标≠包 profile →
    # 新算法只在当前构建，强制 in-process 对照
    profile_contrast = False
    target_profile = pkg_profile
    if profile_id is not None:
        resolve_assessment_profile(profile_id)  # 显式拒绝未注册/planned
        target_profile = profile_id
        profile_contrast = profile_id != pkg_profile

    execution_mode, route_digest = _resolve_replay_execution(loaded)
    if profile_contrast and execution_mode == "controlled-build":
        execution_mode = "in-process-profile-contrast"
    if execution_mode in ("in-process", "in-process-profile-contrast"):
        from app.tasks.kpi_calc import (
            _compute_kpis_three_layer,
            _extract_metrics_detail,
            _extract_metrics_semantic,
        )

        replay_ctx = loaded.manifest.get("replayContext") or {}
        bundles, _ = deserialize_data_blocks(loaded.arrays, loaded.manifest.get("blocks") or [])
        config_bundle = _build_replay_config_bundle(replay_ctx)
        pinned_params, params_source = _pinned_params_from_manifest(loaded.manifest)
        metric_results, composite = _compute_kpis_three_layer(
            bundles, config_bundle, replay_ctx.get("weights"), pinned_params=pinned_params
        )
        replayed = _extract_metrics_detail(metric_results)
        replay_semantic = _extract_metrics_semantic(metric_results, composite)
        executed_digest = route_digest
    else:
        from app.services import controlled_build as cb

        out = cb.run_kpi_replay(route_digest, loaded.npz_path, loaded.manifest_path)
        replayed = out.get("metrics")
        params_source = str(out.get("paramsSource") or "legacy-unpinned")
        executed_digest = str(out.get("buildDigest") or route_digest)
        # 受控构建为复现模式（同包同 profile）；CLI 输出值级明细
        replay_semantic = dict(replayed or {})

    # 值级比较（兼容保留）
    v1_detail = (record.payload or {}).get("metricsDetail") or {}
    differences: dict[str, Any] = {}
    for metric, value in (replayed or {}).items():
        if not isinstance(value, dict):
            continue
        old = v1_detail.get(metric) or {}
        for field in ("value",):
            new_v = value.get(field)
            old_v = old.get(field) if isinstance(old, dict) else None
            if new_v is None and old_v is None:
                continue
            if new_v is None or old_v is None or abs(float(new_v) - float(old_v)) > 1e-9:
                differences[metric] = {"v1": old_v, "replay": new_v}

    # 完整语义比较（P3-08a）：v1 record 含 metricsSemantic 才可比
    v1_semantic = (record.payload or {}).get("metricsSemantic")
    v1_semantic_available = isinstance(v1_semantic, dict) and bool(v1_semantic)
    semantic_differences: dict[str, Any] = {}
    if v1_semantic_available:
        v1_view = extract_semantic_view(
            metrics_semantic=v1_semantic,
            # REWORK-D2：v1 侧全量 assessmentContext（12+2 键）投影到与重放
            # 侧相同的 6 键方法/参数身份域（登记元信息不属漂移判定域）
            identity=_identity_projection((record.payload or {}).get("assessmentContext")),
        )
        replay_view = extract_semantic_view(
            metrics_semantic=replay_semantic,
            identity=_semantic_view_from_manifest_source(loaded.manifest),
        )
        # profile 对照模式下身份差异（profile/releaseGeneration/版本）是
        # 预期归因项，完整列出（语义差异报告不静默吞差异）
        semantic_differences = diff_semantic_views(v1_view, replay_view)

    return {
        "recordId": str(record.id),
        "snapshotId": loaded.snapshot_id,
        "inputHash": loaded.input_hash,
        "replayedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "identical": not differences and not semantic_differences,
        "differences": differences,
        "semanticDifferences": semantic_differences,
        "v1SemanticAvailable": v1_semantic_available,
        "comparisonKind": ("profile-contrast" if profile_contrast else "reproduction"),
        "targetAssessmentProfile": target_profile,
        "replayedMetrics": replayed,
        "paramsSource": params_source,
        "executionMode": execution_mode,
        "executedBuildDigest": executed_digest,
    }


async def offline_acceptance_replay(
    db: AsyncSession,
    record_id: str,
    *,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """离线验收重算（C12-EA3）：读原包按指定 profile 重算并完整语义对照.

    **不改生产投影**（hourly/latest/node/record 均不写——本函数只读原包与
    账本行，输出对照报告；隔离验收结果不进入生产排行/报告，10§7.2）。
    ``profile_id=None`` 按复现模式（同包同 profile 重算）。

    返回 replay 报告 + ``offlineAcceptance=true`` / ``wroteProjection=false``
    （显式声明，读端可核对）；差异经 ``semanticDifferences`` 逐例列出，
    逐例解释归因（profile-contrast 模式差异归因 targetAssessmentProfile）。
    """
    report = await replay_result_record(db, record_id, profile_id=profile_id)
    return {
        **report,
        "offlineAcceptance": True,
        "wroteProjection": False,
    }


def _build_replay_config_bundle(replay_ctx: dict[str, Any]):
    """按包内 replayContext 重建虚拟 CONFIG bundle（控制类型/理想稳态时间）.

    controlType 从 manifest 回读为普通 str，需转回 ControlType 枚举
    （StrEnum 值即 "FC"/"PC"/"TC"/"LC"/"CC"）；未知值回落 FLOW（与
    _loop_type_to_control_type 既有回退口径一致）。
    """
    from app.contracts.data_types import ControlType
    from app.tasks.kpi_calc import _build_config_bundle

    raw = replay_ctx.get("controlType")
    try:
        control_type = ControlType(str(raw)) if raw else ControlType.FLOW
    except ValueError:
        control_type = ControlType.FLOW
    return _build_config_bundle(
        replay_ctx.get("loopId") or "replay",
        control_type,
        replay_ctx.get("idealSettlingTime"),
    )


# ---------------------------------------------------------------------------
# 辨识重放（IDENTIFICATION：原包信号 + 结构化预处理参数 → 重跑管线）
# ---------------------------------------------------------------------------


def _replay_candidate_models(names: list[str] | None):
    """包内候选结构 → ModelType 列表（缺失/空 → None=管线默认候选集）."""
    if not names:
        return None
    from app.services.tuning_identification.types import ModelType

    try:
        return [ModelType(str(n)) for n in names]
    except ValueError:
        return None


async def replay_identification_snapshot(
    db: AsyncSession, snapshot_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """按原包重跑辨识管线（identify_from_history），返回重放结果.

    输入取包内 ``consumed`` 数组（当年实际进入算法栈的分析输入切片），
    不重查 TDengine；结构化预处理参数从 manifest.preprocess 读取比对
    （管线内部按同参数确定性执行）。

    G2-INC-b：包构建 digest ≠ 当前构建 → 经 ``controlled_build`` 在受控
    构建内子进程执行（缺执行配方 → ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE）；
    报告附 ``executionMode``/``executedBuildDigest``。
    """
    loaded = await load_snapshot_for_replay(db, snapshot_id, now=now)
    if loaded.manifest.get("kind") != KIND_IDENTIFICATION:
        raise BizError(
            code=ERR_SNAPSHOT_SCHEMA_UNSUPPORTED,
            message=f"包种类 {loaded.manifest.get('kind')} 不是 IDENTIFICATION，不能按辨识链重放",
            status_code=409,
        )

    execution_mode, route_digest = _resolve_replay_execution(loaded)
    if execution_mode == "controlled-build":
        from app.services import controlled_build as cb

        out = cb.run_identification_replay(route_digest, loaded.npz_path, loaded.manifest_path)
        payload: dict[str, Any] = {
            "snapshotId": loaded.snapshot_id,
            "inputHash": loaded.input_hash,
            "success": bool(out.get("success")),
            "executionMode": execution_mode,
            "executedBuildDigest": str(out.get("buildDigest") or route_digest),
        }
        if out.get("success"):
            payload["modelType"] = out.get("modelType")
            payload["params"] = out.get("params")
            payload["fittingScore"] = out.get("fittingScore")
            payload["confidenceLevel"] = out.get("confidenceLevel")
        else:
            payload["reason"] = out.get("reason")
        return payload

    from app.services.tuning_identification.pipeline import identify_from_history

    consumed_desc = loaded.manifest.get("consumed") or {}
    ts_rel = loaded.arrays.get("consumed__ts_rel")
    op = loaded.arrays.get("consumed__sig_op")
    pv = loaded.arrays.get("consumed__sig_pv")
    if ts_rel is None or op is None or pv is None:
        raise BizError(
            code=ERR_SNAPSHOT_SCHEMA_UNSUPPORTED,
            message="包内缺少 consumed 输入数组（旧版包结构），不可完整复现",
            status_code=409,
        )
    sp = loaded.arrays.get("consumed__sig_sp")
    mode_arr = loaded.arrays.get("consumed__sig_mode")
    ts = float(consumed_desc.get("samplingPeriodSeconds") or 1.0)
    ctx = loaded.manifest.get("replayContext") or {}
    result = identify_from_history(
        op=[float(v) for v in op],
        pv=[float(v) for v in pv],
        sp=[float(v) for v in sp] if sp is not None else None,
        ts=ts,
        theta_estimate=ctx.get("thetaEstimate"),
        mode=[None if np.isnan(v) else int(round(v)) for v in mode_arr]
        if mode_arr is not None
        else None,
        candidate_models=_replay_candidate_models(ctx.get("candidateModels")),
        op_limits=tuple(ctx.get("opLimits") or (0.0, 100.0)),
    )
    payload = {
        "snapshotId": loaded.snapshot_id,
        "inputHash": loaded.input_hash,
        "success": bool(result.success),
        "executionMode": execution_mode,
        "executedBuildDigest": route_digest,
    }
    if result.success and result.best_model is not None:
        payload["modelType"] = result.best_model.params.model_type.value
        payload["params"] = result.best_model.params.to_dict()
        payload["fittingScore"] = round(result.best_model.fitting_score, 2)
        payload["confidenceLevel"] = result.best_model.confidence.value
    else:
        payload["reason"] = result.reason
    return payload


__all__ = [
    "ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE",
    "ERR_SNAPSHOT_BUILD_UNAVAILABLE",
    "ERR_SNAPSHOT_CLEANED",
    "ERR_SNAPSHOT_EXPIRED",
    "ERR_SNAPSHOT_FILES_MISSING",
    "ERR_SNAPSHOT_HASH_MISMATCH",
    "ERR_SNAPSHOT_NOT_SAVED",
    "ERR_SNAPSHOT_SCHEMA_UNSUPPORTED",
    "KIND_DIAGNOSIS_RAW",
    "KIND_IDENTIFICATION",
    "KIND_KPI_GRID",
    "KIND_KPI_ORIGINAL_EVENTS",
    "LoadedSnapshot",
    "ORPHAN_FILE_GRACE_HOURS",
    "RETENTION_DAYS_DEFAULT",
    "RETENTION_FROZEN",
    "RETENTION_NORMAL",
    "SNAPSHOT_SCHEMA_VERSION",
    "UNVERSIONED_BUILD",
    "cleanup_expired_snapshots",
    "compute_input_hash",
    "derivation_section",
    "deserialize_data_blocks",
    "deserialize_grid_signals",
    "diff_semantic_views",
    "executable_build_digest",
    "extract_semantic_view",
    "find_referencing_records",
    "freeze_snapshot",
    "load_snapshot_for_replay",
    "offline_acceptance_replay",
    "replay_identification_snapshot",
    "replay_result_record",
    "retained_build_digests",
    "save_calculation_snapshot",
    "save_identification_snapshot",
    "save_original_events_snapshot",
    "serialize_data_blocks",
    "serialize_grid_signals",
    "snapshot_file_paths",
    "snapshot_root",
    "unfreeze_snapshot",
]
