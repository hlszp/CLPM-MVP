"""受控构建执行（G2-INC-b）：digest 校验通过后，重放按"保存该包的构建"执行.

背景（G2 增量批缺口 G2-INC-b）：P2-03 已有"受控执行构建 digest + 旧版本
读取配方"（``retained-builds.json`` 门禁层），但重放路径仍调用**当前**计算
器——本模块接通执行侧：旧包路由到其构建 digest 对应的真实受控构建
（源码树副本）内以子进程执行，旧结果可复现。

retained-builds.json 扩展（只加不改，P2-03 初版 ``{"digests": [...]}``
完全兼容）::

    {
      "digests": ["<digest>", ...],            # 门禁层（既有）：保留清单
      "builds": {                               # 执行层（本批新增）：执行配方
        "<digest>": {
          "kind": "source",                     # 目前仅源码型受控构建
          "path": "/abs/path/to/build/backend", # 构建根目录（含 app/ 与 uv.lock）
          "env": {"CLPM_BUILD_CODE_SHA": "..."} # 构建身份环境变量（构成其 digest）
        }
      }
    }

语义（10 号文 C12-EA2）：
- digest 进 ``digests`` 但无 ``builds`` 配方 → "只登记 digest 不算具备执行
  能力" → 显式拒绝 ``ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE``（409）；
- 配方 path 失效 / 子进程失败 / 输出契约破坏 → 同错误码拒绝（附 stderr
  摘要），绝不静默用当前构建冒充旧构建执行；
- 子进程执行 ``python -m app.services.snapshot_replay_cli``（随构建存档的
  标准模块）于构建目录内——旧构建**自己的代码**读包计算并自报 buildDigest；
- 与 P2-03 门禁分工：``calc_snapshot.load_snapshot_for_replay`` 负责"构建
  可用性"门禁（digests ∪ builds 键），本模块负责"执行能力"路由。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import status

from app.core.exceptions import BizError
from app.services.calc_snapshot import ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE, snapshot_root

logger = logging.getLogger(__name__)

#: 受控构建子进程默认超时（秒）——KPI 全网格重放可达分钟级
BUILD_REPLAY_TIMEOUT_SECONDS = 600.0


def _read_retained_builds() -> dict[str, Any]:
    path = snapshot_root() / "retained-builds.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def retained_build_recipes() -> dict[str, dict[str, Any]]:
    """执行配方表 ``{digest: {"kind", "path", "env"}}``（文件缺失=空表）."""
    builds = _read_retained_builds().get("builds")
    if not isinstance(builds, dict):
        return {}
    return {str(k): dict(v) for k, v in builds.items() if isinstance(v, dict)}


def _reject(pkg_digest: str, detail: str) -> BizError:
    return BizError(
        code=ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE,
        message=(
            f"包绑定的执行构建 {pkg_digest} 不具备执行能力（{detail}）——"
            "digest 已登记但无可执行受控构建，不可完整复现；由环境 keeper 按"
            " DEC-10 补齐 builds 执行配方（kind=source 的构建源码树）"
        ),
        status_code=status.HTTP_409_CONFLICT,
    )


def run_build_cli(
    pkg_digest: str,
    cli_args: list[str],
    *,
    timeout: float = BUILD_REPLAY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """在受控构建内执行 replay CLI 并解析其 JSON 输出.

    Raises:
        BizError: ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE——无配方/路径失效/
            子进程失败/输出契约破坏（含 stderr 摘要，不静默降级）。
    """
    recipe = retained_build_recipes().get(pkg_digest)
    if recipe is None:
        raise _reject(pkg_digest, "retained-builds.json 无该 digest 的 builds 执行配方")
    root = Path(str(recipe.get("path") or ""))
    if recipe.get("kind", "source") != "source" or not root.is_dir():
        raise _reject(pkg_digest, f"构建源码树不可用: {root}")
    env = {**os.environ, **(recipe.get("env") or {})}
    env["PYTHONPATH"] = str(root)
    cmd = [sys.executable, "-m", "app.services.snapshot_replay_cli", *cli_args]
    try:
        proc = subprocess.run(  # noqa: S603 - 固定模块名+受控参数
            cmd,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _reject(pkg_digest, f"受控构建执行超时（>{timeout:.0f}s）") from exc
    except OSError as exc:
        raise _reject(pkg_digest, f"受控构建进程启动失败: {exc}") from exc
    if proc.returncode != 0:
        raise _reject(
            pkg_digest,
            f"受控构建进程退出码 {proc.returncode}: {_tail(proc.stderr)}",
        )
    # 契约：stdout 恰一行 JSON
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise _reject(pkg_digest, "受控构建输出为空（snapshot_replay_cli 缺失或损坏）")
    try:
        payload = json.loads(lines[-1])
    except ValueError as exc:
        raise _reject(pkg_digest, f"受控构建输出契约破坏: {_tail(proc.stdout)}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise _reject(
            pkg_digest, f"受控构建报告失败: {json.dumps(payload, ensure_ascii=False)[:400]}"
        )
    reported = payload.get("buildDigest")
    if reported is not None and str(reported) != pkg_digest:
        raise _reject(
            pkg_digest,
            f"受控构建自报 digest {reported} 与包绑定 digest 不一致（构建配方漂移）",
        )
    logger.info(
        "[controlled-build] digest=%s 已按受控构建执行: %s",
        pkg_digest,
        cli_args[:1],
    )
    return payload


def run_kpi_replay(pkg_digest: str, npz_path: Path, manifest_path: Path) -> dict[str, Any]:
    """KPI 链：在受控构建内读原包重算（返回含 metrics/paramsSource/buildDigest）."""
    return run_build_cli(
        pkg_digest, ["kpi", "--npz", str(npz_path), "--manifest", str(manifest_path)]
    )


def run_identification_replay(
    pkg_digest: str, npz_path: Path, manifest_path: Path
) -> dict[str, Any]:
    """辨识链：在受控构建内读原包重跑管线."""
    return run_build_cli(
        pkg_digest,
        ["identification", "--npz", str(npz_path), "--manifest", str(manifest_path)],
    )


def query_build_digest(recipe: dict[str, Any]) -> str:
    """向受控构建查询其自身 digest（`digest` 子命令；诊断/验收用）.

    Raises:
        BizError: 构建不可执行（与重放同口径）。
    """
    root = Path(str(recipe.get("path") or ""))
    if recipe.get("kind", "source") != "source" or not root.is_dir():
        raise _reject("<query>", f"构建源码树不可用: {root}")
    env = {**os.environ, **(recipe.get("env") or {})}
    env["PYTHONPATH"] = str(root)
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "app.services.snapshot_replay_cli", "digest"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _reject("<query>", f"构建进程失败: {exc}") from exc
    if proc.returncode != 0:
        raise _reject("<query>", f"构建进程退出码 {proc.returncode}: {_tail(proc.stderr)}")
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1])
    except (ValueError, IndexError) as exc:
        raise _reject("<query>", "构建 digest 查询输出契约破坏") from exc
    digest = payload.get("buildDigest")
    if not isinstance(digest, str) or not digest:
        raise _reject("<query>", "构建 digest 查询无有效返回")
    return digest


def _tail(text: str, limit: int = 400) -> str:
    return (text or "").strip()[-limit:]


__all__ = [
    "BUILD_REPLAY_TIMEOUT_SECONDS",
    "query_build_digest",
    "retained_build_recipes",
    "run_build_cli",
    "run_identification_replay",
    "run_kpi_replay",
]
