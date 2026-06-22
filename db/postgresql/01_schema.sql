-- =============================================================================
-- 数据库名: clpm
-- 脚本版本: v1.1
-- 创建日期: 2026-06-20
-- 对应 DDS 版本: DDS v3.0 (产品化架构重构版)
-- 设计依据: PRD v3.0, FDS v3.0, ADS v3.0, 关键算法设计说明 v1.0
-- 说明: 本脚本遵循 ADS v3.0 "存算分离" 原则，承载关系型业务域数据模型。
--       共 14 张表（DDS v3.0 中 13 张 + 新增 sys_user 认证表）。
-- 变更记录:
--   v1.0 2026-06-20: 初始版本（DDS v3.0 14 张表）
--   v1.1 2026-06-22: 算法设计同步DDL变更（metric_config/kpi_snapshot_hourly/diagnosis_config/tuning_record 4表字段调整）
-- =============================================================================

-- 启用 UUID 生成扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- 1. sys_user (用户表) [新增，用于登录认证]
-- =============================================================================
CREATE TABLE IF NOT EXISTS sys_user (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    username        VARCHAR(50)     NOT NULL,
    password_hash   VARCHAR(255)    NOT NULL,
    display_name    VARCHAR(100)    NOT NULL,
    email           VARCHAR(255),
    role            VARCHAR(20)     NOT NULL,
    is_active       BOOLEAN         DEFAULT TRUE,
    last_login_at   TIMESTAMP,
    created_at      TIMESTAMP       DEFAULT NOW(),
    updated_at      TIMESTAMP       DEFAULT NOW(),
    CONSTRAINT uk_sys_user_username UNIQUE (username),
    CONSTRAINT uk_sys_user_email    UNIQUE (email),
    CONSTRAINT ck_sys_user_role     CHECK (role IN ('ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT'))
);

COMMENT ON TABLE  sys_user IS '用户表（用于登录认证）';
COMMENT ON COLUMN sys_user.id IS '用户主键';
COMMENT ON COLUMN sys_user.username IS '登录用户名（唯一）';
COMMENT ON COLUMN sys_user.password_hash IS '密码哈希值（bcrypt）';
COMMENT ON COLUMN sys_user.display_name IS '显示名称';
COMMENT ON COLUMN sys_user.email IS '邮箱（唯一）';
COMMENT ON COLUMN sys_user.role IS '角色：ADMIN/IC_ENGINEER/PE_ENGINEER/SPONSOR/EXPERT';
COMMENT ON COLUMN sys_user.is_active IS '是否启用';
COMMENT ON COLUMN sys_user.last_login_at IS '最后登录时间';
COMMENT ON COLUMN sys_user.created_at IS '创建时间';
COMMENT ON COLUMN sys_user.updated_at IS '更新时间';

-- =============================================================================
-- 2. plant_node (工厂节点)
-- =============================================================================
CREATE TABLE IF NOT EXISTS plant_node (
    id          UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(100)    NOT NULL,
    type        VARCHAR(20)     NOT NULL,
    parent_id   UUID,
    created_at  TIMESTAMP       DEFAULT NOW(),
    updated_at  TIMESTAMP       DEFAULT NOW(),
    CONSTRAINT fk_plant_node_parent FOREIGN KEY (parent_id) REFERENCES plant_node(id) ON DELETE RESTRICT,
    CONSTRAINT ck_plant_node_type   CHECK (type IN ('FACTORY', 'UNIT', 'EQUIPMENT'))
);

COMMENT ON TABLE  plant_node IS '工厂节点（工厂 → 装置 → 单元多级层级树）';
COMMENT ON COLUMN plant_node.id IS '节点主键';
COMMENT ON COLUMN plant_node.name IS '节点名称（如：常减压装置）';
COMMENT ON COLUMN plant_node.type IS '节点类型：FACTORY/UNIT/EQUIPMENT';
COMMENT ON COLUMN plant_node.parent_id IS '父节点 ID（自引用）';
COMMENT ON COLUMN plant_node.created_at IS '创建时间';
COMMENT ON COLUMN plant_node.updated_at IS '更新时间';

-- =============================================================================
-- 3. loop_ledger (回路台账)
-- =============================================================================
CREATE TABLE IF NOT EXISTS loop_ledger (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    tag_name        VARCHAR(100)    NOT NULL,
    description     VARCHAR(255),
    unit_id         UUID,
    score_weight    DECIMAL(5,2),
    is_active       BOOLEAN         DEFAULT TRUE,
    last_aas_sync_at TIMESTAMP,
    status          VARCHAR(20)     NOT NULL DEFAULT 'PARTIAL',
    created_at      TIMESTAMP       DEFAULT NOW(),
    updated_at      TIMESTAMP       DEFAULT NOW(),
    created_by      VARCHAR(50),
    score_weights   JSONB,
    remark          VARCHAR(500),
    updated_by      VARCHAR(50),
    CONSTRAINT uk_loop_ledger_tag_name UNIQUE (tag_name),
    CONSTRAINT fk_loop_ledger_unit_id  FOREIGN KEY (unit_id) REFERENCES plant_node(id) ON DELETE RESTRICT,
    CONSTRAINT ck_loop_ledger_status   CHECK (status IN ('READY', 'PARTIAL', 'INACTIVE'))
);

COMMENT ON TABLE  loop_ledger IS '回路台账（系统核心实体）';
COMMENT ON COLUMN loop_ledger.id IS '回路主键';
COMMENT ON COLUMN loop_ledger.tag_name IS '唯一位号标识（如：101-FC-1023）';
COMMENT ON COLUMN loop_ledger.description IS '回路描述（如：常顶塔顶温度调节回路）';
COMMENT ON COLUMN loop_ledger.unit_id IS '所属工艺单元 ID';
COMMENT ON COLUMN loop_ledger.score_weight IS '评分权重（用于装置/单元级聚合加权计算）';
COMMENT ON COLUMN loop_ledger.is_active IS '是否启用全量评估计算';
COMMENT ON COLUMN loop_ledger.last_aas_sync_at IS '最后 AAS 同步时间';
COMMENT ON COLUMN loop_ledger.status IS '回路状态：READY/PARTIAL/INACTIVE';
COMMENT ON COLUMN loop_ledger.created_at IS '创建时间';
COMMENT ON COLUMN loop_ledger.updated_at IS '更新时间';
COMMENT ON COLUMN loop_ledger.created_by IS '创建人';
COMMENT ON COLUMN loop_ledger.score_weights IS '6 大 KPI 评分权重 JSONB（good_value_rate/auto_mode_rate/steady_rate/accuracy_rate/oscillation_rate/saturation_rate）';
COMMENT ON COLUMN loop_ledger.remark IS '备注（最长 500 字符）';
COMMENT ON COLUMN loop_ledger.updated_by IS '最后更新人';

-- =============================================================================
-- 4. tag_registry (AAS Tag 注册表)
-- =============================================================================
CREATE TABLE IF NOT EXISTS tag_registry (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    tag_name        VARCHAR(100)    NOT NULL,
    tag_description VARCHAR(255),
    tag_type        VARCHAR(20)     NOT NULL,
    current_value   FLOAT,
    quality         VARCHAR(20),
    last_sync_at    TIMESTAMP       NOT NULL,
    is_linked       BOOLEAN         DEFAULT FALSE,
    CONSTRAINT uk_tag_registry_tag_name UNIQUE (tag_name),
    CONSTRAINT ck_tag_registry_type     CHECK (tag_type IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D', 'OTHER')),
    CONSTRAINT ck_tag_registry_quality  CHECK (quality IS NULL OR quality IN ('GOOD', 'BAD', 'UNCERTAIN'))
);

COMMENT ON TABLE  tag_registry IS 'AAS Tag 注册表（AAS 同步的 OPC Tag 位号信息）';
COMMENT ON COLUMN tag_registry.id IS 'Tag 主键';
COMMENT ON COLUMN tag_registry.tag_name IS 'Tag 位号名（OPC Item ID）';
COMMENT ON COLUMN tag_registry.tag_description IS 'Tag 描述（来自 AAS）';
COMMENT ON COLUMN tag_registry.tag_type IS 'Tag 类型：PV/SP/OP/MODE/PID_P/PID_I/PID_D/OTHER';
COMMENT ON COLUMN tag_registry.current_value IS '当前值（最近一次同步快照）';
COMMENT ON COLUMN tag_registry.quality IS '数据质量码：GOOD/BAD/UNCERTAIN';
COMMENT ON COLUMN tag_registry.last_sync_at IS '最后同步时间';
COMMENT ON COLUMN tag_registry.is_linked IS '是否已关联到回路';

-- =============================================================================
-- 5. loop_tag_mapping (回路-Tag 关联)
-- =============================================================================
CREATE TABLE IF NOT EXISTS loop_tag_mapping (
    id          UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id     UUID            NOT NULL,
    tag_id      UUID            NOT NULL,
    tag_role    VARCHAR(20)     NOT NULL,
    is_required BOOLEAN         NOT NULL,
    created_at  TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_loop_tag_mapping_loop_role UNIQUE (loop_id, tag_role),
    CONSTRAINT fk_loop_tag_mapping_loop_id   FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT fk_loop_tag_mapping_tag_id    FOREIGN KEY (tag_id)  REFERENCES tag_registry(id) ON DELETE RESTRICT,
    CONSTRAINT ck_loop_tag_mapping_role      CHECK (tag_role IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D'))
);

COMMENT ON TABLE  loop_tag_mapping IS '回路-Tag 关联（回路与 7 个 OPC Tag 的关联关系）';
COMMENT ON COLUMN loop_tag_mapping.id IS '关联主键';
COMMENT ON COLUMN loop_tag_mapping.loop_id IS '关联回路 ID';
COMMENT ON COLUMN loop_tag_mapping.tag_id IS '关联 Tag ID';
COMMENT ON COLUMN loop_tag_mapping.tag_role IS 'Tag 角色：PV/SP/OP/MODE/PID_P/PID_I/PID_D';
COMMENT ON COLUMN loop_tag_mapping.is_required IS '是否必填 Tag（PV/SP/OP/MODE 为 TRUE，PID_* 为 FALSE）';
COMMENT ON COLUMN loop_tag_mapping.created_at IS '关联创建时间';

-- =============================================================================
-- 6. metric_config (性能指标配置)
-- =============================================================================
CREATE TABLE IF NOT EXISTS metric_config (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    metric_code     VARCHAR(50)     NOT NULL,
    metric_name     VARCHAR(100)    NOT NULL,
    formula         TEXT,
    weight          DECIMAL(5,2),
    threshold       JSONB,
    control_type    VARCHAR(20)     DEFAULT 'STABLE',
    is_enabled      BOOLEAN         DEFAULT TRUE,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP,
    version         INT             DEFAULT 1,
    CONSTRAINT uk_metric_config_code UNIQUE (metric_code),
    CONSTRAINT ck_metric_config_control_type CHECK (control_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC'))
);

COMMENT ON TABLE  metric_config IS '性能指标配置（6 大核心 KPI 及变体指标的可配置元数据）';
COMMENT ON COLUMN metric_config.id IS '指标主键';
COMMENT ON COLUMN metric_config.metric_code IS '指标代码（如：GOOD_VALUE_RATE/AUTO_MODE_RATE/STEADY_RATE/IAE/ISE/OVERSHOOT）';
COMMENT ON COLUMN metric_config.metric_name IS '指标名称（如：好值率）';
COMMENT ON COLUMN metric_config.formula IS '计算公式（支持用户自定义表达式）';
COMMENT ON COLUMN metric_config.weight IS '权重（总和须为 100%）';
COMMENT ON COLUMN metric_config.threshold IS '阈值JSONB结构 {min, max, alert}';
COMMENT ON COLUMN metric_config.control_type IS '控制类型 STABLE/SLOW/FAST/LOGIC';
COMMENT ON COLUMN metric_config.is_enabled IS '是否启用';
COMMENT ON COLUMN metric_config.updated_by IS '最后更新人';
COMMENT ON COLUMN metric_config.updated_at IS '最后更新时间';
COMMENT ON COLUMN metric_config.version IS '配置版本号（用于变更追溯与回滚）';

-- =============================================================================
-- 7. diagnosis_config (诊断指标配置)
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnosis_config (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    diag_code       VARCHAR(50)     NOT NULL,
    diag_name       VARCHAR(100)    NOT NULL,
    algorithm_type  VARCHAR(50)     NOT NULL,
    calc_method     VARCHAR(50),
    params          JSON,
    threshold       JSONB,
    is_enabled      BOOLEAN         DEFAULT TRUE,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP,
    version         INT             DEFAULT 1,
    CONSTRAINT uk_diagnosis_config_code UNIQUE (diag_code)
);

COMMENT ON TABLE  diagnosis_config IS '诊断指标配置（振荡检测/粘滞检测/参数过激检测/质量码规则等）';
COMMENT ON COLUMN diagnosis_config.id IS '诊断指标主键';
COMMENT ON COLUMN diagnosis_config.diag_code IS '诊断代码（如：OSCILLATION_FFT/STICTION_SCATTER/OVERAGGRESSIVE/QUALITY_CODE）';
COMMENT ON COLUMN diagnosis_config.diag_name IS '诊断指标名称（如：振荡检测-FFT）';
COMMENT ON COLUMN diagnosis_config.algorithm_type IS '算法类型（如：FFT/SCATTER_FIT/THRESHOLD）';
COMMENT ON COLUMN diagnosis_config.calc_method IS '计算方法 IAE_ZERO_CROSSING/FFT_WELCH/CHOUDHURY_NGI_NLI/KANO_STATISTICAL/EXPERT_RULE';
COMMENT ON COLUMN diagnosis_config.params IS '算法参数（如：FFT 窗口长度、散点拟合阶数）';
COMMENT ON COLUMN diagnosis_config.threshold IS '阈值JSONB结构 {min, max, alert}';
COMMENT ON COLUMN diagnosis_config.is_enabled IS '是否启用';
COMMENT ON COLUMN diagnosis_config.updated_by IS '最后更新人';
COMMENT ON COLUMN diagnosis_config.updated_at IS '最后更新时间';
COMMENT ON COLUMN diagnosis_config.version IS '配置版本号';

-- =============================================================================
-- 8. engine_rule (引擎规则配置)
-- =============================================================================
CREATE TABLE IF NOT EXISTS engine_rule (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_code       VARCHAR(50)     NOT NULL,
    rule_name       VARCHAR(100)    NOT NULL,
    rule_type       VARCHAR(20)     NOT NULL,
    params          JSON,
    is_enabled      BOOLEAN         DEFAULT TRUE,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP,
    CONSTRAINT uk_engine_rule_code UNIQUE (rule_code),
    CONSTRAINT ck_engine_rule_type CHECK (rule_type IN ('CALC_CYCLE', 'DATA_FETCH', 'SCHEDULE'))
);

COMMENT ON TABLE  engine_rule IS '引擎规则配置（评估/诊断引擎的计算周期、数据拉取规则、调度参数）';
COMMENT ON COLUMN engine_rule.id IS '规则主键';
COMMENT ON COLUMN engine_rule.rule_code IS '规则代码（如：EVAL_CALC_CYCLE/DATA_FETCH_WINDOW/SCHEDULE_CONCURRENCY）';
COMMENT ON COLUMN engine_rule.rule_name IS '规则名称（如：评估计算周期）';
COMMENT ON COLUMN engine_rule.rule_type IS '规则类型：CALC_CYCLE/DATA_FETCH/SCHEDULE';
COMMENT ON COLUMN engine_rule.params IS '规则参数（如：{"cycle_minutes": 60, "concurrency": 16}）';
COMMENT ON COLUMN engine_rule.is_enabled IS '是否启用';
COMMENT ON COLUMN engine_rule.updated_by IS '最后更新人';
COMMENT ON COLUMN engine_rule.updated_at IS '最后更新时间';

-- =============================================================================
-- 9. kpi_snapshot_hourly (每小时性能评估快照)
-- =============================================================================
CREATE TABLE IF NOT EXISTS kpi_snapshot_hourly (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id             UUID,
    ts_start            TIMESTAMP       NOT NULL,
    ts_end              TIMESTAMP       NOT NULL,
    score               DECIMAL(5,2),
    good_value_rate     DECIMAL(5,2),
    auto_mode_rate      DECIMAL(5,2),
    steady_rate         DECIMAL(5,2),
    accuracy_rate       DECIMAL(5,2),
    oscillation_rate    DECIMAL(5,2),
    saturation_rate     DECIMAL(5,2),
    status              VARCHAR(20)     NOT NULL,
    CONSTRAINT fk_kpi_snapshot_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_kpi_snapshot_status  CHECK (status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')),
    CONSTRAINT ck_kpi_snapshot_window  CHECK (ts_end > ts_start)
);

COMMENT ON TABLE  kpi_snapshot_hourly IS '每小时性能评估快照（好值率基于 PV 质量码统计）';
COMMENT ON COLUMN kpi_snapshot_hourly.id IS '快照主键';
COMMENT ON COLUMN kpi_snapshot_hourly.loop_id IS '关联回路 ID';
COMMENT ON COLUMN kpi_snapshot_hourly.ts_start IS '评估窗口起始时间';
COMMENT ON COLUMN kpi_snapshot_hourly.ts_end IS '评估窗口结束时间';
COMMENT ON COLUMN kpi_snapshot_hourly.score IS '综合评分（0-100）';
COMMENT ON COLUMN kpi_snapshot_hourly.good_value_rate IS '好值率（%），基于 PV 质量码统计';
COMMENT ON COLUMN kpi_snapshot_hourly.auto_mode_rate IS '自控率（%）';
COMMENT ON COLUMN kpi_snapshot_hourly.steady_rate IS '平稳率（%）';
COMMENT ON COLUMN kpi_snapshot_hourly.accuracy_rate IS '准确率(%)';
COMMENT ON COLUMN kpi_snapshot_hourly.oscillation_rate IS '振荡率（%）';
COMMENT ON COLUMN kpi_snapshot_hourly.saturation_rate IS '饱和率(%)';
COMMENT ON COLUMN kpi_snapshot_hourly.status IS '计算状态：SUCCESS/INCONCLUSIVE/PARTIAL';

-- =============================================================================
-- 10. action_tracker (轻量级异常追踪记录)
-- =============================================================================
CREATE TABLE IF NOT EXISTS action_tracker (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id         UUID,
    diagnosis_label VARCHAR(100),
    action_status   VARCHAR(20)     NOT NULL DEFAULT 'PENDING',
    evidence_url    VARCHAR(255),
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP,
    CONSTRAINT fk_action_tracker_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_action_tracker_status  CHECK (action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'RESOLVED'))
);

COMMENT ON TABLE  action_tracker IS '轻量级异常追踪记录（诊断中心子模块）';
COMMENT ON COLUMN action_tracker.id IS '追踪记录主键';
COMMENT ON COLUMN action_tracker.loop_id IS '关联回路 ID';
COMMENT ON COLUMN action_tracker.diagnosis_label IS '自动预诊结论（如：疑似阀门粘滞）';
COMMENT ON COLUMN action_tracker.action_status IS '处理状态：PENDING/IN_PROGRESS/IGNORED/RESOLVED';
COMMENT ON COLUMN action_tracker.evidence_url IS '《诊断建议书》PDF S3 存储路径';
COMMENT ON COLUMN action_tracker.updated_by IS '最后操作人（仪控工程师）';
COMMENT ON COLUMN action_tracker.updated_at IS '状态变更时间戳';

-- =============================================================================
-- 11. diagnosis_result (诊断结果表)
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnosis_result (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id             UUID,
    diag_label          VARCHAR(100),
    confidence          DECIMAL(5,2),
    feature_values      JSON,
    evidence_chain      JSON,
    algorithm_version   VARCHAR(50),
    diagnosed_at        TIMESTAMP       NOT NULL,
    CONSTRAINT fk_diagnosis_result_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_diagnosis_result_conf    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 100))
);

COMMENT ON TABLE  diagnosis_result IS '诊断结果表（诊断引擎对回路的自动预诊结果）';
COMMENT ON COLUMN diagnosis_result.id IS '诊断结果主键';
COMMENT ON COLUMN diagnosis_result.loop_id IS '关联回路 ID';
COMMENT ON COLUMN diagnosis_result.diag_label IS '预诊标签（如：疑似阀门粘滞、参数过激、原因不明需人工介入）';
COMMENT ON COLUMN diagnosis_result.confidence IS '置信度（0-100）';
COMMENT ON COLUMN diagnosis_result.feature_values IS '特征值（FFT 主频、散点拟合参数等）';
COMMENT ON COLUMN diagnosis_result.evidence_chain IS '证据链引用（波形时间段、散点图数据引用等）';
COMMENT ON COLUMN diagnosis_result.algorithm_version IS '算法版本号';
COMMENT ON COLUMN diagnosis_result.diagnosed_at IS '诊断时间';

-- =============================================================================
-- 12. tuning_record (整定记录) [Phase 2]
-- =============================================================================
CREATE TABLE IF NOT EXISTS tuning_record (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id             UUID            NOT NULL,
    model_type          VARCHAR(20)     NOT NULL,
    model_params        JSON,
    algorithm           VARCHAR(50)     NOT NULL,
    recommended_pid     JSON,
    simulation_result   JSON,
    fitting_score       DECIMAL(5,2),
    status              VARCHAR(20)     NOT NULL,
    created_by          VARCHAR(50),
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_tuning_record_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_tuning_record_model   CHECK (model_type IN ('FOPDT', 'SOPDT', 'IPDT')),
    CONSTRAINT ck_tuning_record_algo    CHECK (algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON')),
    CONSTRAINT ck_tuning_record_status  CHECK (status IN ('PENDING', 'IDENTIFIED', 'SIMULATED', 'APPLIED', 'VERIFIED'))
);

COMMENT ON TABLE  tuning_record IS '整定记录（回路整定任务记录，Phase 1 仅建表，Phase 2 实现算法）';
COMMENT ON COLUMN tuning_record.id IS '整定记录主键';
COMMENT ON COLUMN tuning_record.loop_id IS '关联回路 ID';
COMMENT ON COLUMN tuning_record.model_type IS '模型类型：FOPDT/SOPDT/IPDT';
COMMENT ON COLUMN tuning_record.model_params IS '模型参数（如：{"K": 1.2, "T": 30.5, "tau": 5.0}）';
COMMENT ON COLUMN tuning_record.algorithm IS '整定算法：IMC/LAMBDA/ZN/COHEN_COON';
COMMENT ON COLUMN tuning_record.recommended_pid IS '推荐 PID 参数（如：{"P": 1.5, "I": 0.8, "D": 0.2}）';
COMMENT ON COLUMN tuning_record.simulation_result IS '闭环仿真结果（含阶跃响应曲线、性能指标对比）';
COMMENT ON COLUMN tuning_record.fitting_score IS '模型拟合度评分(0-100)';
COMMENT ON COLUMN tuning_record.status IS '整定状态：PENDING/IDENTIFIED/SIMULATED/APPLIED/VERIFIED';
COMMENT ON COLUMN tuning_record.created_by IS '创建人';
COMMENT ON COLUMN tuning_record.created_at IS '创建时间';

-- =============================================================================
-- 13. report_record (自动报表记录)
-- =============================================================================
CREATE TABLE IF NOT EXISTS report_record (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_period   VARCHAR(20)     NOT NULL,
    generated_at    TIMESTAMP       NOT NULL,
    status          VARCHAR(20)     NOT NULL,
    file_url        VARCHAR(255),
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_report_record_period CHECK (report_period IN ('SHIFT', 'DAILY', 'WEEKLY', 'MONTHLY')),
    CONSTRAINT ck_report_record_status CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED'))
);

COMMENT ON TABLE  report_record IS '自动报表记录（按班/日/周/月自动生成的《控制回路性能评估报告》归档记录）';
COMMENT ON COLUMN report_record.id IS '报表记录主键';
COMMENT ON COLUMN report_record.report_period IS '报表周期：SHIFT/DAILY/WEEKLY/MONTHLY';
COMMENT ON COLUMN report_record.generated_at IS '生成时间';
COMMENT ON COLUMN report_record.status IS '生成状态：PROCESSING/COMPLETED/FAILED';
COMMENT ON COLUMN report_record.file_url IS '报表文件存储路径（S3/MinIO）';
COMMENT ON COLUMN report_record.created_at IS '记录创建时间';

-- =============================================================================
-- 14. sys_audit_log (系统审计日志)
-- =============================================================================
CREATE TABLE IF NOT EXISTS sys_audit_log (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    operator        VARCHAR(50)     NOT NULL,
    operation_type  VARCHAR(50)     NOT NULL,
    target_type     VARCHAR(50),
    target_id       VARCHAR(36),
    before_value    TEXT,
    after_value     TEXT,
    operated_at     TIMESTAMP       NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  sys_audit_log IS '系统审计日志（所有配置变更均落入本表，不可物理删除）';
COMMENT ON COLUMN sys_audit_log.id IS '日志主键';
COMMENT ON COLUMN sys_audit_log.operator IS '操作人';
COMMENT ON COLUMN sys_audit_log.operation_type IS '操作类型（如：METRIC_CONFIG_UPDATE/ROLE_ASSIGN/LOOP_CREATE）';
COMMENT ON COLUMN sys_audit_log.target_type IS '操作对象类型（如：loop_ledger/metric_config）';
COMMENT ON COLUMN sys_audit_log.target_id IS '操作对象 ID';
COMMENT ON COLUMN sys_audit_log.before_value IS '变更前值（JSON 序列化）';
COMMENT ON COLUMN sys_audit_log.after_value IS '变更后值（JSON 序列化）';
COMMENT ON COLUMN sys_audit_log.operated_at IS '操作时间';

-- =============================================================================
-- 15. sys_config (系统配置 key-value 表) [S2-LOOP-003 新增]
-- =============================================================================
CREATE TABLE IF NOT EXISTS sys_config (
    key             VARCHAR(100)    PRIMARY KEY,
    value           TEXT,
    description     VARCHAR(255),
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP       DEFAULT NOW()
);

COMMENT ON TABLE  sys_config IS '系统配置 key-value 表（运行时可变配置存储）';
COMMENT ON COLUMN sys_config.key IS '配置键（如 aas.endpoint）';
COMMENT ON COLUMN sys_config.value IS '配置值（文本）';
COMMENT ON COLUMN sys_config.description IS '配置描述';
COMMENT ON COLUMN sys_config.updated_by IS '最后更新人';
COMMENT ON COLUMN sys_config.updated_at IS '最后更新时间';

-- =============================================================================
-- 索引（高频查询字段）
-- =============================================================================

-- loop_ledger 索引
CREATE INDEX IF NOT EXISTS idx_loop_ledger_unit_id ON loop_ledger (unit_id);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_status  ON loop_ledger (status);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_tag_name ON loop_ledger (tag_name);

-- tag_registry 索引
CREATE INDEX IF NOT EXISTS idx_tag_registry_tag_name ON tag_registry (tag_name);
CREATE INDEX IF NOT EXISTS idx_tag_registry_tag_type ON tag_registry (tag_type);
CREATE INDEX IF NOT EXISTS idx_tag_registry_is_linked ON tag_registry (is_linked);

-- loop_tag_mapping 索引
CREATE INDEX IF NOT EXISTS idx_loop_tag_mapping_loop_id ON loop_tag_mapping (loop_id);
CREATE INDEX IF NOT EXISTS idx_loop_tag_mapping_tag_id  ON loop_tag_mapping (tag_id);
-- (loop_id, tag_role) 已由唯一约束 uk_loop_tag_mapping_loop_role 自动创建索引

-- kpi_snapshot_hourly 索引
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_loop_id  ON kpi_snapshot_hourly (loop_id);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_ts_start ON kpi_snapshot_hourly (ts_start);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_status   ON kpi_snapshot_hourly (status);

-- action_tracker 索引
CREATE INDEX IF NOT EXISTS idx_action_tracker_loop_id       ON action_tracker (loop_id);
CREATE INDEX IF NOT EXISTS idx_action_tracker_action_status ON action_tracker (action_status);

-- diagnosis_result 索引
CREATE INDEX IF NOT EXISTS idx_diagnosis_result_loop_id    ON diagnosis_result (loop_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_result_diagnosed  ON diagnosis_result (diagnosed_at);

-- sys_audit_log 索引
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_operator        ON sys_audit_log (operator);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_operation_type  ON sys_audit_log (operation_type);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_operated_at     ON sys_audit_log (operated_at);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_target_type     ON sys_audit_log (target_type);

-- sys_user 索引（username/email 已由唯一约束自动创建索引）
CREATE INDEX IF NOT EXISTS idx_sys_user_is_active ON sys_user (is_active);

-- =============================================================================
-- 脚本结束
-- =============================================================================
