"""P2：Excel 导入上传上限测试（upload_guard + 5 处导入端点 + loop.py 流式化）。

背景：各导入端点 ``await file.read()`` 全量进内存、服务层
``list(ws.iter_rows())`` 全量物化，无文件大小/行数上限，超大 xlsx 可打爆
API 进程内存。修复后由 ``app.api.upload_guard.read_excel_upload`` 统一拦截：
- 文件大小上限 10MB（分块读取，超限立即 422，不全量进内存）
- 数据行数上限 5000 行（read_only 流式统计，不转 list）
- loop.import_loops 改为流式逐行处理（不转 list 物化）

覆盖：
- read_excel_upload 单元测试：正常文件 / 超 10MB / 超 5000 行 / 非 xlsx 放行
- 5 处导入端点：超大文件 422 且业务服务未被调用
- POST /loops/import：3000 行 Excel 正常导入、5001 行被 422
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import openpyxl
import pytest
from fastapi import UploadFile

from app.api.upload_guard import MAX_IMPORT_ROWS, MAX_UPLOAD_BYTES, read_excel_upload
from app.core.exceptions import BizError
from tests.conftest import TEST_USERS, mock_current_user

_AUTH = {"Authorization": "Bearer fake-token"}
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _make_xlsx(row_count: int) -> bytes:
    """构造内存 .xlsx：第 1 行表头 + ``row_count`` 行数据。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["回路编号", "描述", "SP", "PV", "OP", "MODE"])
    for i in range(row_count):
        ws.append([f"LOOP-{i:05d}", f"回路{i}", "", "", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload_file(data: bytes, filename: str = "import.xlsx") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


# ===========================================================================
# read_excel_upload 单元测试
# ===========================================================================


class TestReadExcelUpload:
    """upload_guard.read_excel_upload 大小/行数上限。"""

    async def test_small_file_ok(self) -> None:
        data = _make_xlsx(3)
        result = await read_excel_upload(_upload_file(data))
        assert result == data

    async def test_oversized_file_rejected_422(self) -> None:
        # 10MB + 1 字节：分块读取累计超限即拒绝（内容无需是合法 xlsx）
        data = b"0" * (MAX_UPLOAD_BYTES + 1)
        with pytest.raises(BizError) as exc_info:
            await read_excel_upload(_upload_file(data))
        assert exc_info.value.code == "ERR_FILE_TOO_LARGE"
        assert exc_info.value.status_code == 422
        assert "10MB" in exc_info.value.message

    async def test_exactly_max_bytes_ok(self) -> None:
        # 边界：恰好 10MB 的非 xlsx 内容（行数校验对解析失败放行）
        data = b"0" * MAX_UPLOAD_BYTES
        result = await read_excel_upload(_upload_file(data))
        assert result == data

    async def test_too_many_rows_rejected_422(self) -> None:
        data = _make_xlsx(MAX_IMPORT_ROWS + 1)
        with pytest.raises(BizError) as exc_info:
            await read_excel_upload(_upload_file(data))
        assert exc_info.value.code == "ERR_TOO_MANY_ROWS"
        assert exc_info.value.status_code == 422
        assert str(MAX_IMPORT_ROWS) in exc_info.value.message

    async def test_exactly_max_rows_ok(self) -> None:
        data = _make_xlsx(MAX_IMPORT_ROWS)
        result = await read_excel_upload(_upload_file(data))
        assert result == data

    async def test_non_xlsx_small_file_passes_guard(self) -> None:
        """解析失败不在 guard 层报错，交由业务层统一返回 ERR_FILE_PARSE。"""
        data = b"not-an-xlsx"
        result = await read_excel_upload(_upload_file(data))
        assert result == data


# ===========================================================================
# 5 处导入端点：超大文件 422 且业务服务未被调用
# ===========================================================================

# (URL, 业务服务 patch 目标)
_IMPORT_ENDPOINTS = [
    ("/api/v1/loops/import", "app.api.v1.endpoints.loops.import_loops"),
    ("/api/v1/tags/import", "app.api.v1.endpoints.tags.import_tags"),
    ("/api/v1/plant-nodes/import", "app.api.v1.endpoints.plant_nodes.import_plant_nodes"),
    ("/api/v1/dcs/vendors/import", "app.api.v1.endpoints.dcs.svc_import_vendors"),
    ("/api/v1/dcs/models/import", "app.api.v1.endpoints.dcs.svc_import_models"),
]


class TestImportEndpointsSizeLimit:
    """10MB+ 文件统一被 422，业务服务层不被触达。"""

    @pytest.mark.parametrize(("url", "svc_target"), _IMPORT_ENDPOINTS)
    def test_oversized_file_rejected(
        self, client, mock_db, fake_redis, url: str, svc_target: str
    ) -> None:
        svc_mock = AsyncMock()
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(svc_target, new=svc_mock),
        ):
            resp = client.post(
                url,
                headers=_AUTH,
                files={"file": ("big.xlsx", b"0" * (MAX_UPLOAD_BYTES + 1), _XLSX_MEDIA)},
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_FILE_TOO_LARGE"
        svc_mock.assert_not_called()


# ===========================================================================
# POST /loops/import：行数上限 + 3000 行正常导入
# ===========================================================================


class TestLoopImportEndpoint:
    """loops/import 端点行数上限与正常导入路径。"""

    def test_too_many_rows_rejected(self, client, mock_db, fake_redis) -> None:
        svc_mock = AsyncMock()
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.api.v1.endpoints.loops.import_loops", new=svc_mock),
        ):
            resp = client.post(
                "/api/v1/loops/import",
                headers=_AUTH,
                files={"file": ("rows.xlsx", _make_xlsx(MAX_IMPORT_ROWS + 1), _XLSX_MEDIA)},
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_TOO_MANY_ROWS"
        svc_mock.assert_not_called()

    def test_3000_rows_import_ok(self, client, mock_db, fake_redis) -> None:
        """3000 行 Excel 正常导入（上限内，业务服务被调用且返回 200）。"""
        svc_mock = AsyncMock(
            return_value={
                "total": 3000,
                "inserted": 3000,
                "updated": 0,
                "failed": 0,
                "errors": [],
                "warnings": [],
            }
        )
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.api.v1.endpoints.loops.import_loops", new=svc_mock),
        ):
            resp = client.post(
                "/api/v1/loops/import",
                headers=_AUTH,
                files={"file": ("loops.xlsx", _make_xlsx(3000), _XLSX_MEDIA)},
            )
        assert resp.status_code == 200, f"3000 行导入失败: {resp.json()}"
        svc_mock.assert_awaited_once()
        # 文件字节完整传递给服务层
        assert svc_mock.await_args.kwargs["file_bytes"]
