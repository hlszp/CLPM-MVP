"""输入包重放 CLI（在受控构建自己的环境内执行，G2-INC-b）.

用法（由 ``app.services.controlled_build`` 以 ``python -m`` 在构建目录内
调起；也可供环境 keeper 手工诊断）::

    python -m app.services.snapshot_replay_cli digest
    python -m app.services.snapshot_replay_cli kpi --npz <p> --manifest <m>
    python -m app.services.snapshot_replay_cli identification --npz <p> --manifest <m>

输出契约：stdout 恰一行 JSON。成功 ``{"ok": true, ...}`` 退出码 0；失败
``{"ok": false, "error": "...", "detail": "..."}`` 退出码 2。

关键性质（C12-EA2"只登记 digest 不算执行能力"的执行侧）：
- 执行代码 = **本模块所在的构建**（旧构建存档随发布产物归档，本 CLI 属
  标准代码随之存档）；调用方在旧构建目录内以该构建的解释器依赖运行；
- ``digest`` 子命令用本构建自己的 ``executable_build_digest()``（单源
  公式，不分叉）自报身份，供 orchestrator 核对"回传其 digest"；
- ``kpi`` 子命令读原包（不查库/不查源），参数按包 manifest
  ``source.effectiveParams`` 固定（G2-INC-a：重放不读当前配置缓存；
  旧版包缺该节 → paramsSource=legacy-unpinned 显式标记）；
- G2 增量之前的构建不含本模块 → 子进程模块级失败 → 调用方显式拒绝
  ``ERR_SNAPSHOT_BUILD_NOT_EXECUTABLE``（诚实口径，不降级当前代码冒充）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))  # noqa: T201 - CLI 契约输出
    return 0 if payload.get("ok") else 2


def _cmd_digest() -> dict:
    from app.services.calc_snapshot import executable_build_digest

    return {"ok": True, "buildDigest": executable_build_digest()}


def _cmd_kpi(npz: str, manifest_path: str) -> dict:
    from app.services.calc_snapshot import (
        KIND_KPI_GRID,
        _build_replay_config_bundle,
        _pinned_params_from_manifest,
        _read_files,
        deserialize_data_blocks,
        executable_build_digest,
    )
    from app.tasks.kpi_calc import _compute_kpis_three_layer, _extract_metrics_detail

    manifest, arrays = _read_files(Path(npz), Path(manifest_path))
    if manifest.get("kind") != KIND_KPI_GRID:
        return {"ok": False, "error": f"包种类 {manifest.get('kind')} 不是 KPI_GRID"}
    replay_ctx = manifest.get("replayContext") or {}
    bundles, _ = deserialize_data_blocks(arrays, manifest.get("blocks") or [])
    config_bundle = _build_replay_config_bundle(replay_ctx)
    pinned_params, params_source = _pinned_params_from_manifest(manifest)
    metric_results, _composite = _compute_kpis_three_layer(
        bundles, config_bundle, replay_ctx.get("weights"), pinned_params=pinned_params
    )
    return {
        "ok": True,
        "buildDigest": executable_build_digest(),
        "metrics": _extract_metrics_detail(metric_results),
        "paramsSource": params_source,
    }


def _cmd_identification(npz: str, manifest_path: str) -> dict:
    import numpy as np

    from app.services.calc_snapshot import (
        KIND_IDENTIFICATION,
        _read_files,
        _replay_candidate_models,
        executable_build_digest,
    )
    from app.services.tuning_identification.pipeline import identify_from_history

    manifest, arrays = _read_files(Path(npz), Path(manifest_path))
    if manifest.get("kind") != KIND_IDENTIFICATION:
        return {"ok": False, "error": f"包种类 {manifest.get('kind')} 不是 IDENTIFICATION"}
    consumed_desc = manifest.get("consumed") or {}
    ts_rel = arrays.get("consumed__ts_rel")
    op = arrays.get("consumed__sig_op")
    pv = arrays.get("consumed__sig_pv")
    if ts_rel is None or op is None or pv is None:
        return {"ok": False, "error": "包内缺少 consumed 输入数组（旧版包结构）"}
    sp = arrays.get("consumed__sig_sp")
    mode_arr = arrays.get("consumed__sig_mode")
    ts = float(consumed_desc.get("samplingPeriodSeconds") or 1.0)
    ctx = manifest.get("replayContext") or {}
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
    payload: dict = {
        "ok": True,
        "buildDigest": executable_build_digest(),
        "success": bool(result.success),
    }
    if result.success and result.best_model is not None:
        payload["modelType"] = result.best_model.params.model_type.value
        payload["params"] = result.best_model.params.to_dict()
        payload["fittingScore"] = round(result.best_model.fitting_score, 2)
        payload["confidenceLevel"] = result.best_model.confidence.value
    else:
        payload["reason"] = result.reason
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snapshot-replay-cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("digest")
    p_kpi = sub.add_parser("kpi")
    p_kpi.add_argument("--npz", required=True)
    p_kpi.add_argument("--manifest", required=True)
    p_ident = sub.add_parser("identification")
    p_ident.add_argument("--npz", required=True)
    p_ident.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "digest":
            return _emit(_cmd_digest())
        if args.command == "kpi":
            return _emit(_cmd_kpi(args.npz, args.manifest))
        return _emit(_cmd_identification(args.npz, args.manifest))
    except Exception as exc:  # noqa: BLE001 - CLI 契约：失败也输出 JSON
        return _emit({"ok": False, "error": type(exc).__name__, "detail": str(exc)})


if __name__ == "__main__":
    sys.exit(main())
