from __future__ import annotations
async def _calculate_loop_kpi(
    db,
    loop: LoopLedger,
    metric_configs: dict[str, MetricConfig],
    ts_start: datetime,
    ts_end: datetime,
    data_planner,
    type_weights: dict[str, dict] | None = None,
    custom_task_id: str | None = None,
    loop_cfg: dict | None = None,
    source: str | None = None,
    source_task_id: str | None = None,
    logical_run_id: str | None = None,
    save_input_snapshot: bool = False,
    pinned_config: dict | None = None,
    release_context: dict[str, Any] | None = None,
    runtime_overrides: dict | None = None,
    template_keys: dict[str, str] | None = None,
    failed_source_ids: set[str] | None = None,
) -> dict | None:
    """计算单回路 KPI 并写入快照（v4.0 三层架构，幂等）。

    v4.0 架构：DataPlanner → 12 Calculator → ConfidenceEvaluator
    - 通过 data_planner.request_bundles 获取预处理后的 MetricDataBundle
    - _compute_kpis_three_layer 编排三层计算
    - _persist_snapshot 通过 UPSERT 写入快照（含 7 个数据血缘字段）

    P2 IA优化：KPI 聚合完成后调用 compute_fitness，得到 fitness_level/tags/detail
    并随快照 UPSERT 写入。fitness 失败不阻断主流程，fitness_level 置 NULL。

    P1-05 结果账本：logical_run_id 为本次逻辑运行身份（None 时由
    _persist_snapshot 按运行上下文推导）；config_revision 取实际消费配置
    （类型权重 + fitness sys_config）的内容摘要。

    P2-03 计算上下文与历史复现：
    - configRevision 接 P2-02 持久 revision（任务边界固定，格式
      ``rev:{n}:{digest16}``；不可得时回落内容摘要 ``digest:...``）；
    - ``save_input_snapshot=True`` 时按 DEC-10 类别（显式人工请求）保存
      不可变 NPZ+manifest 输入包并填充 record.datasetSnapshotId；保存失败
      显式降级（payload 标 inputSnapshot 状态，绝不静默冒充可复现）。

    G2-INC-a 完整旧参数固定：
    - ``pinned_config`` 为任务边界经 ``config_publish.pin_config_snapshot``
      固定的完整快照；本函数按回路 scope 解析五层有效参数并注入全部算子
      （``with_pinned_params``），计算全程不读当前缓存——运行中发布 v2
      不影响本任务；None=未固定（回填/单回路既有口径，读当前缓存不变）。
    - 落包时 manifest.source.effectiveParams 携带五层展开值与逐参来源，
      重放据此按旧参数复现（不再读当前配置）。

    P3-08a 发布代际接线：
    - ``release_context`` 为任务边界经 ``assessment_profile.
      resolve_task_release_context`` 固定的上线清单六要素；落包 manifest
      ``source.{assessmentProfile, releaseGeneration, dataPolicyVersion}``
      （1.3 只加不改；algorithmVersion/configRevision/executableBuildDigest
      既有）+ record payload ``assessmentContext``；None=未解析（回填/单回
      路既有口径，不写新节——旧读端按节缺失=当时未记录处理）。
    - record payload 增存 ``metricsSemantic``/``composite``（完整语义明细，
      供重放完整语义比较；latest 投影形状不动）。

    Args:
        db: 异步数据库会话
        loop: 回路对象
        metric_configs: 指标配置字典 {metric_code: MetricConfig}
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        data_planner: DataPlanner 实例（v4.0 统一取数）
        type_weights: 回路类型权重映射（LoopTypeWeight）
        custom_task_id: 自定义任务 ID（非 None 时写入 kpi_snapshot_custom）
        loop_cfg: 预加载的回路配置（op_lower/op_upper/range_min/range_max）
        logical_run_id: 结果账本逻辑运行身份（批次级，None=上下文推导）
        save_input_snapshot: 保存不可变输入包（DEC-10 范围控制，默认关）
        pinned_config: 任务边界固定的完整配置快照（P2-02 pin；None=未固定）
        runtime_overrides: P5-02 批级共享运行时阈值（fitness 阈值 + KPI 观测
            覆盖率阈值，``_load_batch_runtime_overrides`` 产物）；None=独立
            调用方（benchmark 等），本函数内回落逐回路加载（兼容口径）。
        template_keys: P5-02 批级预载的 ``{template_id: template_key}`` 映射
            （五层解析 TEMPLATE 层）；None=回落逐回路查询（兼容口径）。

    Returns:
        快照字典，包含 status 字段
    """
    from app.services.loop_config import infer_score_type

    control_type = _loop_type_to_control_type(loop.loop_type)
    time_window = TimeWindow(start=ts_start, end=ts_end)

    # P2: fitness 阈值配置（sys_config 中 fitness.* 前缀）。
    # P5-02 批量元信息预载：批级 runtime_overrides 优先（同批所有回路共享
    # 同一快照，等价基线：值与逐回路读取同源同值 → configRevision/分值不变）；
    # None（独立调用方）时回落逐回路查询。
    if runtime_overrides is not None:
        fitness_sys_configs = runtime_overrides.get("fitness")
    else:
        fitness_sys_configs = None
        try:
            fitness_sys_configs = await _load_fitness_sys_configs(db)
        except Exception:  # noqa: BLE001
            fitness_sys_configs = None  # 使用默认阈值

    # P1-05 结果账本：实际消费配置内容摘要作为 configRevision；P2-03 起叠加
    # P2-02 任务边界固定的全局持久 revision（格式 rev:{n}:{digest16}，任务中
    # 不切参；不可得时回落内容摘要口径 digest:...，不伪补版本）
    config_revision = result_ledger.config_revision_digest(
        {"typeWeights": type_weights or {}, "fitness": fitness_sys_configs or {}}
    )
    pinned_revision = pinned_config.get("configRevision") if pinned_config else None
    if pinned_revision is not None:
        config_revision = f"rev:{pinned_revision}:{config_revision.removeprefix('digest:')}"

    # G2-INC-a：在固定快照上解析本回路五层有效参数（任务中不换参——后续
    # 发布只改当前缓存/DB，不影响本 map；新任务重新 pin 取新值）
    pinned_params: dict[tuple[str, str], dict] | None = None
    pinned_resolve_meta: dict | None = None
    if pinned_config is not None:
        try:
            pinned_params, pinned_resolve_meta = await _resolve_loop_pinned_params(
                db, pinned_config, loop, template_keys=template_keys
            )
        except Exception:  # noqa: BLE001
            logger.warning("回路 %s 五层参数解析失败（回落当前缓存口径）", loop.id, exc_info=True)
            pinned_params = None
            pinned_resolve_meta = None

    # 分段计时（2026-10-08 评估吞吐优化观测）：read=TDengine 取数 /
    # persist=快照写库 / compute=其余（门禁+三层计算+fitness）。
    # 结果 dict 附 _timing 键（仅内存返回供批量聚合日志，不入库）。
    # P2-03：输入包状态（save_input_snapshot 时在三层计算前填充；保存失败
    # 显式降级标记随 payload 入账本，datasetSnapshotId=None）
    dataset_snapshot_state: dict[str, Any] | None = None
    # P3-08a：完整语义明细（三层计算后填充；早退分支 None=该窗口无指标语义）
    metrics_semantic: dict[str, Any] | None = None
    _t0 = time.perf_counter()
    _t_read = 0.0
    _t_persist = 0.0

    def _timing_into(result: dict | None) -> dict | None:
        if isinstance(result, dict):
            result["_timing"] = {
                "read": round(_t_read, 3),
                "compute": round(time.perf_counter() - _t0 - _t_read - _t_persist, 3),
                "persist": round(_t_persist, 3),
            }
        return result

    async def _timed_persist(**kwargs: Any) -> dict:
        nonlocal _t_persist
        _tp = time.perf_counter()
        # P1-05 结果账本：逻辑运行身份 + 配置摘要注入（调用方未显式指定时）
        kwargs.setdefault("logical_run_id", logical_run_id)
        kwargs.setdefault("config_revision", config_revision)
        # P2-03：输入包引用注入（save_input_snapshot 时已保存；含显式降级标记）
        if dataset_snapshot_state is not None:
            kwargs.setdefault("dataset_snapshot_id", dataset_snapshot_state.get("snapshotId"))
            kwargs.setdefault("input_snapshot_state", dataset_snapshot_state)
        # P3-08a：语义明细与发布上下文随 record payload 入账本（幂等 setdefault，
        # 早退分支 metrics_semantic=None → _persist_snapshot 跳过写键）
        kwargs.setdefault("metrics_semantic", metrics_semantic)
        # P5-02 显式重评链门（批级判定集合；None=未判定不解析）
        kwargs.setdefault("failed_source_ids", failed_source_ids)
        # REWORK-D2：assessmentContext 回填任务实际 configRevision/paramsSource
        # （与包 manifest.source 同源，重放语义比较 identity 域两侧恒等）
        kwargs.setdefault(
            "assessment_context",
            _release_context_with_actuals(
                release_context, config_revision, params_pinned=pinned_params is not None
            ),
        )
        _r = await _persist_snapshot(**kwargs)
        _t_persist += time.perf_counter() - _tp
        return _r

    # 通过 DataPlanner 获取所有指标的 MetricDataBundle
    _tr = time.perf_counter()
    try:
        bundles = await data_planner.request_bundles(
            loop_id=str(loop.id),
            metrics=_ALL_METRIC_CODES_DB,
            time_window=time_window,
            control_type=control_type,
        )
        _t_read += time.perf_counter() - _tr
    except Exception as exc:  # noqa: BLE001
        # ⑤（2026-10-13 连接风暴）：加 exc_info——首发现场只记 message 导致
        # 毒化首发行的精确位置无法钉死（诊断报告 §3.5 的直接原因）。
        logger.warning("DataPlanner 取数失败（回路 %s）: %s", loop.tag_name, exc, exc_info=True)
        # P2: DataPlanner 失败 → 数据严重不足 → L0
        return _timing_into(
            await _timed_persist(
                db=db,
                loop_id=str(loop.id),
                ts_start=ts_start,
                ts_end=ts_end,
                status="INCONCLUSIVE",
                custom_task_id=custom_task_id,
                fitness_level="L0",
                fitness_tags={"tags": ["DATA_INSUFFICIENT"]},
                fitness_detail={"reason": "DataPlanner取数失败", "error": str(exc)},
                assess_level="L0",
                diagnose_level="L0",
                tune_level="L0",
                source=source,
                source_task_id=source_task_id,
            )
        )

    if not bundles:
        logger.info("回路 %s 无数据（空 Bundle），返回 INCONCLUSIVE", loop.tag_name)
        # P2: 空 Bundle → 数据严重不足 → L0
        return _timing_into(
            await _timed_persist(
                db=db,
                loop_id=str(loop.id),
                ts_start=ts_start,
                ts_end=ts_end,
                status="INCONCLUSIVE",
                custom_task_id=custom_task_id,
                fitness_level="L0",
                fitness_tags={"tags": ["DATA_INSUFFICIENT"]},
                fitness_detail={"reason": "空Bundle，无可用数据"},
                assess_level="L0",
                diagnose_level="L0",
                tune_level="L0",
                source=source,
                source_task_id=source_task_id,
            )
        )

    # P2: 提取 BASE 时序（fitness 的 OP_SATURATED / SP_PV_DEVIATION 逐点统计用）
    op_series, sp_series, pv_series, mode_series, base_point_count = _extract_base_series(bundles)
    op_range, pv_range = _resolve_op_pv_ranges(loop, loop_cfg, pv_series, sp_series, bundles)

    # P2: 复用 gate.py 评估门禁 → L0 判定口径与诊断链路完全一致
    # R14-3（2026-09-06）：期望点数基于契约采样间隔（thresholds.base_sampling_freq）
    # 而非 DataBlock.sampling_freq（后者现已反映实际中位间隔，稀疏数据下
    # 用它反推期望会把缺口洗白）
    from app.services.preprocessing.thresholds import get_threshold as _get_threshold

    expected_points = _derive_expected_points(
        bundles,
        ts_start,
        ts_end,
        expected_interval_s=_get_threshold(control_type).base_sampling_freq,
    )
    base_valid_rate = _compute_loop_valid_rate_from_bundles(bundles)

    # AD03/I05（§4.2）：point 网格门禁口径——expected=N（上下文期望格点数，
    # 不用名义间隔推算）；point_count 投影为"可用有效样本"（核心角色交集
    # 有效数），不得传 N 个含未知占位的行数；gapRatio 由此代表不可用样本
    # 比例。legacy 路径实参完全不变。
    _point_ctx = _point_context_from_bundles(bundles)
    if _point_ctx is not None:
        expected_points = int(_point_ctx.expected_slots)
        base_point_count = int(round(base_valid_rate * expected_points)) if base_valid_rate else 0

    # 构造虚拟 CONFIG bundle（提供 control_type / 手动理想稳态时间信号给计算器）
    config_bundle = _build_config_bundle(str(loop.id), control_type, loop.ideal_settling_time)

    # 构造权重映射（MetricConfig.weight > LoopTypeWeight > None）
    score_type = infer_score_type(loop.loop_type)
    weights = _build_weights_map(type_weights, score_type, metric_configs)

    # P2-03：保存不可变输入包（DEC-10 类别=显式人工请求；小时例行不落包）。
    # 包内容=实际消费的 DataBlock 全网格（缺口以逐槽 mask 显式保留，不删点
    # 不拼段）+ manifest（绑定/量程/权重/配置上下文/构建摘要）。写入次序=
    # 先原子写文件核 hash 再提交 DB 引用；失败显式降级（不阻断计算主链）。
    if save_input_snapshot:
        try:
            from app.services import calc_snapshot as cs
            from app.services.assessment_profile import release_manifest_source_section

            arrays, blocks_meta = cs.serialize_data_blocks(bundles)
            dataset_ref = (
                getattr(_point_ctx, "dataset_ref", None) if _point_ctx is not None else None
            ) or f"loop:{loop.id}"
            manifest_extra: dict[str, Any] = {
                "loopId": str(loop.id),
                "tagName": loop.tag_name,
                "loopType": loop.loop_type,
                "window": {"tsStart": ts_start.isoformat(), "tsEnd": ts_end.isoformat()},
                "binding": {
                    "datasetRef": dataset_ref,
                    "bindingVersion": getattr(_point_ctx, "binding_version", None)
                    if _point_ctx is not None
                    else None,
                },
                "engineering": {
                    "opRange": list(op_range) if op_range else None,
                    "pvRange": list(pv_range) if pv_range else None,
                },
                "blocks": blocks_meta,
                "replayContext": {
                    "loopId": str(loop.id),
                    "controlType": control_type,
                    "idealSettlingTime": loop.ideal_settling_time,
                    "weights": weights,
                    "opRange": list(op_range) if op_range else None,
                    "pvRange": list(pv_range) if pv_range else None,
                },
                "source": {
                    "algorithmVersion": (
                        (release_context or {}).get("algorithmVersion") or ALGORITHM_VERSION
                    ),
                    "configRevision": config_revision,
                    # P3-08a（schema 1.3 只加不改）：发布代际三要素——上线
                    # 清单六要素之 assessmentProfile/releaseGeneration/
                    # dataPolicyVersion（algorithmVersion/configRevision/
                    # executableBuildDigest 既有；schemaVersion=manifest 自身）。
                    # release_context=None（回填/单回路）不写新节=当时未记录。
                    **(
                        release_manifest_source_section(release_context)
                        if release_context is not None
                        else {}
                    ),
                    "effectiveParams": {
                        "typeWeights": type_weights or {},
                        "fitness": fitness_sys_configs or {},
                        # G2-INC-a：完整五层有效参数（展开值+逐参来源+解析作用域），
                        # 重放据此按旧参数复现；UNPINNED=任务边界固定失败（诚实标记，
                        # 不伪补；重放按 legacy-unpinned 口径显式标注）
                        "status": "PINNED" if pinned_params is not None else "UNPINNED",
                        "configRevision": pinned_revision,
                        "scope": (
                            {
                                "templateKey": pinned_resolve_meta.get("templateKey"),
                                "nodeId": pinned_resolve_meta.get("nodeId"),
                                "loopId": pinned_resolve_meta.get("loopId"),
                            }
                            if pinned_resolve_meta is not None
                            else None
                        ),
                        "params": (
                            {f"{m}|{ct}": p for (m, ct), p in sorted(pinned_params.items())}
                            if pinned_params is not None
                            else None
                        ),
                        "sources": (
                            {
                                f"{m}|{ct}": s
                                for (m, ct), s in sorted(
                                    (pinned_resolve_meta.get("sources") or {}).items()
                                )
                            }
                            if pinned_params is not None
                            else None
                        ),
                    },
                },
            }
            snap = await cs.save_calculation_snapshot(
                db,
                kind=cs.KIND_KPI_GRID,
                ts_start=ts_start,
                ts_end=ts_end,
                dataset_ref=dataset_ref,
                arrays=arrays,
                manifest_extra=manifest_extra,
                loop_id=str(loop.id),
            )
            if snap is not None:
                dataset_snapshot_state = {
                    "snapshotId": str(snap.id),
                    "inputHash": snap.input_hash,
                    "schemaVersion": cs.SNAPSHOT_SCHEMA_VERSION,
                    "status": "SAVED",
                }
        except Exception as exc:  # noqa: BLE001
            dataset_snapshot_state = {"status": "SAVE_FAILED", "error": str(exc)}
            logger.warning(
                "输入包保存失败（回路 %s 窗口 %s~%s），record 将标记不可完整复现",
                loop.tag_name,
                ts_start,
                ts_end,
                exc_info=True,
            )

    # 三层计算：Layer1（无依赖）→ Layer2（有依赖）→ Layer3（综合评分）
    # G2-INC-a：pinned_params 非空时全部算子只消费任务固定快照（不读当前缓存）
    metric_results, composite_result = _compute_kpis_three_layer(
        bundles, config_bundle, weights, pinned_params=pinned_params
    )

    # 12 子指标值+可信度（随 loop_confidence_latest 存储）
    metrics_detail = _extract_metrics_detail(metric_results)
    # P3-08a：完整语义明细（record payload 专用；latest 投影形状不动）
    metrics_semantic = _extract_metrics_semantic(metric_results, composite_result)

    # 提取数据血缘信息
    # 可信度统一 Phase 1（P1-5）：valid_rate 改用回路级口径（核心 tag 交集 / point_count），
    # 与 confidence_level 同口径，消除 §2.2 字段错配。
    # 优先从 BASE 块（含 pv/sp/mode）计算回路级 valid_rate；缺失时回退到指标级 lineage。
    # G5-γ1（2026-10-13）：前移至 INCONCLUSIVE 分支之前——该分支同样需要
    # 血缘字段（algorithm_version 等），此前分支内缺参导致快照/latest 版本列
    # 全 NULL（批 1 实测 100 行 DATA_INSUFFICIENT 四条件违约）。
    loop_valid_rate = base_valid_rate
    lineage_info = _extract_lineage_info(
        metric_results, composite_result, loop_valid_rate=loop_valid_rate
    )

    # P2: 基于门禁（gate.py evaluate_gate）构造 GateResult 供 compute_fitness L0 判定复用
    conf_level_for_gate = (
        composite_result.confidence_level if composite_result.confidence_level else "E"
    )
    valid_rate_for_gate = base_valid_rate if base_valid_rate is not None else 0.0
    try:
        gate_result = evaluate_gate(
            point_count=base_point_count,
            expected_points=expected_points,
            valid_rate=float(valid_rate_for_gate),
            confidence_level=conf_level_for_gate,
        )
    except Exception:  # noqa: BLE001
        gate_result = None

    # 综合评分为 None（R 可信度 E 级）→ INCONCLUSIVE
    if composite_result.value is None:
        logger.info("回路 %s 综合评分为 None（E 级），返回 INCONCLUSIVE", loop.tag_name)
        # P2: 可信度 E 级 + gate 构建 → compute_fitness 大概率 L0，但允许 L1 兜底
        # G5-γ1（2026-10-13 批 1 实测）：source/source_task_id 原放本 dict，
        # 被 _build_fitness_kwargs_from_result 整体替换吞掉（该函数只含
        # fitness/三性六键）→ 快照/record/latest 的 source 与版本列全 NULL。
        # 修复：显式字段改在 _timed_persist 调用点平铺（对齐主路径口径），
        # 本 dict 只保留可被整体替换的 fitness 域键。
        fitness_kwargs: dict[str, Any] = {
            "fitness_level": None,
            "fitness_tags": None,
            "fitness_detail": None,
        }
        try:
            fresult = compute_fitness(
                kpi_values={},
                gate_result=gate_result,
                op_series=op_series,
                sp_series=sp_series,
                pv_series=pv_series,
                mode_series=mode_series,
                op_range=op_range,
                pv_range=pv_range,
                sys_configs=fitness_sys_configs,
            )
            fitness_kwargs = _build_fitness_kwargs_from_result(fresult)
        except Exception as exc:  # noqa: BLE001
            logger.warning("回路 %s fitness 计算失败（INCONCLUSIVE分支）: %s", loop.tag_name, exc)
        return _timing_into(
            await _timed_persist(
                db=db,
                loop_id=str(loop.id),
                ts_start=ts_start,
                ts_end=ts_end,
                status="INCONCLUSIVE",
                custom_task_id=custom_task_id,
                metrics_detail=metrics_detail,
                # 来源标注（G5-γ1）：平铺传递，不被 fitness_kwargs 整体替换波及
                source=source,
                source_task_id=source_task_id,
                # 血缘字段（G5-γ1）：algorithm_version 等与主路径同源，
                # 闭合快照/latest 版本列 NULL 违约；confidence_level 经
                # lineage_info 携带（composite 可信度，E 兜底）
                **lineage_info,
                **fitness_kwargs,
            )
        )

    # 提取 KPI 值（Calculator 代码 → DB 列名）
    kpi_values = _extract_kpi_values(metric_results)

    # 提取阀门运行区间 op_min/op_max（从 valve_operating_range details，Task 6）
    valve_op_min, valve_op_max = _extract_valve_op_range(metric_results)

    # 数据血缘（G5-γ1 前移至 INCONCLUSIVE 分支之前，主路径沿用同一变量）

    # P2: 核心路径 — KPI 聚合完成后调用 compute_fitness
    # 规则：fitness 计算失败不得阻断 KPI 主流程，catch 异常后 fitness_level=NULL
    main_fitness_kwargs: dict[str, Any] = {
        "fitness_level": None,
        "fitness_tags": None,
        "fitness_detail": None,
    }
    try:
        # 把 Decimal KPI 转换为 float 归一化映射给 fitness
        kpi_for_fitness: dict[str, Any] = {}
        for k, v in kpi_values.items():
            if v is None:
                continue
            try:
                kpi_for_fitness[k] = float(v)
            except (TypeError, ValueError):
                pass
        fresult_main = compute_fitness(
            kpi_values=kpi_for_fitness,
            gate_result=gate_result,
            op_series=op_series,
            sp_series=sp_series,
            pv_series=pv_series,
            mode_series=mode_series,
            op_range=op_range,
            pv_range=pv_range,
            sys_configs=fitness_sys_configs,
        )
        main_fitness_kwargs = _build_fitness_kwargs_from_result(fresult_main)
    except Exception as exc:  # noqa: BLE001
        logger.warning("回路 %s fitness 计算失败（主路径，已兜底NULL）: %s", loop.tag_name, exc)

    # 判定状态：必需指标缺失 → PARTIAL
    status = "SUCCESS"
    required_kpis = ("good_value_rate", "auto_mode_rate", "steady_rate")
    if any(kpi_values.get(k) is None for k in required_kpis):
        status = "PARTIAL"

    # R14-3（2026-09-06）：缺口 gate 结论贯穿快照——gate 不通过时最终快照
    # 不得 SUCCESS+高分。现有状态枚举已含 INCONCLUSIVE，此处直接采用；
    # 评分置 None、可信度按最低等级 E + 原因字符串兼容表达（无 DB 迁移）。
    # 指标值仍随快照落库（供诊断明细参考），但页面级评分/状态不可用。
    final_score = (
        _quantize(Decimal(str(composite_result.value)))
        if composite_result.value is not None
        else None
    )
    gate_failed = gate_result is not None and not gate_result.passed
    if gate_failed:
        status = "INCONCLUSIVE"
        final_score = None
        lineage_info = dict(lineage_info)
        lineage_info["confidence_level"] = "E"
        fitness_detail = main_fitness_kwargs.get("fitness_detail")
        merged_detail = dict(fitness_detail) if isinstance(fitness_detail, dict) else {}
        merged_detail["gate_failed_reason"] = gate_result.reason
        merged_detail["gate"] = gate_result.to_dict()
        main_fitness_kwargs = dict(main_fitness_kwargs)
        main_fitness_kwargs["fitness_detail"] = merged_detail
        logger.info(
            "回路 %s 数据门禁不通过，快照置 INCONCLUSIVE（score=None）: %s",
            loop.tag_name,
            gate_result.reason,
        )

    # 3（2026-09-25）：KPI 观测覆盖率闸门（sys_config 开关，缺省不启用）。
    # 现状：缺口小时被 HELD 填平后照常给分 = 拿填充值算 KPI；开关启用后
    # 覆盖率不足即降级 INCONCLUSIVE（与 gate_failed 同一处置口径：score=None、
    # 可信度 E、原因写进 fitness_detail）。
    if status == "SUCCESS":
        # P5-02 批级共享（runtime_overrides 优先；None=独立调用方回落逐回路查询）
        if runtime_overrides is not None:
            min_observed_ratio = runtime_overrides.get("kpiMinObservedRatio")
        else:
            min_observed_ratio = await _load_kpi_min_observed_ratio(db)
        if min_observed_ratio is not None:
            observed_ratio = _observed_ratio_from_bundles(bundles)
            if observed_ratio is not None and observed_ratio < min_observed_ratio:
                status = "INCONCLUSIVE"
                final_score = None
                lineage_info = dict(lineage_info)
                lineage_info["confidence_level"] = "E"
                _coverage_detail = main_fitness_kwargs.get("fitness_detail")
                _merged_coverage = (
                    dict(_coverage_detail) if isinstance(_coverage_detail, dict) else {}
                )
                _merged_coverage["coverage_insufficient"] = {
                    "observedRatio": round(observed_ratio, 4),
                    "minObservedRatio": min_observed_ratio,
                }
                main_fitness_kwargs = dict(main_fitness_kwargs)
                main_fitness_kwargs["fitness_detail"] = _merged_coverage
                logger.info(
                    "回路 %s 窗口 %s 观测覆盖率 %.3f < 阈值 %.3f，快照置 INCONCLUSIVE（数据不足）",
                    loop.tag_name,
                    ts_start,
                    observed_ratio,
                    min_observed_ratio,
                )

    return _timing_into(
        await _timed_persist(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status=status,
            custom_task_id=custom_task_id,
            metrics_detail=metrics_detail,
            score=final_score,
            # 来源标注（G5 前置立卡项1，2026-10-13）：主成功/部分路径此前漏传
            # source/source_task_id——快照 UPSERT 把两列覆盖为 NULL（早退分支
            # 均已透传，唯主路径缺失），批 1' 现场 1000 行 source=None 即此因。
            source=source,
            source_task_id=source_task_id,
            good_value_rate=kpi_values.get("good_value_rate"),
            auto_mode_rate=kpi_values.get("auto_mode_rate"),
            effective_auto_rate=kpi_values.get("effective_auto_rate"),
            steady_rate=kpi_values.get("steady_rate"),
            accuracy_rate=kpi_values.get("accuracy_rate"),
            fast_rate=kpi_values.get("fast_rate"),
            oscillation_rate=kpi_values.get("oscillation_rate"),
            saturation_rate=kpi_values.get("saturation_rate"),
            stiction_index=kpi_values.get("stiction_index"),
            output_trip_index=kpi_values.get("output_trip_index"),
            settling_time=kpi_values.get("settling_time"),
            ideal_settling_time=kpi_values.get("ideal_settling_time"),
            # Phase 1 新增指标（HiaMonitor 借鉴，2026-07-23）
            instrument_fault_rate=kpi_values.get("instrument_fault_rate"),
            pv_mean=kpi_values.get("pv_mean"),
            pv_std=kpi_values.get("pv_std"),
            sp_mean=kpi_values.get("sp_mean"),
            sp_std=kpi_values.get("sp_std"),
            op_mean=kpi_values.get("op_mean"),
            op_std=kpi_values.get("op_std"),
            error_mean=kpi_values.get("error_mean"),
            error_std=kpi_values.get("error_std"),
            valve_linearity=kpi_values.get("valve_linearity"),
            valve_nonlinearity=kpi_values.get("valve_nonlinearity"),
            valve_op_min=valve_op_min,
            valve_op_max=valve_op_max,
            setpoint_crossing_count=kpi_values.get("setpoint_crossing_count"),
            oscillation_amplitude=kpi_values.get("oscillation_amplitude"),
            time_constant=kpi_values.get("time_constant"),
            **lineage_info,
            **main_fitness_kwargs,
        )
    )
