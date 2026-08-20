"""SQLAlchemy 2.0 ORM models for CLPM.

Imports all model modules so that ``Base.metadata`` is fully populated when
``app.models`` is imported (required by Alembic autogenerate).
"""

from __future__ import annotations

from app.models.alert import (
    AlertEvent,
    AlertRule,
    AlertRuleAuditLog,
    AlertRuleSubscription,
    AlertSuppression,
)
from app.models.algorithm_parameter import AlgorithmParameter
from app.models.audit import SysAuditLog
from app.models.base import Base, TimestampMixin
from app.models.dcs_mode_mapping import DcsModeMapping
from app.models.dcs_model import DcsModel
from app.models.dcs_pid_structure import DcsPidStructure
from app.models.dcs_vendor import DcsVendor
from app.models.diagnosis import DiagnosisConfig, DiagnosisResult, DiagnosisTag
from app.models.diagnosis_run import DiagnosisRun
from app.models.engine import EngineRule
from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.loop_action_item import LoopActionItem
from app.models.loop_config import LoopLevelWeight, LoopModeMapping, LoopTypeWeight
from app.models.metric import (
    KpiSnapshotCustom,
    KpiSnapshotHourly,
    LoopConfidenceLatest,
    MetricConfig,
)
from app.models.metric_data_requirement import ClpmMetricDataRequirement
from app.models.mode_definition import ModeDefinition
from app.models.node_kpi import (
    KpiNodeSnapshotDaily,
    KpiNodeSnapshotHourly,
    KpiNodeSnapshotMonthly,
)
from app.models.plant_node import PlantNode
from app.models.process_model_version import ProcessModelVersion
from app.models.report import ReportRecord
from app.models.report_config import ReportConfig
from app.models.sys_config import SysConfig
from app.models.sys_user import SysUser
from app.models.tag import TagRegistry
from app.models.tracker import ActionTracker
from app.models.tuning import TuningRecord
from app.models.tuning_knowledge import TuningKnowledgeEntry
from app.models.unit_kpi_summary import UnitKpiSummary

__all__ = [
    "Base",
    "TimestampMixin",
    "AlgorithmParameter",
    "SysUser",
    "PlantNode",
    "ProcessModelVersion",
    "LoopLedger",
    "LoopActionItem",
    "HandlingOrder",
    "LoopTagMapping",
    "LoopModeMapping",
    "LoopTypeWeight",
    "LoopLevelWeight",
    "TagRegistry",
    "MetricConfig",
    "DiagnosisConfig",
    "EngineRule",
    "KpiSnapshotHourly",
    "KpiSnapshotCustom",
    "LoopConfidenceLatest",
    "ClpmMetricDataRequirement",
    "DiagnosisTag",
    "UnitKpiSummary",
    "KpiNodeSnapshotHourly",
    "KpiNodeSnapshotDaily",
    "KpiNodeSnapshotMonthly",
    "ActionTracker",
    "DiagnosisResult",
    "DiagnosisRun",
    "TuningRecord",
    "TuningKnowledgeEntry",
    "ReportRecord",
    "ReportConfig",
    "SysAuditLog",
    "SysConfig",
    # DCS 配置（v6.1 新增）
    "DcsVendor",
    "DcsModel",
    "ModeDefinition",
    "DcsModeMapping",
    "DcsPidStructure",
    # 智能预警规则引擎（v2.6 新增）
    "AlertRule",
    "AlertRuleSubscription",
    "AlertEvent",
    "AlertRuleAuditLog",
    "AlertSuppression",
]
