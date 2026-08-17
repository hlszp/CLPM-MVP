"""自动诊断三层触发单测（设计文档 §12）。

覆盖：trigger_type 链路透传（batch→orchestrator 落库参数）、
调度密度门禁、事件驱动防抖与窗口钳制、API 标签透出。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.alert_rule_engine import dispatcher as disp
from app.services.alert_rule_engine.dsl import validate_dsl


class TestDslActionWhitelist:
    def test_trigger_diagnosis_accepted(self) -> None:
        """TRIGGER_DIAGNOSIS 进入动作白名单（合法 DSL）。"""
        dsl = {
            "ruleCode": "R-EVT-01",
            "name": "PV 越限触发诊断",
            "ruleType": "THRESHOLD",
            "scope": {"loopSelector": {"type": "ALL"}},
            "condition": {
                "type": "THRESHOLD",
                "metric": "PV",
                "operator": ">",
                "value": 100,
                "windowSeconds": 1800,
            },
            "actions": [
                {"type": "CREATE_EVENT"},
                {"type": "TRIGGER_DIAGNOSIS"},
            ],
            "severity": "WARN",
        }
        result = validate_dsl(dsl)
        assert result.get("valid") is True or not result.get("errors")


class TestEventTriggerDedup:
    def _mock_session(self, scalar_value):
        """构造 AsyncSessionLocal patch 上下文（函数内延迟 import，patch 源模块）。"""
        mock_execute = MagicMock()
        mock_execute.scalar_one_or_none = MagicMock(return_value=scalar_value)
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_execute)
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = mock_db
        return patch("app.core.db.AsyncSessionLocal", return_value=session_cm)

    @pytest.mark.asyncio
    async def test_dedup_skips_recent_event_run(self) -> None:
        """同回路 6h 内已有 EVENT 诊断 → 防抖跳过（不建任务）。"""
        recent_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        with self._mock_session(recent_at):
            with patch(
                "app.services.task_tracker.create_task", new_callable=AsyncMock
            ) as mock_create:
                result = await disp._trigger_diagnosis({"ruleCode": "R1", "dsl": {}}, "loop-1")
        assert result is None
        mock_create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_window_clamped(self) -> None:
        """windowSeconds 钳制 [1h, 7d]：1800s → 3600s；trigger_type=EVENT。"""
        with self._mock_session(None):
            with (
                patch(
                    "app.services.task_tracker.create_task", new_callable=AsyncMock
                ) as mock_create,
                patch("app.tasks.diagnosis_v2.run_diagnosis_batch") as mock_batch,
                patch("app.services.task_tracker.set_celery_task_ids", new_callable=AsyncMock),
            ):
                mock_batch.delay.return_value = MagicMock(id="celery-x")
                rule = {
                    "ruleCode": "R1",
                    "ruleName": "规则一",
                    "dsl": {"condition": {"windowSeconds": 1800}},
                }
                await disp._trigger_diagnosis(rule, "loop-1")
        kwargs = mock_batch.delay.call_args.kwargs
        start = datetime.fromisoformat(kwargs["start"])
        end = datetime.fromisoformat(kwargs["end"])
        assert (end - start).total_seconds() == 3600
        assert kwargs["trigger_type"] == "EVENT"
        assert kwargs["triggered_by"] == "alert:R1"
        mock_create.assert_awaited_once()


class TestScheduleDensityGate:
    @pytest.mark.asyncio
    async def test_sparse_window_skipped(self) -> None:
        """窗口行数 < 预期 50% → 密度门禁拦截。"""
        from app.tasks.diagnosis_schedule import _density_ok

        meta = {"subtable": "d_loop_test"}
        start = datetime(2026, 8, 18, 0, 0)
        end = datetime(2026, 8, 18, 1, 0)  # 预期 3600 点
        rows = [{"count(*)": 100}]  # 仅 100 行 → 不足
        with patch("app.tasks.diagnosis_schedule.execute_sql", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = rows
            assert await _density_ok(meta, start, end) is False

    @pytest.mark.asyncio
    async def test_dense_window_passes(self) -> None:
        from app.tasks.diagnosis_schedule import _density_ok

        meta = {"subtable": "d_loop_test"}
        start = datetime(2026, 8, 18, 0, 0)
        end = datetime(2026, 8, 18, 1, 0)
        with patch("app.tasks.diagnosis_schedule.execute_sql", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [{"count(*)": 3000}]
            assert await _density_ok(meta, start, end) is True


class TestTriggerTypePassthrough:
    def test_batch_signature_has_trigger_type(self) -> None:
        """run_diagnosis_batch 暴露 trigger_type 参数（默认 MANUAL）。"""
        import inspect

        from app.tasks.diagnosis_v2 import run_diagnosis_batch

        sig = inspect.signature(run_diagnosis_batch)
        assert "trigger_type" in sig.parameters
        assert sig.parameters["trigger_type"].default == "MANUAL"

    def test_trigger_type_labels(self) -> None:
        from app.api.v1.endpoints.diagnosis_v2 import _TRIGGER_TYPE_LABELS

        assert _TRIGGER_TYPE_LABELS["MANUAL"] == "手动诊断"
        assert _TRIGGER_TYPE_LABELS["SCHEDULED"] == "定期诊断"
        assert _TRIGGER_TYPE_LABELS["EVENT"] == "事件触发"
