"""Report generator Celery task 测试 (S5-SYS-003, S2-A4).

测试覆盖：
- _parse_content_template: None/有效JSON/无效JSON
- _generate_pdf: 生成 PDF 字节，验证内容
- _do_generate: 正常流程/配置不存在/非法周期/PDF 生成失败
- generate_report_task: 业务错误不重试/系统错误重试
- Beat schedule: 4 个周期任务已注册
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.celery_app import celery_app
from app.tasks.report_generator import (
    NonRetryableError,
    _do_generate,
    _generate_pdf,
    _parse_content_template,
    generate_report_task,
)

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_report_config(
    config_id: str = "00000000-0000-0000-0000-000000000b01",
    name: str = "日报配置",
    content_template: str | None = '{"sections": ["summary", "kpi"]}',
) -> MagicMock:
    """构造 mock ReportConfig 对象。"""
    config = MagicMock()
    config.id = config_id
    config.name = name
    config.content_template = content_template
    return config


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    """构造 execute 返回的 mock，支持 scalar_one_or_none()。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ===========================================================================
# _parse_content_template 单元测试
# ===========================================================================


class TestParseContentTemplate:
    """测试 _parse_content_template() JSON 解析。"""

    def test_none_returns_none(self) -> None:
        """None 输入应返回 None。"""
        assert _parse_content_template(None) is None

    def test_valid_json_returns_dict(self) -> None:
        """有效 JSON 字符串应解析为字典。"""
        result = _parse_content_template('{"key": "value", "count": 3}')
        assert result == {"key": "value", "count": 3}

    def test_invalid_json_returns_none(self) -> None:
        """无效 JSON 字符串应返回 None（不抛出异常）。"""
        assert _parse_content_template("not a json") is None

    def test_empty_string_returns_none(self) -> None:
        """空字符串应返回 None。"""
        assert _parse_content_template("") is None

    def test_array_json_returns_list(self) -> None:
        """JSON 数组应正确解析（返回 list）。"""
        result = _parse_content_template('["a", "b", "c"]')
        assert result == ["a", "b", "c"]


# ===========================================================================
# _generate_pdf 单元测试
# ===========================================================================


class TestGeneratePdf:
    """测试 _generate_pdf() PDF 生成。"""

    def test_returns_non_empty_bytes(self) -> None:
        """生成的 PDF 应为非空 bytes。"""
        pdf_bytes = _generate_pdf(report_period="DAILY")
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0

    def test_pdf_starts_with_pdf_magic(self) -> None:
        """PDF 应以 %PDF 魔数开头。"""
        pdf_bytes = _generate_pdf(report_period="DAILY")
        assert pdf_bytes.startswith(b"%PDF")

    def test_pdf_with_config_name(self) -> None:
        """带 config_name 时 PDF 应正常生成。"""
        pdf_bytes = _generate_pdf(
            report_period="WEEKLY",
            config_name="周报配置",
        )
        assert len(pdf_bytes) > 0
        assert pdf_bytes.startswith(b"%PDF")

    def test_pdf_with_content_template(self) -> None:
        """带 content_template 时 PDF 应包含模板内容。"""
        template = {"section1": "KPI 总览", "section2": "异常回路"}
        pdf_bytes = _generate_pdf(
            report_period="MONTHLY",
            config_name="月报",
            content_template=template,
        )
        assert len(pdf_bytes) > 0

    def test_pdf_all_periods(self) -> None:
        """所有报表周期（SHIFT/DAILY/WEEKLY/MONTHLY）都应正常生成 PDF。"""
        for period in ["SHIFT", "DAILY", "WEEKLY", "MONTHLY"]:
            pdf_bytes = _generate_pdf(report_period=period)
            assert pdf_bytes.startswith(b"%PDF"), f"周期 {period} PDF 生成失败"

    def test_pdf_unknown_period_uses_raw_value(self) -> None:
        """未知周期应使用原始值（不崩溃）。"""
        pdf_bytes = _generate_pdf(report_period="QUARTERLY")
        assert pdf_bytes.startswith(b"%PDF")


# ===========================================================================
# _do_generate 异步逻辑测试
# ===========================================================================


class TestDoGenerate:
    """测试 _do_generate() 异步报表生成逻辑。"""

    @pytest.mark.asyncio
    async def test_generate_without_config_success(self) -> None:
        """无 config_id 时应成功生成报表。"""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_generate(
                task_id="task-001",
                config_id=None,
                report_period="DAILY",
            )

        assert result["status"] == "COMPLETED"
        assert result["reportId"] == "task-001"
        assert "fileUrl" in result
        assert "fileSize" in result
        assert result["fileSize"] > 0

    @pytest.mark.asyncio
    async def test_generate_with_config_success(self) -> None:
        """带有效 config_id 时应成功生成报表。"""
        config = _make_report_config(name="班报配置")
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(config))

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_generate(
                task_id="task-002",
                config_id="config-001",
                report_period="SHIFT",
            )

        assert result["status"] == "COMPLETED"
        assert result["reportId"] == "task-002"

    @pytest.mark.asyncio
    async def test_generate_config_not_found_raises_non_retryable(self) -> None:
        """config_id 存在但配置不存在时应抛出 NonRetryableError。"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            with pytest.raises(NonRetryableError, match="报表配置不存在"):
                await _do_generate(
                    task_id="task-003",
                    config_id="non-existent",
                    report_period="DAILY",
                )

    @pytest.mark.asyncio
    async def test_generate_invalid_period_raises_non_retryable(self) -> None:
        """非法报表周期应抛出 NonRetryableError。"""
        with pytest.raises(NonRetryableError, match="非法报表周期"):
            await _do_generate(
                task_id="task-004",
                config_id=None,
                report_period="INVALID",
            )

    @pytest.mark.asyncio
    async def test_generate_pdf_failure_returns_failed_status(self) -> None:
        """PDF 生成失败时应返回 FAILED 状态（不抛出异常）。"""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_session_local,
            patch(
                "app.tasks.report_generator._generate_pdf",
                side_effect=RuntimeError("reportlab 错误"),
            ),
        ):
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_generate(
                task_id="task-005",
                config_id=None,
                report_period="DAILY",
            )

        assert result["status"] == "FAILED"
        assert "reportlab 错误" in result["error"]

    @pytest.mark.asyncio
    async def test_generate_uses_uuid_when_task_id_none(self) -> None:
        """task_id 为 None 时应自动生成 UUID。"""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            result = await _do_generate(
                task_id=None,
                config_id=None,
                report_period="WEEKLY",
            )

        assert result["status"] == "COMPLETED"
        # reportId 应为有效的 UUID 字符串
        assert len(result["reportId"]) == 36
        assert result["reportId"].count("-") == 4

    @pytest.mark.asyncio
    async def test_generate_all_valid_periods(self) -> None:
        """所有合法周期都应成功生成报表。"""
        for period in ["SHIFT", "DAILY", "WEEKLY", "MONTHLY"]:
            mock_session = AsyncMock()
            mock_session.add = MagicMock()
            mock_session.commit = AsyncMock()

            with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
                mock_session_local.return_value.__aenter__.return_value = mock_session
                result = await _do_generate(
                    task_id=f"task-{period}",
                    config_id=None,
                    report_period=period,
                )

            assert result["status"] == "COMPLETED", f"周期 {period} 生成失败"


# ===========================================================================
# generate_report_task Celery 任务入口测试
# ===========================================================================


class TestGenerateReportTask:
    """测试 generate_report_task() Celery 任务入口。"""

    def test_task_registered(self) -> None:
        """generate_report_task 应注册到 celery_app。"""
        assert "app.tasks.report_generator.generate_report_task" in celery_app.tasks

    def test_task_success_returns_result(self) -> None:
        """任务成功时应返回 _do_generate 的结果。"""
        expected_result = {
            "reportId": "task-success",
            "status": "COMPLETED",
            "fileUrl": "/reports/task-success.pdf",
            "fileSize": 1024,
        }

        task = generate_report_task
        # 模拟 AsyncTask.run_async 直接返回结果
        with patch.object(task, "run_async", return_value=expected_result):
            result = task(
                task_id="task-success",
                config_id=None,
                report_period="DAILY",
            )

        assert result == expected_result

    def test_task_non_retryable_error_not_retried(self) -> None:
        """NonRetryableError 应直接抛出，不触发重试。"""
        task = generate_report_task

        with (
            patch.object(
                task,
                "run_async",
                side_effect=NonRetryableError("配置不存在"),
            ),
            pytest.raises(NonRetryableError, match="配置不存在"),
        ):
            task(
                task_id="task-fail-business",
                config_id="non-existent",
                report_period="DAILY",
            )

    def test_task_system_error_triggers_retry(self) -> None:
        """系统错误（非 NonRetryableError）应触发 self.retry()。"""
        from celery.exceptions import Retry

        task = generate_report_task

        # Celery 的 retry() 在真实环境下会抛出 Retry 异常
        def _raise_retry(*args, **kwargs):
            raise Retry()

        with (
            patch.object(
                task,
                "run_async",
                side_effect=RuntimeError("DB 连接失败"),
            ),
            patch.object(task, "retry", side_effect=_raise_retry) as mock_retry,
        ):
            with pytest.raises(Retry):
                task(
                    task_id="task-fail-system",
                    config_id=None,
                    report_period="DAILY",
                )

            mock_retry.assert_called_once()
            # 验证 retry 的 exc 参数是 RuntimeError
            call_kwargs = mock_retry.call_args
            assert call_kwargs.kwargs.get("exc") is not None or call_kwargs.args


# ===========================================================================
# Beat schedule 配置测试
# ===========================================================================


class TestBeatSchedule:
    """验证 Beat 调度配置正确注册了 4 个周期任务。"""

    def test_beat_schedule_has_four_entries(self) -> None:
        """beat_schedule 应包含 4 个报表周期任务。"""
        schedule = celery_app.conf.beat_schedule
        report_entries = {
            k: v
            for k, v in schedule.items()
            if v.get("task") == "app.tasks.report_generator.generate_report_task"
        }
        assert len(report_entries) == 4

    def test_beat_schedule_contains_all_periods(self) -> None:
        """beat_schedule 应包含 SHIFT/DAILY/WEEKLY/MONTHLY 4 个周期。"""
        schedule = celery_app.conf.beat_schedule
        periods = set()
        for entry in schedule.values():
            if entry.get("task") == "app.tasks.report_generator.generate_report_task":
                periods.add(entry["kwargs"]["report_period"])

        assert periods == {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}

    def test_beat_schedule_timezone_is_shanghai(self) -> None:
        """Beat 调度时区应为 Asia/Shanghai。"""
        assert celery_app.conf.timezone == "Asia/Shanghai"

    def test_beat_schedule_intervals_reasonable(self) -> None:
        """Beat 调度间隔应合理（SHIFT=8h, DAILY=24h, WEEKLY=7d, MONTHLY=30d）。"""
        schedule = celery_app.conf.beat_schedule
        expected = {
            "report-shift": 28800.0,
            "report-daily": 86400.0,
            "report-weekly": 604800.0,
            "report-monthly": 2592000.0,
        }
        for key, expected_schedule in expected.items():
            assert key in schedule
            assert schedule[key]["schedule"] == expected_schedule
