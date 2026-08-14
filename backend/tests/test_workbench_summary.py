"""工作台摘要 workbench_summary.py 单元测试（整改方案 §8.2 / §7.2 / §7.3）。

测试覆盖：
- 生命周期五阶段状态构建（MW-P3-02）：NOT_STARTED/READY/RUNNING/COMPLETED/
  INCONCLUSIVE/BLOCKED/OVERDUE/NOT_REQUIRED 八态
- 推荐下一步 nextAction（MW-P3-03）：方案 §7.3 八条优先级规则 + 角色过滤
- 数据新鲜度 dataFreshness：FRESH/DELAYED/UNKNOWN + 阈值复用
- 配置完整性判定：缺 Tag / 已停用
- nextAction 角色过滤：PE 写动作 disabled、EXPERT 仅整定可写、SPONSOR 不走此路径
- 主入口 get_workbench_summary 部分失败 partial 容错（mock 各来源异常）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.workbench_summary import (
    _build_data_freshness,
    _build_effect_compare,
    _build_lifecycle,
    _build_next_action,
    _check_config_completeness,
    get_workbench_summary,
)

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_loop(**overrides) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = overrides.get("id", "00000000-0000-0000-0000-000000000001")
    loop.tag_name = overrides.get("tag_name", "LIC-101")
    loop.description = overrides.get("description", "测试回路")
    loop.unit_id = overrides.get("unit_id", None)
    loop.loop_type = overrides.get("loop_type", "LEVEL")
    loop.control_type = overrides.get("control_type", "STABLE")
    loop.status = overrides.get("status", "READY")
    loop.is_active = overrides.get("is_active", True)
    loop.importance_level = overrides.get("importance_level", 2)
    return loop


def _make_mapping(role: str) -> MagicMock:
    """构造 LoopTagMapping mock。"""
    m = MagicMock()
    m.tag_role = role
    m.tag_id = f"tag-{role}"
    return m


def _full_mappings() -> dict[str, MagicMock]:
    """7 角色齐全的 mappings。"""
    return {
        role: _make_mapping(role) for role in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")
    }


def _runtime(read_at: str | None = "2026-08-09T10:00:00Z") -> dict:
    return {
        "pv": 50.0,
        "sp": 50.0,
        "op": 50.0,
        "mode": 1,
        "modeLabel": "Auto",
        "pvQuality": "GOOD",
        "pvUnit": "%",
        "pvRange": {"min": 0, "max": 100},
        "opRange": {"min": 0, "max": 100},
        "readAt": read_at,
        "controlMode": "Auto",
    }


def _data_freshness(status: str = "FRESH") -> dict:
    return {"status": status, "thresholdSeconds": 300, "reason": None}


def _data_health() -> dict:
    return {
        "validRate": 0.95,
        "confidenceLevel": "A",
        "pvCompleteness": 0.98,
        "overallCompleteness": 0.97,
        "integrityStatus": "OK",
    }


def _assessment(score: float = 80.0, day_trend: str = "FLAT", score_delta: float = 0.0) -> dict:
    return {
        "score": score,
        "confidenceLevel": "A",
        "status": "SUCCESS",
        "resultAt": "2026-08-09T07:00:00Z",
        "timeWindow": "latest_hourly",
        "summary": f"综合评分 {score:.1f}",
        "_scoreDelta": score_delta,
        "_dayTrend": day_trend,
    }


def _diagnosis(label: str = "OSCILLATION", status: str = "SUCCESS") -> dict:
    return {
        "diagLabel": label,
        "confidence": 85.0,
        "status": status,
        "resultAt": "2026-08-09T08:00:00Z",
        "taskId": "task-001",
        "labels": [label],
        "summary": f"诊断标签：{label}",
    }


def _tuning(status: str = "COMPLETED") -> dict:
    return {
        "status": status,
        "modelType": "FOPDT",
        "algorithm": "IMC",
        "confidenceLevel": "B",
        "resultAt": "2026-08-09T09:00:00Z",
        "currentPid": {"p": 1.0, "i": 0.1, "d": 0.0},
        "recommendedPid": {"p": 1.5, "i": 0.15, "d": 0.0},
        "fittingScore": 90.0,
        "riskLevel": "LOW",
        "summary": f"整定状态：{status}",
    }


def _tracker(action_status: str = "CLOSED", is_overdue: bool = False) -> dict:
    return {
        "trackerId": "tracker-001",
        "diagnosisLabel": "OSCILLATION",
        "actionStatus": action_status,
        "severity": "WARN",
        "triggerType": "auto",
        "assignee": "ic_engineer",
        "createdAt": "2026-08-08T10:00:00Z",
        "updatedAt": "2026-08-09T06:00:00Z",
        "implementedAt": "2026-08-09T05:00:00Z",
        "implementedBy": "ic_engineer",
        "newPid": {"p": 1.5, "i": 0.15, "d": 0.0},
        "mocRef": "MOC-001",
        "mocNotApplicable": False,
        "plannedAt": None,
        "closedAt": "2026-08-09T06:00:00Z" if action_status == "CLOSED" else None,
        "effectVerified": True if action_status == "CLOSED" else None,
        "effectVerifiedAt": "2026-08-09T06:00:00Z" if action_status == "CLOSED" else None,
        "abCompareSummary": None,
        "reopenReason": None,
        "isOverdue": is_overdue,
        "overdueHours": 9.0 if is_overdue else None,
    }


# ===========================================================================
# MW-P3-02: 生命周期构建器测试
# ===========================================================================


class TestLifecycleBuilder:
    """五阶段生命周期状态构建（方案 §7.2）。"""

    def _build(self, **overrides) -> dict:
        """构造生命周期。"""
        return _build_lifecycle(
            loop=_make_loop(),
            config_complete=overrides.get("config_complete", True),
            config_reason=overrides.get("config_reason", None),
            runtime=overrides.get("runtime", _runtime()),
            data_freshness=overrides.get("data_freshness", _data_freshness()),
            data_health=overrides.get("data_health", _data_health()),
            assessment=overrides.get("assessment", _assessment()),
            diagnosis=overrides.get("diagnosis", _diagnosis()),
            tuning=overrides.get("tuning", _tuning()),
            tracker=overrides.get("tracker", _tracker()),
        )

    def test_五阶段全部返回(self):
        lc = self._build()
        assert len(lc["stages"]) == 5
        stages = [s["stage"] for s in lc["stages"]]
        assert stages == ["MONITOR", "ASSESS", "DIAGNOSE", "TUNE", "VERIFY"]

    def test_MONITOR配置完整且运行态正常为READY(self):
        lc = self._build(config_complete=True, runtime=_runtime("2026-08-09T10:00:00Z"))
        assert lc["stages"][0]["status"] == "READY"

    def test_MONITOR配置不完整为BLOCKED(self):
        lc = self._build(config_complete=False, config_reason="缺失必填 Tag：PV")
        assert lc["stages"][0]["status"] == "BLOCKED"
        assert "PV" in lc["stages"][0]["reason"]

    def test_MONITOR无运行态数据为NOT_STARTED(self):
        lc = self._build(runtime=_runtime(read_at=None))
        assert lc["stages"][0]["status"] == "NOT_STARTED"

    def test_MONITOR数据停滞为OVERDUE(self):
        lc = self._build(data_freshness=_data_freshness("DELAYED"))
        assert lc["stages"][0]["status"] == "OVERDUE"

    def test_ASSESS无快照为NOT_STARTED(self):
        lc = self._build(assessment=None)
        assert lc["stages"][1]["status"] == "NOT_STARTED"

    def test_ASSESS_INCONCLUSIVE状态为INCONCLUSIVE(self):
        lc = self._build(assessment=_assessment(day_trend="FLAT"))
        a = _assessment()
        a["status"] = "INCONCLUSIVE"
        lc = self._build(assessment=a)
        assert lc["stages"][1]["status"] == "INCONCLUSIVE"

    def test_ASSESS_SUCCESS状态为COMPLETED(self):
        lc = self._build(assessment=_assessment())
        assert lc["stages"][1]["status"] == "COMPLETED"

    def test_DIAGNOSE无诊断为NOT_STARTED(self):
        lc = self._build(diagnosis=None)
        assert lc["stages"][2]["status"] == "NOT_STARTED"

    def test_DIAGNOSE早于评估为NOT_STARTED需重新诊断(self):
        # 诊断时间早于评估时间 → 同轴不满足
        diag = _diagnosis()
        diag["resultAt"] = "2026-08-09T06:00:00Z"  # 早于评估 07:00
        lc = self._build(diagnosis=diag, assessment=_assessment())
        assert lc["stages"][2]["status"] == "NOT_STARTED"
        assert "重新诊断" in lc["stages"][2]["reason"]

    def test_DIAGNOSE同轴完成为COMPLETED(self):
        # 诊断时间不早于评估 → COMPLETED（默认 diagnosis 08:00 >= assessment 07:00）
        lc = self._build(diagnosis=_diagnosis(), assessment=_assessment())
        assert lc["stages"][2]["status"] == "COMPLETED"

    def test_DIAGNOSE_RUNNING状态为RUNNING(self):
        diag = _diagnosis(status="RUNNING")
        lc = self._build(diagnosis=diag)
        assert lc["stages"][2]["status"] == "RUNNING"

    def test_DIAGNOSE_FAILED状态为BLOCKED(self):
        diag = _diagnosis(status="FAILED")
        lc = self._build(diagnosis=diag)
        assert lc["stages"][2]["status"] == "BLOCKED"

    def test_TUNE无整定记录为NOT_REQUIRED(self):
        lc = self._build(tuning=None)
        assert lc["stages"][3]["status"] == "NOT_REQUIRED"

    def test_TUNE_IDENTIFIED为RUNNING已辨识(self):
        lc = self._build(tuning=_tuning("IDENTIFIED"))
        assert lc["stages"][3]["status"] == "RUNNING"

    def test_TUNE_SIMULATED为COMPLETED(self):
        lc = self._build(tuning=_tuning("SIMULATED"))
        assert lc["stages"][3]["status"] == "COMPLETED"

    def test_TUNE_COMPLETED为COMPLETED(self):
        lc = self._build(tuning=_tuning("COMPLETED"))
        assert lc["stages"][3]["status"] == "COMPLETED"

    def test_TUNE_INCONCLUSIVE为INCONCLUSIVE(self):
        lc = self._build(tuning=_tuning("INCONCLUSIVE"))
        assert lc["stages"][3]["status"] == "INCONCLUSIVE"

    def test_TUNE_ROLLED_BACK为BLOCKED(self):
        lc = self._build(tuning=_tuning("ROLLED_BACK"))
        assert lc["stages"][3]["status"] == "BLOCKED"

    def test_VERIFY无Tracker为NOT_REQUIRED(self):
        lc = self._build(tracker=None)
        assert lc["stages"][4]["status"] == "NOT_REQUIRED"

    def test_VERIFY_CLOSED为COMPLETED(self):
        lc = self._build(tracker=_tracker("CLOSED"))
        assert lc["stages"][4]["status"] == "COMPLETED"

    def test_VERIFY_REOPENED为BLOCKED(self):
        t = _tracker("REOPENED")
        t["reopenReason"] = "验证失败"
        lc = self._build(tracker=t)
        assert lc["stages"][4]["status"] == "BLOCKED"

    def test_VERIFY_VERIFYING未超期为RUNNING(self):
        lc = self._build(tracker=_tracker("VERIFYING", is_overdue=False))
        assert lc["stages"][4]["status"] == "RUNNING"

    def test_VERIFY_VERIFYING超期为OVERDUE(self):
        lc = self._build(tracker=_tracker("VERIFYING", is_overdue=True))
        assert lc["stages"][4]["status"] == "OVERDUE"
        assert "超期" in lc["stages"][4]["reason"]

    def test_VERIFY_PENDING为NOT_STARTED尚未实施(self):
        lc = self._build(tracker=_tracker("PENDING"))
        assert lc["stages"][4]["status"] == "NOT_STARTED"

    def test_currentStage指向第一个未完成阶段(self):
        lc = self._build(
            assessment=None,  # ASSESS=NOT_STARTED
            diagnosis=None,
            tuning=None,
            tracker=None,
        )
        # MONITOR=READY, ASSESS=NOT_STARTED → currentStage=ASSESS
        assert lc["currentStage"] == "ASSESS"


# ===========================================================================
# MW-P3-03: 推荐下一步测试
# ===========================================================================


class TestNextAction:
    """推荐下一步规则（方案 §7.3 八条优先级 + 角色过滤）。"""

    def _build(self, **overrides) -> dict:
        lc = overrides.get("lifecycle") or _build_lifecycle(
            loop=_make_loop(),
            config_complete=overrides.get("config_complete", True),
            config_reason=overrides.get("config_reason", None),
            runtime=overrides.get("runtime", _runtime()),
            data_freshness=overrides.get("data_freshness", _data_freshness()),
            data_health=overrides.get("data_health", _data_health()),
            assessment=overrides.get("assessment", _assessment()),
            diagnosis=overrides.get("diagnosis", _diagnosis()),
            tuning=overrides.get("tuning", _tuning()),
            tracker=overrides.get("tracker", _tracker()),
        )
        return _build_next_action(
            loop_id="00000000-0000-0000-0000-000000000001",
            role=overrides.get("role", "ADMIN"),
            config_complete=overrides.get("config_complete", True),
            config_reason=overrides.get("config_reason", None),
            has_runtime=overrides.get("has_runtime", True),
            assessment=overrides.get("assessment", _assessment()),
            diagnosis=overrides.get("diagnosis", _diagnosis()),
            tuning=overrides.get("tuning", _tuning()),
            tracker=overrides.get("tracker", _tracker()),
            active_attention=overrides.get("active_attention", {"total": 0}),
            lifecycle=lc,
        )

    def test_规则1_配置不完整返回修复Tag(self):
        action = self._build(config_complete=False, config_reason="缺失必填 Tag：PV")
        assert action["actionType"] == "FIX_TAG_CONFIG"
        assert "PV" in action["reason"]

    def test_规则1_无运行态数据返回导入数据(self):
        action = self._build(has_runtime=False)
        assert action["actionType"] == "IMPORT_DATA"

    def test_规则2_评估缺失返回发起评估(self):
        action = self._build(assessment=None)
        assert action["actionType"] == "RUN_ASSESSMENT"
        assert action["enabled"] is True

    def test_规则2_评分恶化返回重新评估(self):
        a = _assessment(score=60.0, day_trend="WORSENED", score_delta=-5.0)
        action = self._build(assessment=a)
        assert action["actionType"] == "RUN_ASSESSMENT"
        assert "下降" in action["reason"]

    def test_规则3_无诊断返回发起诊断(self):
        action = self._build(diagnosis=None)
        assert action["actionType"] == "RUN_DIAGNOSIS"

    def test_规则4_非可整定标签且无Tracker返回创建工单(self):
        # 非可整定标签（外扰频繁）+ 无任何 tracker → 创建工单
        action = self._build(
            diagnosis=_diagnosis("EXTERNAL_DISTURBANCE"),
            tracker=None,
        )
        assert action["actionType"] == "CREATE_TRACKER"

    def test_规则5_可整定标签返回回路整定(self):
        # 整定记录为 INCONCLUSIVE + 振荡标签
        action = self._build(
            tuning=_tuning("INCONCLUSIVE"),
            diagnosis=_diagnosis("OSCILLATION"),
            tracker=_tracker("CLOSED"),  # 已闭环，不触发规则4/6
        )
        assert action["actionType"] == "RUN_TUNING"

    def test_规则7_VERIFYING未超期返回进入验证(self):
        action = self._build(tracker=_tracker("VERIFYING", is_overdue=False))
        assert action["actionType"] == "VERIFY_EFFECT"
        assert action["enabled"] is True

    def test_规则7_VERIFYING超期返回立即验证(self):
        action = self._build(tracker=_tracker("VERIFYING", is_overdue=True))
        assert action["actionType"] == "VERIFY_EFFECT"
        assert "超期" in action["reason"]

    def test_规则8_无开放问题返回持续监控(self):
        # 完整闭环 + 无恶化
        action = self._build(
            assessment=_assessment(),
            diagnosis=_diagnosis(),
            tuning=_tuning("COMPLETED"),
            tracker=_tracker("CLOSED"),
        )
        assert action["actionType"] == "CONTINUE_MONITORING"
        assert action["target"] is None

    def test_角色过滤_PE写动作disabled(self):
        # 评估缺失 → RUN_ASSESSMENT，PE 应禁用
        action = self._build(assessment=None, role="PE_ENGINEER")
        assert action["actionType"] == "RUN_ASSESSMENT"
        assert action["enabled"] is False
        assert action["disabledReason"] is not None

    def test_角色过滤_EXPERT整定可写(self):
        # 可整定场景 → RUN_TUNING，EXPERT 应可写
        action = self._build(
            tuning=_tuning("INCONCLUSIVE"),
            diagnosis=_diagnosis("OSCILLATION"),
            tracker=_tracker("CLOSED"),
            role="EXPERT",
        )
        assert action["actionType"] == "RUN_TUNING"
        assert action["enabled"] is True

    def test_角色过滤_EXPERT诊断不可写(self):
        # 无诊断 → RUN_DIAGNOSIS，EXPERT 应禁用
        action = self._build(diagnosis=None, role="EXPERT")
        assert action["actionType"] == "RUN_DIAGNOSIS"
        assert action["enabled"] is False

    def test_角色过滤_ADMIN所有写动作可用(self):
        action = self._build(assessment=None, role="ADMIN")
        assert action["enabled"] is True

    def test_nextAction必含label和reason(self):
        action = self._build()
        assert "label" in action
        assert "reason" in action
        assert action["label"]


# ===========================================================================
# 数据新鲜度测试
# ===========================================================================


class TestDataFreshness:
    """数据新鲜度判定。"""

    def test_无readAt返回UNKNOWN(self):
        f = _build_data_freshness(None)
        assert f["status"] == "UNKNOWN"
        assert f["thresholdSeconds"] > 0

    def test_近期readAt返回FRESH(self):
        recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        f = _build_data_freshness(recent)
        assert f["status"] == "FRESH"

    def test_超阈值返回DELAYED(self):
        old = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
        f = _build_data_freshness(old)
        assert f["status"] == "DELAYED"
        assert "停滞" in (f["reason"] or "")

    def test_阈值复用实时链路配置(self):
        f = _build_data_freshness(None)
        # 阈值应来自 settings.SIGNALR_STALL_TIMEOUT_SECONDS（默认 300）
        assert f["thresholdSeconds"] == 300


# ===========================================================================
# 配置完整性测试
# ===========================================================================


class TestConfigCompleteness:
    """回路配置完整性判定。"""

    def test_七Tag齐全且活跃为完整(self):
        loop = _make_loop(is_active=True)
        complete, reason = _check_config_completeness(_full_mappings(), loop)
        assert complete is True
        assert reason is None

    def test_缺失Tag为不完整(self):
        loop = _make_loop()
        mappings = _full_mappings()
        del mappings["PV"]
        complete, reason = _check_config_completeness(mappings, loop)
        assert complete is False
        assert "PV" in reason

    def test_已停用为不完整(self):
        loop = _make_loop(is_active=False)
        complete, reason = _check_config_completeness(_full_mappings(), loop)
        assert complete is False
        assert "停用" in reason


# ===========================================================================
# MW-P3-04: 主入口 get_workbench_summary 测试（含 partial 容错）
# ===========================================================================


class TestGetWorkbenchSummary:
    """工作台摘要主入口——含部分失败容错。"""

    @pytest.mark.asyncio
    async def test_回路不存在抛404(self) -> None:
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)
        from app.core.exceptions import BizError

        with pytest.raises(BizError) as exc_info:
            await get_workbench_summary(db=db, loop_id="00000000-0000-0000-0000-000000000099")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_非法UUID抛400(self) -> None:
        db = AsyncMock()
        from app.core.exceptions import BizError

        with pytest.raises(BizError) as exc_info:
            await get_workbench_summary(db=db, loop_id="not-a-uuid")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_正常返回含全部顶层字段(self) -> None:
        """mock 全部依赖，验证响应结构完整。"""
        db = AsyncMock()
        loop = _make_loop()

        # mock loop 查询
        loop_result = MagicMock()
        loop_result.scalar_one_or_none.return_value = loop
        # unit 查询返回 None
        unit_result = MagicMock()
        unit_result.scalar_one_or_none.return_value = None

        db.execute = AsyncMock(side_effect=[loop_result, unit_result])

        with (
            patch(
                "app.services.workbench_summary._build_runtime",
                AsyncMock(return_value=(_runtime(), {}, _full_mappings())),
            ),
            patch(
                "app.services.workbench_summary._build_data_health",
                AsyncMock(return_value=_data_health()),
            ),
            patch(
                "app.services.workbench_summary._build_assessment_summary",
                AsyncMock(return_value=_assessment()),
            ),
            patch(
                "app.services.workbench_summary._build_diagnosis_summary",
                AsyncMock(return_value=_diagnosis()),
            ),
            patch(
                "app.services.workbench_summary._build_tuning_summary",
                AsyncMock(return_value=_tuning()),
            ),
            patch(
                "app.services.workbench_summary._build_tracker_timeline",
                AsyncMock(return_value=_tracker()),
            ),
            patch(
                "app.services.workbench_summary._build_active_attention",
                AsyncMock(return_value={"total": 0, "highestPriority": None, "items": []}),
            ),
        ):
            data = await get_workbench_summary(
                db=db, loop_id="00000000-0000-0000-0000-000000000001", role="ADMIN"
            )

        # 顶层字段完整性
        for key in (
            "loopId",
            "tagName",
            "runtime",
            "dataFreshness",
            "dataHealth",
            "scoreTrend",
            "activeAttention",
            "assessment",
            "diagnosis",
            "tuning",
            "trackerTimeline",
            "lifecycle",
            "nextAction",
            "partial",
            "unavailableSections",
        ):
            assert key in data, f"缺失字段 {key}"
        assert data["partial"] is False
        assert data["unavailableSections"] == []
        assert len(data["lifecycle"]["stages"]) == 5

    @pytest.mark.asyncio
    async def test_部分来源失败返回partial_true(self) -> None:
        """评估/数据健康来源抛异常时 partial=true。（MVP：诊断/整定/tracker 恒为 None）"""
        db = AsyncMock()
        loop = _make_loop()

        loop_result = MagicMock()
        loop_result.scalar_one_or_none.return_value = loop
        unit_result = MagicMock()
        unit_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[loop_result, unit_result])

        with (
            patch(
                "app.services.workbench_summary._build_runtime",
                AsyncMock(return_value=(_runtime(), {}, _full_mappings())),
            ),
            patch(
                "app.services.workbench_summary._build_data_health",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "app.services.workbench_summary._build_assessment_summary",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            # MVP 精简：诊断/整定/tracker 已屏蔽，不再调用 _build_*_summary/timeline
            patch(
                "app.services.workbench_summary._build_active_attention",
                AsyncMock(return_value={"total": 0, "highestPriority": None, "items": []}),
            ),
        ):
            data = await get_workbench_summary(
                db=db, loop_id="00000000-0000-0000-0000-000000000001", role="ADMIN"
            )

        assert data["partial"] is True
        assert "assessment" in data["unavailableSections"]
        assert "dataHealth" in data["unavailableSections"]
        # 失败的来源为 None
        assert data["assessment"] is None
        assert data["dataHealth"] == {}
        # MVP 屏蔽：诊断/整定/tracker 恒为 None
        assert data["diagnosis"] is None
        assert data["tuning"] is None
        assert data["trackerTimeline"] is None

    @pytest.mark.asyncio
    async def test_PE角色写动作disabled(self) -> None:
        """PE_ENGINEER 的 nextAction 写动作应 disabled。"""
        db = AsyncMock()
        loop = _make_loop()

        loop_result = MagicMock()
        loop_result.scalar_one_or_none.return_value = loop
        unit_result = MagicMock()
        unit_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[loop_result, unit_result])

        with (
            patch(
                "app.services.workbench_summary._build_runtime",
                AsyncMock(return_value=(_runtime(), {}, _full_mappings())),
            ),
            patch(
                "app.services.workbench_summary._build_data_health",
                AsyncMock(return_value=_data_health()),
            ),
            # 评估缺失 → RUN_ASSESSMENT
            patch(
                "app.services.workbench_summary._build_assessment_summary",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.services.workbench_summary._build_diagnosis_summary",
                AsyncMock(return_value=_diagnosis()),
            ),
            patch(
                "app.services.workbench_summary._build_tuning_summary",
                AsyncMock(return_value=_tuning()),
            ),
            patch(
                "app.services.workbench_summary._build_tracker_timeline",
                AsyncMock(return_value=_tracker()),
            ),
            patch(
                "app.services.workbench_summary._build_active_attention",
                AsyncMock(return_value={"total": 0, "highestPriority": None, "items": []}),
            ),
        ):
            data = await get_workbench_summary(
                db=db, loop_id="00000000-0000-0000-0000-000000000001", role="PE_ENGINEER"
            )

        assert data["nextAction"]["actionType"] == "RUN_ASSESSMENT"
        assert data["nextAction"]["enabled"] is False
        assert data["nextAction"]["disabledReason"] is not None


# ===========================================================================
# MW-P3-09: 实施前后对比 EffectCompare 测试
# ===========================================================================


def _make_tracker_model(
    *,
    implemented_at: datetime | None = None,
    new_pid_p: float | None = 1.5,
    new_pid_i: float | None = 0.15,
    new_pid_d: float | None = 0.0,
    ab_compare_summary: dict | None = None,
    effect_verified: bool | None = None,
    effect_verified_at: datetime | None = None,
) -> MagicMock:
    """构造 ActionTracker mock。"""
    tracker = MagicMock()
    tracker.implemented_at = implemented_at
    tracker.new_pid_p = new_pid_p
    tracker.new_pid_i = new_pid_i
    tracker.new_pid_d = new_pid_d
    tracker.ab_compare_summary = ab_compare_summary
    tracker.effect_verified = effect_verified
    tracker.effect_verified_at = effect_verified_at
    return tracker


def _ab_summary(
    *,
    data_insufficient: bool = False,
    improved_count: int = 3,
    deteriorated_count: int = 1,
    unchanged_count: int = 4,
) -> dict:
    """构造 ab_compare_summary。"""
    return {
        "improvedCount": improved_count,
        "deterioratedCount": deteriorated_count,
        "unchangedCount": unchanged_count,
        "dataInsufficient": data_insufficient,
        "kpiComparison": [
            {
                "metricKey": "score",
                "metricName": "综合评分",
                "before": 75.0,
                "after": 82.0,
                "change": 7.0,
                "improved": True,
            },
            {
                "metricKey": "steady_rate",
                "metricName": "平稳率",
                "before": 80.0,
                "after": 90.0,
                "change": 10.0,
                "improved": True,
            },
            {
                "metricKey": "oscillation_rate",
                "metricName": "振荡率",
                "before": 15.0,
                "after": 5.0,
                "change": -10.0,
                "improved": True,
            },
            {
                "metricKey": "saturation_rate",
                "metricName": "饱和率",
                "before": 5.0,
                "after": 8.0,
                "change": 3.0,
                "improved": False,
            },
            {
                "metricKey": "accuracy_rate",
                "metricName": "控制精度",
                "before": 70.0,
                "after": 75.0,
                "change": 5.0,
                "improved": True,
            },
        ],
    }


class TestEffectCompare:
    """实施前后对比构建器（MW-P3-09）。"""

    def test_无Tracker返回None(self):
        """无 Tracker 时不展示对比区。"""
        result = _build_effect_compare(
            tracker=None,
            ab_compare_summary=None,
            effect_verified=None,
            effect_verified_at=None,
            tuning_current_pid={"p": 1.0, "i": 0.1, "d": 0.0},
        )
        assert result is None

    def test_无实施时间返回None(self):
        """Tracker 无 implemented_at 时不展示对比区。"""
        tracker = _make_tracker_model(implemented_at=None)
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=None,
            effect_verified=None,
            effect_verified_at=None,
            tuning_current_pid=None,
        )
        assert result is None

    def test_有实施无ab_summary为PENDING(self):
        """有实施时间但无 ab_compare_summary → PENDING（未到验证周期）。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 5, 10, 0, 0),
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=None,
            effect_verified=None,
            effect_verified_at=None,
            tuning_current_pid={"p": 1.0, "i": 0.1, "d": 0.0},
        )
        assert result is not None
        assert result["status"] == "PENDING"
        assert result["conclusion"] is None
        assert result["conclusionLabel"] == "待验证"
        assert result["scoreChange"] is None
        assert result["coreKpiChanges"] == []
        assert result["pidBefore"] == {"p": 1.0, "i": 0.1, "d": 0.0}
        assert result["pidAfter"] == {"p": 1.5, "i": 0.15, "d": 0.0}
        assert result["timeWindow"] is not None
        assert result["timeWindow"]["beforeStart"] != result["timeWindow"]["afterEnd"]

    def test_数据不足为INCONCLUSIVE不显示伪0(self):
        """有 ab_compare_summary 但 dataInsufficient=true → INCONCLUSIVE。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 5, 10, 0, 0),
            ab_compare_summary=_ab_summary(data_insufficient=True),
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=_ab_summary(data_insufficient=True),
            effect_verified=None,
            effect_verified_at=None,
            tuning_current_pid=None,
        )
        assert result["status"] == "INCONCLUSIVE"
        assert result["conclusion"] is None
        assert result["conclusionLabel"] == "证据不足"
        assert result["dataInsufficient"] is True
        assert result["confidence"] == "INSUFFICIENT"
        # 评分变化仍然返回（有 before/after），但不作为结论依据
        assert result["scoreChange"] is not None
        assert result["scoreChange"]["before"] == 75.0

    def test_改善多于恶化为IMPROVED(self):
        """改善指标数 > 恶化指标数 → IMPROVED。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 1, 10, 0, 0),
            ab_compare_summary=_ab_summary(
                improved_count=4, deteriorated_count=1, data_insufficient=False
            ),
            effect_verified=True,
            effect_verified_at=datetime(2026, 8, 8, 10, 0, 0),
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=tracker.ab_compare_summary,
            effect_verified=tracker.effect_verified,
            effect_verified_at=tracker.effect_verified_at,
            tuning_current_pid={"p": 1.0, "i": 0.1, "d": 0.0},
        )
        assert result["status"] == "COMPLETED"
        assert result["conclusion"] == "IMPROVED"
        assert result["conclusionLabel"] == "改善"
        assert result["confidence"] == "HIGH"

    def test_恶化多于改善为DETERIORATED(self):
        """恶化指标数 > 改善指标数 → DETERIORATED。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 1, 10, 0, 0),
            ab_compare_summary=_ab_summary(
                improved_count=1, deteriorated_count=3, data_insufficient=False
            ),
            effect_verified=False,
            effect_verified_at=datetime(2026, 8, 8, 10, 0, 0),
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=tracker.ab_compare_summary,
            effect_verified=tracker.effect_verified,
            effect_verified_at=tracker.effect_verified_at,
            tuning_current_pid=None,
        )
        assert result["status"] == "COMPLETED"
        assert result["conclusion"] == "DETERIORATED"
        assert result["conclusionLabel"] == "恶化"

    def test_改善等于恶化为NO_CHANGE(self):
        """改善==恶化 → NO_CHANGE（无明显变化）。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 1, 10, 0, 0),
            ab_compare_summary=_ab_summary(
                improved_count=2, deteriorated_count=2, data_insufficient=False
            ),
            effect_verified=True,
            effect_verified_at=datetime(2026, 8, 8, 10, 0, 0),
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=tracker.ab_compare_summary,
            effect_verified=tracker.effect_verified,
            effect_verified_at=tracker.effect_verified_at,
            tuning_current_pid=None,
        )
        assert result["status"] == "COMPLETED"
        assert result["conclusion"] == "NO_CHANGE"
        assert result["conclusionLabel"] == "无明显变化"

    def test_评分变化从kpiComparison提取(self):
        """综合评分从 kpiComparison[metricKey=score] 单独提取。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 1, 10, 0, 0),
            ab_compare_summary=_ab_summary(data_insufficient=False),
            effect_verified=True,
            effect_verified_at=datetime(2026, 8, 8, 10, 0, 0),
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=tracker.ab_compare_summary,
            effect_verified=tracker.effect_verified,
            effect_verified_at=tracker.effect_verified_at,
            tuning_current_pid=None,
        )
        assert result["scoreChange"] is not None
        assert result["scoreChange"]["before"] == 75.0
        assert result["scoreChange"]["after"] == 82.0
        assert result["scoreChange"]["change"] == 7.0
        assert result["scoreChange"]["improved"] is True

    def test_核心KPI排除评分最多4项(self):
        """coreKpiChanges 排除综合评分，最多 4 项。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 1, 10, 0, 0),
            ab_compare_summary=_ab_summary(data_insufficient=False),
            effect_verified=True,
            effect_verified_at=datetime(2026, 8, 8, 10, 0, 0),
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=tracker.ab_compare_summary,
            effect_verified=tracker.effect_verified,
            effect_verified_at=tracker.effect_verified_at,
            tuning_current_pid=None,
        )
        assert len(result["coreKpiChanges"]) <= 4
        # 不包含 score
        assert all(k["metricKey"] != "score" for k in result["coreKpiChanges"])

    def test_时间窗口为T减7天到T加7天(self):
        """时间窗为 [T-7d, T) 与 (T, T+7d]。"""
        impl_at = datetime(2026, 8, 5, 10, 0, 0)
        tracker = _make_tracker_model(
            implemented_at=impl_at,
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=None,
            effect_verified=None,
            effect_verified_at=None,
            tuning_current_pid=None,
        )
        tw = result["timeWindow"]
        # beforeStart = T - 7d
        assert "2026-07-29" in tw["beforeStart"]
        # beforeEnd = T
        assert "2026-08-05" in tw["beforeEnd"]
        # afterStart = T
        assert "2026-08-05" in tw["afterStart"]
        # afterEnd = T + 7d
        assert "2026-08-12" in tw["afterEnd"]

    def test_无new_pid时pidAfter为None(self):
        """Tracker 无 new_pid 时 pidAfter 为 None。"""
        tracker = _make_tracker_model(
            implemented_at=datetime(2026, 8, 5, 10, 0, 0),
            new_pid_p=None,
            new_pid_i=None,
            new_pid_d=None,
        )
        result = _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=None,
            effect_verified=None,
            effect_verified_at=None,
            tuning_current_pid={"p": 1.0, "i": 0.1, "d": 0.0},
        )
        assert result["pidAfter"] is None
        assert result["pidBefore"] == {"p": 1.0, "i": 0.1, "d": 0.0}
