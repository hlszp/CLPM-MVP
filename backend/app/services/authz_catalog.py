"""授权目录与持久策略服务（P4-01，方案 01 §3.5 / DEC-04 五角色+两维度）.

单一事实源结构：

- ``CATALOG``：页面码/动作码目录（kind=PAGE_GROUP/PAGE/ACTION；码间依赖
  ``requires`` 显式；protected 码=ADMIN 关键管理能力）。种子同批写入
  alembic 迁移（p401authz01）与引导 SQL（db/postgresql/01/02），一致性由
  集成测试三方对照钉死（迁移种子 == 引导 SQL 种子 == 本常量）。
- ``ROLE_SEED``：五角色默认矩阵，按 **P1-04 实际裁决**（2026-10-11
  AUTH-02 止血后）落库——IC 历史 ``loop:*``/``metric:*`` 等通配按真实
  require_roles 白名单展开为显式码集合，**无机械通配展开**（``loop:*``
  不自动获得 ``loop:delete``）。SPONSOR 零写红线显式钉死。

策略操作（全部走 ``sys_authz_policy`` 账本 + SysAuditLog 审计）：

- ``update_role_grants``：未知码/类型不符/漏依赖/ADMIN protected 移除/
  expectedRevision 失配（409）校验链
- ``update_user_scope``：ALL/NODE_SUBTREE + plant_node 绑定校验
- ``rollback_revision``：回退=发布逆操作（恢复 before 快照为新 revision，
  不删历史；SEED 不可回退；同一 revision 仅可回退一次）

module enabled 与 fitness 门禁不被授权覆盖：目录码带 ``module_key``，
``get_effective_access`` 返回每码 ``moduleEnabled`` 标记——运行时生效
判定恒为"授权 AND 模块启用 AND 业务门禁（fitness L0/L1 等）"，授权矩阵
只收紧不放宽（消费接线在 P4-02）。

兼容性：``services/auth.py ROLE_PERMISSIONS`` 现行五角色判定链
（deps.has_perms/require_perms）零改动，本目录叠加不破坏。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.authz import SysAuthzPolicy, SysRolePermission, SysUserScope
from app.models.plant_node import PlantNode
from app.models.sys_user import SysUser

VALID_ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "SPONSOR", "EXPERT")
SCOPE_TYPES = ("ALL", "NODE_SUBTREE")


# ---------------------------------------------------------------------------
# 目录定义（迁移种子与引导 SQL 的同批事实源）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermDef:
    """目录码定义。"""

    kind: str  # PAGE_GROUP / PAGE / ACTION
    parent: str | None = None
    requires: tuple[str, ...] = ()
    module_key: str | None = None
    protected: bool = False
    description: str = ""
    order: int = 0


def _g(module_key: str | None, description: str, order: int) -> PermDef:
    return PermDef(kind="PAGE_GROUP", module_key=module_key, description=description, order=order)


def _p(
    parent: str,
    description: str,
    roles_note: str = "",
    module_key: str | None = None,
    protected: bool = False,
    order: int = 0,
) -> PermDef:
    return PermDef(
        kind="PAGE",
        parent=parent,
        module_key=module_key,
        protected=protected,
        description=f"{description}｜{roles_note}" if roles_note else description,
        order=order,
    )


def _a(
    requires: tuple[str, ...],
    description: str,
    module_key: str | None = None,
    protected: bool = False,
    order: int = 0,
) -> PermDef:
    return PermDef(
        kind="ACTION",
        requires=requires,
        module_key=module_key,
        protected=protected,
        description=description,
        order=order,
    )


CATALOG: dict[str, PermDef] = {
    # ---------------- 页面组（10 组 = 一级导航） ----------------
    "page:cockpit": _g(None, "驾驶舱（全局观察入口，不受模块开关约束）", 0),
    "page:workbench": _g(None, "工作台（回路全生命周期单页）", 1),
    "page:monitor": _g("monitor", "回路监控", 2),
    "page:assess": _g("assess", "性能评估", 3),
    "page:diagnosis": _g("diagnosis", "回路诊断", 4),
    "page:tuning": _g("tuning", "参数整定", 5),
    "page:handling": _g("handling", "运维处置", 6),
    "page:reports": _g("reports", "统计分析", 7),
    "page:config": _g("config", "参数配置", 8),
    "page:system": _g("system", "系统管理", 9),
    # ---------------- 页面（对应现行路由模块清单） ----------------
    # 驾驶舱六页签（authority 无限制=五角色全可见；舱内页签消费对应模块端点）
    "page:cockpit.overview": _p("page:cockpit", "驾驶舱-总览", "五角色", None, False, 10),
    "page:cockpit.loops": _p("page:cockpit", "驾驶舱-回路", "五角色", "monitor", False, 20),
    "page:cockpit.performance": _p("page:cockpit", "驾驶舱-性能", "五角色", "assess", False, 30),
    "page:cockpit.diagnosis": _p("page:cockpit", "驾驶舱-诊断", "五角色", "diagnosis", False, 40),
    "page:cockpit.tuning": _p("page:cockpit", "驾驶舱-整定", "五角色", "tuning", False, 50),
    "page:cockpit.handling": _p("page:cockpit", "驾驶舱-处置", "五角色", "handling", False, 60),
    # 工作台（/loop/workbench360；SPONSOR 不可见）
    "page:workbench.main": _p(
        "page:workbench", "工作台主页（回路 360 全生命周期）", "ADMIN/IC/PE/EXPERT", None, False, 10
    ),
    # 回路监控
    "page:monitor.loops": _p("page:monitor", "回路监视", "五角色", "monitor", False, 10),
    "page:monitor.alerts": _p("page:monitor", "预警事件", "五角色", "monitor", False, 20),
    "page:monitor.attention": _p("page:monitor", "关注队列", "五角色", "monitor", False, 30),
    "page:monitor.table": _p("page:monitor", "回路实时表格", "ADMIN/IC/PE", "monitor", False, 40),
    "page:alert.rules": _p("page:monitor", "预警规则管理（旧路径）", "ADMIN", "monitor", False, 50),
    # 性能评估（/metric/*）
    "page:assess.dashboard": _p(
        "page:assess", "装置性能", "ADMIN/IC/PE/SPONSOR", "assess", False, 10
    ),
    "page:assess.indicator": _p(
        "page:assess", "指标分析", "ADMIN/IC/PE/SPONSOR", "assess", False, 20
    ),
    "page:assess.loop-evaluation": _p(
        "page:assess", "回路评估", "ADMIN/IC/PE/SPONSOR", "assess", False, 30
    ),
    "page:assess.performance": _p(
        "page:assess", "回路性能", "ADMIN/IC/PE/SPONSOR", "assess", False, 40
    ),
    "page:assess.tasks": _p("page:assess", "评估任务", "ADMIN/IC", "assess", False, 50),
    "page:assess.history": _p("page:assess", "评估记录", "ADMIN/IC", "assess", False, 60),
    "page:assess.matrix": _p("page:assess", "指标矩阵", "ADMIN/IC/PE/SPONSOR", "assess", False, 70),
    "page:assess.kpi-report": _p(
        "page:assess", "绩效报告", "ADMIN/IC/PE/SPONSOR", "assess", False, 80
    ),
    # 诊断三页式
    "page:diagnosis.overview": _p("page:diagnosis", "诊断概览", "五角色", "diagnosis", False, 10),
    "page:diagnosis.tasks": _p("page:diagnosis", "诊断任务", "五角色", "diagnosis", False, 20),
    "page:diagnosis.records": _p("page:diagnosis", "诊断记录", "五角色", "diagnosis", False, 30),
    # 整定三页式
    "page:tuning.overview": _p("page:tuning", "整定总览", "五角色", "tuning", False, 10),
    "page:tuning.records": _p("page:tuning", "整定记录", "五角色", "tuning", False, 20),
    "page:tuning.verification": _p("page:tuning", "效果验证", "五角色", "tuning", False, 30),
    # 处置 v2.0 双实体
    "page:handling.suggestions": _p("page:handling", "诊断建议", "五角色", "handling", False, 10),
    "page:handling.tasks": _p("page:handling", "处置任务", "五角色", "handling", False, 20),
    "page:handling.orders": _p("page:handling", "处置工单", "五角色", "handling", False, 30),
    "page:handling.archive": _p("page:handling", "处置档案", "五角色", "handling", False, 40),
    "page:handling.workbench": _p("page:handling", "处置工作台", "五角色", "handling", False, 50),
    "page:handling.statistics": _p("page:handling", "处置报告", "五角色", "handling", False, 60),
    # 统计分析
    "page:reports.performance": _p("page:reports", "绩效报告", "五角色", "reports", False, 10),
    "page:reports.data-quality": _p("page:reports", "数据质量", "五角色", "reports", False, 20),
    "page:reports.alert-statistics": _p("page:reports", "预警统计", "五角色", "reports", False, 30),
    "page:reports.diagnosis": _p("page:reports", "诊断报告", "五角色", "reports", False, 40),
    "page:reports.handling": _p("page:reports", "处置报告", "五角色", "reports", False, 50),
    "page:reports.benefit": _p(
        "page:reports", "收益报告", "ADMIN/IC/PE/SPONSOR", "reports", False, 60
    ),
    "page:reports.subscription": _p("page:reports", "订阅配置", "ADMIN", "reports", False, 70),
    # 参数配置
    "page:config.link": _p("page:config", "链路配置", "ADMIN", "config", False, 10),
    "page:config.tag": _p("page:config", "测点配置", "ADMIN/IC/PE", "config", False, 20),
    "page:config.loop": _p("page:config", "回路配置", "ADMIN/IC/PE", "config", False, 30),
    "page:config.ledger": _p("page:config", "回路台账", "ADMIN/IC/PE", "config", False, 40),
    "page:config.factory": _p("page:config", "工厂配置", "ADMIN/IC/PE", "config", False, 50),
    "page:config.datasource": _p("page:config", "数据导入", "ADMIN/IC/PE", "config", False, 60),
    "page:config.metric": _p("page:config", "指标配置", "ADMIN", "config", False, 70),
    "page:config.diagnosis": _p("page:config", "诊断配置", "ADMIN", "config", False, 80),
    "page:config.alert-rules": _p("page:config", "预警规则", "ADMIN", "config", False, 90),
    "page:config.pid-template": _p("page:config", "PID 结构模板", "ADMIN", "config", False, 100),
    # 系统管理（protected=最后管理员失权保护码）
    "page:system.basic-info": _p("page:system", "基础信息", "ADMIN", "system", False, 10),
    "page:system.users": _p("page:system", "用户管理", "ADMIN（protected）", "system", True, 20),
    "page:system.audit": _p("page:system", "审计日志", "ADMIN（protected）", "system", True, 30),
    "page:system.dict": _p("page:system", "字典管理", "ADMIN", "system", False, 40),
    "page:system.permissions": _p(
        "page:system", "权限矩阵", "ADMIN（protected）", "system", True, 50
    ),
    "page:system.modules": _p("page:system", "模块管理", "ADMIN（protected）", "system", True, 60),
    "page:system.reports": _p("page:system", "订阅配置", "ADMIN", "system", False, 70),
    "page:system.algorithm-params": _p("page:system", "算法参数配置", "ADMIN", "system", False, 80),
    # 横切详情页（无独立导航组）
    "page:task.detail": _p("page:assess", "任务详情（横切）", "ADMIN/IC/PE", None, False, 200),
    # ---------------- 动作码（读码 + 操作码） ----------------
    # 读码（P1-04 后现行 ROLE_PERMISSIONS 读码一一对应；无通配）
    "portal:view": _a((), "门户/驾驶舱读", None, False, 900),
    "loop:view": _a((), "回路读", "monitor", False, 901),
    "metric:view": _a((), "性能评估读", "assess", False, 902),
    "diagnosis:view": _a((), "诊断读", "diagnosis", False, 903),
    "tuning:view": _a((), "整定读", "tuning", False, 904),
    "alert:view": _a((), "预警读", "monitor", False, 905),
    "handling:view": _a((), "处置读", "handling", False, 906),
    "tracker:view": _a((), "处置建议分派读", "handling", False, 907),
    "tracker:review": _a((), "处置建议复核读（EXPERT 专属读）", "handling", False, 908),
    "config:view": _a((), "参数配置域读", "config", False, 909),
    "system:view": _a((), "系统管理域读", "system", False, 910),
    # 回路操作（IC 的历史 loop:* 按真实 require_roles 白名单显式展开——
    # loop:delete 仅 ADMIN，loop:batch-config 仅 ADMIN，IC 不因通配获得）
    "loop:create": _a(("loop:view",), "回路创建", "config", False, 1000),
    "loop:edit": _a(("loop:view",), "回路编辑", "config", False, 1001),
    "loop:export": _a(("loop:view",), "回路导出", "config", False, 1002),
    "loop:import": _a(("loop:view",), "回路导入", "config", False, 1003),
    "loop:batch-config": _a(("loop:view",), "回路批量配置（ADMIN）", "config", False, 1004),
    "loop:batch-grouping": _a(("loop:view",), "回路批量分组", "config", False, 1005),
    "loop:delete": _a(("loop:view",), "回路删除（ADMIN 专属）", "config", False, 1006),
    # 评估操作
    "metric:task-run": _a(("metric:view",), "发起/启动评估任务", "assess", False, 1100),
    "metric:task-manage": _a(("metric:view",), "取消/删除评估任务", "assess", False, 1101),
    "metric:snapshot-manage": _a(("metric:view",), "性能快照删除", "assess", False, 1102),
    "metric-config:manage": _a(
        ("config:view",), "指标定义/权重/阈值/置信度等配置写", "config", False, 1103
    ),
    # 诊断操作（diagnosis_v2 四角色触发）
    "diagnosis:trigger": _a(("diagnosis:view",), "发起诊断", "diagnosis", False, 1200),
    "diagnosis:task-manage": _a(
        ("diagnosis:view",), "诊断任务运行/取消/归档/删除", "diagnosis", False, 1201
    ),
    "diagnosis:review": _a(("diagnosis:view",), "诊断结论复核/处置动作", "diagnosis", False, 1202),
    "diagnosis:interpret": _a(("diagnosis:view",), "诊断解释/报告/导出", "diagnosis", False, 1203),
    "diagnosis:threshold-override": _a(
        ("diagnosis:view",), "诊断阈值覆盖/模板应用", "diagnosis", False, 1204
    ),
    "diagnosis-config:manage": _a(
        ("config:view",), "诊断配置写（指标/规则/触发配置）", "config", False, 1205
    ),
    # 整定操作（fitness L0/L1 阻断等业务门禁由服务层执行，授权不可放宽）
    "tuning:identify": _a(("tuning:view",), "发起辨识", "tuning", False, 1300),
    "tuning:tune": _a(("tuning:view",), "发起整定", "tuning", False, 1301),
    "tuning:simulate": _a(("tuning:view",), "闭环仿真/方案对比", "tuning", False, 1302),
    "tuning:task-create": _a(("tuning:view",), "创建整定批量任务", "tuning", False, 1303),
    "tuning:task-manage": _a(("tuning:view",), "取消整定任务", "tuning", False, 1304),
    "tuning:advanced": _a(
        ("tuning:view",),
        "高级参数字段（ADMIN/EXPERT；fitness 门禁不因授权放宽）",
        "tuning",
        False,
        1305,
    ),
    # 处置操作
    "handling:operate": _a(("handling:view",), "建议流转/工单全流转操作", "handling", False, 1400),
    "tracker:operate": _a(("tracker:view",), "处置建议分派写", "handling", False, 1401),
    # 预警操作
    "alert:rule-manage": _a(("alert:view",), "预警规则 CRUD/启停（ADMIN）", "monitor", False, 1500),
    "alert:event-handle": _a(("alert:view",), "预警事件确认/解决/误报", "monitor", False, 1501),
    "alert:suppression": _a(("alert:view",), "预警抑制管理", "monitor", False, 1502),
    "alert:dry-run": _a(("alert:view",), "预警规则试运行", "monitor", False, 1503),
    # P4-02 观察项①（P4-01 验收报告）：订阅 CRUD 原与规则管理混域无独立码，
    # create/delete_subscription 端点（require_roles A/I）补独立动作码
    "alert:subscription-manage": _a(
        ("alert:view",), "预警订阅创建/删除（ADMIN/IC）", "monitor", False, 1504
    ),
    # 配置域操作（config:publish=DEC-03 ADMIN）
    "config:publish": _a(
        ("config:view",), "分层配置发布/重置/回退（ADMIN，DEC-03）", "config", False, 1600
    ),
    "tag:manage": _a(("config:view",), "Tag 注册 CRUD/导入", "config", False, 1601),
    "plant:manage": _a(("config:view",), "工厂节点 CRUD/导入导出", "config", False, 1602),
    "dcs:manage": _a(("config:view",), "DCS 品牌/型号/MODE 配置写", "config", False, 1603),
    "data:manage": _a(("config:view",), "数据源配置/DataPlanner/工厂同步", "config", False, 1604),
    "ai:insight": _a(("diagnosis:view",), "AI 洞察生成", None, False, 1605),
    # P4-02 尾批：历史数据导入（loop_data.py /loops/data-import 6 端点）活链
    # require_roles 白名单为 A/I/P，宽于 data:manage 种子（ADMIN——对应
    # datasource/dataplanner/factory_sync 的 A-only 基线）。扩 data:manage 种子
    # 会放大那 13 个端点，故按 P4-01"种子=活链真实白名单"原则补独立码
    "data:import": _a(
        ("config:view",), "历史数据导入/KPI 回算（ADMIN/IC/PE）", "config", False, 1606
    ),
    # 系统管理操作（protected=最后管理员失权保护码）
    "system:user-manage": _a(
        ("system:view",), "用户 CRUD/重置密码（protected）", "system", True, 1700
    ),
    "system:authorization-manage": _a(
        ("system:view",), "角色矩阵/用户 scope/回退管理（protected）", "system", True, 1701
    ),
    "system:module-manage": _a(("system:view",), "模块启停（protected）", "system", True, 1702),
    "system:dict-manage": _a(("system:view",), "字典 CRUD", "system", False, 1703),
    "system:basic-manage": _a(("system:view",), "基础信息/LOGO 配置", "system", False, 1704),
    "system:audit-view": _a(
        ("system:view",), "审计日志查询/导出（protected）", "system", True, 1705
    ),
    "system:algorithm-params": _a(("system:view",), "算法参数 PUT 兼容通道", "system", False, 1706),
    "system:report-config": _a(("system:view",), "报表配置/生成/阶段锁", "system", False, 1707),
}

#: protected 码集合（不得从 ADMIN 角色矩阵移除——所有 ADMIN 共享矩阵，
#: 移除即全体管理员失权，含"最后一名 ADMIN"场景在内的任何移除尝试拒绝）
PROTECTED_CODES: frozenset[str] = frozenset(code for code, d in CATALOG.items() if d.protected)


# ---------------------------------------------------------------------------
# 五角色种子矩阵（P1-04 实际裁决，2026-10-11 AUTH-02 止血后口径）
# ---------------------------------------------------------------------------

# 读码集合按角色（来源：ROLE_PERMISSIONS P1-04 后实际值，通配按真实端点语义展开）
_READ_CODES = {
    "ADMIN": frozenset(
        {
            "portal:view",
            "loop:view",
            "metric:view",
            "diagnosis:view",
            "tuning:view",
            "alert:view",
            "handling:view",
            "tracker:view",
            "tracker:review",
            "config:view",
            "system:view",
        }
    ),
    "IC_ENGINEER": frozenset(
        {
            "portal:view",
            "loop:view",
            "metric:view",
            "diagnosis:view",
            "tuning:view",
            "alert:view",
            "handling:view",
            "config:view",
        }
    ),
    "PE_ENGINEER": frozenset(
        {
            "portal:view",
            "loop:view",
            "metric:view",
            "diagnosis:view",
            "tuning:view",
            "alert:view",
            "handling:view",
            "config:view",
            "tracker:view",
        }
    ),
    "SPONSOR": frozenset(
        {
            "portal:view",
            "loop:view",
            "metric:view",
            "diagnosis:view",
            "tuning:view",
            "alert:view",
            "handling:view",
        }
    ),
    "EXPERT": frozenset(
        {
            "portal:view",
            "loop:view",
            "metric:view",
            "diagnosis:view",
            "tuning:view",
            "alert:view",
            "handling:view",
            "tracker:review",
        }
    ),
}

# 动作码集合按角色（来源：后端 require_roles 真实白名单，活链端点）
_ACTION_CODES = {
    "ADMIN": frozenset(code for code, d in CATALOG.items() if d.kind == "ACTION"),
    "IC_ENGINEER": frozenset(
        {
            "loop:create",
            "loop:edit",
            "loop:export",
            "loop:import",
            "loop:batch-grouping",
            "metric:task-run",
            "metric:task-manage",
            "metric:snapshot-manage",
            "diagnosis:trigger",
            "diagnosis:task-manage",
            "diagnosis:review",
            "diagnosis:interpret",
            "diagnosis:threshold-override",
            "tuning:identify",
            "tuning:tune",
            "tuning:simulate",
            "tuning:task-create",
            "tuning:task-manage",
            "handling:operate",
            "alert:event-handle",
            "alert:suppression",
            "alert:dry-run",
            "alert:subscription-manage",
            "tag:manage",
            "ai:insight",
            "data:import",
        }
    ),
    "PE_ENGINEER": frozenset(
        {
            "loop:create",
            "loop:edit",
            "loop:export",
            "metric:task-run",
            "metric:task-manage",
            "diagnosis:trigger",
            "diagnosis:task-manage",
            "diagnosis:review",
            "diagnosis:interpret",
            "tuning:identify",
            "tuning:tune",
            "tuning:simulate",
            "tuning:task-create",
            "tuning:task-manage",
            "handling:operate",
            "tracker:operate",
            "ai:insight",
            "data:import",
        }
    ),
    "SPONSOR": frozenset(),  # 零写红线（P1-04 裁决钉死）
    "EXPERT": frozenset(
        {
            "diagnosis:trigger",
            "diagnosis:task-manage",
            "diagnosis:review",
            "tuning:identify",
            "tuning:tune",
            "tuning:simulate",
            "tuning:task-create",
            "tuning:task-manage",
            "tuning:advanced",
            "ai:insight",
        }
    ),
}

# 页面码集合按角色（来源：前端路由 authority 实际矩阵；PAGE_GROUP 不参与授权）
_COCKPIT_PAGES = frozenset(
    {
        "page:cockpit.overview",
        "page:cockpit.loops",
        "page:cockpit.performance",
        "page:cockpit.diagnosis",
        "page:cockpit.tuning",
        "page:cockpit.handling",
    }
)
_ASSESS_PAGES_COMMON = frozenset(
    {
        "page:assess.dashboard",
        "page:assess.indicator",
        "page:assess.loop-evaluation",
        "page:assess.performance",
        "page:assess.matrix",
        "page:assess.kpi-report",
    }
)
_DIAG_PAGES = frozenset(
    {"page:diagnosis.overview", "page:diagnosis.tasks", "page:diagnosis.records"}
)
_TUNING_PAGES = frozenset(
    {"page:tuning.overview", "page:tuning.records", "page:tuning.verification"}
)
_HANDLING_PAGES = frozenset(
    {
        "page:handling.suggestions",
        "page:handling.tasks",
        "page:handling.orders",
        "page:handling.archive",
        "page:handling.workbench",
        "page:handling.statistics",
    }
)
_REPORTS_PAGES_COMMON = frozenset(
    {
        "page:reports.performance",
        "page:reports.data-quality",
        "page:reports.alert-statistics",
        "page:reports.diagnosis",
        "page:reports.handling",
    }
)
_MONITOR_PAGES_COMMON = frozenset(
    {"page:monitor.loops", "page:monitor.alerts", "page:monitor.attention"}
)
_CONFIG_PAGES_ENG = frozenset(
    {
        "page:config.tag",
        "page:config.loop",
        "page:config.ledger",
        "page:config.factory",
        "page:config.datasource",
    }
)
_SYSTEM_PAGES_ALL = frozenset(
    {
        "page:system.basic-info",
        "page:system.users",
        "page:system.audit",
        "page:system.dict",
        "page:system.permissions",
        "page:system.modules",
        "page:system.reports",
        "page:system.algorithm-params",
    }
)

_PAGE_CODES = {
    "ADMIN": frozenset(c for c, d in CATALOG.items() if d.kind == "PAGE"),
    "IC_ENGINEER": _COCKPIT_PAGES
    | {"page:workbench.main"}
    | _MONITOR_PAGES_COMMON
    | {"page:monitor.table"}
    | _ASSESS_PAGES_COMMON
    | {"page:assess.tasks", "page:assess.history"}
    | _DIAG_PAGES
    | _TUNING_PAGES
    | _HANDLING_PAGES
    | _REPORTS_PAGES_COMMON
    | {"page:reports.benefit"}
    | _CONFIG_PAGES_ENG
    | {"page:task.detail"},
    "PE_ENGINEER": _COCKPIT_PAGES
    | {"page:workbench.main"}
    | _MONITOR_PAGES_COMMON
    | {"page:monitor.table"}
    | _ASSESS_PAGES_COMMON
    | _DIAG_PAGES
    | _TUNING_PAGES
    | _HANDLING_PAGES
    | _REPORTS_PAGES_COMMON
    | {"page:reports.benefit"}
    | _CONFIG_PAGES_ENG
    | {"page:task.detail"},
    "SPONSOR": _COCKPIT_PAGES
    | _MONITOR_PAGES_COMMON
    | _ASSESS_PAGES_COMMON
    | _DIAG_PAGES
    | _TUNING_PAGES
    | _HANDLING_PAGES
    | _REPORTS_PAGES_COMMON
    | {"page:reports.benefit"},
    "EXPERT": _COCKPIT_PAGES
    | {"page:workbench.main"}
    | _MONITOR_PAGES_COMMON
    | _ASSESS_PAGES_COMMON
    | _DIAG_PAGES
    | _TUNING_PAGES
    | _HANDLING_PAGES
    | _REPORTS_PAGES_COMMON,
}

#: 五角色种子矩阵（迁移种子与引导 SQL 的同批事实源）
ROLE_SEED: dict[str, frozenset[str]] = {
    role: frozenset(_READ_CODES[role] | _ACTION_CODES[role] | _PAGE_CODES[role])
    for role in VALID_ROLES
}


# ---------------------------------------------------------------------------
# 目录结构自检（import 期即校验，防止定义漂移）
# ---------------------------------------------------------------------------


def _validate_catalog() -> None:
    known = set(CATALOG.keys())
    for code, d in CATALOG.items():
        if d.kind == "PAGE_GROUP" and d.parent is not None:
            raise ValueError(f"PAGE_GROUP 不得挂父级: {code}")
        if d.parent is not None and d.parent not in known:
            raise ValueError(f"未知父级码: {code} -> {d.parent}")
        if d.kind == "PAGE" and d.parent is not None and CATALOG[d.parent].kind != "PAGE_GROUP":
            raise ValueError(f"页面父级必须是组码: {code} -> {d.parent}")
        for req in d.requires:
            if req not in known:
                raise ValueError(f"未知依赖码: {code} -> {req}")
            if CATALOG[req].kind != "ACTION":
                raise ValueError(f"依赖必须指向读码（ACTION）: {code} -> {req}")
        if "*" in code or code.endswith(":*"):
            raise ValueError(f"目录禁止通配码: {code}")
        if d.module_key is not None and d.module_key not in {
            "monitor",
            "assess",
            "diagnosis",
            "tuning",
            "handling",
            "reports",
            "config",
            "system",
        }:
            raise ValueError(f"未知 module_key: {code} -> {d.module_key}")
    for role, codes in ROLE_SEED.items():
        unknown = codes - known
        if unknown:
            raise ValueError(f"{role} 种子含未知码: {sorted(unknown)}")
    # 种子依赖闭包完整性（种子矩阵自身必须通过漏依赖校验）
    for role, codes in ROLE_SEED.items():
        missing = validate_grants(role, codes)
        if missing:
            raise ValueError(f"{role} 种子漏依赖: {missing}")


def validate_grants(role: str, codes: frozenset[str] | set[str]) -> dict[str, list[str]]:
    """校验授权集合的依赖闭包完整性（纯函数，无 DB）。

    返回 ``{码: [缺失依赖...]}``；空 dict=通过。未知码/通配码/类型不符
    抛 ``BizError``（由 ``_check_known_codes`` 承担，本函数只查依赖）。
    """
    missing: dict[str, list[str]] = {}
    for code in codes:
        d = CATALOG.get(code)
        if d is None:
            continue  # 未知码由 _check_known_codes 拒绝
        lacks = [req for req in d.requires if req not in codes]
        if lacks:
            missing[code] = lacks
    return missing


_validate_catalog()


# ---------------------------------------------------------------------------
# 校验链（未知码/类型不符/漏依赖/管理员保护）
# ---------------------------------------------------------------------------


def _check_known_codes(codes: frozenset[str] | set[str]) -> None:
    """未知码（含 ``*`` 通配形态）拒绝——无机械通配展开的入口防线。"""
    unknown = sorted(c for c in codes if c not in CATALOG)
    if unknown:
        raise BizError(
            code="ERR_AUTHZ_UNKNOWN_CODE",
            message=f"未知权限码（目录未登记，不接受通配）: {', '.join(unknown)}",
            status_code=400,
        )


def _check_code_kinds(page_codes: list[str], action_codes: list[str]) -> None:
    """pageCodes/actionCodes 两列与目录 kind 必须一一对应（类型不符拒绝）。"""
    bad_pages = [c for c in page_codes if CATALOG[c].kind not in ("PAGE", "PAGE_GROUP")]
    if bad_pages:
        raise BizError(
            code="ERR_AUTHZ_CODE_KIND_MISMATCH",
            message=f"以下码不是页面码（PAGE/PAGE_GROUP）: {', '.join(bad_pages)}",
            status_code=400,
        )
    bad_actions = [c for c in action_codes if CATALOG[c].kind != "ACTION"]
    if bad_actions:
        raise BizError(
            code="ERR_AUTHZ_CODE_KIND_MISMATCH",
            message=f"以下码不是动作码（ACTION）: {', '.join(bad_actions)}",
            status_code=400,
        )


def _check_admin_protection(role: str, codes: frozenset[str] | set[str]) -> None:
    """最后管理员失权保护：protected 码不得从 ADMIN 矩阵移除。

    所有 ADMIN 用户共享角色矩阵——移除 protected 码即全体管理员同时
    失去关键管理能力（含"最后一名 ADMIN"场景），任何移除尝试直接拒绝。
    """
    if role != "ADMIN":
        return
    removed = sorted(PROTECTED_CODES - set(codes))
    if removed:
        raise BizError(
            code="ERR_AUTHZ_ADMIN_PROTECTED",
            message=(
                "管理员保护：以下关键权限码不可从 ADMIN 角色移除"
                f"（最后一名管理员失权保护）: {', '.join(removed)}"
            ),
            status_code=403,
        )


def validate_role_matrix(
    role: str, page_codes: list[str], action_codes: list[str]
) -> frozenset[str]:
    """完整校验链（纯函数）：未知码 → 类型不符 → 漏依赖 → 管理员保护。

    返回合并后的码集合。"""
    if role not in VALID_ROLES:
        raise BizError(
            code="ERR_AUTHZ_ROLE_NOT_FOUND",
            message=f"未知角色: {role}（有效: {', '.join(VALID_ROLES)}）",
            status_code=404,
        )
    merged = frozenset(page_codes) | frozenset(action_codes)
    _check_known_codes(merged)
    _check_code_kinds(page_codes, action_codes)
    missing = validate_grants(role, merged)
    if missing:
        detail = "; ".join(f"{code} 缺少依赖 {deps}" for code, deps in sorted(missing.items()))
        raise BizError(
            code="ERR_AUTHZ_MISSING_DEPENDENCY",
            message=f"漏依赖拒绝（写权限依赖必要读取码）: {detail}",
            status_code=400,
        )
    _check_admin_protection(role, merged)
    return merged


# ---------------------------------------------------------------------------
# 策略读取
# ---------------------------------------------------------------------------


async def get_current_revision(db: AsyncSession) -> int:
    """当前策略 revision（无账本行=0：尚未播种的空库）。"""
    result = await db.execute(select(func.max(SysAuthzPolicy.revision)))
    rev = result.scalar_one_or_none()
    return int(rev) if rev is not None else 0


async def get_role_grants(db: AsyncSession, role: str) -> dict[str, list[str]]:
    """角色当前矩阵（pages=PAGE+PAGE_GROUP / actions=ACTION）。"""
    result = await db.execute(
        select(SysRolePermission.permission_code).where(SysRolePermission.role == role)
    )
    codes = {row[0] for row in result.fetchall()}
    pages = sorted(c for c in codes if c in CATALOG and CATALOG[c].kind in ("PAGE", "PAGE_GROUP"))
    actions = sorted(c for c in codes if c in CATALOG and CATALOG[c].kind == "ACTION")
    return {"pages": pages, "actions": actions}


async def get_user_scope(db: AsyncSession, user_id: str) -> SysUserScope | None:
    result = await db.execute(select(SysUserScope).where(SysUserScope.user_id == user_id))
    return result.scalar_one_or_none()


def get_effective_access(
    role: str,
    codes: frozenset[str] | set[str],
    enabled_modules: set[str] | None = None,
) -> dict[str, Any]:
    """有效授权视图（依赖闭包展开 + 模块启用标注，纯函数）。

    - ``effectiveActions``: 依赖闭包展开后的动作码全集（授 A 自动补齐
      前置读码 B 是**展示层展开**，不新增被授操作——写操作本身仍逐码校验）
    - 每码附 ``moduleEnabled``：module disabled 的码不可用（授权只收紧
      不放宽；fitness L0/L1 等业务门禁由服务层另行执行，不因授权放宽）
    """
    closure = set(codes)
    changed = True
    while changed:
        changed = False
        for code in list(closure):
            for req in CATALOG.get(code, PermDef(kind="ACTION")).requires:
                if req not in closure:
                    closure.add(req)
                    changed = True
    actions = sorted(c for c in closure if c in CATALOG and CATALOG[c].kind == "ACTION")
    pages = sorted(c for c in codes if c in CATALOG and CATALOG[c].kind in ("PAGE", "PAGE_GROUP"))
    disabled_actions: list[str] = []
    disabled_pages: list[str] = []
    if enabled_modules is not None:
        for c in actions:
            mk = CATALOG[c].module_key
            if mk and mk not in enabled_modules:
                disabled_actions.append(c)
        for c in pages:
            mk = CATALOG[c].module_key
            if mk and mk not in enabled_modules:
                disabled_pages.append(c)
    return {
        "role": role,
        "pages": pages,
        "actions": actions,
        "unavailableByModule": {
            "actions": sorted(disabled_actions),
            "pages": sorted(disabled_pages),
        },
        "note": (
            "授权只收紧不放宽：module enabled 与 fitness 等业务门禁在授权之外"
            "独立执行（AND 语义），授权矩阵不产生放宽效果"
        ),
    }


# ---------------------------------------------------------------------------
# 策略变更（账本 + 审计 + revision 乐观锁）
# ---------------------------------------------------------------------------


def _revision_conflict(current: int) -> BizError:
    return BizError(
        code="ERR_AUTHZ_REVISION_CONFLICT",
        message=f"授权策略版本冲突，请刷新后重试（当前 revision={current}）",
        status_code=409,
        data={"currentRevision": current},
    )


async def _append_policy(
    db: AsyncSession,
    *,
    revision: int,
    operation: str,
    operator: str,
    reason: str | None,
    role: str | None = None,
    user_id: str | None = None,
    before: dict[str, Any] | list | None = None,
    after: dict[str, Any] | list | None = None,
    rollback_of: int | None = None,
) -> None:
    db.add(
        SysAuthzPolicy(
            revision=revision,
            operation=operation,
            role=role,
            user_id=user_id,
            reason=reason,
            operator=operator,
            before_value=before,
            after_value=after,
            rollback_of=rollback_of,
        )
    )
    db.add(
        SysAuditLog(
            id=str(uuid4()),
            operator=operator,
            operation_type=f"AUTHZ_{operation}",
            target_type="Role" if role else ("User" if user_id else "AuthzPolicy"),
            target_id=role or (str(user_id) if user_id else str(revision)),
            before_value=json.dumps(before, ensure_ascii=False) if before is not None else None,
            after_value=json.dumps(after, ensure_ascii=False) if after is not None else None,
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )


async def update_role_grants(
    db: AsyncSession,
    *,
    role: str,
    expected_revision: int,
    page_codes: list[str],
    action_codes: list[str],
    reason: str | None,
    operator: str,
) -> dict[str, Any]:
    """更新角色授权矩阵（expectedRevision 乐观锁 + 完整校验链 + 账本审计）。

    Raises:
        ERR_AUTHZ_ROLE_NOT_FOUND(404) / ERR_AUTHZ_UNKNOWN_CODE(400) /
        ERR_AUTHZ_CODE_KIND_MISMATCH(400) / ERR_AUTHZ_MISSING_DEPENDENCY(400) /
        ERR_AUTHZ_ADMIN_PROTECTED(403) / ERR_AUTHZ_REVISION_CONFLICT(409)
    """
    merged = validate_role_matrix(role, page_codes, action_codes)

    current = await get_current_revision(db)
    if expected_revision != current:
        raise _revision_conflict(current)

    before = await get_role_grants(db, role)

    try:
        await db.execute(delete(SysRolePermission).where(SysRolePermission.role == role))
        if merged:
            await db.execute(
                insert(SysRolePermission),
                [{"role": role, "permission_code": c} for c in sorted(merged)],
            )
        after = {
            "pages": sorted(c for c in merged if CATALOG[c].kind in ("PAGE", "PAGE_GROUP")),
            "actions": sorted(c for c in merged if CATALOG[c].kind == "ACTION"),
        }
        await _append_policy(
            db,
            revision=current + 1,
            operation="ROLE_GRANT",
            operator=operator,
            reason=reason,
            role=role,
            before={"revision": current, **before},
            after=after,
        )
        await db.commit()
    except IntegrityError as exc:  # 并发兜底：revision 主键冲突=另一方已提交
        await db.rollback()
        raise _revision_conflict(current) from exc

    return {
        "role": role,
        "revision": current + 1,
        "pages": after["pages"],
        "actions": after["actions"],
    }


async def update_user_scope(
    db: AsyncSession,
    *,
    user_id: str,
    expected_revision: int,
    scope_type: str,
    node_id: str | None,
    reason: str | None,
    operator: str,
) -> dict[str, Any]:
    """更新用户数据范围（ALL/NODE_SUBTREE + plant_node 绑定校验）。

    NODE_SUBTREE 必须携带存在的 plant_node；ALL 必须不带 node_id。
    """
    if scope_type not in SCOPE_TYPES:
        raise BizError(
            code="ERR_AUTHZ_SCOPE_INVALID",
            message=f"无效 scopeType: {scope_type}（有效: ALL/NODE_SUBTREE）",
            status_code=400,
        )
    if scope_type == "ALL" and node_id is not None:
        raise BizError(
            code="ERR_AUTHZ_SCOPE_INVALID",
            message="scopeType=ALL 不允许携带 nodeId",
            status_code=400,
        )
    if scope_type == "NODE_SUBTREE" and node_id is None:
        raise BizError(
            code="ERR_AUTHZ_SCOPE_INVALID",
            message="scopeType=NODE_SUBTREE 必须携带 nodeId（plant_node 绑定）",
            status_code=400,
        )

    user = (await db.execute(select(SysUser).where(SysUser.id == user_id))).scalar_one_or_none()
    if user is None:
        raise BizError(
            code="ERR_AUTHZ_USER_NOT_FOUND",
            message=f"用户不存在: {user_id}",
            status_code=404,
        )
    if scope_type == "NODE_SUBTREE":
        node = (
            await db.execute(select(PlantNode).where(PlantNode.id == node_id))
        ).scalar_one_or_none()
        if node is None:
            raise BizError(
                code="ERR_AUTHZ_SCOPE_INVALID",
                message=f"plant_node 不存在: {node_id}",
                status_code=400,
            )

    current = await get_current_revision(db)
    if expected_revision != current:
        raise _revision_conflict(current)

    existing = await get_user_scope(db, user_id)
    before = {"scopeType": existing.scope_type, "nodeId": existing.node_id} if existing else None

    try:
        if existing:
            existing.scope_type = scope_type
            existing.node_id = node_id
            existing.updated_by = operator
        else:
            db.add(
                SysUserScope(
                    user_id=user_id, scope_type=scope_type, node_id=node_id, updated_by=operator
                )
            )
        await _append_policy(
            db,
            revision=current + 1,
            operation="USER_SCOPE",
            operator=operator,
            reason=reason,
            user_id=user_id,
            before={"revision": current, **(before or {})},
            after={"scopeType": scope_type, "nodeId": node_id, "username": user.username},
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _revision_conflict(current) from exc

    return {
        "userId": user_id,
        "username": user.username,
        "revision": current + 1,
        "scopeType": scope_type,
        "nodeId": node_id,
    }


async def rollback_revision(
    db: AsyncSession,
    *,
    revision: int,
    expected_revision: int,
    reason: str,
    operator: str,
) -> dict[str, Any]:
    """回退=发布逆操作：恢复目标 revision 的 before 快照为新 revision。

    - SEED 行不可回退（初始态无逆操作）
    - 同一 revision 仅可回退一次（rollback_of 唯一约束）
    - 逆操作恢复的矩阵/快照仍走完整校验链（before 是历史合法状态；
      若与现行保护规则冲突则拒绝回退而非破坏保护）
    - 回退落独立 ROLLBACK 账本行（不删历史、不改写原行）
    """
    target = (
        await db.execute(select(SysAuthzPolicy).where(SysAuthzPolicy.revision == revision))
    ).scalar_one_or_none()
    if target is None:
        raise BizError(
            code="ERR_AUTHZ_REVISION_NOT_FOUND",
            message=f"策略版本不存在: revision={revision}",
            status_code=404,
        )
    if target.operation == "SEED":
        raise BizError(
            code="ERR_AUTHZ_PROTECTED_REVISION",
            message="初始种子版本不可回退",
            status_code=400,
        )
    if target.operation == "ROLLBACK":
        raise BizError(
            code="ERR_AUTHZ_PROTECTED_REVISION",
            message="回退产生的版本不可再回退（如需恢复请直接修改矩阵/scope）",
            status_code=400,
        )
    if target.operation == "CATALOG_EXTENSION":
        # P4-02 目录增量（如 alert:subscription-manage）随迁移落账本——
        # 目录扩展是系统基线变更而非角色策略操作，无逆操作可回退
        raise BizError(
            code="ERR_AUTHZ_PROTECTED_REVISION",
            message="目录扩展版本不可回退（系统基线变更，随迁移发布）",
            status_code=400,
        )
    already = (
        await db.execute(
            select(SysAuthzPolicy.revision).where(SysAuthzPolicy.rollback_of == revision)
        )
    ).scalar_one_or_none()
    if already is not None:
        raise BizError(
            code="ERR_AUTHZ_ALREADY_ROLLED_BACK",
            message=f"revision={revision} 已被回退（经 revision={already}），不可重复回退",
            status_code=400,
        )

    current = await get_current_revision(db)
    if expected_revision != current:
        raise _revision_conflict(current)

    before_snapshot = target.before_value or {}
    after: dict[str, Any]

    if target.operation == "ROLE_GRANT":
        assert target.role is not None
        page_codes = list(before_snapshot.get("pages", []))
        action_codes = list(before_snapshot.get("actions", []))
        merged = validate_role_matrix(target.role, page_codes, action_codes)
        prior = await get_role_grants(db, target.role)
        try:
            await db.execute(delete(SysRolePermission).where(SysRolePermission.role == target.role))
            if merged:
                await db.execute(
                    insert(SysRolePermission),
                    [{"role": target.role, "permission_code": c} for c in sorted(merged)],
                )
            after = {
                "role": target.role,
                "pages": sorted(c for c in merged if CATALOG[c].kind in ("PAGE", "PAGE_GROUP")),
                "actions": sorted(c for c in merged if CATALOG[c].kind == "ACTION"),
            }
            await _append_policy(
                db,
                revision=current + 1,
                operation="ROLLBACK",
                operator=operator,
                reason=f"rollback revision={revision}: {reason}",
                role=target.role,
                before={"revision": current, "role": target.role, **prior},
                after=after,
                rollback_of=revision,
            )
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise _revision_conflict(current) from exc
        return {"operation": "ROLLBACK", "revision": current + 1, "rollbackOf": revision, **after}

    # USER_SCOPE 逆操作
    assert target.user_id is not None
    scope_type = before_snapshot.get("scopeType", "ALL")
    node_id = before_snapshot.get("nodeId")
    user = (
        await db.execute(select(SysUser).where(SysUser.id == target.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise BizError(
            code="ERR_AUTHZ_USER_NOT_FOUND",
            message=f"用户不存在: {target.user_id}",
            status_code=404,
        )
    if scope_type == "NODE_SUBTREE" and node_id is None:
        raise BizError(
            code="ERR_AUTHZ_SCOPE_INVALID",
            message=f"revision={revision} 的 before 快照缺失 nodeId，无法恢复 NODE_SUBTREE",
            status_code=400,
        )
    existing = await get_user_scope(db, target.user_id)
    try:
        if existing:
            existing.scope_type = scope_type
            existing.node_id = node_id
            existing.updated_by = operator
        else:
            db.add(
                SysUserScope(
                    user_id=target.user_id,
                    scope_type=scope_type,
                    node_id=node_id,
                    updated_by=operator,
                )
            )
        after = {
            "userId": target.user_id,
            "username": user.username,
            "scopeType": scope_type,
            "nodeId": node_id,
        }
        await _append_policy(
            db,
            revision=current + 1,
            operation="ROLLBACK",
            operator=operator,
            reason=f"rollback revision={revision}: {reason}",
            user_id=target.user_id,
            before={
                "revision": current,
                "scopeType": existing.scope_type if existing else None,
                "nodeId": existing.node_id if existing else None,
            },
            after=after,
            rollback_of=revision,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _revision_conflict(current) from exc
    return {"operation": "ROLLBACK", "revision": current + 1, "rollbackOf": revision, **after}


__all__ = [
    "CATALOG",
    "PROTECTED_CODES",
    "ROLE_SEED",
    "SCOPE_TYPES",
    "VALID_ROLES",
    "get_current_revision",
    "get_effective_access",
    "get_role_grants",
    "get_user_scope",
    "rollback_revision",
    "update_role_grants",
    "update_user_scope",
    "validate_grants",
    "validate_role_matrix",
]
