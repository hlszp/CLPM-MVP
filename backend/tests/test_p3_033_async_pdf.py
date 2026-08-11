"""V62-P3-33 异步 PDF 导出闭环测试.

覆盖：
- ``generate_diagnosis_pdf_task`` Celery 任务注册 + 入口（成功/失败兜底）
- ``_do_generate_diagnosis_pdf_task`` 异步核心逻辑：
  * 进度阶段顺序（0.25 → 0.50 → 0.75 → 0.95 → 1.00）
  * variant=tracker_export / diagnosis_report 两种文件名
  * 文件落盘 + Redis Hash 写入 file_name/file_path/result_url
  * 返回值结构
- ``_maybe_mark_failed`` 异常兜底
- ``_task_to_response`` 对 PDF 产物字段（file_name/result_url）的映射
- ``GET /tasks/{taskId}/download`` 端点安全校验：
  * 任务不存在 → 404
  * 非创建者非 ADMIN → 403
  * PENDING/RUNNING → 425
  * file_path 缺失 → 404
  * 文件物理不存在 → 410
  * 成功 → FileResponse

依赖 mock：AsyncSessionLocal / TaskTracker.update_status / generate_diagnosis_report /
        Redis hset/expire，1s 内跑完。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints.tasks import _task_to_response
from app.schemas.task import TaskResponse, TaskStatus, TaskType
from app.tasks.celery_app import celery_app
from app.tasks.report_generator import (
    _do_generate_diagnosis_pdf_task,
    _maybe_mark_failed,
    generate_diagnosis_pdf_task,
)

# ===========================================================================
# 辅助
# ===========================================================================


def _fake_redis() -> MagicMock:
    """构造异步 redis_client mock（仅 hset/expire）。"""
    redis = MagicMock()
    redis.hset = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    return redis


def _make_loop_mock(tag_name: str = "LIC-101") -> MagicMock:
    loop = MagicMock()
    loop.tag_name = tag_name
    return loop


def _make_db_mock(loop: MagicMock | None = None) -> AsyncMock:
    """构造 AsyncSession mock——execute 返回 loop 查询结果。"""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = loop
    db.execute = AsyncMock(return_value=result)
    return db


# ===========================================================================
# generate_diagnosis_pdf_task 入口测试
# ===========================================================================


class TestGenerateDiagnosisPdfTask:
    """测试 generate_diagnosis_pdf_task() Celery 任务入口。"""

    def test_task_registered(self) -> None:
        """任务应注册到 celery_app。"""
        assert "app.tasks.report_generator.generate_diagnosis_pdf_task" in celery_app.tasks

    def test_task_success_returns_result(self) -> None:
        """任务成功时应返回 _do_generate_diagnosis_pdf_task 的结果。"""
        expected = {
            "taskId": "task-pdf-001",
            "status": "SUCCESS",
            "fileName": "CLPM-诊断建议书-LIC-101-2026-08-11.pdf",
            "resultUrl": "/api/v1/tasks/task-pdf-001/download",
            "fileSize": 2048,
        }
        task = generate_diagnosis_pdf_task
        with patch.object(task, "run_async", return_value=expected):
            result = task(
                tracker_task_id="task-pdf-001",
                loop_id="loop-001",
                variant="tracker_export",
            )
        assert result == expected

    def test_task_failure_invokes_maybe_mark_failed_and_reraises(self) -> None:
        """任务异常时应调用 _maybe_mark_failed 兜底并重新抛出原始异常。

        注意：源码 ``self.run_async(_maybe_mark_failed(tracker_task_id=...))``
        先**同步求值** ``_maybe_mark_failed(...)`` 得到 coroutine，再交给 run_async。
        因 run_async 本身被 patch 为抛错，coroutine 不会被 await，但函数已被调用。
        因此用 MagicMock + assert_called_once_with 验证调用，而非 assert_awaited。
        """
        task = generate_diagnosis_pdf_task
        original_error = RuntimeError("PDF 渲染崩溃")

        mark_failed_mock = MagicMock(return_value=AsyncMock())
        with (
            patch.object(
                task,
                "run_async",
                side_effect=original_error,
            ),
            patch(
                "app.tasks.report_generator._maybe_mark_failed",
                new=mark_failed_mock,
            ),
        ):
            with pytest.raises(RuntimeError, match="PDF 渲染崩溃"):
                task(
                    tracker_task_id="task-pdf-fail",
                    loop_id="loop-001",
                    variant="tracker_export",
                )
            # _maybe_mark_failed(tracker_task_id=...) 已被同步调用
            mark_failed_mock.assert_called_once_with(tracker_task_id="task-pdf-fail")


# ===========================================================================
# _do_generate_diagnosis_pdf_task 异步核心逻辑测试
# ===========================================================================


class TestDoGenerateDiagnosisPdfTask:
    """测试 _do_generate_diagnosis_pdf_task() 异步核心逻辑。"""

    @pytest.mark.asyncio
    async def test_tracker_export_success_progress_stages_and_file_written(
        self,
    ) -> None:
        """tracker_export variant：验证进度阶段顺序、文件落盘、Redis Hash 字段、返回值。"""
        tracker_task_id = "task-tracker-export"
        loop_id = "loop-101"

        # 捕获 update_status 的所有调用，按顺序断言 progress
        update_calls: list[float] = []

        async def _capture_update(task_id, status, **kwargs):  # noqa: ANN001
            update_calls.append(kwargs.get("progress", 0.0))

        loop_mock = _make_loop_mock(tag_name="LIC-202")
        db_mock = _make_db_mock(loop=loop_mock)
        pdf_bytes = b"%PDF-1.4 fake pdf bytes"

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"CLPM_EXPORT_DIR": tmpdir}),
            patch("app.core.db.AsyncSessionLocal") as mock_session_local,
            patch(
                "app.services.task_tracker.update_status",
                new=_capture_update,
            ),
            patch(
                "app.services.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value={"loop_id": loop_id}),
            ),
            patch(
                "app.services.diagnosis_recommendation.get_recommendations_for_loop",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.diagnosis_report.generate_diagnosis_report",
                return_value=pdf_bytes,
            ),
            patch(
                "app.services.task_tracker.redis_client",
                new=_fake_redis(),
            ),
        ):
            mock_session_local.return_value.__aenter__.return_value = db_mock
            result = await _do_generate_diagnosis_pdf_task(
                tracker_task_id=tracker_task_id,
                loop_id=loop_id,
                variant="tracker_export",
                tag_codes=None,
            )

            # 进度阶段：0.25 → 0.50 → 0.75 → 0.95 → 1.00（5 个阶段）
            assert update_calls == [0.25, 0.50, 0.75, 0.95, 1.0]

            # 文件名格式：CLPM-诊断建议书-{tagName}-{date}.pdf
            assert result["taskId"] == tracker_task_id
            assert result["status"] == "SUCCESS"
            assert result["fileName"].startswith("CLPM-诊断建议书-LIC-202-")
            assert result["fileName"].endswith(".pdf")
            assert result["resultUrl"] == f"/api/v1/tasks/{tracker_task_id}/download"
            assert result["fileSize"] == len(pdf_bytes)

            # 验证文件已落盘（在 tmpdir 退出前校验）
            files = os.listdir(tmpdir)
            assert len(files) == 1
            assert files[0].startswith(f"{tracker_task_id}_CLPM-诊断建议书-")

    @pytest.mark.asyncio
    async def test_diagnosis_report_variant_filename(self) -> None:
        """diagnosis_report variant：文件名为 diagnosis_report_{loop_id}.pdf。"""
        tracker_task_id = "task-diag-report"
        loop_id = "loop-303"

        loop_mock = _make_loop_mock()
        db_mock = _make_db_mock(loop=loop_mock)

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"CLPM_EXPORT_DIR": tmpdir}),
            patch("app.core.db.AsyncSessionLocal") as mock_session_local,
            patch("app.services.task_tracker.update_status", new=AsyncMock()),
            patch(
                "app.services.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.services.diagnosis_recommendation.get_recommendations",
                return_value=[],
            ),
            patch(
                "app.services.diagnosis_report.generate_diagnosis_report",
                return_value=b"%PDF-1.4",
            ),
            patch(
                "app.services.task_tracker.redis_client",
                new=_fake_redis(),
            ),
        ):
            mock_session_local.return_value.__aenter__.return_value = db_mock
            result = await _do_generate_diagnosis_pdf_task(
                tracker_task_id=tracker_task_id,
                loop_id=loop_id,
                variant="diagnosis_report",
                tag_codes=["OSCILLATION"],
            )

        assert result["fileName"] == f"diagnosis_report_{loop_id}.pdf"
        assert result["resultUrl"] == f"/api/v1/tasks/{tracker_task_id}/download"

    @pytest.mark.asyncio
    async def test_redis_hash_writes_file_metadata(self) -> None:
        """Redis Hash 应写入 file_name / file_path / result_url 三个字段。"""
        tracker_task_id = "task-redis-write"
        loop_id = "loop-404"

        loop_mock = _make_loop_mock(tag_name="FIC-505")
        db_mock = _make_db_mock(loop=loop_mock)
        fake_redis = _fake_redis()

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"CLPM_EXPORT_DIR": tmpdir}),
            patch("app.core.db.AsyncSessionLocal") as mock_session_local,
            patch("app.services.task_tracker.update_status", new=AsyncMock()),
            patch(
                "app.services.diagnosis.get_diagnosis_detail",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.services.diagnosis_recommendation.get_recommendations_for_loop",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.diagnosis_report.generate_diagnosis_report",
                return_value=b"%PDF-1.4",
            ),
            patch("app.services.task_tracker.redis_client", new=fake_redis),
        ):
            mock_session_local.return_value.__aenter__.return_value = db_mock
            await _do_generate_diagnosis_pdf_task(
                tracker_task_id=tracker_task_id,
                loop_id=loop_id,
                variant="tracker_export",
                tag_codes=None,
            )

            # 在 tmpdir 退出前校验文件物理存在
            fake_redis.hset.assert_awaited_once()
            call_args = fake_redis.hset.call_args
            # hset(hash_key, mapping={...}) — 第一位置参数为 hash_key
            assert call_args.args[0] == f"task:{tracker_task_id}"
            mapping = call_args.kwargs["mapping"]
            assert "file_name" in mapping
            assert "file_path" in mapping
            assert mapping["result_url"] == f"/api/v1/tasks/{tracker_task_id}/download"
            # file_path 应是真实落盘的绝对路径
            assert os.path.isabs(mapping["file_path"])
            assert os.path.exists(mapping["file_path"])
            # TTL 设置
            fake_redis.expire.assert_awaited_once()


# ===========================================================================
# _maybe_mark_failed 兜底测试
# ===========================================================================


class TestMaybeMarkFailed:
    """测试 _maybe_mark_failed() 异常兜底。"""

    @pytest.mark.asyncio
    async def test_marks_failed_with_generic_message(self) -> None:
        """任务异常时调用 update_status(FAILED)，错误消息为通用文案。"""
        with patch("app.services.task_tracker.update_status", new=AsyncMock()) as mock_update:
            await _maybe_mark_failed(tracker_task_id="task-doomed")

        mock_update.assert_awaited_once()
        call_args = mock_update.call_args
        assert call_args.args[0] == "task-doomed"
        assert call_args.args[1] == TaskStatus.FAILED
        assert call_args.kwargs.get("progress") == 0.0
        assert "异常" in call_args.kwargs.get("error_message", "")
        # finished_at 应是 ISO 字符串
        finished_at = call_args.kwargs.get("finished_at")
        assert isinstance(finished_at, str)
        datetime.fromisoformat(finished_at)  # 不抛即合法

    @pytest.mark.asyncio
    async def test_swallows_update_status_failure(self) -> None:
        """update_status 自身抛错时，_maybe_mark_failed 应吞掉异常（兜底层不能再失败）。"""
        with patch(
            "app.services.task_tracker.update_status",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            # 不应抛出
            await _maybe_mark_failed(tracker_task_id="task-doomed")


# ===========================================================================
# _task_to_response PDF 产物字段映射测试
# ===========================================================================


class TestTaskToResponsePdfFields:
    """测试 _task_to_response() 对 PDF 产物字段的映射。"""

    def test_maps_file_name_and_result_url(self) -> None:
        """Redis Hash 中的 file_name/result_url 应映射到 TaskResponse 同名字段。"""
        data = {
            "task_id": "task-pdf-resp",
            "task_type": TaskType.REPORT.value,
            "status": TaskStatus.SUCCESS.value,
            "created_at": "2026-08-11T10:00:00",
            "created_by": "admin",
            "progress": "1.0",
            "file_name": "CLPM-诊断建议书-LIC-101-2026-08-11.pdf",
            "file_path": "/tmp/exports/task-pdf-resp_CLPM-诊断建议书-LIC-101-2026-08-11.pdf",
            "result_url": "/api/v1/tasks/task-pdf-resp/download",
        }

        resp = _task_to_response(data)

        assert isinstance(resp, TaskResponse)
        assert resp.taskType == TaskType.REPORT
        assert resp.status == TaskStatus.SUCCESS
        assert resp.progress == 1.0
        assert resp.fileName == "CLPM-诊断建议书-LIC-101-2026-08-11.pdf"
        assert resp.resultUrl == "/api/v1/tasks/task-pdf-resp/download"

    def test_pdf_fields_none_when_missing(self) -> None:
        """非 REPORT 任务（如 STANDARD）的 file_name/result_url 应为 None。"""
        data = {
            "task_id": "task-eval-001",
            "task_type": TaskType.STANDARD.value,
            "status": TaskStatus.SUCCESS.value,
            "created_at": "2026-08-11T10:00:00",
            "created_by": "admin",
            "progress": "1.0",
            # 不含 file_name/file_path/result_url
        }

        resp = _task_to_response(data)

        assert resp.taskType == TaskType.STANDARD
        assert resp.fileName is None
        assert resp.resultUrl is None

    def test_pdf_fields_none_when_empty_string(self) -> None:
        """Redis Hash 中 file_name/result_url 为空字符串时应映射为 None。"""
        data = {
            "task_id": "task-pdf-empty",
            "task_type": TaskType.REPORT.value,
            "status": TaskStatus.PENDING.value,
            "created_at": "2026-08-11T10:00:00",
            "created_by": "admin",
            "file_name": "",
            "result_url": "",
        }

        resp = _task_to_response(data)

        assert resp.fileName is None
        assert resp.resultUrl is None


# ===========================================================================
# GET /tasks/{taskId}/download 端点安全校验测试
# ===========================================================================


def _make_user_mock(username: str = "alice", role: str = "IC_ENGINEER") -> MagicMock:
    """构造 SysUser mock。

    SysUser 角色字段是 ``role``（单数 String），不是 ``role_names``。
    与 app/api/v1/endpoints/tasks.py:510 ``user.role == "ADMIN"`` 对齐。
    """
    user = MagicMock()
    user.username = username
    user.id = "user-id-001"
    user.role = role
    return user


class TestDownloadTaskArtifactEndpoint:
    """测试 GET /tasks/{taskId}/download 端点安全校验。

    通过直接调用 endpoint 函数 + mock 依赖，避免启动 FastClient 全栈。
    """

    @pytest.mark.asyncio
    async def test_task_not_found_raises_404(self) -> None:
        """任务不存在时应抛 BizError 404。"""
        from app.api.v1.endpoints.tasks import download_task_artifact_endpoint
        from app.core.exceptions import BizError

        user = _make_user_mock()

        with patch("app.services.task_tracker.get_task", new=AsyncMock(return_value=None)):
            with pytest.raises(BizError) as exc_info:
                await download_task_artifact_endpoint(task_id="missing", user=user)
            assert exc_info.value.status_code == 404
            assert "ERR_TASK_NOT_FOUND" in exc_info.value.code

    @pytest.mark.asyncio
    async def test_forbidden_for_non_owner_non_admin(self) -> None:
        """非创建者非 ADMIN 用户应抛 BizError 403。"""
        from app.api.v1.endpoints.tasks import download_task_artifact_endpoint
        from app.core.exceptions import BizError

        user = _make_user_mock(username="attacker", role="IC_ENGINEER")
        task_data = {
            "task_id": "task-pdf-001",
            "status": "SUCCESS",
            "created_by": "alice",  # 不是 attacker
            "file_path": "/tmp/x.pdf",
            "file_name": "x.pdf",
        }

        with patch(
            "app.services.task_tracker.get_task",
            new=AsyncMock(return_value=task_data),
        ):
            with pytest.raises(BizError) as exc_info:
                await download_task_artifact_endpoint(task_id="task-pdf-001", user=user)
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_download_others_task(self) -> None:
        """ADMIN 角色可下载其他用户的任务产物。"""
        from fastapi.responses import FileResponse

        from app.api.v1.endpoints.tasks import download_task_artifact_endpoint

        admin = _make_user_mock(username="admin", role="ADMIN")

        with (
            tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile,
            patch(
                "app.services.task_tracker.get_task",
                new=AsyncMock(
                    return_value={
                        "task_id": "task-pdf-002",
                        "status": "SUCCESS",
                        "created_by": "alice",  # 非 admin
                        "file_path": tmpfile.name,
                        "file_name": "report.pdf",
                    }
                ),
            ),
        ):
            try:
                response = await download_task_artifact_endpoint(task_id="task-pdf-002", user=admin)
                assert isinstance(response, FileResponse)
                assert response.media_type == "application/octet-stream"
            finally:
                os.unlink(tmpfile.name)

    @pytest.mark.asyncio
    async def test_running_task_returns_425(self) -> None:
        """PENDING/RUNNING 状态应抛 BizError 425 Too Early。"""
        from app.api.v1.endpoints.tasks import download_task_artifact_endpoint
        from app.core.exceptions import BizError

        owner = _make_user_mock(username="alice")

        for running_status in ("PENDING", "RUNNING"):
            task_data = {
                "task_id": "task-running",
                "status": running_status,
                "created_by": "alice",
                "file_path": "/tmp/x.pdf",
                "file_name": "x.pdf",
            }
            with patch(
                "app.services.task_tracker.get_task",
                new=AsyncMock(return_value=task_data),
            ):
                with pytest.raises(BizError) as exc_info:
                    await download_task_artifact_endpoint(task_id="task-running", user=owner)
                assert exc_info.value.status_code == 425

    @pytest.mark.asyncio
    async def test_missing_file_path_returns_404(self) -> None:
        """file_path 字段为空时应抛 BizError 404。"""
        from app.api.v1.endpoints.tasks import download_task_artifact_endpoint
        from app.core.exceptions import BizError

        owner = _make_user_mock(username="alice")
        task_data = {
            "task_id": "task-no-artifact",
            "status": "SUCCESS",
            "created_by": "alice",
            "file_path": "",  # 空
            "file_name": "",
        }

        with patch(
            "app.services.task_tracker.get_task",
            new=AsyncMock(return_value=task_data),
        ):
            with pytest.raises(BizError) as exc_info:
                await download_task_artifact_endpoint(task_id="task-no-artifact", user=owner)
            assert exc_info.value.status_code == 404
            assert "ERR_FILE_MISSING" in exc_info.value.code

    @pytest.mark.asyncio
    async def test_physical_file_missing_returns_410(self) -> None:
        """file_path 非空但物理文件不存在时应抛 BizError 410 Gone。"""
        from app.api.v1.endpoints.tasks import download_task_artifact_endpoint
        from app.core.exceptions import BizError

        owner = _make_user_mock(username="alice")
        task_data = {
            "task_id": "task-gone",
            "status": "SUCCESS",
            "created_by": "alice",
            "file_path": "/tmp/definitely-not-exists-12345.pdf",
            "file_name": "gone.pdf",
        }

        with patch(
            "app.services.task_tracker.get_task",
            new=AsyncMock(return_value=task_data),
        ):
            with pytest.raises(BizError) as exc_info:
                await download_task_artifact_endpoint(task_id="task-gone", user=owner)
            assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_success_returns_file_response(self) -> None:
        """所有校验通过时应返回 FileResponse。"""
        from fastapi.responses import FileResponse

        from app.api.v1.endpoints.tasks import download_task_artifact_endpoint

        owner = _make_user_mock(username="alice")

        with (
            tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmpfile,
            patch(
                "app.services.task_tracker.get_task",
                new=AsyncMock(
                    return_value={
                        "task_id": "task-ok",
                        "status": "SUCCESS",
                        "created_by": "alice",
                        "file_path": tmpfile.name,
                        "file_name": "CLPM-诊断建议书-LIC-101-2026-08-11.pdf",
                    }
                ),
            ),
        ):
            try:
                response = await download_task_artifact_endpoint(task_id="task-ok", user=owner)
                assert isinstance(response, FileResponse)
                assert response.media_type == "application/octet-stream"
                # Content-Disposition 应包含 UTF-8 filename*（中文文件名兼容）
                cd = response.headers.get("content-disposition", "")
                assert "filename*=UTF-8''" in cd
                assert "CLPM" in cd or "task-ok" in cd
            finally:
                os.unlink(tmpfile.name)
