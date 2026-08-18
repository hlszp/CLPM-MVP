"""POST /tuning/tune/matrix 全算法矩阵端点测试（09 设计方案 §4.2）。

覆盖：
- 5 算法一次全算（真实 tune_pid 纯计算路径）
- 单算法失败不阻断（mock 第 2 行抛 BizError，其余行正常）
- 角色权限（SPONSOR 只读 → 403）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.core.exceptions import BizError
from tests.conftest import TEST_USERS, mock_current_user

_MATRIX_URL = "/api/v1/tuning/tune/matrix"
_MATRIX_BODY = {
    "modelType": "FOPDT",
    "modelParams": {"K": 1.0, "tau": 30.0, "theta": 5.0},
    "modelSource": "MANUAL",
    "riskConfirmed": True,
}
_AUTH = {"Authorization": "Bearer fake-token"}


class TestTuneMatrixAPI:
    """全算法矩阵端点。"""

    def test_matrix_returns_five_rows(self, client) -> None:
        """5 算法各出 1 组推荐 PID，全部 ok=True。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(_MATRIX_URL, headers=_AUTH, json=_MATRIX_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "0"
        rows = data["data"]["rows"]
        assert [r["algorithm"] for r in rows] == [
            "IMC",
            "LAMBDA",
            "ZN",
            "COHEN_COON",
            "SIMC",
        ]
        for row in rows:
            assert row["ok"] is True, f"{row['algorithm']} 行计算失败: {row.get('error')}"
            pid = row["result"]["recommendedPid"]
            assert pid["kp"] > 0
            assert "ti" in pid
            assert "td" in pid

    def test_matrix_row_failure_not_blocking(self, client) -> None:
        """单行失败不阻断：第 2 行 ok=False 带错误信息，其余行不受影响。"""
        ok_result = {
            "algorithm": "IMC",
            "recommendedPid": {"kp": 1.0, "ti": 30.0, "td": 0.0},
        }
        side_effects = [
            ok_result,
            BizError(code="ERR_MODEL_PARAMS_MISSING", message="LAMBDA 缺参", status_code=400),
            ok_result,
            ok_result,
            ok_result,
        ]
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning.tune_pid",
                new=AsyncMock(side_effect=side_effects),
            ),
        ):
            resp = client.post(_MATRIX_URL, headers=_AUTH, json=_MATRIX_BODY)
        assert resp.status_code == 200
        rows = resp.json()["data"]["rows"]
        assert len(rows) == 5
        assert rows[0]["ok"] is True
        assert rows[1]["ok"] is False
        assert "LAMBDA" in rows[1]["error"]
        assert all(r["ok"] is True for r in rows[2:])

    def test_matrix_permission_sponsor_forbidden(self, client) -> None:
        """SPONSOR 只读角色 → 403。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(_MATRIX_URL, headers=_AUTH, json=_MATRIX_BODY)
        assert resp.status_code == 403
