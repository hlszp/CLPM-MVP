"""authorization catalog & persisted policy (P4-01)

Revision ID: p401authz01
Revises: p204engtpl01
Create Date: 2026-10-11

P4-01 授权目录与持久策略（方案 01 §3.5 / 03§7 P4-01 / DEC-04 五角色+两维度）：

1. 新表 ``sys_permission``：页面码/动作码目录（PAGE_GROUP/PAGE/ACTION；
   requires_codes 显式依赖；module_key 标注热插拔模块；is_protected=
   ADMIN 关键管理能力标记）。**无机械通配展开**——目录不含也不接受
   ``loop:*`` 型通配码。
2. 新表 ``sys_role_permission``：五角色授权矩阵，种子按 P1-04 实际裁决
   （2026-10-11 AUTH-02 止血后）落库：IC 历史 ``loop:*`` 等通配按真实
   require_roles 白名单展开为显式码集合（IC 无 loop:delete/batch-config）；
   SPONSOR 零写红线；ADMIN 显式全码（无 ``*``）。
3. 新表 ``sys_user_scope``：用户数据范围 ALL/NODE_SUBTREE + plant_node
   绑定；存量用户种子=ALL（迁移前无 scope 概念=全量可见，前后等效；
   贯穿消费属 P4-03）。
4. 新表 ``sys_authz_policy``：策略版本账本（不可变追加式；expectedRevision
   乐观锁 + 审计信封 + rollback_of 唯一约束防重复回退）；种子落 revision=1
   SEED 行。模式复用 P2-02 config_publication。

种子与引导 SQL（db/postgresql/01_schema.sql §9 / 02_seed_data.sql §P4-01）
同批同内容；一致性由 tests/integration/test_p4_01_real_pg.py 三方对照钉死
（迁移种子 == 引导 SQL 种子 == services.authz_catalog.CATALOG 常量）。

存量升级前后有效授权一致：矩阵码集=迁移前 ROLE_PERMISSIONS 语义展开+
前端 authority 矩阵（活链端点白名单），由单测
test_p4_01_authz_catalog.py::TestSeedParityWithP1_04 逐码断言。

downgrade：DROP 四表（幂等）。策略账本/矩阵仅本批新增数据，无存量
损失；scope 行同样整表删除。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p401authz01"
down_revision: str | None = "p204engtpl01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. 目录（页面码/动作码 + 显式依赖 + 模块标注 + 保护标记）
    # ------------------------------------------------------------------
    op.create_table(
        "sys_permission",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "parent_code",
            sa.String(64),
            sa.ForeignKey("sys_permission.code", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("requires_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("module_key", sa.String(20), nullable=True),
        sa.Column(
            "is_protected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ('PAGE_GROUP', 'PAGE', 'ACTION')", name="ck_sys_permission_kind"
        ),
        sa.CheckConstraint(
            "kind = 'PAGE' OR parent_code IS NULL", name="ck_sys_permission_parent_kind"
        ),
    )
    op.create_index("idx_sys_permission_kind", "sys_permission", ["kind"])
    op.create_index("idx_sys_permission_module_key", "sys_permission", ["module_key"])

    # ------------------------------------------------------------------
    # 2. 五角色授权矩阵
    # ------------------------------------------------------------------
    op.create_table(
        "sys_role_permission",
        sa.Column("role", sa.String(20), primary_key=True),
        sa.Column(
            "permission_code",
            sa.String(64),
            sa.ForeignKey("sys_permission.code", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("granted_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT')",
            name="ck_sys_role_permission_role",
        ),
    )
    op.create_index("idx_sys_role_permission_role", "sys_role_permission", ["role"])

    # ------------------------------------------------------------------
    # 3. 用户数据范围（ALL/NODE_SUBTREE + plant_node 绑定）
    # ------------------------------------------------------------------
    op.create_table(
        "sys_user_scope",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("sys_user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column(
            "node_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("plant_node.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("scope_type IN ('ALL', 'NODE_SUBTREE')", name="ck_sys_user_scope_type"),
        sa.CheckConstraint(
            "(scope_type = 'ALL' AND node_id IS NULL) "
            "OR (scope_type = 'NODE_SUBTREE' AND node_id IS NOT NULL)",
            name="ck_sys_user_scope_node",
        ),
    )

    # ------------------------------------------------------------------
    # 4. 策略版本账本（不可变追加式）
    # ------------------------------------------------------------------
    op.create_table(
        "sys_authz_policy",
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("operator", sa.String(50), nullable=False),
        sa.Column("before_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rollback_of", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "operation IN ('SEED', 'ROLE_GRANT', 'USER_SCOPE', 'ROLLBACK')",
            name="ck_sys_authz_policy_operation",
        ),
        sa.UniqueConstraint("rollback_of", name="uq_sys_authz_policy_rollback_of"),
    )
    op.create_index("idx_sys_authz_policy_operation", "sys_authz_policy", ["operation"])
    op.create_index("idx_sys_authz_policy_created_at", "sys_authz_policy", ["created_at"])

    # ------------------------------------------------------------------
    # 5. 种子：目录 + 五角色矩阵（P1-04 实际裁决）+ SEED 账本行
    #    + 存量用户 scope=ALL（与 services.authz_catalog.CATALOG 同批同内容）
    # ------------------------------------------------------------------
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected,"
        "description, sort_order) VALUES"
        "('page:cockpit', 'PAGE_GROUP', NULL, '[]'::jsonb, NULL, false, "
        "'驾驶舱（全局观察入口，不受模块开关约束）', 0),"
        "('page:workbench', 'PAGE_GROUP', NULL, '[]'::jsonb, NULL, false, "
        "'工作台（回路全生命周期单页）', 1),"
        "('page:monitor', 'PAGE_GROUP', NULL, '[]'::jsonb, 'monitor', false, '回路监控', 2),"
        "('page:assess', 'PAGE_GROUP', NULL, '[]'::jsonb, 'assess', false, '性能评估', 3),"
        "('page:diagnosis', 'PAGE_GROUP', NULL, '[]'::jsonb, 'diagnosis', false, '回路诊断', 4),"
        "('page:tuning', 'PAGE_GROUP', NULL, '[]'::jsonb, 'tuning', false, '参数整定', 5),"
        "('page:handling', 'PAGE_GROUP', NULL, '[]'::jsonb, 'handling', false, '运维处置', 6),"
        "('page:reports', 'PAGE_GROUP', NULL, '[]'::jsonb, 'reports', false, '统计分析', 7),"
        "('page:config', 'PAGE_GROUP', NULL, '[]'::jsonb, 'config', false, '参数配置', 8),"
        "('page:system', 'PAGE_GROUP', NULL, '[]'::jsonb, 'system', false, '系统管理', 9),"
        "('page:cockpit.overview', 'PAGE', 'page:cockpit', '[]'::jsonb, NULL, false, "
        "'驾驶舱-总览｜五角色', 10),"
        "('page:cockpit.loops', 'PAGE', 'page:cockpit', '[]'::jsonb, 'monitor', false, "
        "'驾驶舱-回路｜五角色', 20),"
        "('page:cockpit.performance', 'PAGE', 'page:cockpit', '[]'::jsonb, 'assess', false, "
        "'驾驶舱-性能｜五角色', 30),"
        "('page:cockpit.diagnosis', 'PAGE', 'page:cockpit', '[]'::jsonb, 'diagnosis', false, "
        "'驾驶舱-诊断｜五角色', 40),"
        "('page:cockpit.tuning', 'PAGE', 'page:cockpit', '[]'::jsonb, 'tuning', false, "
        "'驾驶舱-整定｜五角色', 50),"
        "('page:cockpit.handling', 'PAGE', 'page:cockpit', '[]'::jsonb, 'handling', false, "
        "'驾驶舱-处置｜五角色', 60),"
        "('page:workbench.main', 'PAGE', 'page:workbench', '[]'::jsonb, NULL, false, "
        "'工作台主页（回路 360 全生命周期）｜ADMIN/IC/PE/EXPERT', 10),"
        "('page:monitor.loops', 'PAGE', 'page:monitor', '[]'::jsonb, 'monitor', false, "
        "'回路监视｜五角色', 10),"
        "('page:monitor.alerts', 'PAGE', 'page:monitor', '[]'::jsonb, 'monitor', false, "
        "'预警事件｜五角色', 20),"
        "('page:monitor.attention', 'PAGE', 'page:monitor', '[]'::jsonb, 'monitor', false, "
        "'关注队列｜五角色', 30),"
        "('page:monitor.table', 'PAGE', 'page:monitor', '[]'::jsonb, 'monitor', false, "
        "'回路实时表格｜ADMIN/IC/PE', 40),"
        "('page:alert.rules', 'PAGE', 'page:monitor', '[]'::jsonb, 'monitor', false, "
        "'预警规则管理（旧路径）｜ADMIN', 50),"
        "('page:assess.dashboard', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'装置性能｜ADMIN/IC/PE/SPONSOR', 10),"
        "('page:assess.indicator', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'指标分析｜ADMIN/IC/PE/SPONSOR', 20),"
        "('page:assess.loop-evaluation', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'回路评估｜ADMIN/IC/PE/SPONSOR', 30),"
        "('page:assess.performance', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'回路性能｜ADMIN/IC/PE/SPONSOR', 40),"
        "('page:assess.tasks', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'评估任务｜ADMIN/IC', 50),"
        "('page:assess.history', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'评估记录｜ADMIN/IC', 60),"
        "('page:assess.matrix', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'指标矩阵｜ADMIN/IC/PE/SPONSOR', 70),"
        "('page:assess.kpi-report', 'PAGE', 'page:assess', '[]'::jsonb, 'assess', false, "
        "'绩效报告｜ADMIN/IC/PE/SPONSOR', 80),"
        "('page:diagnosis.overview', 'PAGE', 'page:diagnosis', '[]'::jsonb, 'diagnosis', false, "
        "'诊断概览｜五角色', 10),"
        "('page:diagnosis.tasks', 'PAGE', 'page:diagnosis', '[]'::jsonb, 'diagnosis', false, "
        "'诊断任务｜五角色', 20),"
        "('page:diagnosis.records', 'PAGE', 'page:diagnosis', '[]'::jsonb, 'diagnosis', false, "
        "'诊断记录｜五角色', 30),"
        "('page:tuning.overview', 'PAGE', 'page:tuning', '[]'::jsonb, 'tuning', false, "
        "'整定总览｜五角色', 10),"
        "('page:tuning.records', 'PAGE', 'page:tuning', '[]'::jsonb, 'tuning', false, "
        "'整定记录｜五角色', 20),"
        "('page:tuning.verification', 'PAGE', 'page:tuning', '[]'::jsonb, 'tuning', false, "
        "'效果验证｜五角色', 30),"
        "('page:handling.suggestions', 'PAGE', 'page:handling', '[]'::jsonb, 'handling', false, "
        "'诊断建议｜五角色', 10),"
        "('page:handling.tasks', 'PAGE', 'page:handling', '[]'::jsonb, 'handling', false, "
        "'处置任务｜五角色', 20),"
        "('page:handling.orders', 'PAGE', 'page:handling', '[]'::jsonb, 'handling', false, "
        "'处置工单｜五角色', 30),"
        "('page:handling.archive', 'PAGE', 'page:handling', '[]'::jsonb, 'handling', false, "
        "'处置档案｜五角色', 40)"
        "\nON CONFLICT (code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected,"
        "description, sort_order) VALUES"
        "('page:handling.workbench', 'PAGE', 'page:handling', '[]'::jsonb, 'handling', false, "
        "'处置工作台｜五角色', 50),"
        "('page:handling.statistics', 'PAGE', 'page:handling', '[]'::jsonb, 'handling', false, "
        "'处置报告｜五角色', 60),"
        "('page:reports.performance', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false, "
        "'绩效报告｜五角色', 10),"
        "('page:reports.data-quality', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false, "
        "'数据质量｜五角色', 20),"
        "('page:reports.alert-statistics', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false,"
        " '预警统计｜五角色', 30),"
        "('page:reports.diagnosis', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false, "
        "'诊断报告｜五角色', 40),"
        "('page:reports.handling', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false, "
        "'处置报告｜五角色', 50),"
        "('page:reports.benefit', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false, "
        "'收益报告｜ADMIN/IC/PE/SPONSOR', 60),"
        "('page:reports.subscription', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false, "
        "'订阅配置｜ADMIN', 70),"
        "('page:config.link', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'链路配置｜ADMIN', 10),"
        "('page:config.tag', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'测点配置｜ADMIN/IC/PE', 20),"
        "('page:config.loop', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'回路配置｜ADMIN/IC/PE', 30),"
        "('page:config.ledger', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'回路台账｜ADMIN/IC/PE', 40),"
        "('page:config.factory', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'工厂配置｜ADMIN/IC/PE', 50),"
        "('page:config.datasource', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'数据导入｜ADMIN/IC/PE', 60),"
        "('page:config.metric', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'指标配置｜ADMIN', 70),"
        "('page:config.diagnosis', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'诊断配置｜ADMIN', 80),"
        "('page:config.alert-rules', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, "
        "'预警规则｜ADMIN', 90),"
        "('page:config.pid-template', 'PAGE', 'page:config', '[]'::jsonb, 'config', false, 'PID "
        "结构模板｜ADMIN', 100),"
        "('page:system.basic-info', 'PAGE', 'page:system', '[]'::jsonb, 'system', false, "
        "'基础信息｜ADMIN', 10),"
        "('page:system.users', 'PAGE', 'page:system', '[]'::jsonb, 'system', true, "
        "'用户管理｜ADMIN（protected）', 20),"
        "('page:system.audit', 'PAGE', 'page:system', '[]'::jsonb, 'system', true, "
        "'审计日志｜ADMIN（protected）', 30),"
        "('page:system.dict', 'PAGE', 'page:system', '[]'::jsonb, 'system', false, "
        "'字典管理｜ADMIN', 40),"
        "('page:system.permissions', 'PAGE', 'page:system', '[]'::jsonb, 'system', true, "
        "'权限矩阵｜ADMIN（protected）', 50),"
        "('page:system.modules', 'PAGE', 'page:system', '[]'::jsonb, 'system', true, "
        "'模块管理｜ADMIN（protected）', 60),"
        "('page:system.reports', 'PAGE', 'page:system', '[]'::jsonb, 'system', false, "
        "'订阅配置｜ADMIN', 70),"
        "('page:system.algorithm-params', 'PAGE', 'page:system', '[]'::jsonb, 'system', false, "
        "'算法参数配置｜ADMIN', 80),"
        "('page:task.detail', 'PAGE', 'page:assess', '[]'::jsonb, NULL, false, "
        "'任务详情（横切）｜ADMIN/IC/PE', 200),"
        "('portal:view', 'ACTION', NULL, '[]'::jsonb, NULL, false, '门户/驾驶舱读', 900),"
        "('loop:view', 'ACTION', NULL, '[]'::jsonb, 'monitor', false, '回路读', 901),"
        "('metric:view', 'ACTION', NULL, '[]'::jsonb, 'assess', false, '性能评估读', 902),"
        "('diagnosis:view', 'ACTION', NULL, '[]'::jsonb, 'diagnosis', false, '诊断读', 903),"
        "('tuning:view', 'ACTION', NULL, '[]'::jsonb, 'tuning', false, '整定读', 904),"
        "('alert:view', 'ACTION', NULL, '[]'::jsonb, 'monitor', false, '预警读', 905),"
        "('handling:view', 'ACTION', NULL, '[]'::jsonb, 'handling', false, '处置读', 906),"
        "('tracker:view', 'ACTION', NULL, '[]'::jsonb, 'handling', false, '处置建议分派读', 907),"
        "('tracker:review', 'ACTION', NULL, '[]'::jsonb, 'handling', false, "
        "'处置建议复核读（EXPERT 专属读）', 908),"
        "('config:view', 'ACTION', NULL, '[]'::jsonb, 'config', false, '参数配置域读', 909),"
        "('system:view', 'ACTION', NULL, '[]'::jsonb, 'system', false, '系统管理域读', 910),"
        "('loop:create', 'ACTION', NULL, '[\"loop:view\"]'::jsonb, 'config', false, '回路创建', "
        "1000)"
        "\nON CONFLICT (code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected,"
        "description, sort_order) VALUES"
        "('loop:edit', 'ACTION', NULL, '[\"loop:view\"]'::jsonb, 'config', false, '回路编辑', "
        "1001),"
        "('loop:export', 'ACTION', NULL, '[\"loop:view\"]'::jsonb, 'config', false, '回路导出', "
        "1002),"
        "('loop:import', 'ACTION', NULL, '[\"loop:view\"]'::jsonb, 'config', false, '回路导入', "
        "1003),"
        "('loop:batch-config', 'ACTION', NULL, '[\"loop:view\"]'::jsonb, 'config', false, "
        "'回路批量配置（ADMIN）', 1004),"
        "('loop:batch-grouping', 'ACTION', NULL, '[\"loop:view\"]'::jsonb, 'config', false, "
        "'回路批量分组', 1005),"
        "('loop:delete', 'ACTION', NULL, '[\"loop:view\"]'::jsonb, 'config', false, "
        "'回路删除（ADMIN 专属）', 1006),"
        "('metric:task-run', 'ACTION', NULL, '[\"metric:view\"]'::jsonb, 'assess', false, "
        "'发起/启动评估任务', 1100),"
        "('metric:task-manage', 'ACTION', NULL, '[\"metric:view\"]'::jsonb, 'assess', false, "
        "'取消/删除评估任务', 1101),"
        "('metric:snapshot-manage', 'ACTION', NULL, '[\"metric:view\"]'::jsonb, 'assess', false, "
        "'性能快照删除', 1102),"
        "('metric-config:manage', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', false, "
        "'指标定义/权重/阈值/置信度等配置写', 1103),"
        "('diagnosis:trigger', 'ACTION', NULL, '[\"diagnosis:view\"]'::jsonb, 'diagnosis', false,"
        " '发起诊断', 1200),"
        "('diagnosis:task-manage', 'ACTION', NULL, '[\"diagnosis:view\"]'::jsonb, 'diagnosis', "
        "false, '诊断任务运行/取消/归档/删除', 1201),"
        "('diagnosis:review', 'ACTION', NULL, '[\"diagnosis:view\"]'::jsonb, 'diagnosis', false, "
        "'诊断结论复核/处置动作', 1202),"
        "('diagnosis:interpret', 'ACTION', NULL, '[\"diagnosis:view\"]'::jsonb, 'diagnosis', "
        "false, '诊断解释/报告/导出', 1203),"
        "('diagnosis:threshold-override', 'ACTION', NULL, '[\"diagnosis:view\"]'::jsonb, "
        "'diagnosis', false, '诊断阈值覆盖/模板应用', 1204),"
        "('diagnosis-config:manage', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', false,"
        " '诊断配置写（指标/规则/触发配置）', 1205),"
        "('tuning:identify', 'ACTION', NULL, '[\"tuning:view\"]'::jsonb, 'tuning', false, "
        "'发起辨识', 1300),"
        "('tuning:tune', 'ACTION', NULL, '[\"tuning:view\"]'::jsonb, 'tuning', false, '发起整定',"
        " 1301),"
        "('tuning:simulate', 'ACTION', NULL, '[\"tuning:view\"]'::jsonb, 'tuning', false, "
        "'闭环仿真/方案对比', 1302),"
        "('tuning:task-create', 'ACTION', NULL, '[\"tuning:view\"]'::jsonb, 'tuning', false, "
        "'创建整定批量任务', 1303),"
        "('tuning:task-manage', 'ACTION', NULL, '[\"tuning:view\"]'::jsonb, 'tuning', false, "
        "'取消整定任务', 1304),"
        "('tuning:advanced', 'ACTION', NULL, '[\"tuning:view\"]'::jsonb, 'tuning', false, "
        "'高级参数字段（ADMIN/EXPERT；fitness 门禁不因授权放宽）', 1305),"
        "('handling:operate', 'ACTION', NULL, '[\"handling:view\"]'::jsonb, 'handling', false, "
        "'建议流转/工单全流转操作', 1400),"
        "('tracker:operate', 'ACTION', NULL, '[\"tracker:view\"]'::jsonb, 'handling', false, "
        "'处置建议分派写', 1401),"
        "('alert:rule-manage', 'ACTION', NULL, '[\"alert:view\"]'::jsonb, 'monitor', false, "
        "'预警规则 CRUD/启停（ADMIN）', 1500),"
        "('alert:event-handle', 'ACTION', NULL, '[\"alert:view\"]'::jsonb, 'monitor', false, "
        "'预警事件确认/解决/误报', 1501),"
        "('alert:suppression', 'ACTION', NULL, '[\"alert:view\"]'::jsonb, 'monitor', false, "
        "'预警抑制管理', 1502),"
        "('alert:dry-run', 'ACTION', NULL, '[\"alert:view\"]'::jsonb, 'monitor', false, "
        "'预警规则试运行', 1503),"
        "('config:publish', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', false, "
        "'分层配置发布/重置/回退（ADMIN，DEC-03）', 1600),"
        "('tag:manage', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', false, 'Tag 注册 "
        "CRUD/导入', 1601),"
        "('plant:manage', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', false, '工厂节点 "
        "CRUD/导入导出', 1602),"
        "('dcs:manage', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', false, 'DCS "
        "品牌/型号/MODE 配置写', 1603),"
        "('data:manage', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', false, "
        "'数据源配置/历史导入/DataPlanner/工厂同步', 1604),"
        "('ai:insight', 'ACTION', NULL, '[\"diagnosis:view\"]'::jsonb, NULL, false, 'AI "
        "洞察生成', 1605),"
        "('system:user-manage', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', true, "
        "'用户 CRUD/重置密码（protected）', 1700),"
        "('system:authorization-manage', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', "
        "true, '角色矩阵/用户 scope/回退管理（protected）', 1701),"
        "('system:module-manage', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', true, "
        "'模块启停（protected）', 1702),"
        "('system:dict-manage', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', false, "
        "'字典 CRUD', 1703),"
        "('system:basic-manage', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', false, "
        "'基础信息/LOGO 配置', 1704),"
        "('system:audit-view', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', true, "
        "'审计日志查询/导出（protected）', 1705)"
        "\nON CONFLICT (code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected,"
        "description, sort_order) VALUES"
        "('system:algorithm-params', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', false,"
        " '算法参数 PUT 兼容通道', 1706),"
        "('system:report-config', 'ACTION', NULL, '[\"system:view\"]'::jsonb, 'system', false, "
        "'报表配置/生成/阶段锁', 1707)"
        "\nON CONFLICT (code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'ai:insight'),"
        "('ADMIN', 'alert:dry-run'),"
        "('ADMIN', 'alert:event-handle'),"
        "('ADMIN', 'alert:rule-manage'),"
        "('ADMIN', 'alert:suppression'),"
        "('ADMIN', 'alert:view'),"
        "('ADMIN', 'config:publish'),"
        "('ADMIN', 'config:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'data:manage'),"
        "('ADMIN', 'dcs:manage'),"
        "('ADMIN', 'diagnosis-config:manage'),"
        "('ADMIN', 'diagnosis:interpret'),"
        "('ADMIN', 'diagnosis:review'),"
        "('ADMIN', 'diagnosis:task-manage'),"
        "('ADMIN', 'diagnosis:threshold-override'),"
        "('ADMIN', 'diagnosis:trigger')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'diagnosis:view'),"
        "('ADMIN', 'handling:operate'),"
        "('ADMIN', 'handling:view'),"
        "('ADMIN', 'loop:batch-config'),"
        "('ADMIN', 'loop:batch-grouping'),"
        "('ADMIN', 'loop:create'),"
        "('ADMIN', 'loop:delete'),"
        "('ADMIN', 'loop:edit')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'loop:export'),"
        "('ADMIN', 'loop:import'),"
        "('ADMIN', 'loop:view'),"
        "('ADMIN', 'metric-config:manage'),"
        "('ADMIN', 'metric:snapshot-manage'),"
        "('ADMIN', 'metric:task-manage'),"
        "('ADMIN', 'metric:task-run'),"
        "('ADMIN', 'metric:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:alert.rules'),"
        "('ADMIN', 'page:assess.dashboard'),"
        "('ADMIN', 'page:assess.history'),"
        "('ADMIN', 'page:assess.indicator'),"
        "('ADMIN', 'page:assess.kpi-report'),"
        "('ADMIN', 'page:assess.loop-evaluation'),"
        "('ADMIN', 'page:assess.matrix'),"
        "('ADMIN', 'page:assess.performance')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:assess.tasks'),"
        "('ADMIN', 'page:cockpit.diagnosis'),"
        "('ADMIN', 'page:cockpit.handling'),"
        "('ADMIN', 'page:cockpit.loops'),"
        "('ADMIN', 'page:cockpit.overview'),"
        "('ADMIN', 'page:cockpit.performance'),"
        "('ADMIN', 'page:cockpit.tuning'),"
        "('ADMIN', 'page:config.alert-rules')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:config.datasource'),"
        "('ADMIN', 'page:config.diagnosis'),"
        "('ADMIN', 'page:config.factory'),"
        "('ADMIN', 'page:config.ledger'),"
        "('ADMIN', 'page:config.link'),"
        "('ADMIN', 'page:config.loop'),"
        "('ADMIN', 'page:config.metric'),"
        "('ADMIN', 'page:config.pid-template')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:config.tag'),"
        "('ADMIN', 'page:diagnosis.overview'),"
        "('ADMIN', 'page:diagnosis.records'),"
        "('ADMIN', 'page:diagnosis.tasks'),"
        "('ADMIN', 'page:handling.archive'),"
        "('ADMIN', 'page:handling.orders'),"
        "('ADMIN', 'page:handling.statistics'),"
        "('ADMIN', 'page:handling.suggestions')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:handling.tasks'),"
        "('ADMIN', 'page:handling.workbench'),"
        "('ADMIN', 'page:monitor.alerts'),"
        "('ADMIN', 'page:monitor.attention'),"
        "('ADMIN', 'page:monitor.loops'),"
        "('ADMIN', 'page:monitor.table'),"
        "('ADMIN', 'page:reports.alert-statistics'),"
        "('ADMIN', 'page:reports.benefit')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:reports.data-quality'),"
        "('ADMIN', 'page:reports.diagnosis'),"
        "('ADMIN', 'page:reports.handling'),"
        "('ADMIN', 'page:reports.performance'),"
        "('ADMIN', 'page:reports.subscription'),"
        "('ADMIN', 'page:system.algorithm-params'),"
        "('ADMIN', 'page:system.audit'),"
        "('ADMIN', 'page:system.basic-info')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:system.dict'),"
        "('ADMIN', 'page:system.modules'),"
        "('ADMIN', 'page:system.permissions'),"
        "('ADMIN', 'page:system.reports'),"
        "('ADMIN', 'page:system.users'),"
        "('ADMIN', 'page:task.detail'),"
        "('ADMIN', 'page:tuning.overview'),"
        "('ADMIN', 'page:tuning.records')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:tuning.verification'),"
        "('ADMIN', 'page:workbench.main'),"
        "('ADMIN', 'plant:manage'),"
        "('ADMIN', 'portal:view'),"
        "('ADMIN', 'system:algorithm-params'),"
        "('ADMIN', 'system:audit-view'),"
        "('ADMIN', 'system:authorization-manage'),"
        "('ADMIN', 'system:basic-manage')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'system:dict-manage'),"
        "('ADMIN', 'system:module-manage'),"
        "('ADMIN', 'system:report-config'),"
        "('ADMIN', 'system:user-manage'),"
        "('ADMIN', 'system:view'),"
        "('ADMIN', 'tag:manage'),"
        "('ADMIN', 'tracker:operate'),"
        "('ADMIN', 'tracker:review')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'tracker:view'),"
        "('ADMIN', 'tuning:advanced'),"
        "('ADMIN', 'tuning:identify'),"
        "('ADMIN', 'tuning:simulate'),"
        "('ADMIN', 'tuning:task-create'),"
        "('ADMIN', 'tuning:task-manage'),"
        "('ADMIN', 'tuning:tune'),"
        "('ADMIN', 'tuning:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'ai:insight'),"
        "('IC_ENGINEER', 'alert:dry-run'),"
        "('IC_ENGINEER', 'alert:event-handle'),"
        "('IC_ENGINEER', 'alert:suppression'),"
        "('IC_ENGINEER', 'alert:view'),"
        "('IC_ENGINEER', 'config:view'),"
        "('IC_ENGINEER', 'diagnosis:interpret'),"
        "('IC_ENGINEER', 'diagnosis:review')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'diagnosis:task-manage'),"
        "('IC_ENGINEER', 'diagnosis:threshold-override'),"
        "('IC_ENGINEER', 'diagnosis:trigger'),"
        "('IC_ENGINEER', 'diagnosis:view'),"
        "('IC_ENGINEER', 'handling:operate'),"
        "('IC_ENGINEER', 'handling:view'),"
        "('IC_ENGINEER', 'loop:batch-grouping'),"
        "('IC_ENGINEER', 'loop:create')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'loop:edit'),"
        "('IC_ENGINEER', 'loop:export'),"
        "('IC_ENGINEER', 'loop:import'),"
        "('IC_ENGINEER', 'loop:view'),"
        "('IC_ENGINEER', 'metric:snapshot-manage'),"
        "('IC_ENGINEER', 'metric:task-manage'),"
        "('IC_ENGINEER', 'metric:task-run'),"
        "('IC_ENGINEER', 'metric:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'page:assess.dashboard'),"
        "('IC_ENGINEER', 'page:assess.history'),"
        "('IC_ENGINEER', 'page:assess.indicator'),"
        "('IC_ENGINEER', 'page:assess.kpi-report'),"
        "('IC_ENGINEER', 'page:assess.loop-evaluation'),"
        "('IC_ENGINEER', 'page:assess.matrix'),"
        "('IC_ENGINEER', 'page:assess.performance'),"
        "('IC_ENGINEER', 'page:assess.tasks')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'page:cockpit.diagnosis'),"
        "('IC_ENGINEER', 'page:cockpit.handling'),"
        "('IC_ENGINEER', 'page:cockpit.loops'),"
        "('IC_ENGINEER', 'page:cockpit.overview'),"
        "('IC_ENGINEER', 'page:cockpit.performance'),"
        "('IC_ENGINEER', 'page:cockpit.tuning'),"
        "('IC_ENGINEER', 'page:config.datasource'),"
        "('IC_ENGINEER', 'page:config.factory')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'page:config.ledger'),"
        "('IC_ENGINEER', 'page:config.loop'),"
        "('IC_ENGINEER', 'page:config.tag'),"
        "('IC_ENGINEER', 'page:diagnosis.overview'),"
        "('IC_ENGINEER', 'page:diagnosis.records'),"
        "('IC_ENGINEER', 'page:diagnosis.tasks'),"
        "('IC_ENGINEER', 'page:handling.archive'),"
        "('IC_ENGINEER', 'page:handling.orders')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'page:handling.statistics'),"
        "('IC_ENGINEER', 'page:handling.suggestions'),"
        "('IC_ENGINEER', 'page:handling.tasks'),"
        "('IC_ENGINEER', 'page:handling.workbench'),"
        "('IC_ENGINEER', 'page:monitor.alerts'),"
        "('IC_ENGINEER', 'page:monitor.attention'),"
        "('IC_ENGINEER', 'page:monitor.loops'),"
        "('IC_ENGINEER', 'page:monitor.table')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'page:reports.alert-statistics'),"
        "('IC_ENGINEER', 'page:reports.benefit'),"
        "('IC_ENGINEER', 'page:reports.data-quality'),"
        "('IC_ENGINEER', 'page:reports.diagnosis'),"
        "('IC_ENGINEER', 'page:reports.handling'),"
        "('IC_ENGINEER', 'page:reports.performance'),"
        "('IC_ENGINEER', 'page:task.detail'),"
        "('IC_ENGINEER', 'page:tuning.overview')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'page:tuning.records'),"
        "('IC_ENGINEER', 'page:tuning.verification'),"
        "('IC_ENGINEER', 'page:workbench.main'),"
        "('IC_ENGINEER', 'portal:view'),"
        "('IC_ENGINEER', 'tag:manage'),"
        "('IC_ENGINEER', 'tuning:identify'),"
        "('IC_ENGINEER', 'tuning:simulate'),"
        "('IC_ENGINEER', 'tuning:task-create')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('IC_ENGINEER', 'tuning:task-manage'),"
        "('IC_ENGINEER', 'tuning:tune'),"
        "('IC_ENGINEER', 'tuning:view'),"
        "('PE_ENGINEER', 'ai:insight'),"
        "('PE_ENGINEER', 'alert:view'),"
        "('PE_ENGINEER', 'config:view'),"
        "('PE_ENGINEER', 'diagnosis:interpret'),"
        "('PE_ENGINEER', 'diagnosis:review')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'diagnosis:task-manage'),"
        "('PE_ENGINEER', 'diagnosis:trigger'),"
        "('PE_ENGINEER', 'diagnosis:view'),"
        "('PE_ENGINEER', 'handling:operate'),"
        "('PE_ENGINEER', 'handling:view'),"
        "('PE_ENGINEER', 'loop:create'),"
        "('PE_ENGINEER', 'loop:edit'),"
        "('PE_ENGINEER', 'loop:export')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'loop:view'),"
        "('PE_ENGINEER', 'metric:task-manage'),"
        "('PE_ENGINEER', 'metric:task-run'),"
        "('PE_ENGINEER', 'metric:view'),"
        "('PE_ENGINEER', 'page:assess.dashboard'),"
        "('PE_ENGINEER', 'page:assess.indicator'),"
        "('PE_ENGINEER', 'page:assess.kpi-report'),"
        "('PE_ENGINEER', 'page:assess.loop-evaluation')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'page:assess.matrix'),"
        "('PE_ENGINEER', 'page:assess.performance'),"
        "('PE_ENGINEER', 'page:cockpit.diagnosis'),"
        "('PE_ENGINEER', 'page:cockpit.handling'),"
        "('PE_ENGINEER', 'page:cockpit.loops'),"
        "('PE_ENGINEER', 'page:cockpit.overview'),"
        "('PE_ENGINEER', 'page:cockpit.performance'),"
        "('PE_ENGINEER', 'page:cockpit.tuning')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'page:config.datasource'),"
        "('PE_ENGINEER', 'page:config.factory'),"
        "('PE_ENGINEER', 'page:config.ledger'),"
        "('PE_ENGINEER', 'page:config.loop'),"
        "('PE_ENGINEER', 'page:config.tag'),"
        "('PE_ENGINEER', 'page:diagnosis.overview'),"
        "('PE_ENGINEER', 'page:diagnosis.records'),"
        "('PE_ENGINEER', 'page:diagnosis.tasks')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'page:handling.archive'),"
        "('PE_ENGINEER', 'page:handling.orders'),"
        "('PE_ENGINEER', 'page:handling.statistics'),"
        "('PE_ENGINEER', 'page:handling.suggestions'),"
        "('PE_ENGINEER', 'page:handling.tasks'),"
        "('PE_ENGINEER', 'page:handling.workbench'),"
        "('PE_ENGINEER', 'page:monitor.alerts'),"
        "('PE_ENGINEER', 'page:monitor.attention')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'page:monitor.loops'),"
        "('PE_ENGINEER', 'page:monitor.table'),"
        "('PE_ENGINEER', 'page:reports.alert-statistics'),"
        "('PE_ENGINEER', 'page:reports.benefit'),"
        "('PE_ENGINEER', 'page:reports.data-quality'),"
        "('PE_ENGINEER', 'page:reports.diagnosis'),"
        "('PE_ENGINEER', 'page:reports.handling'),"
        "('PE_ENGINEER', 'page:reports.performance')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'page:task.detail'),"
        "('PE_ENGINEER', 'page:tuning.overview'),"
        "('PE_ENGINEER', 'page:tuning.records'),"
        "('PE_ENGINEER', 'page:tuning.verification'),"
        "('PE_ENGINEER', 'page:workbench.main'),"
        "('PE_ENGINEER', 'portal:view'),"
        "('PE_ENGINEER', 'tracker:operate'),"
        "('PE_ENGINEER', 'tracker:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('PE_ENGINEER', 'tuning:identify'),"
        "('PE_ENGINEER', 'tuning:simulate'),"
        "('PE_ENGINEER', 'tuning:task-create'),"
        "('PE_ENGINEER', 'tuning:task-manage'),"
        "('PE_ENGINEER', 'tuning:tune'),"
        "('PE_ENGINEER', 'tuning:view'),"
        "('SPONSOR', 'alert:view'),"
        "('SPONSOR', 'diagnosis:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('SPONSOR', 'handling:view'),"
        "('SPONSOR', 'loop:view'),"
        "('SPONSOR', 'metric:view'),"
        "('SPONSOR', 'page:assess.dashboard'),"
        "('SPONSOR', 'page:assess.indicator'),"
        "('SPONSOR', 'page:assess.kpi-report'),"
        "('SPONSOR', 'page:assess.loop-evaluation'),"
        "('SPONSOR', 'page:assess.matrix')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('SPONSOR', 'page:assess.performance'),"
        "('SPONSOR', 'page:cockpit.diagnosis'),"
        "('SPONSOR', 'page:cockpit.handling'),"
        "('SPONSOR', 'page:cockpit.loops'),"
        "('SPONSOR', 'page:cockpit.overview'),"
        "('SPONSOR', 'page:cockpit.performance'),"
        "('SPONSOR', 'page:cockpit.tuning'),"
        "('SPONSOR', 'page:diagnosis.overview')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('SPONSOR', 'page:diagnosis.records'),"
        "('SPONSOR', 'page:diagnosis.tasks'),"
        "('SPONSOR', 'page:handling.archive'),"
        "('SPONSOR', 'page:handling.orders'),"
        "('SPONSOR', 'page:handling.statistics'),"
        "('SPONSOR', 'page:handling.suggestions'),"
        "('SPONSOR', 'page:handling.tasks'),"
        "('SPONSOR', 'page:handling.workbench')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('SPONSOR', 'page:monitor.alerts'),"
        "('SPONSOR', 'page:monitor.attention'),"
        "('SPONSOR', 'page:monitor.loops'),"
        "('SPONSOR', 'page:reports.alert-statistics'),"
        "('SPONSOR', 'page:reports.benefit'),"
        "('SPONSOR', 'page:reports.data-quality'),"
        "('SPONSOR', 'page:reports.diagnosis'),"
        "('SPONSOR', 'page:reports.handling')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('SPONSOR', 'page:reports.performance'),"
        "('SPONSOR', 'page:tuning.overview'),"
        "('SPONSOR', 'page:tuning.records'),"
        "('SPONSOR', 'page:tuning.verification'),"
        "('SPONSOR', 'portal:view'),"
        "('SPONSOR', 'tuning:view'),"
        "('EXPERT', 'ai:insight'),"
        "('EXPERT', 'alert:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('EXPERT', 'diagnosis:review'),"
        "('EXPERT', 'diagnosis:task-manage'),"
        "('EXPERT', 'diagnosis:trigger'),"
        "('EXPERT', 'diagnosis:view'),"
        "('EXPERT', 'handling:view'),"
        "('EXPERT', 'loop:view'),"
        "('EXPERT', 'metric:view'),"
        "('EXPERT', 'page:assess.dashboard')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('EXPERT', 'page:assess.indicator'),"
        "('EXPERT', 'page:assess.kpi-report'),"
        "('EXPERT', 'page:assess.loop-evaluation'),"
        "('EXPERT', 'page:assess.matrix'),"
        "('EXPERT', 'page:assess.performance'),"
        "('EXPERT', 'page:cockpit.diagnosis'),"
        "('EXPERT', 'page:cockpit.handling'),"
        "('EXPERT', 'page:cockpit.loops')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('EXPERT', 'page:cockpit.overview'),"
        "('EXPERT', 'page:cockpit.performance'),"
        "('EXPERT', 'page:cockpit.tuning'),"
        "('EXPERT', 'page:diagnosis.overview'),"
        "('EXPERT', 'page:diagnosis.records'),"
        "('EXPERT', 'page:diagnosis.tasks'),"
        "('EXPERT', 'page:handling.archive'),"
        "('EXPERT', 'page:handling.orders')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('EXPERT', 'page:handling.statistics'),"
        "('EXPERT', 'page:handling.suggestions'),"
        "('EXPERT', 'page:handling.tasks'),"
        "('EXPERT', 'page:handling.workbench'),"
        "('EXPERT', 'page:monitor.alerts'),"
        "('EXPERT', 'page:monitor.attention'),"
        "('EXPERT', 'page:monitor.loops'),"
        "('EXPERT', 'page:reports.alert-statistics')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('EXPERT', 'page:reports.data-quality'),"
        "('EXPERT', 'page:reports.diagnosis'),"
        "('EXPERT', 'page:reports.handling'),"
        "('EXPERT', 'page:reports.performance'),"
        "('EXPERT', 'page:tuning.overview'),"
        "('EXPERT', 'page:tuning.records'),"
        "('EXPERT', 'page:tuning.verification'),"
        "('EXPERT', 'page:workbench.main')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('EXPERT', 'portal:view'),"
        "('EXPERT', 'tracker:review'),"
        "('EXPERT', 'tuning:advanced'),"
        "('EXPERT', 'tuning:identify'),"
        "('EXPERT', 'tuning:simulate'),"
        "('EXPERT', 'tuning:task-create'),"
        "('EXPERT', 'tuning:task-manage'),"
        "('EXPERT', 'tuning:tune')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('EXPERT', 'tuning:view')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_authz_policy (revision, operation, role, user_id, reason, operator,"
        "before_value, after_value, rollback_of, created_at) VALUES"
        "(1, 'SEED', NULL, NULL, 'P4-01 初始种子：五角色默认矩阵（P1-04 实际裁决）"
        "+ 存量用户 scope=ALL', 'system:seed', NULL, "
        "jsonb_build_object('source', 'p401authz01'), NULL, now())"
        "\nON CONFLICT (revision) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_user_scope (user_id, scope_type, node_id, updated_by, updated_at)"
        "SELECT u.id, 'ALL', NULL, 'system:seed', now() FROM sys_user u"
        "\nON CONFLICT (user_id) DO NOTHING;"
    )

    # ------------------------------------------------------------------
    # 6. 新增表注释（引导 SQL 对齐）
    # ------------------------------------------------------------------
    op.execute("COMMENT ON TABLE sys_permission IS '页面码/动作码目录（P4-01；无机械通配展开）'")
    op.execute("COMMENT ON TABLE sys_role_permission IS '五角色授权矩阵（P4-01）'")
    op.execute(
        "COMMENT ON TABLE sys_user_scope IS '用户数据范围 ALL/NODE_SUBTREE（P4-01；消费在 P4-03）'"
    )
    op.execute("COMMENT ON TABLE sys_authz_policy IS '授权策略版本账本（P4-01；不可变追加式）'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sys_authz_policy")
    op.execute("DROP TABLE IF EXISTS sys_user_scope")
    op.execute("DROP TABLE IF EXISTS sys_role_permission")
    op.execute("DROP TABLE IF EXISTS sys_permission")
