"""人工批量评估直接计算，避免全批预热屏障。"""

from unittest.mock import AsyncMock, MagicMock


async def test_custom_batch_starts_without_full_batch_prewarm(monkeypatch):
    from app.tasks import kpi_calc

    loop = MagicMock(id="loop-1", tag_name="T-01")
    loops = MagicMock()
    loops.scalars.return_value.all.return_value = [loop]
    metrics = MagicMock()
    metrics.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute.side_effect = [loops, metrics]
    session = AsyncMock()
    session.__aenter__.return_value = db
    monkeypatch.setattr("app.core.db.AsyncSessionLocal", lambda: session)
    for name, result in [
        ("_batch_load_loop_configs", {}),
        ("_pin_task_config", None),
        ("_load_batch_runtime_overrides", {}),
        ("_batch_load_template_keys", {}),
        ("_resolve_task_release_context", None),
    ]:
        monkeypatch.setattr(kpi_calc, name, AsyncMock(return_value=result))
    monkeypatch.setattr(
        "app.services.loop_config.get_loop_type_weights_map", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        kpi_calc.result_ledger, "loop_ids_with_failed_records", AsyncMock(return_value=set())
    )
    prewarm = AsyncMock(side_effect=AssertionError("不能等待全批预热"))
    monkeypatch.setattr(kpi_calc, "_prewarm_cache_for_loops", prewarm)
    calculate = AsyncMock(return_value=[{"status": "SUCCESS"}])
    monkeypatch.setattr(kpi_calc, "_run_batch_loop_calculations", calculate)
    monkeypatch.setattr("app.services.task_tracker.update_status", AsyncMock())
    result = await kpi_calc._do_calculate_custom_batch(
        "task-1043", ["loop-1"], "2026-10-18T00:00:00", "2026-10-19T00:00:00"
    )
    calculate.assert_awaited_once()
    prewarm.assert_not_awaited()
    assert result["success"] == 1
    assert result["data_loading"] == "on_demand"
