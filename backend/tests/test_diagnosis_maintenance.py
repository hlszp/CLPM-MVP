"""诊断证据保留策略单测（diagnosis_maintenance，2026-08-18）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks import diagnosis_maintenance as dm


class TestEvidenceCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_executes_update_and_commits(self) -> None:
        """清理任务执行 UPDATE 并提交，返回清理行数。"""
        mock_result = MagicMock()
        mock_result.rowcount = 7
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        session_cm = AsyncMock()
        session_cm.__aenter__.return_value = mock_db

        with patch("app.tasks.diagnosis_maintenance.AsyncSessionLocal", return_value=session_cm):
            out = await dm._cleanup_expired_evidence()

        assert out["cleaned"] == 7
        assert out["cutoff"]
        sql_text = str(mock_db.execute.call_args.args[0])
        # 超期保护：每回路仅保留最新一条（NOT IN DISTINCT ON）
        assert "DISTINCT ON (loop_id)" in sql_text
        assert "evidence_charts = NULL" in sql_text
        assert "operator_results = NULL" in sql_text
        mock_db.commit.assert_awaited_once()

    def test_retention_window_is_30_days(self) -> None:
        assert dm.EVIDENCE_RETENTION_DAYS == 30

    def test_beat_schedule_registered(self) -> None:
        from app.tasks.celery_app import celery_app

        entry = (celery_app.conf.beat_schedule or {}).get("diagnosis-evidence-cleanup")
        assert entry is not None
        assert entry["task"] == "app.tasks.diagnosis_maintenance.cleanup_evidence"

    def test_task_bindable(self) -> None:
        """回归：装饰器必须 bind=True（同 diagnosis_schedule 教训）。"""
        with patch.object(dm.AsyncTask, "run_async", return_value={"cleaned": 0}):
            result = dm.cleanup_evidence.apply().get()
        assert result == {"cleaned": 0}
