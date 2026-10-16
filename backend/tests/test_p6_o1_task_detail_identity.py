"""O-1（P6 四批/五批验收挂账）：整定记录身份/冻结上下文字段随响应下发.

缺口：`GET /tuning/tasks/{task_id}` 的 `response_model=ApiResponse[TuningTaskDetail]`
与 `GET /tuning/history` 的 `ApiResponse[TuningHistoryStats]`
（`recentTasks: list[TuningTaskItem]`）未声明服务层 `_record_to_dict`
已产出的身份/冻结上下文字段——被 response_model 过滤恒不下发。

修复口径（schema 声明补齐，服务层产出逻辑不变）：
- 四批 O-1：`TuningTaskDetail` 补四身份字段（sourceRecordId/
  processModelVersionId/modelSource/dcsTemplateRevision）；
- 五批同链补齐：六字段上移至 `TuningTaskItem`（history recentTasks 与
  detail 同源下发，detail 继承）并补 unitBasis/currentPidContext。

本测试在 service+schema 层复现 response_model 的精确过滤路径
（`_record_to_dict` → schema `model_validate` → `model_dump`）：
- detail：六字段实值下发 / 旧记录 None 直出（四批用例保持）；
- history recentTasks（TuningTaskItem）：六字段实值下发 / None 直出；
- detail unitBasis/currentPidContext：实值下发 / None 直出。
"""

from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.tuning import TuningRecord
from app.schemas.tuning import TuningTaskDetail, TuningTaskItem
from app.services.tuning import _record_to_dict


def _make_record(**overrides) -> MagicMock:
    """构造 spec=TuningRecord 的记录（新协议保存链冻结身份形态）。"""
    record = MagicMock(spec=TuningRecord)
    record.id = str(uuid4())
    record.loop_id = str(uuid4())
    record.model_type = "FOPDT"
    record.model_params = {"K": 1.2, "tau": 30.0, "theta": 5.0}
    record.algorithm = "IMC"
    record.recommended_pid = {"kp": 1.5, "ti": 20.0, "td": 0.0}
    record.simulation_result = {
        "timestamps": [0.0, 1.0],
        "candidateResponses": [{"label": "IMC", "response": {}, "metrics": {}}],
    }
    record.fitting_score = 92.5
    record.status = "SIMULATED"
    record.created_by = "admin"
    record.created_at = datetime(2026, 10, 13, 10, 0, 0)
    record.identify_method = None
    record.data_source = None
    record.confidence_level = None
    record.confidence_reason = None
    record.excitation_score = None
    record.residual_test_passed = None
    record.task_id = None
    record.completed_at = None
    record.process_model_version_id = "mv-0001"
    record.current_pid = {"kp": 2.0, "ti": 45.0, "td": 0.0}
    record.risk_assessment = {"riskLevel": "LOW", "factors": []}
    record.rollback_pid = {"kp": 2.0, "ti": 45.0, "td": 0.0}
    record.time_window_start = None
    record.time_window_end = None
    record.source_record_id = "src-rec-0001"
    record.dcs_template_revision = "rev-3"
    record.calc_context = {
        "schemaVersion": "1",
        "kind": "TUNING_PROPOSAL",
        "modelSource": "IDENTIFICATION_RECORD",
        "sourceRecordId": "src-rec-0001",
        "modelVersionId": "mv-0001",
        "dcsTemplateRevision": "rev-3",
        "unitBasis": {
            "processKUnit": "%",
            "kUnitBasis": "RAW_RANGE",
            "note": "K 单位域按量程原始口径冻结",
        },
        "currentPid": {
            "standard": {"kp": 2.0, "ti": 45.0, "td": 0.0},
            "raw": {"p": 20.0, "i": 0.02, "d": 0.0},
            "conversion": {"pType": "GAIN", "iUnit": "SECONDS"},
            "status": "ACTUAL",
            "reason": None,
        },
    }
    record.pid_candidates = None
    record.candidate_results = None
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


class TestTaskDetailIdentityFieldsDelivered:
    """四身份字段经 TuningTaskDetail response_model 下发（O-1 修复点）。"""

    def test_frozen_identity_fields_survive_response_model(self) -> None:
        """有冻结身份实值：四字段经 response_model 序列化逐值下发。"""
        record = _make_record()
        data = _record_to_dict(record, tag_name="FIC-101", include_detail=True)
        # response_model 精确路径：校验 + 序列化（此前四字段在此被过滤）
        dumped = TuningTaskDetail.model_validate(data).model_dump()

        assert dumped["sourceRecordId"] == "src-rec-0001"
        assert dumped["processModelVersionId"] == "mv-0001"
        assert dumped["modelSource"] == "IDENTIFICATION_RECORD"
        assert dumped["dcsTemplateRevision"] == "rev-3"

    def test_legacy_record_identity_fields_none_passthrough(self) -> None:
        """旧记录（无冻结身份）：四字段 None 直出（显式未知不伪补）。"""
        record = _make_record(
            source_record_id=None,
            process_model_version_id=None,
            dcs_template_revision=None,
            calc_context=None,
        )
        data = _record_to_dict(record, tag_name="FIC-101", include_detail=True)
        dumped = TuningTaskDetail.model_validate(data).model_dump()

        # 键在场值为 None（FastAPI response_model 序列化为 null）——
        # 前端按 null 显示「—」，不因键存在制造身份
        assert "sourceRecordId" in dumped
        assert dumped["sourceRecordId"] is None
        assert dumped["processModelVersionId"] is None
        assert dumped["modelSource"] is None
        assert dumped["dcsTemplateRevision"] is None

    def test_service_layer_output_unchanged(self) -> None:
        """回归守护：服务层 _record_to_dict 产出逻辑未变（仅补 schema 声明）——
        四字段在 schema 校验前的服务层 dict 中照常产出。"""
        record = _make_record()
        data = _record_to_dict(record, tag_name="FIC-101", include_detail=True)
        assert data["sourceRecordId"] == "src-rec-0001"
        assert data["processModelVersionId"] == "mv-0001"
        assert data["modelSource"] == "IDENTIFICATION_RECORD"
        assert data["dcsTemplateRevision"] == "rev-3"


#: O-1 同链补齐的六字段（Item 层声明，history recentTasks/detail 同源下发）
_SIX_FIELDS = (
    "sourceRecordId",
    "processModelVersionId",
    "modelSource",
    "dcsTemplateRevision",
    "unitBasis",
    "currentPidContext",
)


class TestHistoryRecentTasksIdentityFields:
    """history recentTasks（list[TuningTaskItem]）六字段下发（五批同链补齐）。"""

    def test_recent_tasks_six_fields_with_values(self) -> None:
        """有冻结身份实值：六字段经 TuningTaskItem response_model 逐值下发。"""
        record = _make_record()
        data = _record_to_dict(record, tag_name="FIC-101")
        dumped = TuningTaskItem.model_validate(data).model_dump()

        assert dumped["sourceRecordId"] == "src-rec-0001"
        assert dumped["processModelVersionId"] == "mv-0001"
        assert dumped["modelSource"] == "IDENTIFICATION_RECORD"
        assert dumped["dcsTemplateRevision"] == "rev-3"
        assert dumped["unitBasis"]["kUnitBasis"] == "RAW_RANGE"
        assert dumped["currentPidContext"]["standard"] == {
            "kp": 2.0,
            "ti": 45.0,
            "td": 0.0,
        }
        assert dumped["currentPidContext"]["status"] == "ACTUAL"

    def test_recent_tasks_legacy_record_six_fields_none(self) -> None:
        """旧记录（无冻结身份）：六字段 None 直出（显式未知不伪补）。"""
        record = _make_record(
            source_record_id=None,
            process_model_version_id=None,
            dcs_template_revision=None,
            calc_context=None,
        )
        data = _record_to_dict(record, tag_name="FIC-101")
        dumped = TuningTaskItem.model_validate(data).model_dump()

        for field in _SIX_FIELDS:
            assert field in dumped
            assert dumped[field] is None


class TestTaskDetailFrozenContextFields:
    """detail 的 unitBasis/currentPidContext 下发（五批同链补齐，继承 Item 声明）。"""

    def test_detail_frozen_context_with_values(self) -> None:
        """有冻结上下文实值：两字段经 TuningTaskDetail response_model 下发。"""
        record = _make_record()
        data = _record_to_dict(record, tag_name="FIC-101", include_detail=True)
        dumped = TuningTaskDetail.model_validate(data).model_dump()

        assert dumped["unitBasis"]["processKUnit"] == "%"
        assert dumped["unitBasis"]["kUnitBasis"] == "RAW_RANGE"
        assert dumped["currentPidContext"]["standard"] == {
            "kp": 2.0,
            "ti": 45.0,
            "td": 0.0,
        }
        assert dumped["currentPidContext"]["raw"] == {
            "p": 20.0,
            "i": 0.02,
            "d": 0.0,
        }
        assert dumped["currentPidContext"]["conversion"] == {
            "pType": "GAIN",
            "iUnit": "SECONDS",
        }

    def test_detail_frozen_context_none_passthrough(self) -> None:
        """旧记录（calc_context=None）：两字段 None 直出（不伪补三元组）。"""
        record = _make_record(calc_context=None)
        data = _record_to_dict(record, tag_name="FIC-101", include_detail=True)
        dumped = TuningTaskDetail.model_validate(data).model_dump()

        assert "unitBasis" in dumped
        assert dumped["unitBasis"] is None
        assert "currentPidContext" in dumped
        assert dumped["currentPidContext"] is None
