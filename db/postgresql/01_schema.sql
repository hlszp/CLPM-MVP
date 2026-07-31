-- =============================================================================
-- 数据库名: clpm
-- 脚本版本: v1.7
-- 创建日期: 2026-06-20
-- 对应 DDS 版本: DDS v3.0 (产品化架构重构版)
-- 设计依据: PRD v3.0, FDS v3.0, ADS v3.0, 关键算法设计说明 v1.0
-- 说明: 本脚本遵循 ADS v3.0 "存算分离" 原则，承载关系型业务域数据模型。
--       共 14 张表（DDS v3.0 中 13 张 + 新增 sys_user 认证表）。
-- 变更记录:
-- v1.0 2026-06-20: 初始版本（DDS v3.0 14 张表）
--   v1.1 2026-06-22: 算法设计同步DDL变更（metric_config/kpi_snapshot_hourly/diagnosis_config/tuning_record 4表字段调整）
--   v1.2 2026-06-24: 重构方案 P0 — 新增 loop_mode_mapping/loop_type_weight/loop_level_weight 三表 + loop_ledger 加 level/modeattr_tag_id/data_retention_days 字段
--   v1.3 2026-06-24: kpi_snapshot_hourly 加 3 个故障诊断指标字段（stiction_coeff/steady_state_time/output_travel_index，nullable 向后兼容）
--   v1.4 2026-06-24: 新增 kpi_node_snapshot_daily / kpi_node_snapshot_monthly 两表（节点级日/月聚合快照）
--   v1.5 2026-06-24: plant_node 加 monitor_tag_id/monitor_trigger_value 字段（SVC-10 位号触发监控）
--   v1.6 2026-07-28: sys_user 加 must_change_password 字段（S5-AUTH P1 首次登录强制改密，NOT NULL DEFAULT FALSE）
--   v1.7 2026-07-29: 生产 bootstrap 收敛至 37 张 ORM 表，补齐迁移链新增的 16 张表
--   v1.8 2026-07-31: V62-P3-003 新增 process_model_version 表（38 张），tuning_record 加 process_model_version_id 外键；
--                    V62-P3-006 tuning_record.algorithm CHECK 新增 IDENTIFICATION_ONLY（纯辨识记录不再用 IMC 占位）；
--                    V62-P3-007 tuning_record 加 current_pid/risk_assessment/rollback_pid 人工实施清单字段；
--                    V62-P3-008 action_tracker 加 assignee/planned_at 负责人与计划执行时间
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
    must_change_password BOOLEAN    NOT NULL DEFAULT FALSE,
    last_login_at   TIMESTAMP,
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
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
COMMENT ON COLUMN sys_user.must_change_password IS '首次登录强制改密标志（S5-AUTH P1）：TRUE 时除改密/登出外的写操作端点拒绝，改密成功后清除';
COMMENT ON COLUMN sys_user.last_login_at IS '最后登录时间';
COMMENT ON COLUMN sys_user.created_at IS '创建时间';
COMMENT ON COLUMN sys_user.updated_at IS '更新时间';

-- =============================================================================
-- 2. plant_node (工厂节点)
-- =============================================================================
CREATE TABLE IF NOT EXISTS plant_node (
    id                      UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                    VARCHAR(100)    NOT NULL,
    type                    VARCHAR(20)     NOT NULL,
    parent_id               UUID,
    is_kpi_enabled          BOOLEAN         DEFAULT FALSE,
    monitor_tag_id          UUID,
    monitor_trigger_value   VARCHAR(20),
    created_at              TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_plant_node_parent        FOREIGN KEY (parent_id) REFERENCES plant_node(id) ON DELETE RESTRICT,
    CONSTRAINT ck_plant_node_type          CHECK (type IN ('FACTORY', 'AREA', 'UNIT'))
);

COMMENT ON TABLE  plant_node IS '工厂节点（工厂 → 装置 → 单元多级层级树）';
COMMENT ON COLUMN plant_node.id IS '节点主键';
COMMENT ON COLUMN plant_node.name IS '节点名称（如：常减压装置）';
COMMENT ON COLUMN plant_node.type IS '节点类型：FACTORY/AREA/UNIT；回路挂在 UNIT';
COMMENT ON COLUMN plant_node.parent_id IS '父节点 ID（自引用）';
COMMENT ON COLUMN plant_node.is_kpi_enabled IS '是否纳入性能评估（TRUE 时生成节点级 KPI 快照）';
COMMENT ON COLUMN plant_node.monitor_tag_id IS '位号触发监控的位号 ID（NULL 表示默认监控，FK→tag_registry）';
COMMENT ON COLUMN plant_node.monitor_trigger_value IS '触发监控的位号值（如 "true"/"1"/"ON"），值匹配时该节点下回路应监控';
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
    loop_type       VARCHAR(20)     DEFAULT 'OTHER',
    control_type    VARCHAR(20),
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(50),
    score_weights   JSONB,
    remark          VARCHAR(500),
    updated_by      VARCHAR(50),
    importance_level SMALLINT      NOT NULL DEFAULT 2,
    include_in_evaluation BOOLEAN  NOT NULL DEFAULT TRUE,
    modeattr_tag_id UUID,
    data_retention_days INTEGER,
    op_output_lower_limit FLOAT,
    op_output_upper_limit FLOAT,
    dcs_model_id    UUID,
    ideal_settling_time FLOAT,
    complex_loop_group_id UUID,
    complex_role    VARCHAR(10),
    CONSTRAINT uk_loop_ledger_tag_name UNIQUE (tag_name),
    CONSTRAINT fk_loop_ledger_unit_id  FOREIGN KEY (unit_id) REFERENCES plant_node(id) ON DELETE RESTRICT,
    CONSTRAINT ck_loop_ledger_status   CHECK (status IN ('READY', 'PARTIAL', 'INACTIVE')),
    CONSTRAINT ck_loop_ledger_loop_type CHECK (loop_type IN ('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', 'ANALYSIS', 'SPEED', 'OTHER')),
    CONSTRAINT ck_loop_ledger_importance_level CHECK (importance_level IN (1, 2, 3)),
    CONSTRAINT ck_loop_ledger_complex_role CHECK (complex_role IS NULL OR complex_role IN ('MAIN', 'SUB')),
    CONSTRAINT ck_loop_ledger_complex_group_coherence CHECK (
        (complex_loop_group_id IS NULL AND complex_role IS NULL)
        OR (complex_loop_group_id IS NOT NULL AND complex_role IS NOT NULL)
    )
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
COMMENT ON COLUMN loop_ledger.importance_level IS '回路重要等级 1/2/3（默认2，对齐 GB/T 44693.2-2024 附表2）';
COMMENT ON COLUMN loop_ledger.modeattr_tag_id IS 'APC 识别位号 ID（位号值为 program 时算自动控制，影响有效自控率和投用率）';
COMMENT ON COLUMN loop_ledger.data_retention_days IS '数据保存周期（天），NULL 表示用系统默认';

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
    range_min       FLOAT,
    range_max       FLOAT,
    unit            VARCHAR(20),
    measure_type    VARCHAR(20),
    tdengine_tag_id VARCHAR(100),
    CONSTRAINT uk_tag_registry_tag_name UNIQUE (tag_name),
    CONSTRAINT ck_tag_registry_type     CHECK (tag_type IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D', 'OTHER')),
    CONSTRAINT ck_tag_registry_quality  CHECK (quality IS NULL OR quality IN ('GOOD', 'BAD', 'UNCERTAIN')),
    CONSTRAINT ck_tag_registry_measure_type CHECK (
        measure_type IS NULL OR measure_type IN ('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', 'ANALYSIS', 'SPEED', 'OTHER')
    )
);

-- plant_node / loop_ledger 在 tag_registry 之前创建；延后添加跨表外键，
-- 保证空 PostgreSQL 的生产 bootstrap 可顺序执行。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_plant_node_monitor_tag'
          AND conrelid = 'plant_node'::regclass
    ) THEN
        ALTER TABLE plant_node
            ADD CONSTRAINT fk_plant_node_monitor_tag
            FOREIGN KEY (monitor_tag_id) REFERENCES tag_registry(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_loop_ledger_modeattr'
          AND conrelid = 'loop_ledger'::regclass
    ) THEN
        ALTER TABLE loop_ledger
            ADD CONSTRAINT fk_loop_ledger_modeattr
            FOREIGN KEY (modeattr_tag_id) REFERENCES tag_registry(id) ON DELETE RESTRICT;
    END IF;
END
$$;

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
    grading_thresholds JSONB,
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
    effective_auto_rate DECIMAL(5,2),
    steady_rate         DECIMAL(5,2),
    accuracy_rate       DECIMAL(5,2),
    fast_rate           DECIMAL(5,2),
    oscillation_rate    DECIMAL(5,2),
    saturation_rate     DECIMAL(5,2),
    stiction_index      DECIMAL(5,2),
    settling_time       DECIMAL(8,2),
    output_trip_index   DECIMAL(8,2),
    status              VARCHAR(20)     NOT NULL,
    ideal_settling_time DECIMAL(8,2),
    algorithm_version   VARCHAR(50),
    sampling_freq       VARCHAR(10),
    quality_policy      VARCHAR(30),
    valid_rate          DECIMAL(5,4),
    confidence_level    CHAR(1),
    data_lineage        JSONB,
    instrument_fault_rate DECIMAL(5,2),
    pv_mean             DECIMAL(10,3),
    pv_std              DECIMAL(10,3),
    sp_mean             DECIMAL(10,3),
    sp_std              DECIMAL(10,3),
    op_mean             DECIMAL(10,3),
    op_std              DECIMAL(10,3),
    error_mean          DECIMAL(10,3),
    error_std           DECIMAL(10,3),
    valve_linearity     DECIMAL(5,4),
    valve_nonlinearity  DECIMAL(5,4),
    valve_op_min        DECIMAL(8,2),
    valve_op_max        DECIMAL(8,2),
    oscillation_amplitude DECIMAL(8,2),
    setpoint_crossing_count DECIMAL(10,0),
    time_constant       DECIMAL(8,2),
    CONSTRAINT fk_kpi_snapshot_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_kpi_snapshot_status  CHECK (status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')),
    CONSTRAINT ck_kpi_snapshot_window  CHECK (ts_end > ts_start),
    CONSTRAINT ck_kpi_snapshot_confidence CHECK (
        confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')
    ),
    CONSTRAINT uq_kpi_snapshot_hourly_loop_ts UNIQUE (loop_id, ts_start)
);

COMMENT ON TABLE  kpi_snapshot_hourly IS '每小时性能评估快照（好值率基于 PV 质量码统计，对齐 GB/T 44693.2-2024）';
COMMENT ON COLUMN kpi_snapshot_hourly.id IS '快照主键';
COMMENT ON COLUMN kpi_snapshot_hourly.loop_id IS '关联回路 ID';
COMMENT ON COLUMN kpi_snapshot_hourly.ts_start IS '评估窗口起始时间';
COMMENT ON COLUMN kpi_snapshot_hourly.ts_end IS '评估窗口结束时间';
COMMENT ON COLUMN kpi_snapshot_hourly.score IS '综合评分（0-100）';
COMMENT ON COLUMN kpi_snapshot_hourly.good_value_rate IS '好值率（%），仅显示不参与综合评分加权';
COMMENT ON COLUMN kpi_snapshot_hourly.auto_mode_rate IS '自控率（%）';
COMMENT ON COLUMN kpi_snapshot_hourly.effective_auto_rate IS '有效自控率（%），作为综合评分乘数因子';
COMMENT ON COLUMN kpi_snapshot_hourly.steady_rate IS '平稳率（%）';
COMMENT ON COLUMN kpi_snapshot_hourly.accuracy_rate IS '准确率(%)';
COMMENT ON COLUMN kpi_snapshot_hourly.fast_rate IS '快速率（%），控制回路对设定值变化的响应速度';
COMMENT ON COLUMN kpi_snapshot_hourly.oscillation_rate IS '振荡率（%）';
COMMENT ON COLUMN kpi_snapshot_hourly.saturation_rate IS '饱和率(%)';
COMMENT ON COLUMN kpi_snapshot_hourly.stiction_index IS '黏滞指数（0-100，0=无黏滞）';
COMMENT ON COLUMN kpi_snapshot_hourly.settling_time IS '稳态时间（秒）：PV 与 SP 偏差进入容差带所需时间';
COMMENT ON COLUMN kpi_snapshot_hourly.output_trip_index IS '输出值行程指数（0-100）：OP 总行程归一化指数';
COMMENT ON COLUMN kpi_snapshot_hourly.status IS '计算状态：SUCCESS/INCONCLUSIVE/PARTIAL';

-- =============================================================================
-- 9.1 kpi_node_snapshot_hourly (节点级每小时性能评估快照)
--   对齐 GB/T 44693.2-2024 §6.4 综合评估：企业级/装置级/单元级 KPI 加权聚合
--   按 plant_node 递归收集下属回路，以 score_weight 加权聚合回路级快照
-- =============================================================================
CREATE TABLE IF NOT EXISTS kpi_node_snapshot_hourly (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    plant_node_id       UUID            NOT NULL,
    ts_start            TIMESTAMP       NOT NULL,
    ts_end              TIMESTAMP       NOT NULL,
    score               DECIMAL(5,2),
    good_value_rate     DECIMAL(5,2),
    auto_mode_rate      DECIMAL(5,2),
    effective_auto_rate DECIMAL(5,2),
    steady_rate         DECIMAL(5,2),
    accuracy_rate       DECIMAL(5,2),
    fast_rate           DECIMAL(5,2),
    oscillation_rate    DECIMAL(5,2),
    saturation_rate     DECIMAL(5,2),
    instrument_fault_rate DECIMAL(5,2),
    stiction_index      DECIMAL(5,2),
    settling_time       DECIMAL(8,2),
    output_trip_index   DECIMAL(8,2),
    ideal_settling_time DECIMAL(8,2),
    auto_loop_ratio     DECIMAL(5,2),
    realtime_auto_rate  DECIMAL(5,2),
    loop_count          INTEGER         NOT NULL DEFAULT 0,
    status              VARCHAR(20)     NOT NULL,
    algorithm_version   VARCHAR(30),
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_kpi_node_snapshot_node FOREIGN KEY (plant_node_id) REFERENCES plant_node(id) ON DELETE CASCADE,
    CONSTRAINT ck_kpi_node_snapshot_status CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')),
    CONSTRAINT ck_kpi_node_snapshot_window CHECK (ts_end > ts_start)
);

COMMENT ON TABLE  kpi_node_snapshot_hourly IS '节点级每小时性能评估快照（按 plant_node 递归聚合，对齐 GB/T 44693.2-2024 §6.4）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.id IS '快照主键';
COMMENT ON COLUMN kpi_node_snapshot_hourly.plant_node_id IS '工厂节点 ID（FACTORY/UNIT/EQUIPMENT 任意层级）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.ts_start IS '评估窗口起始时间';
COMMENT ON COLUMN kpi_node_snapshot_hourly.ts_end IS '评估窗口结束时间';
COMMENT ON COLUMN kpi_node_snapshot_hourly.score IS '加权综合评分（0-100）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.good_value_rate IS '好值率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.auto_mode_rate IS '自控率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.effective_auto_rate IS '有效自控率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.steady_rate IS '平稳率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.accuracy_rate IS '准确率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.fast_rate IS '快速率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.oscillation_rate IS '振荡率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.saturation_rate IS '饱和率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.auto_loop_ratio IS '投自动回路占比（%）';
COMMENT ON COLUMN kpi_node_snapshot_hourly.realtime_auto_rate IS '实时自控率（%）：当前时刻处于自动模式的回路占比';
COMMENT ON COLUMN kpi_node_snapshot_hourly.loop_count IS '参与聚合的回路数';
COMMENT ON COLUMN kpi_node_snapshot_hourly.status IS '节点级定级：EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE';
COMMENT ON COLUMN kpi_node_snapshot_hourly.algorithm_version IS '算法版本号';
COMMENT ON COLUMN kpi_node_snapshot_hourly.created_at IS '创建时间';

-- =============================================================================
-- 9.2 kpi_node_snapshot_daily (节点级日性能评估快照) [v1.4 新增]
--   按 loop_count 加权聚合当天 24 条小时快照
--   realtime_auto_rate 取当天最后一次小时快照的值（非聚合）
-- =============================================================================
CREATE TABLE IF NOT EXISTS kpi_node_snapshot_daily (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    plant_node_id       UUID            NOT NULL,
    stat_date           DATE            NOT NULL,
    score               DECIMAL(5,2),
    good_value_rate     DECIMAL(5,2),
    auto_mode_rate      DECIMAL(5,2),
    effective_auto_rate DECIMAL(5,2),
    steady_rate         DECIMAL(5,2),
    accuracy_rate       DECIMAL(5,2),
    fast_rate           DECIMAL(5,2),
    oscillation_rate    DECIMAL(5,2),
    saturation_rate     DECIMAL(5,2),
    instrument_fault_rate DECIMAL(5,2),
    stiction_index      DECIMAL(5,2),
    settling_time       DECIMAL(8,2),
    output_trip_index   DECIMAL(8,2),
    ideal_settling_time DECIMAL(8,2),
    auto_loop_ratio     DECIMAL(5,2),
    realtime_auto_rate  DECIMAL(5,2),
    loop_count          INTEGER         NOT NULL DEFAULT 0,
    status              VARCHAR(20)     NOT NULL,
    algorithm_version   VARCHAR(30),
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_kpi_node_snapshot_daily_node FOREIGN KEY (plant_node_id) REFERENCES plant_node(id) ON DELETE CASCADE,
    CONSTRAINT ck_kpi_node_snapshot_daily_status CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')),
    CONSTRAINT uk_kpi_node_snapshot_daily_node_date UNIQUE (plant_node_id, stat_date)
);

COMMENT ON TABLE  kpi_node_snapshot_daily IS '节点级日性能评估快照（按 loop_count 加权聚合当天小时快照，对齐 GB/T 44693.2-2024 §6.4）';
COMMENT ON COLUMN kpi_node_snapshot_daily.id IS '快照主键';
COMMENT ON COLUMN kpi_node_snapshot_daily.plant_node_id IS '工厂节点 ID（FACTORY/UNIT/EQUIPMENT 任意层级）';
COMMENT ON COLUMN kpi_node_snapshot_daily.stat_date IS '统计日期（DATE）';
COMMENT ON COLUMN kpi_node_snapshot_daily.score IS '加权综合评分（0-100）';
COMMENT ON COLUMN kpi_node_snapshot_daily.good_value_rate IS '好值率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.auto_mode_rate IS '自控率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.effective_auto_rate IS '有效自控率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.steady_rate IS '平稳率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.accuracy_rate IS '准确率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.fast_rate IS '快速率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.oscillation_rate IS '振荡率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.saturation_rate IS '饱和率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.auto_loop_ratio IS '投自动回路占比加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_daily.realtime_auto_rate IS '实时自控率（%）：取当天最后一次小时快照的值（非聚合）';
COMMENT ON COLUMN kpi_node_snapshot_daily.loop_count IS '参与聚合的回路数（取当天最大值）';
COMMENT ON COLUMN kpi_node_snapshot_daily.status IS '节点级定级：EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE';
COMMENT ON COLUMN kpi_node_snapshot_daily.algorithm_version IS '算法版本号';
COMMENT ON COLUMN kpi_node_snapshot_daily.created_at IS '创建时间';

-- =============================================================================
-- 9.3 kpi_node_snapshot_monthly (节点级月性能评估快照) [v1.4 新增]
--   按 loop_count 加权聚合当月所有日快照
--   realtime_auto_rate 取当月最后一次小时快照的值（非聚合）
-- =============================================================================
CREATE TABLE IF NOT EXISTS kpi_node_snapshot_monthly (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    plant_node_id       UUID            NOT NULL,
    stat_month          DATE            NOT NULL,
    score               DECIMAL(5,2),
    good_value_rate     DECIMAL(5,2),
    auto_mode_rate      DECIMAL(5,2),
    effective_auto_rate DECIMAL(5,2),
    steady_rate         DECIMAL(5,2),
    accuracy_rate       DECIMAL(5,2),
    fast_rate           DECIMAL(5,2),
    oscillation_rate    DECIMAL(5,2),
    saturation_rate     DECIMAL(5,2),
    instrument_fault_rate DECIMAL(5,2),
    stiction_index      DECIMAL(5,2),
    settling_time       DECIMAL(8,2),
    output_trip_index   DECIMAL(8,2),
    ideal_settling_time DECIMAL(8,2),
    auto_loop_ratio     DECIMAL(5,2),
    realtime_auto_rate  DECIMAL(5,2),
    loop_count          INTEGER         NOT NULL DEFAULT 0,
    status              VARCHAR(20)     NOT NULL,
    algorithm_version   VARCHAR(30),
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_kpi_node_snapshot_monthly_node FOREIGN KEY (plant_node_id) REFERENCES plant_node(id) ON DELETE CASCADE,
    CONSTRAINT ck_kpi_node_snapshot_monthly_status CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')),
    CONSTRAINT uk_kpi_node_snapshot_monthly_node_month UNIQUE (plant_node_id, stat_month)
);

COMMENT ON TABLE  kpi_node_snapshot_monthly IS '节点级月性能评估快照（按 loop_count 加权聚合当月日快照，对齐 GB/T 44693.2-2024 §6.4）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.id IS '快照主键';
COMMENT ON COLUMN kpi_node_snapshot_monthly.plant_node_id IS '工厂节点 ID（FACTORY/UNIT/EQUIPMENT 任意层级）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.stat_month IS '统计月份（DATE，月初）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.score IS '加权综合评分（0-100）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.good_value_rate IS '好值率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.auto_mode_rate IS '自控率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.effective_auto_rate IS '有效自控率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.steady_rate IS '平稳率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.accuracy_rate IS '准确率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.fast_rate IS '快速率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.oscillation_rate IS '振荡率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.saturation_rate IS '饱和率加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.auto_loop_ratio IS '投自动回路占比加权均值（%）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.realtime_auto_rate IS '实时自控率（%）：取当月最后一次小时快照的值（非聚合）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.loop_count IS '参与聚合的回路数（取当月最大值）';
COMMENT ON COLUMN kpi_node_snapshot_monthly.status IS '节点级定级：EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE';
COMMENT ON COLUMN kpi_node_snapshot_monthly.algorithm_version IS '算法版本号';
COMMENT ON COLUMN kpi_node_snapshot_monthly.created_at IS '创建时间';

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
    created_at      TIMESTAMP       NOT NULL DEFAULT (timezone('UTC', now())),
    comment         VARCHAR(500),
    moc_ref         VARCHAR(255),
    moc_not_applicable BOOLEAN,
    moc_reason      VARCHAR(500),
    diagnosis_result_id UUID,
    trigger_type    VARCHAR(10)     NOT NULL DEFAULT 'manual',
    triggered_by    VARCHAR(50),
    severity        VARCHAR(20),
    effect_verified BOOLEAN,
    effect_verified_at TIMESTAMP,
    ab_compare_summary JSONB,
    -- V62-P3-008：负责人与计划执行时间
    assignee              VARCHAR(50),
    planned_at            TIMESTAMP,
    CONSTRAINT fk_action_tracker_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_action_tracker_status  CHECK (action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'IMPLEMENTED')),
    CONSTRAINT ck_action_tracker_trigger_type CHECK (trigger_type IN ('auto', 'manual')),
    CONSTRAINT ck_action_tracker_severity CHECK (
        severity IS NULL OR severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')
    )
);

COMMENT ON TABLE  action_tracker IS '轻量级异常追踪记录（诊断中心子模块）';
COMMENT ON COLUMN action_tracker.id IS '追踪记录主键';
COMMENT ON COLUMN action_tracker.loop_id IS '关联回路 ID';
COMMENT ON COLUMN action_tracker.diagnosis_label IS '自动预诊结论（如：疑似阀门粘滞）';
COMMENT ON COLUMN action_tracker.action_status IS '处理状态：PENDING/IN_PROGRESS/IGNORED/IMPLEMENTED（FDS §5.4.4 "已实施"）';
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

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_action_tracker_diagnosis_result'
          AND conrelid = 'action_tracker'::regclass
    ) THEN
        ALTER TABLE action_tracker
            ADD CONSTRAINT fk_action_tracker_diagnosis_result
            FOREIGN KEY (diagnosis_result_id) REFERENCES diagnosis_result(id) ON DELETE SET NULL;
    END IF;
END
$$;

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
    -- Phase 2.2 辨识元数据（迁移 e5f6a7b8c9d0）
    identify_method     VARCHAR(30),
    data_source         VARCHAR(20),
    time_window_start   TIMESTAMP,
    time_window_end     TIMESTAMP,
    confidence_level    VARCHAR(12),
    confidence_reason   VARCHAR(200),
    excitation_score    DECIMAL(5,2),
    residual_test_passed BOOLEAN,
    -- Phase 2.3 多 PID 对比（迁移 e5f6a7b8c9d0）
    pid_candidates      JSON,
    candidate_results   JSON,
    -- Phase 2.2 异步任务关联（迁移 e5f6a7b8c9d0）
    task_id             VARCHAR(64),
    completed_at        TIMESTAMP,
    -- V62-P3-006：引用过程模型版本（可空，兼容旧 record；迁移 p3a1b2c3d4e5）
    process_model_version_id UUID,
    -- V62-P3-007：人工实施清单字段（迁移 p3d4e5f6g7h8）
    current_pid          JSON,
    risk_assessment      JSON,
    rollback_pid         JSON,
    CONSTRAINT fk_tuning_record_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT fk_tuning_record_process_model_version FOREIGN KEY (process_model_version_id) REFERENCES process_model_version(id) ON DELETE SET NULL,
    CONSTRAINT ck_tuning_record_model   CHECK (model_type IN ('FOPDT', 'SOPDT', 'IPDT')),
    CONSTRAINT ck_tuning_record_algo    CHECK (algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC', 'IDENTIFICATION_ONLY')),
    CONSTRAINT ck_tuning_record_status  CHECK (status IN ('DRAFT', 'RUNNING', 'IDENTIFIED', 'SIMULATED', 'COMPLETED', 'INCONCLUSIVE', 'ROLLED_BACK', 'PENDING', 'APPLIED', 'VERIFIED')),
    CONSTRAINT ck_tuning_record_identify_method CHECK (identify_method IS NULL OR identify_method IN ('HISTORICAL_ARX', 'HISTORICAL_ARMAX', 'HISTORICAL_IV', 'STEP_TWO_POINT', 'STEP_AREA', 'STEP_NLS')),
    CONSTRAINT ck_tuning_record_data_source CHECK (data_source IS NULL OR data_source IN ('HISTORY', 'STEP_EXPERIMENT', 'fallback_step'))
);

COMMENT ON TABLE  tuning_record IS '整定记录（回路整定任务记录，Phase 1 仅建表，Phase 2 实现算法）';
COMMENT ON COLUMN tuning_record.id IS '整定记录主键';
COMMENT ON COLUMN tuning_record.loop_id IS '关联回路 ID';
COMMENT ON COLUMN tuning_record.model_type IS '模型类型：FOPDT/SOPDT/IPDT';
COMMENT ON COLUMN tuning_record.model_params IS '模型参数（如：{"K": 1.2, "T": 30.5, "tau": 5.0}）';
COMMENT ON COLUMN tuning_record.algorithm IS '整定算法：IMC/LAMBDA/ZN/COHEN_COON/SIMC；IDENTIFICATION_ONLY 表示纯辨识记录（V62-P3-006，不再用 IMC 占位）';
COMMENT ON COLUMN tuning_record.recommended_pid IS '推荐 PID 参数（如：{"P": 1.5, "I": 0.8, "D": 0.2}）';
COMMENT ON COLUMN tuning_record.simulation_result IS '闭环仿真结果（含阶跃响应曲线、性能指标对比）';
COMMENT ON COLUMN tuning_record.fitting_score IS '模型拟合度评分(0-100)';
COMMENT ON COLUMN tuning_record.status IS '整定状态：DRAFT/RUNNING/IDENTIFIED/SIMULATED/COMPLETED/INCONCLUSIVE/ROLLED_BACK（兼容旧枚举 PENDING/APPLIED/VERIFIED）';
COMMENT ON COLUMN tuning_record.created_by IS '创建人';
COMMENT ON COLUMN tuning_record.created_at IS '创建时间';
COMMENT ON COLUMN tuning_record.identify_method IS '辨识方法：HISTORICAL_ARX/HISTORICAL_ARMAX/HISTORICAL_IV/STEP_TWO_POINT/STEP_AREA/STEP_NLS';
COMMENT ON COLUMN tuning_record.data_source IS '数据来源：HISTORY/STEP_EXPERIMENT/fallback_step（AUTO 阶跃兜底）';
COMMENT ON COLUMN tuning_record.time_window_start IS '辨识数据窗口起始时间';
COMMENT ON COLUMN tuning_record.time_window_end IS '辨识数据窗口结束时间';
COMMENT ON COLUMN tuning_record.confidence_level IS '可信度等级：A/B/C/D/E/INCONCLUSIVE';
COMMENT ON COLUMN tuning_record.confidence_reason IS '可信度评估理由';
COMMENT ON COLUMN tuning_record.excitation_score IS '激励充分性评分';
COMMENT ON COLUMN tuning_record.residual_test_passed IS '残差白噪声检验是否通过';
COMMENT ON COLUMN tuning_record.pid_candidates IS '多组候选 PID 参数（多 PID 对比）';
COMMENT ON COLUMN tuning_record.candidate_results IS '各候选 PID 仿真结果（多 PID 对比）';
COMMENT ON COLUMN tuning_record.task_id IS '关联 Celery 异步任务 ID';
COMMENT ON COLUMN tuning_record.completed_at IS '任务完成时间';

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
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  sys_config IS '系统配置 key-value 表（运行时可变配置存储）';
COMMENT ON COLUMN sys_config.key IS '配置键（如 aas.endpoint）';
COMMENT ON COLUMN sys_config.value IS '配置值（文本）';
COMMENT ON COLUMN sys_config.description IS '配置描述';
COMMENT ON COLUMN sys_config.updated_by IS '最后更新人';
COMMENT ON COLUMN sys_config.updated_at IS '最后更新时间';

-- =============================================================================
-- 16. loop_mode_mapping (回路投用定义) [重构方案 v1.2 新增]
--   MODE 值到控制模式的映射，用于实时自控率/有效自控率/投用率计算
--   不硬编码 {1,2,3}=自动，由用户按 DCS 实际语义配置
-- =============================================================================
CREATE TABLE IF NOT EXISTS loop_mode_mapping (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id         UUID            NOT NULL,
    mode_value      INTEGER         NOT NULL,
    mode_label      VARCHAR(20)     NOT NULL,
    is_auto         BOOLEAN         NOT NULL DEFAULT FALSE,
    is_effective    BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_loop_mode_mapping_loop_mode UNIQUE (loop_id, mode_value),
    CONSTRAINT fk_loop_mode_mapping_loop_id   FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_loop_mode_mapping_label    CHECK (mode_label IN ('AUTO', 'CAS', 'REMOTE', 'APC', 'MANUAL'))
);

COMMENT ON TABLE  loop_mode_mapping IS '回路投用定义（MODE 值到控制模式的映射，用于实时自控率/有效自控率计算）';
COMMENT ON COLUMN loop_mode_mapping.id IS '主键';
COMMENT ON COLUMN loop_mode_mapping.loop_id IS '关联回路 ID';
COMMENT ON COLUMN loop_mode_mapping.mode_value IS 'DCS 返回的 MODE 值（整数）';
COMMENT ON COLUMN loop_mode_mapping.mode_label IS '控制模式：AUTO/CAS/REMOTE/APC/MANUAL';
COMMENT ON COLUMN loop_mode_mapping.is_auto IS '是否算自动控制（AUTO/CAS/REMOTE/APC 为 TRUE）';
COMMENT ON COLUMN loop_mode_mapping.is_effective IS '是否算有效自动（不饱和的自动模式为 TRUE）';
COMMENT ON COLUMN loop_mode_mapping.created_at IS '创建时间';

-- =============================================================================
-- 17. loop_type_weight (回路类型权重) [重构方案 v1.2 新增]
--   对齐 GB/T 44693.2-2024 附表1，用于回路级综合评分加权
--   公式：P = [(A*a)+(F*f)+(S*s)]/(a+f+s) * R
-- =============================================================================
CREATE TABLE IF NOT EXISTS loop_type_weight (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_type       VARCHAR(20)     NOT NULL,
    type_name       VARCHAR(50)     NOT NULL,
    weight_a        DECIMAL(3,2)    NOT NULL,
    weight_f        DECIMAL(3,2)    NOT NULL,
    weight_s        DECIMAL(3,2)    NOT NULL,
    description     TEXT,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP       DEFAULT NOW(),
    CONSTRAINT uk_loop_type_weight_type UNIQUE (loop_type),
    CONSTRAINT ck_loop_type_weight_type  CHECK (loop_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC'))
);

COMMENT ON TABLE  loop_type_weight IS '回路类型权重（对齐 GB/T 44693.2-2024 附表1，用于回路级综合评分）';
COMMENT ON COLUMN loop_type_weight.id IS '主键';
COMMENT ON COLUMN loop_type_weight.loop_type IS '回路类型：STABLE/SLOW/FAST/LOGIC';
COMMENT ON COLUMN loop_type_weight.type_name IS '类型名称（稳定型/慢速型/快速型/逻辑型）';
COMMENT ON COLUMN loop_type_weight.weight_a IS '准确率权重 a';
COMMENT ON COLUMN loop_type_weight.weight_f IS '快速率权重 f';
COMMENT ON COLUMN loop_type_weight.weight_s IS '平稳率权重 s';
COMMENT ON COLUMN loop_type_weight.description IS '类型描述';
COMMENT ON COLUMN loop_type_weight.updated_by IS '最后更新人';
COMMENT ON COLUMN loop_type_weight.updated_at IS '最后更新时间';

-- 初始数据（国标附表1）
INSERT INTO loop_type_weight (loop_type, type_name, weight_a, weight_f, weight_s, description) VALUES
    ('STABLE', '稳定型', 0.2, 0.3, 0.5, '温度/压力控制，a/f/s 相似'),
    ('SLOW',   '慢速型', 0.3, 0.1, 0.6, '缓慢调节，f 偏小'),
    ('FAST',   '快速型', 0.2, 0.5, 0.3, '副回路/速度控制，f 偏大'),
    ('LOGIC',  '逻辑型', 0.0, 0.5, 0.6, '逻辑规则控制，a 偏小')
ON CONFLICT (loop_type) DO NOTHING;

-- =============================================================================
-- 18. loop_level_weight (回路级别权重) [重构方案 v1.2 新增]
--   对齐 GB/T 44693.2-2024 附表2，用于装置级聚合加权
--   公式：装置平均性能评分 = Σ(w_i * P_i) / Σw_i
-- =============================================================================
CREATE TABLE IF NOT EXISTS loop_level_weight (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    level           INTEGER         NOT NULL,
    level_name      VARCHAR(50)     NOT NULL,
    weight          DECIMAL(3,1)    NOT NULL,
    description     TEXT,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP       DEFAULT NOW(),
    CONSTRAINT uk_loop_level_weight_level UNIQUE (level),
    CONSTRAINT ck_loop_level_weight_level  CHECK (level IN (1, 2, 3))
);

COMMENT ON TABLE  loop_level_weight IS '回路级别权重（对齐 GB/T 44693.2-2024 附表2，用于装置级聚合加权）';
COMMENT ON COLUMN loop_level_weight.id IS '主键';
COMMENT ON COLUMN loop_level_weight.level IS '回路级别：1/2/3';
COMMENT ON COLUMN loop_level_weight.level_name IS '级别名称（一级/二级/三级）';
COMMENT ON COLUMN loop_level_weight.weight IS '级别权重：3.0/2.0/1.0';
COMMENT ON COLUMN loop_level_weight.description IS '级别描述';
COMMENT ON COLUMN loop_level_weight.updated_by IS '最后更新人';
COMMENT ON COLUMN loop_level_weight.updated_at IS '最后更新时间';

-- 初始数据（国标附表2）
INSERT INTO loop_level_weight (level, level_name, weight, description) VALUES
    (1, '一级', 3.0, '决定性影响：负荷控制/联锁相关'),
    (2, '二级', 2.0, '辅助保障：稳定性/设备安全'),
    (3, '三级', 1.0, '次要辅助：维持辅助设备运行')
ON CONFLICT (level) DO NOTHING;

-- =============================================================================
-- 19. kpi_snapshot_custom（自定义评估任务快照）
-- 证据：ORM app/models/metric.py；迁移 k2f3a4b5c6d7、n7q8r9s0t1u2、
--       33cee6882ec8、h8b9c0d1e2f3
-- =============================================================================
CREATE TABLE IF NOT EXISTS kpi_snapshot_custom (
    id                      UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id                 UUID            NOT NULL,
    loop_id                 UUID            NOT NULL,
    ts_start                TIMESTAMP       NOT NULL,
    ts_end                  TIMESTAMP       NOT NULL,
    score                   DECIMAL(5,2),
    accuracy_rate           DECIMAL(5,2),
    fast_rate               DECIMAL(5,2),
    steady_rate             DECIMAL(5,2),
    effective_auto_rate     DECIMAL(5,2),
    good_value_rate         DECIMAL(5,2),
    oscillation_rate        DECIMAL(5,2),
    saturation_rate         DECIMAL(5,2),
    stiction_index          DECIMAL(5,2),
    output_trip_index       DECIMAL(8,2),
    settling_time           DECIMAL(8,2),
    ideal_settling_time     DECIMAL(8,2),
    auto_mode_rate          DECIMAL(5,2),
    algorithm_version       VARCHAR(50),
    sampling_freq           VARCHAR(10),
    quality_policy          VARCHAR(30),
    status                  VARCHAR(20)     NOT NULL,
    confidence_level        CHAR(1),
    valid_rate              DECIMAL(5,4),
    data_lineage            JSONB,
    created_at              TIMESTAMP       DEFAULT (timezone('UTC', now())),
    instrument_fault_rate   DECIMAL(5,2),
    pv_mean                 DECIMAL(10,3),
    pv_std                  DECIMAL(10,3),
    sp_mean                 DECIMAL(10,3),
    sp_std                  DECIMAL(10,3),
    op_mean                 DECIMAL(10,3),
    op_std                  DECIMAL(10,3),
    error_mean              DECIMAL(10,3),
    error_std               DECIMAL(10,3),
    valve_linearity         DECIMAL(5,4),
    valve_nonlinearity      DECIMAL(5,4),
    valve_op_min            DECIMAL(8,2),
    valve_op_max            DECIMAL(8,2),
    oscillation_amplitude   DECIMAL(8,2),
    setpoint_crossing_count DECIMAL(10,0),
    time_constant           DECIMAL(8,2),
    CONSTRAINT uq_kpi_custom_task_loop UNIQUE (task_id, loop_id),
    CONSTRAINT fk_kpi_custom_loop FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_kpi_custom_status CHECK (status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')),
    CONSTRAINT ck_kpi_custom_window CHECK (ts_end > ts_start)
);

-- =============================================================================
-- 20. clpm_metric_data_requirement（指标数据需求契约）
-- 证据：ORM app/models/metric_data_requirement.py；迁移 k2f3a4b5c6d7
-- =============================================================================
CREATE TABLE IF NOT EXISTS clpm_metric_data_requirement (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    metric_code         VARCHAR(50)     NOT NULL UNIQUE,
    metric_name         VARCHAR(100)    NOT NULL,
    tag_group           VARCHAR(20)     NOT NULL,
    tags                JSONB           NOT NULL,
    sampling_strategy   VARCHAR(30)     NOT NULL,
    quality_policy      VARCHAR(30)     NOT NULL,
    mask_expression     VARCHAR(200),
    aggregation_policy  VARCHAR(20),
    depends_on          JSONB,
    version             VARCHAR(20)     DEFAULT 'v1',
    updated_at          TIMESTAMP       DEFAULT (timezone('UTC', now()))
);

-- =============================================================================
-- 21. diagnosis_tag（诊断标签）
-- 证据：ORM app/models/diagnosis.py；迁移 k2f3a4b5c6d7、h8b9c0d1e2f3
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnosis_tag (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id             UUID            NOT NULL,
    tag_code            VARCHAR(50)     NOT NULL,
    tag_name            VARCHAR(100),
    severity            VARCHAR(20)     NOT NULL,
    source_metric       VARCHAR(50),
    trigger_condition   JSONB,
    trigger_value       DECIMAL(10,4),
    triggered_at        TIMESTAMP       NOT NULL DEFAULT (timezone('UTC', now())),
    resolved_at         TIMESTAMP,
    resolved_by         UUID,
    resolution_note     TEXT,
    status              VARCHAR(20)     NOT NULL DEFAULT 'ACTIVE',
    CONSTRAINT fk_diagnosis_tag_loop FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_diag_tag_severity CHECK (severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')),
    CONSTRAINT ck_diag_tag_status CHECK (status IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED'))
);

-- =============================================================================
-- 22. unit_kpi_summary（装置级 KPI 汇总）
-- 证据：ORM app/models/unit_kpi_summary.py；迁移 k2f3a4b5c6d7、
--       p9r0s1t2u3v4、e7f8a9b0c1d2、h8b9c0d1e2f3
-- =============================================================================
CREATE TABLE IF NOT EXISTS unit_kpi_summary (
    id                      UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_id                 UUID            NOT NULL,
    snapshot_time           TIMESTAMP       NOT NULL,
    avg_score               DECIMAL(5,2),
    auto_mode_rate          DECIMAL(5,2),
    effective_auto_rate     DECIMAL(5,2),
    stability_rate          DECIMAL(5,2),
    accuracy_rate           DECIMAL(5,2),
    fast_rate               DECIMAL(5,2),
    good_value_rate         DECIMAL(5,2),
    oscillation_rate        DECIMAL(5,2),
    saturation_rate         DECIMAL(5,2),
    instrument_fault_rate   DECIMAL(5,2),
    total_loops             INTEGER,
    evaluated_loops         INTEGER,
    inconclusive_loops      INTEGER,
    excluded_loops          INTEGER         NOT NULL DEFAULT 0,
    status                  VARCHAR(20)     NOT NULL DEFAULT 'SUCCESS',
    algorithm_version       VARCHAR(50),
    created_at              TIMESTAMP       DEFAULT (timezone('UTC', now())),
    CONSTRAINT uq_unit_kpi_summary_node_time UNIQUE (node_id, snapshot_time),
    CONSTRAINT fk_unit_kpi_summary_node FOREIGN KEY (node_id) REFERENCES plant_node(id) ON DELETE CASCADE,
    CONSTRAINT ck_unit_kpi_summary_status CHECK (status IN ('SUCCESS', 'PARTIAL', 'EMPTY'))
);

-- =============================================================================
-- 23. report_config（自动报表配置）
-- 证据：ORM app/models/report_config.py
-- =============================================================================
CREATE TABLE IF NOT EXISTS report_config (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                VARCHAR(100)    NOT NULL,
    report_period       VARCHAR(20)     NOT NULL,
    recipients          TEXT            NOT NULL,
    content_template    TEXT,
    is_enabled          BOOLEAN         DEFAULT TRUE,
    created_by          VARCHAR(50),
    updated_by          VARCHAR(50),
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_report_config_period CHECK (report_period IN ('SHIFT', 'DAILY', 'WEEKLY', 'MONTHLY'))
);

-- =============================================================================
-- 24-27. DCS 品牌 / 型号 / 标准 MODE / MODE 映射
-- 证据：ORM app/models/dcs_*.py、mode_definition.py；迁移 v6p1dcs001
-- =============================================================================
CREATE TABLE IF NOT EXISTS dcs_vendor (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    code            VARCHAR(50)     NOT NULL UNIQUE,
    name            VARCHAR(100)    NOT NULL,
    name_en         VARCHAR(100),
    description     VARCHAR(500),
    sort_order      INTEGER         NOT NULL DEFAULT 0,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dcs_model (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    vendor_id       UUID            NOT NULL,
    code            VARCHAR(100)    NOT NULL UNIQUE,
    name            VARCHAR(200)    NOT NULL,
    description     VARCHAR(500),
    sort_order      INTEGER         NOT NULL DEFAULT 0,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_dcs_model_vendor FOREIGN KEY (vendor_id) REFERENCES dcs_vendor(id) ON DELETE RESTRICT
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_loop_ledger_dcs_model'
          AND conrelid = 'loop_ledger'::regclass
    ) THEN
        ALTER TABLE loop_ledger
            ADD CONSTRAINT fk_loop_ledger_dcs_model
            FOREIGN KEY (dcs_model_id) REFERENCES dcs_model(id) ON DELETE SET NULL;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS mode_definition (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    standard_mode   INTEGER         NOT NULL UNIQUE,
    label_zh        VARCHAR(20)     NOT NULL,
    label_en        VARCHAR(20)     NOT NULL,
    is_auto         BOOLEAN         NOT NULL DEFAULT FALSE,
    color           VARCHAR(20)     NOT NULL DEFAULT '#999999',
    sort_order      INTEGER         NOT NULL DEFAULT 0,
    description     VARCHAR(500),
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_mode_definition_standard_mode CHECK (standard_mode IN (0, 1, 2, 3, 4))
);

CREATE TABLE IF NOT EXISTS dcs_mode_mapping (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    dcs_model_id    UUID,
    standard_mode   INTEGER         NOT NULL,
    raw_mode_value  INTEGER         NOT NULL,
    description     VARCHAR(500),
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_dcs_mode_mapping_model FOREIGN KEY (dcs_model_id) REFERENCES dcs_model(id) ON DELETE CASCADE
);

-- =============================================================================
-- 28. diagnosis_task（诊断任务）
-- 证据：ORM app/models/diagnosis.py；迁移 v6p1diag001、h8b9c0d1e2f3
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnosis_task (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id             UUID            NOT NULL,
    trigger_type        VARCHAR(10)     NOT NULL,
    triggered_by        VARCHAR(50),
    status              VARCHAR(20)     NOT NULL DEFAULT 'PENDING',
    time_range_start    TIMESTAMP,
    time_range_end      TIMESTAMP,
    error_message       TEXT,
    triggered_at        TIMESTAMP       NOT NULL DEFAULT (timezone('UTC', now())),
    completed_at        TIMESTAMP,
    is_archived         BOOLEAN         NOT NULL DEFAULT FALSE,
    archived_at         TIMESTAMP,
    archived_by         VARCHAR(50),
    CONSTRAINT fk_diagnosis_task_loop FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_diag_task_trigger_type CHECK (trigger_type IN ('manual', 'auto')),
    CONSTRAINT ck_diag_task_status CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED'))
);

-- diagnosis_result 的 task_id 在 diagnosis_task 创建后补充，避免前向引用。
ALTER TABLE diagnosis_result
    ADD COLUMN IF NOT EXISTS threshold_version INTEGER;
ALTER TABLE diagnosis_result
    ADD COLUMN IF NOT EXISTS task_id UUID;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_diagnosis_result_task'
          AND conrelid = 'diagnosis_result'::regclass
    ) THEN
        ALTER TABLE diagnosis_result
            ADD CONSTRAINT fk_diagnosis_result_task
            FOREIGN KEY (task_id) REFERENCES diagnosis_task(id) ON DELETE SET NULL;
    END IF;
END
$$;

-- =============================================================================
-- 29. dcs_pid_structure（DCS 型号 PID 结构）
-- 证据：ORM app/models/dcs_pid_structure.py；迁移 a9b0c1d2e3f4
-- =============================================================================
CREATE TABLE IF NOT EXISTS dcs_pid_structure (
    id                      UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    dcs_model_id            UUID            NOT NULL UNIQUE,
    p_type                  VARCHAR(20)     NOT NULL DEFAULT 'PROPORTION',
    i_unit                  VARCHAR(10)     NOT NULL DEFAULT 'SECONDS',
    d_unit                  VARCHAR(10)     NOT NULL DEFAULT 'SECONDS',
    d_filter_enabled        BOOLEAN         NOT NULL DEFAULT FALSE,
    d_filter_unit           VARCHAR(10),
    d_filter_multiplier     BOOLEAN         NOT NULL DEFAULT FALSE,
    description             VARCHAR(500),
    created_at              TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_dcs_pid_structure_model FOREIGN KEY (dcs_model_id) REFERENCES dcs_model(id) ON DELETE CASCADE,
    CONSTRAINT ck_dcs_pid_structure_filter_unit CHECK (d_filter_enabled = FALSE OR d_filter_unit IS NOT NULL)
);

-- =============================================================================
-- 30. algorithm_parameter（指标算法参数）
-- 证据：ORM app/models/algorithm_parameter.py；迁移 f8a9b0c1d2e3
-- =============================================================================
CREATE TABLE IF NOT EXISTS algorithm_parameter (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    metric_code     VARCHAR(50)     NOT NULL,
    control_type    VARCHAR(20)     NOT NULL,
    params          JSONB           NOT NULL DEFAULT '{}'::jsonb,
    description     VARCHAR(255),
    is_enabled      BOOLEAN         NOT NULL DEFAULT TRUE,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP,
    version         INTEGER         NOT NULL DEFAULT 1,
    CONSTRAINT ck_algorithm_parameter_control_type CHECK (control_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC')),
    CONSTRAINT uk_algorithm_param_code_type UNIQUE (metric_code, control_type)
);

-- =============================================================================
-- 31. diagnosis_rule（诊断专家规则）
-- 证据：ORM app/models/diagnosis.py；迁移 c3d4e5f6g7h8
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnosis_rule (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_code           VARCHAR(20)     NOT NULL,
    rule_name           VARCHAR(100)    NOT NULL,
    priority            INTEGER         NOT NULL DEFAULT 100,
    condition_expr      TEXT            NOT NULL,
    action_type         VARCHAR(20)     NOT NULL,
    action_params       JSONB,
    is_enabled          BOOLEAN         NOT NULL DEFAULT TRUE,
    version             INTEGER         NOT NULL DEFAULT 1,
    updated_by          VARCHAR(50),
    updated_at          TIMESTAMP,
    CONSTRAINT ck_diag_rule_action_type CHECK (action_type IN ('REMOVE_LABEL', 'ADD_LABEL', 'KEEP_HIGHEST', 'FILTER_ONLY', 'SORT_PRIORITY'))
);

-- =============================================================================
-- 32. diagnosis_threshold_override（诊断阈值作用域覆盖）
-- 证据：ORM app/models/diagnosis.py；迁移 d4e5f6g7h8i9
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnosis_threshold_override (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    diag_code       VARCHAR(50)     NOT NULL,
    scope_type      VARCHAR(20)     NOT NULL,
    scope_id        VARCHAR(100)    NOT NULL,
    threshold       JSONB,
    version         INTEGER         NOT NULL DEFAULT 1,
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP,
    CONSTRAINT ck_diag_threshold_override_scope CHECK (scope_type IN ('loop_type', 'plant', 'loop'))
);

-- =============================================================================
-- 33. diagnosis_config_change（诊断配置审批留痕）
-- 证据：ORM app/models/diagnosis.py；迁移 f6g7h8i9j0k1、h8b9c0d1e2f3
-- =============================================================================
CREATE TABLE IF NOT EXISTS diagnosis_config_change (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    target_type     VARCHAR(20)     NOT NULL,
    target_id       VARCHAR(100)    NOT NULL,
    change_type     VARCHAR(20)     NOT NULL,
    before_value    TEXT,
    after_value     TEXT,
    status          VARCHAR(20)     NOT NULL DEFAULT 'PENDING',
    requested_by    VARCHAR(50)     NOT NULL,
    requested_at    TIMESTAMP       NOT NULL DEFAULT (timezone('UTC', now())),
    reviewed_by     VARCHAR(50),
    reviewed_at     TIMESTAMP,
    review_note     TEXT,
    effective_from  TIMESTAMP,
    CONSTRAINT ck_diag_config_change_target CHECK (target_type IN ('config', 'rule', 'trigger')),
    CONSTRAINT ck_diag_config_change_type CHECK (change_type IN ('update', 'enable', 'disable')),
    CONSTRAINT ck_diag_config_change_status CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED'))
);

-- =============================================================================
-- 34. loop_confidence_latest（回路最新可信度）
-- 证据：ORM app/models/metric.py；迁移 z1a2b3c4d5e6
-- =============================================================================
CREATE TABLE IF NOT EXISTS loop_confidence_latest (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id             UUID            NOT NULL,
    eval_time           TIMESTAMP       NOT NULL,
    data_ts_start       TIMESTAMP       NOT NULL,
    data_ts_end         TIMESTAMP       NOT NULL,
    status              VARCHAR(20)     NOT NULL,
    score               DECIMAL(5,2),
    confidence_level    VARCHAR(1),
    valid_rate          FLOAT,
    metrics             JSONB,
    algorithm_version   VARCHAR(50),
    updated_at          TIMESTAMP,
    CONSTRAINT fk_loop_confidence_latest_loop FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT ck_loop_confidence_latest_status CHECK (status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')),
    CONSTRAINT ck_loop_confidence_latest_confidence CHECK (confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E'))
);

-- =============================================================================
-- 35. process_model_version（过程模型版本聚合，V62-P3-003）
-- 证据：ORM app/models/process_model_version.py；迁移 p3a1b2c3d4e5
-- 不可变版本化辨识证据；CANDIDATE/CURRENT/RETIRED 生命周期
-- =============================================================================
CREATE TABLE IF NOT EXISTS process_model_version (
    id                      UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    loop_id                 UUID            NOT NULL,
    version                 INTEGER         NOT NULL,
    status                  VARCHAR(12)     NOT NULL DEFAULT 'CANDIDATE',
    data_window_start       TIMESTAMP,
    data_window_end         TIMESTAMP,
    data_hash               VARCHAR(64),
    condition_summary       JSON,
    algorithm_version       VARCHAR(50),
    identify_method         VARCHAR(30),
    model_type              VARCHAR(20)     NOT NULL,
    model_params            JSON,
    theta_source            VARCHAR(20),
    sampling_period         FLOAT,
    metrics                 JSON,
    residual_test           JSON,
    uncertainty             JSON,
    physical_feasibility    JSON,
    confidence_level        VARCHAR(12),
    confidence_reason       VARCHAR(500),
    published_by            VARCHAR(50),
    published_at            TIMESTAMP,
    supersedes_version_id   UUID,
    retired_reason          VARCHAR(500),
    retired_at              TIMESTAMP,
    retired_by              VARCHAR(50),
    created_by              VARCHAR(50),
    created_at              TIMESTAMP       NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
    CONSTRAINT fk_process_model_version_loop_id FOREIGN KEY (loop_id) REFERENCES loop_ledger(id) ON DELETE CASCADE,
    CONSTRAINT fk_process_model_version_supersedes FOREIGN KEY (supersedes_version_id) REFERENCES process_model_version(id) ON DELETE SET NULL,
    CONSTRAINT ck_process_model_version_status CHECK (status IN ('CANDIDATE', 'CURRENT', 'RETIRED')),
    CONSTRAINT ck_process_model_version_model_type CHECK (model_type IN ('FOPDT', 'SOPDT', 'IPDT')),
    CONSTRAINT ck_process_model_version_theta_source CHECK (theta_source IS NULL OR theta_source IN ('EXPLICIT', 'SEARCHED', 'HEURISTIC_2TS')),
    CONSTRAINT ck_process_model_version_identify_method CHECK (identify_method IS NULL OR identify_method IN ('HISTORICAL_ARX', 'HISTORICAL_ARMAX', 'HISTORICAL_IV', 'STEP_TWO_POINT', 'STEP_AREA', 'STEP_NLS')),
    CONSTRAINT ck_process_model_version_confidence CHECK (confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E', 'INCONCLUSIVE'))
);

COMMENT ON TABLE  process_model_version IS '过程模型版本聚合（V62-P3-003，不可变版本化辨识证据）';
COMMENT ON COLUMN process_model_version.loop_id IS '关联回路 ID（Loop 是模型所有者，不建 process_model 主表）';
COMMENT ON COLUMN process_model_version.version IS '单回路内单调递增版本号';
COMMENT ON COLUMN process_model_version.status IS '生命周期：CANDIDATE（候选）/ CURRENT（当前生效）/ RETIRED（退役）';
COMMENT ON COLUMN process_model_version.data_hash IS '数据快照哈希（输入时序指纹，漂移比较与重复辨识识别）';
COMMENT ON COLUMN process_model_version.condition_summary IS '工况摘要：MODE 占比/饱和/激励/采样率/有效样本率';
COMMENT ON COLUMN process_model_version.model_params IS '模型参数（如 {"K":1.2,"tau":30.5,"theta":5.0}），不可变';
COMMENT ON COLUMN process_model_version.theta_source IS '纯滞后来源：EXPLICIT/SEARCHED/HEURISTIC_2TS';
COMMENT ON COLUMN process_model_version.metrics IS '验证指标：r2_train/r2_val/nrmse_val/aic/bic/fitting_score';
COMMENT ON COLUMN process_model_version.supersedes_version_id IS '替代的上一版本（自引用，RETIRE 旧版本时回填）';
COMMENT ON COLUMN process_model_version.published_by IS '发布人（仅人工窗口模型可审批为 CURRENT）';

-- =============================================================================
-- 索引（高频查询字段）
-- =============================================================================

-- loop_ledger 索引
CREATE INDEX IF NOT EXISTS idx_loop_ledger_unit_id ON loop_ledger (unit_id);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_status  ON loop_ledger (status);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_tag_name ON loop_ledger (tag_name);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_importance_level ON loop_ledger (importance_level);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_dcs_model ON loop_ledger (dcs_model_id);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_complex_group ON loop_ledger (complex_loop_group_id);

-- tag_registry 索引
CREATE INDEX IF NOT EXISTS idx_tag_registry_tag_name ON tag_registry (tag_name);
CREATE INDEX IF NOT EXISTS idx_tag_registry_tag_type ON tag_registry (tag_type);
CREATE INDEX IF NOT EXISTS idx_tag_registry_is_linked ON tag_registry (is_linked);

-- loop_tag_mapping 索引
CREATE INDEX IF NOT EXISTS idx_loop_tag_mapping_loop_id ON loop_tag_mapping (loop_id);
CREATE INDEX IF NOT EXISTS idx_loop_tag_mapping_tag_id  ON loop_tag_mapping (tag_id);
-- (loop_id, tag_role) 已由唯一约束 uk_loop_tag_mapping_loop_role 自动创建索引

-- v4.0+ / v6.1 新增表索引
CREATE INDEX IF NOT EXISTS ix_kpi_snapshot_custom_task ON kpi_snapshot_custom (task_id);
CREATE INDEX IF NOT EXISTS ix_kpi_snapshot_custom_loop_ts ON kpi_snapshot_custom (loop_id, ts_start);
CREATE INDEX IF NOT EXISTS ix_diagnosis_tag_loop_status ON diagnosis_tag (loop_id, status);
CREATE INDEX IF NOT EXISTS ix_diagnosis_tag_severity ON diagnosis_tag (severity, triggered_at);
CREATE INDEX IF NOT EXISTS ix_unit_kpi_summary_node_time ON unit_kpi_summary (node_id, snapshot_time);
CREATE INDEX IF NOT EXISTS idx_report_config_period ON report_config (report_period);
CREATE INDEX IF NOT EXISTS idx_report_config_is_enabled ON report_config (is_enabled);
CREATE INDEX IF NOT EXISTS idx_dcs_vendor_sort ON dcs_vendor (sort_order);
CREATE INDEX IF NOT EXISTS idx_dcs_model_vendor ON dcs_model (vendor_id);
CREATE INDEX IF NOT EXISTS idx_dcs_model_sort ON dcs_model (sort_order);
CREATE INDEX IF NOT EXISTS idx_mode_definition_sort ON mode_definition (sort_order);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dcs_mode_mapping_model_mode
    ON dcs_mode_mapping (dcs_model_id, standard_mode) WHERE dcs_model_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uk_dcs_mode_mapping_default
    ON dcs_mode_mapping (standard_mode) WHERE dcs_model_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_dcs_mode_mapping_model_raw
    ON dcs_mode_mapping (dcs_model_id, raw_mode_value);
CREATE INDEX IF NOT EXISTS idx_diagnosis_task_loop_id ON diagnosis_task (loop_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_task_status ON diagnosis_task (status);
CREATE INDEX IF NOT EXISTS idx_diagnosis_task_archived ON diagnosis_task (is_archived);
CREATE INDEX IF NOT EXISTS idx_diagnosis_result_task_id ON diagnosis_result (task_id);
CREATE INDEX IF NOT EXISTS idx_dcs_pid_structure_model ON dcs_pid_structure (dcs_model_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_diagnosis_rule_code ON diagnosis_rule (rule_code);
CREATE INDEX IF NOT EXISTS idx_diagnosis_rule_priority ON diagnosis_rule (priority);
CREATE UNIQUE INDEX IF NOT EXISTS uk_diag_threshold_override
    ON diagnosis_threshold_override (diag_code, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_diag_threshold_override_scope
    ON diagnosis_threshold_override (scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_diag_config_change_target
    ON diagnosis_config_change (target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_diag_config_change_status ON diagnosis_config_change (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_loop_confidence_latest_loop_id
    ON loop_confidence_latest (loop_id);

-- kpi_snapshot_hourly 索引
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_loop_id  ON kpi_snapshot_hourly (loop_id);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_ts_start ON kpi_snapshot_hourly (ts_start);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_status   ON kpi_snapshot_hourly (status);
-- 复合索引（S1-C2）：优化常见查询模式
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_ts_loop ON kpi_snapshot_hourly (ts_start, loop_id);

-- kpi_node_snapshot_hourly 索引
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_node_id    ON kpi_node_snapshot_hourly (plant_node_id);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_ts_start   ON kpi_node_snapshot_hourly (ts_start);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_status    ON kpi_node_snapshot_hourly (status);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_node_ts   ON kpi_node_snapshot_hourly (plant_node_id, ts_start);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_ts_status ON kpi_node_snapshot_hourly (ts_start, status, score);

-- kpi_node_snapshot_daily 索引（v1.4 新增）
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_node_id    ON kpi_node_snapshot_daily (plant_node_id);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_stat_date  ON kpi_node_snapshot_daily (stat_date);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_status     ON kpi_node_snapshot_daily (status);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_node_date  ON kpi_node_snapshot_daily (plant_node_id, stat_date);

-- kpi_node_snapshot_monthly 索引（v1.4 新增）
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_node_id    ON kpi_node_snapshot_monthly (plant_node_id);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_stat_month ON kpi_node_snapshot_monthly (stat_month);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_status     ON kpi_node_snapshot_monthly (status);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_node_month ON kpi_node_snapshot_monthly (plant_node_id, stat_month);

-- action_tracker 索引
CREATE INDEX IF NOT EXISTS idx_action_tracker_loop_id       ON action_tracker (loop_id);
CREATE INDEX IF NOT EXISTS idx_action_tracker_action_status ON action_tracker (action_status);
CREATE INDEX IF NOT EXISTS idx_action_tracker_trigger_type ON action_tracker (trigger_type);
CREATE INDEX IF NOT EXISTS idx_action_tracker_severity_status ON action_tracker (severity, action_status);
CREATE INDEX IF NOT EXISTS idx_action_tracker_loop_created ON action_tracker (loop_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_tracker_effect_verified ON action_tracker (effect_verified);
CREATE INDEX IF NOT EXISTS idx_action_tracker_status_updated ON action_tracker (action_status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS uk_action_tracker_open
    ON action_tracker (loop_id, diagnosis_label)
    WHERE action_status IN ('PENDING', 'IN_PROGRESS')
      AND loop_id IS NOT NULL
      AND diagnosis_label IS NOT NULL;

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_sys_config_key ON sys_config (key);

-- loop_mode_mapping 索引（重构方案 v1.2）
CREATE INDEX IF NOT EXISTS idx_loop_mode_mapping_loop_id ON loop_mode_mapping (loop_id);

-- plant_node 索引（SVC-10 位号触发监控）
CREATE INDEX IF NOT EXISTS idx_plant_node_monitor_tag_id ON plant_node (monitor_tag_id);

-- process_model_version 索引（V62-P3-003 模型生命周期）
-- P3-004 并发一致性：同一回路至多一个 CURRENT（部分唯一索引）
CREATE UNIQUE INDEX IF NOT EXISTS uk_process_model_version_current
    ON process_model_version (loop_id) WHERE status = 'CURRENT';
-- (loop_id, version) 唯一：版本号单回路单调不重复
CREATE UNIQUE INDEX IF NOT EXISTS uk_process_model_version_loop_version
    ON process_model_version (loop_id, version);
CREATE INDEX IF NOT EXISTS idx_process_model_version_loop_status
    ON process_model_version (loop_id, status);

-- =============================================================================
-- 脚本结束
-- =============================================================================
