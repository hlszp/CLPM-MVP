-- ============================================================
-- CLPM 数据库 Schema (自动生成自 ORM 模型)
-- 生成时间: 2026-07-10
-- 说明: 由 backend/app/models/ 自动生成，请勿手动修改
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- clpm_metric_data_requirement
-- ============================================================
CREATE TABLE IF NOT EXISTS clpm_metric_data_requirement (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	metric_code VARCHAR(50) NOT NULL, 
	metric_name VARCHAR(100) NOT NULL, 
	tag_group VARCHAR(20) NOT NULL, 
	tags JSONB NOT NULL, 
	sampling_strategy VARCHAR(30) NOT NULL, 
	quality_policy VARCHAR(30) NOT NULL, 
	mask_expression VARCHAR(200), 
	aggregation_policy VARCHAR(20), 
	depends_on JSONB, 
	version VARCHAR(20) DEFAULT 'v1', 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (metric_code)
);

-- ============================================================
-- dcs_vendor
-- ============================================================
CREATE TABLE IF NOT EXISTS dcs_vendor (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	name_en VARCHAR(100), 
	description VARCHAR(500), 
	sort_order INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

-- ============================================================
-- diagnosis_config
-- ============================================================
CREATE TABLE IF NOT EXISTS diagnosis_config (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	diag_code VARCHAR(50) NOT NULL, 
	diag_name VARCHAR(100) NOT NULL, 
	algorithm_type VARCHAR(50) NOT NULL, 
	calc_method VARCHAR(50), 
	params JSON, 
	threshold JSONB, 
	is_enabled BOOLEAN, 
	updated_by VARCHAR(50), 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	version INTEGER, 
	PRIMARY KEY (id)
);

-- ============================================================
-- engine_rule
-- ============================================================
CREATE TABLE IF NOT EXISTS engine_rule (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	rule_code VARCHAR(50) NOT NULL, 
	rule_name VARCHAR(100) NOT NULL, 
	rule_type VARCHAR(20) NOT NULL, 
	params JSON, 
	is_enabled BOOLEAN, 
	updated_by VARCHAR(50), 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_engine_rule_type CHECK (rule_type IN ('CALC_CYCLE', 'DATA_FETCH', 'SCHEDULE'))
);

-- ============================================================
-- loop_level_weight
-- ============================================================
CREATE TABLE IF NOT EXISTS loop_level_weight (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	level INTEGER NOT NULL, 
	level_name VARCHAR(50) NOT NULL, 
	weight NUMERIC(3, 1) NOT NULL, 
	description TEXT, 
	updated_by VARCHAR(50), 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_loop_level_weight_level CHECK (level IN (1, 2, 3)), 
	UNIQUE (level)
);

-- ============================================================
-- loop_type_weight
-- ============================================================
CREATE TABLE IF NOT EXISTS loop_type_weight (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_type VARCHAR(20) NOT NULL, 
	type_name VARCHAR(50) NOT NULL, 
	weight_a NUMERIC(3, 2) NOT NULL, 
	weight_f NUMERIC(3, 2) NOT NULL, 
	weight_s NUMERIC(3, 2) NOT NULL, 
	description TEXT, 
	updated_by VARCHAR(50), 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_loop_type_weight_type CHECK (loop_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC')), 
	UNIQUE (loop_type)
);

-- ============================================================
-- metric_config
-- ============================================================
CREATE TABLE IF NOT EXISTS metric_config (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	metric_code VARCHAR(50) NOT NULL, 
	metric_name VARCHAR(100) NOT NULL, 
	formula TEXT, 
	weight NUMERIC(5, 2), 
	threshold JSONB, 
	control_type VARCHAR(20), 
	grading_thresholds JSONB, 
	is_enabled BOOLEAN, 
	updated_by VARCHAR(50), 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	version INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_metric_config_control_type CHECK (control_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC'))
);

-- ============================================================
-- mode_definition
-- ============================================================
CREATE TABLE IF NOT EXISTS mode_definition (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	standard_mode INTEGER NOT NULL, 
	label_zh VARCHAR(20) NOT NULL, 
	label_en VARCHAR(20) NOT NULL, 
	is_auto BOOLEAN NOT NULL, 
	color VARCHAR(20) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	description VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_mode_definition_standard_mode CHECK (standard_mode IN (0, 1, 2, 3, 4)), 
	UNIQUE (standard_mode)
);

-- ============================================================
-- report_config
-- ============================================================
CREATE TABLE IF NOT EXISTS report_config (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	name VARCHAR(100) NOT NULL, 
	report_period VARCHAR(20) NOT NULL, 
	recipients TEXT NOT NULL, 
	content_template TEXT, 
	is_enabled BOOLEAN, 
	created_by VARCHAR(50), 
	updated_by VARCHAR(50), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_report_config_period CHECK (report_period IN ('SHIFT', 'DAILY', 'WEEKLY', 'MONTHLY'))
);

-- ============================================================
-- report_record
-- ============================================================
CREATE TABLE IF NOT EXISTS report_record (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	report_period VARCHAR(20) NOT NULL, 
	generated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	file_url VARCHAR(255), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_report_record_period CHECK (report_period IN ('SHIFT', 'DAILY', 'WEEKLY', 'MONTHLY')), 
	CONSTRAINT ck_report_record_status CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED'))
);

-- ============================================================
-- sys_audit_log
-- ============================================================
CREATE TABLE IF NOT EXISTS sys_audit_log (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	operator VARCHAR(50) NOT NULL, 
	operation_type VARCHAR(50) NOT NULL, 
	target_type VARCHAR(50), 
	target_id UUID, 
	before_value TEXT, 
	after_value TEXT, 
	operated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

-- ============================================================
-- sys_config
-- ============================================================
CREATE TABLE IF NOT EXISTS sys_config (
	key VARCHAR(100) NOT NULL, 
	value TEXT, 
	description VARCHAR(255), 
	updated_by VARCHAR(50), 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);

-- ============================================================
-- sys_user
-- ============================================================
CREATE TABLE IF NOT EXISTS sys_user (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	username VARCHAR(50) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	display_name VARCHAR(100) NOT NULL, 
	email VARCHAR(255), 
	role VARCHAR(20) NOT NULL, 
	is_active BOOLEAN, 
	last_login_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_sys_user_role CHECK (role IN ('ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT'))
);

-- ============================================================
-- tag_registry
-- ============================================================
CREATE TABLE IF NOT EXISTS tag_registry (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	tag_name VARCHAR(100) NOT NULL, 
	tag_description VARCHAR(255), 
	tag_type VARCHAR(20) NOT NULL, 
	current_value FLOAT, 
	quality VARCHAR(20), 
	last_sync_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	is_linked BOOLEAN, 
	range_min FLOAT, 
	range_max FLOAT, 
	unit VARCHAR(20), 
	measure_type VARCHAR(20), 
	tdengine_tag_id VARCHAR(100), 
	PRIMARY KEY (id), 
	CONSTRAINT ck_tag_registry_type CHECK (tag_type IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D', 'OTHER')), 
	CONSTRAINT ck_tag_registry_quality CHECK (quality IS NULL OR quality IN ('GOOD', 'BAD', 'UNCERTAIN')), 
	CONSTRAINT ck_tag_registry_measure_type CHECK (measure_type IS NULL OR measure_type IN ('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', 'ANALYSIS', 'SPEED', 'OTHER'))
);

-- ============================================================
-- dcs_model
-- ============================================================
CREATE TABLE IF NOT EXISTS dcs_model (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	vendor_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	code VARCHAR(100) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	description VARCHAR(500), 
	sort_order INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(vendor_id) REFERENCES dcs_vendor (id) ON DELETE RESTRICT, 
	UNIQUE (code)
);

-- ============================================================
-- plant_node
-- ============================================================
CREATE TABLE IF NOT EXISTS plant_node (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	name VARCHAR(100) NOT NULL, 
	type VARCHAR(20) NOT NULL, 
	parent_id UUID, 
	is_kpi_enabled BOOLEAN, 
	monitor_tag_id UUID, 
	monitor_trigger_value VARCHAR(20), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_plant_node_type CHECK (type IN ('FACTORY', 'AREA', 'UNIT')), 
	FOREIGN KEY(parent_id) REFERENCES plant_node (id) ON DELETE RESTRICT, 
	FOREIGN KEY(monitor_tag_id) REFERENCES tag_registry (id) ON DELETE RESTRICT
);

-- ============================================================
-- dcs_mode_mapping
-- ============================================================
CREATE TABLE IF NOT EXISTS dcs_mode_mapping (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	dcs_model_id UUID, 
	standard_mode INTEGER NOT NULL, 
	raw_mode_value INTEGER NOT NULL, 
	description VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(dcs_model_id) REFERENCES dcs_model (id) ON DELETE CASCADE
);

-- ============================================================
-- kpi_node_snapshot_daily
-- ============================================================
CREATE TABLE IF NOT EXISTS kpi_node_snapshot_daily (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	plant_node_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	stat_date DATE NOT NULL, 
	score NUMERIC(5, 2), 
	good_value_rate NUMERIC(5, 2), 
	auto_mode_rate NUMERIC(5, 2), 
	effective_auto_rate NUMERIC(5, 2), 
	steady_rate NUMERIC(5, 2), 
	accuracy_rate NUMERIC(5, 2), 
	fast_rate NUMERIC(5, 2), 
	oscillation_rate NUMERIC(5, 2), 
	saturation_rate NUMERIC(5, 2), 
	stiction_index NUMERIC(5, 2), 
	settling_time NUMERIC(8, 2), 
	output_trip_index NUMERIC(8, 2), 
	ideal_settling_time NUMERIC(8, 2), 
	auto_loop_ratio NUMERIC(5, 2), 
	realtime_auto_rate NUMERIC(5, 2), 
	loop_count INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	algorithm_version VARCHAR(30), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_kpi_node_snapshot_daily_status CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')), 
	CONSTRAINT uk_kpi_node_snapshot_daily_node_date UNIQUE (plant_node_id, stat_date), 
	FOREIGN KEY(plant_node_id) REFERENCES plant_node (id) ON DELETE CASCADE
);

-- ============================================================
-- kpi_node_snapshot_hourly
-- ============================================================
CREATE TABLE IF NOT EXISTS kpi_node_snapshot_hourly (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	plant_node_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	ts_start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	ts_end TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	score NUMERIC(5, 2), 
	good_value_rate NUMERIC(5, 2), 
	auto_mode_rate NUMERIC(5, 2), 
	effective_auto_rate NUMERIC(5, 2), 
	steady_rate NUMERIC(5, 2), 
	accuracy_rate NUMERIC(5, 2), 
	fast_rate NUMERIC(5, 2), 
	oscillation_rate NUMERIC(5, 2), 
	saturation_rate NUMERIC(5, 2), 
	stiction_index NUMERIC(5, 2), 
	settling_time NUMERIC(8, 2), 
	output_trip_index NUMERIC(8, 2), 
	ideal_settling_time NUMERIC(8, 2), 
	auto_loop_ratio NUMERIC(5, 2), 
	realtime_auto_rate NUMERIC(5, 2), 
	loop_count INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	algorithm_version VARCHAR(30), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_kpi_node_snapshot_status CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')), 
	CONSTRAINT ck_kpi_node_snapshot_window CHECK (ts_end > ts_start), 
	FOREIGN KEY(plant_node_id) REFERENCES plant_node (id) ON DELETE CASCADE
);

-- ============================================================
-- kpi_node_snapshot_monthly
-- ============================================================
CREATE TABLE IF NOT EXISTS kpi_node_snapshot_monthly (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	plant_node_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	stat_month DATE NOT NULL, 
	score NUMERIC(5, 2), 
	good_value_rate NUMERIC(5, 2), 
	auto_mode_rate NUMERIC(5, 2), 
	effective_auto_rate NUMERIC(5, 2), 
	steady_rate NUMERIC(5, 2), 
	accuracy_rate NUMERIC(5, 2), 
	fast_rate NUMERIC(5, 2), 
	oscillation_rate NUMERIC(5, 2), 
	saturation_rate NUMERIC(5, 2), 
	stiction_index NUMERIC(5, 2), 
	settling_time NUMERIC(8, 2), 
	output_trip_index NUMERIC(8, 2), 
	ideal_settling_time NUMERIC(8, 2), 
	auto_loop_ratio NUMERIC(5, 2), 
	realtime_auto_rate NUMERIC(5, 2), 
	loop_count INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	algorithm_version VARCHAR(30), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_kpi_node_snapshot_monthly_status CHECK (status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')), 
	CONSTRAINT uk_kpi_node_snapshot_monthly_node_month UNIQUE (plant_node_id, stat_month), 
	FOREIGN KEY(plant_node_id) REFERENCES plant_node (id) ON DELETE CASCADE
);

-- ============================================================
-- loop_ledger
-- ============================================================
CREATE TABLE IF NOT EXISTS loop_ledger (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	tag_name VARCHAR(100) NOT NULL, 
	description VARCHAR(255), 
	unit_id UUID, 
	score_weight NUMERIC(5, 2), 
	is_active BOOLEAN, 
	last_aas_sync_at TIMESTAMP WITHOUT TIME ZONE, 
	status VARCHAR(20) NOT NULL, 
	loop_type VARCHAR(20), 
	control_type VARCHAR(20), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	created_by VARCHAR(50), 
	score_weights JSONB, 
	remark VARCHAR(500), 
	updated_by VARCHAR(50), 
	importance_level SMALLINT NOT NULL, 
	include_in_evaluation BOOLEAN NOT NULL, 
	modeattr_tag_id UUID, 
	data_retention_days INTEGER, 
	op_output_lower_limit FLOAT, 
	op_output_upper_limit FLOAT, 
	dcs_model_id UUID, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_loop_ledger_status CHECK (status IN ('READY', 'PARTIAL', 'INACTIVE')), 
	CONSTRAINT ck_loop_ledger_loop_type CHECK (loop_type IN ('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', 'ANALYSIS', 'SPEED', 'OTHER')), 
	CONSTRAINT ck_loop_ledger_importance_level CHECK (importance_level IN (1, 2, 3)), 
	FOREIGN KEY(unit_id) REFERENCES plant_node (id) ON DELETE RESTRICT, 
	FOREIGN KEY(modeattr_tag_id) REFERENCES tag_registry (id) ON DELETE RESTRICT, 
	FOREIGN KEY(dcs_model_id) REFERENCES dcs_model (id) ON DELETE SET NULL
);

-- ============================================================
-- unit_kpi_summary
-- ============================================================
CREATE TABLE IF NOT EXISTS unit_kpi_summary (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	node_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	snapshot_time TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	avg_score NUMERIC(5, 2), 
	auto_mode_rate NUMERIC(5, 2), 
	effective_auto_rate NUMERIC(5, 2), 
	stability_rate NUMERIC(5, 2), 
	accuracy_rate NUMERIC(5, 2), 
	fast_rate NUMERIC(5, 2), 
	good_value_rate NUMERIC(5, 2), 
	oscillation_rate NUMERIC(5, 2), 
	saturation_rate NUMERIC(5, 2), 
	total_loops INTEGER, 
	evaluated_loops INTEGER, 
	inconclusive_loops INTEGER, 
	excluded_loops INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	algorithm_version VARCHAR(50), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_unit_kpi_summary_node_time UNIQUE (node_id, snapshot_time), 
	CONSTRAINT ck_unit_kpi_summary_status CHECK (status IN ('SUCCESS', 'PARTIAL', 'EMPTY')), 
	FOREIGN KEY(node_id) REFERENCES plant_node (id) ON DELETE CASCADE
);

-- ============================================================
-- action_tracker
-- ============================================================
CREATE TABLE IF NOT EXISTS action_tracker (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID, 
	diagnosis_label VARCHAR(100), 
	action_status VARCHAR(20) NOT NULL, 
	evidence_url VARCHAR(255), 
	updated_by VARCHAR(50), 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_action_tracker_status CHECK (action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'IMPLEMENTED')), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE
);

-- ============================================================
-- diagnosis_tag
-- ============================================================
CREATE TABLE IF NOT EXISTS diagnosis_tag (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	tag_code VARCHAR(50) NOT NULL, 
	tag_name VARCHAR(100), 
	severity VARCHAR(20) NOT NULL, 
	source_metric VARCHAR(50), 
	trigger_condition JSONB, 
	trigger_value NUMERIC(10, 4), 
	triggered_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	resolved_at TIMESTAMP WITHOUT TIME ZONE, 
	resolved_by UUID, 
	resolution_note TEXT, 
	status VARCHAR(20) DEFAULT 'ACTIVE' NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_diag_tag_severity CHECK (severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')), 
	CONSTRAINT ck_diag_tag_status CHECK (status IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE
);

-- ============================================================
-- diagnosis_task
-- ============================================================
CREATE TABLE IF NOT EXISTS diagnosis_task (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	trigger_type VARCHAR(10) NOT NULL, 
	triggered_by VARCHAR(50), 
	status VARCHAR(20) DEFAULT 'PENDING' NOT NULL, 
	time_range_start TIMESTAMP WITHOUT TIME ZONE, 
	time_range_end TIMESTAMP WITHOUT TIME ZONE, 
	error_message TEXT, 
	triggered_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	completed_at TIMESTAMP WITHOUT TIME ZONE, 
	is_archived BOOLEAN DEFAULT false NOT NULL, 
	archived_at TIMESTAMP WITHOUT TIME ZONE, 
	archived_by VARCHAR(50), 
	PRIMARY KEY (id), 
	CONSTRAINT ck_diag_task_trigger_type CHECK (trigger_type IN ('manual', 'auto')), 
	CONSTRAINT ck_diag_task_status CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE
);

-- ============================================================
-- kpi_snapshot_custom
-- ============================================================
CREATE TABLE IF NOT EXISTS kpi_snapshot_custom (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	task_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	ts_start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	ts_end TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	score NUMERIC(5, 2), 
	accuracy_rate NUMERIC(5, 2), 
	fast_rate NUMERIC(5, 2), 
	steady_rate NUMERIC(5, 2), 
	effective_auto_rate NUMERIC(5, 2), 
	good_value_rate NUMERIC(5, 2), 
	oscillation_rate NUMERIC(5, 2), 
	saturation_rate NUMERIC(5, 2), 
	stiction_index NUMERIC(5, 2), 
	output_trip_index NUMERIC(8, 2), 
	settling_time NUMERIC(8, 2), 
	ideal_settling_time NUMERIC(8, 2), 
	auto_mode_rate NUMERIC(5, 2), 
	algorithm_version VARCHAR(50), 
	sampling_freq VARCHAR(10), 
	quality_policy VARCHAR(30), 
	status VARCHAR(20) NOT NULL, 
	confidence_level CHAR(1), 
	valid_rate NUMERIC(5, 4), 
	data_lineage JSONB, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT ck_kpi_custom_status CHECK (status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')), 
	CONSTRAINT ck_kpi_custom_window CHECK (ts_end > ts_start), 
	CONSTRAINT uq_kpi_custom_task_loop UNIQUE (task_id, loop_id), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE
);

-- ============================================================
-- kpi_snapshot_hourly
-- ============================================================
CREATE TABLE IF NOT EXISTS kpi_snapshot_hourly (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID, 
	ts_start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	ts_end TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	score NUMERIC(5, 2), 
	good_value_rate NUMERIC(5, 2), 
	auto_mode_rate NUMERIC(5, 2), 
	steady_rate NUMERIC(5, 2), 
	accuracy_rate NUMERIC(5, 2), 
	oscillation_rate NUMERIC(5, 2), 
	saturation_rate NUMERIC(5, 2), 
	fast_rate NUMERIC(5, 2), 
	effective_auto_rate NUMERIC(5, 2), 
	stiction_index NUMERIC(5, 2), 
	settling_time NUMERIC(8, 2), 
	output_trip_index NUMERIC(8, 2), 
	status VARCHAR(20) NOT NULL, 
	ideal_settling_time NUMERIC(8, 2), 
	algorithm_version VARCHAR(50), 
	sampling_freq VARCHAR(10), 
	quality_policy VARCHAR(30), 
	valid_rate NUMERIC(5, 4), 
	confidence_level CHAR(1), 
	data_lineage JSONB, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_kpi_snapshot_status CHECK (status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')), 
	CONSTRAINT ck_kpi_snapshot_window CHECK (ts_end > ts_start), 
	CONSTRAINT ck_kpi_snapshot_confidence CHECK (confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')), 
	CONSTRAINT uq_kpi_snapshot_hourly_loop_ts UNIQUE (loop_id, ts_start), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE
);

-- ============================================================
-- loop_mode_mapping
-- ============================================================
CREATE TABLE IF NOT EXISTS loop_mode_mapping (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	mode_value INTEGER NOT NULL, 
	mode_label VARCHAR(20) NOT NULL, 
	is_auto BOOLEAN NOT NULL, 
	is_effective BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_loop_mode_mapping_label CHECK (mode_label IN ('AUTO', 'CAS', 'REMOTE', 'APC', 'MANUAL')), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE
);

-- ============================================================
-- loop_tag_mapping
-- ============================================================
CREATE TABLE IF NOT EXISTS loop_tag_mapping (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	tag_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	tag_role VARCHAR(20) NOT NULL, 
	is_required BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_loop_tag_mapping_role CHECK (tag_role IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D')), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE, 
	FOREIGN KEY(tag_id) REFERENCES tag_registry (id) ON DELETE RESTRICT
);

-- ============================================================
-- tuning_record
-- ============================================================
CREATE TABLE IF NOT EXISTS tuning_record (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	model_type VARCHAR(20) NOT NULL, 
	model_params JSON, 
	algorithm VARCHAR(50) NOT NULL, 
	recommended_pid JSON, 
	simulation_result JSON, 
	fitting_score NUMERIC(5, 2), 
	status VARCHAR(20) NOT NULL, 
	created_by VARCHAR(50), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_tuning_record_model CHECK (model_type IN ('FOPDT', 'SOPDT', 'IPDT')), 
	CONSTRAINT ck_tuning_record_algo CHECK (algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC')), 
	CONSTRAINT ck_tuning_record_status CHECK (status IN ('PENDING', 'IDENTIFIED', 'SIMULATED', 'APPLIED', 'VERIFIED')), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE
);

-- ============================================================
-- diagnosis_result
-- ============================================================
CREATE TABLE IF NOT EXISTS diagnosis_result (
	id UUID NOT NULL DEFAULT uuid_generate_v4(), 
	loop_id UUID, 
	diag_label VARCHAR(100), 
	confidence NUMERIC(5, 2), 
	feature_values JSON, 
	evidence_chain JSON, 
	algorithm_version VARCHAR(50), 
	diagnosed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	task_id UUID, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_diagnosis_result_conf CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 100)), 
	FOREIGN KEY(loop_id) REFERENCES loop_ledger (id) ON DELETE CASCADE, 
	FOREIGN KEY(task_id) REFERENCES diagnosis_task (id) ON DELETE SET NULL
);

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_dcs_vendor_sort ON dcs_vendor (sort_order);
CREATE UNIQUE INDEX IF NOT EXISTS uk_diagnosis_config_code ON diagnosis_config (diag_code);
CREATE UNIQUE INDEX IF NOT EXISTS uk_engine_rule_code ON engine_rule (rule_code);
CREATE UNIQUE INDEX IF NOT EXISTS uk_metric_config_code ON metric_config (metric_code);
CREATE INDEX IF NOT EXISTS idx_mode_definition_sort ON mode_definition (sort_order);
CREATE INDEX IF NOT EXISTS idx_report_config_is_enabled ON report_config (is_enabled);
CREATE INDEX IF NOT EXISTS idx_report_config_period ON report_config (report_period);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_operator ON sys_audit_log (operator);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_operated_at ON sys_audit_log (operated_at);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_operation_type ON sys_audit_log (operation_type);
CREATE INDEX IF NOT EXISTS idx_sys_audit_log_target_type ON sys_audit_log (target_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sys_config_key ON sys_config (key);
CREATE UNIQUE INDEX IF NOT EXISTS uk_sys_user_email ON sys_user (email);
CREATE UNIQUE INDEX IF NOT EXISTS uk_sys_user_username ON sys_user (username);
CREATE INDEX IF NOT EXISTS idx_sys_user_is_active ON sys_user (is_active);
CREATE INDEX IF NOT EXISTS idx_tag_registry_tag_type ON tag_registry (tag_type);
CREATE INDEX IF NOT EXISTS idx_tag_registry_is_linked ON tag_registry (is_linked);
CREATE UNIQUE INDEX IF NOT EXISTS uk_tag_registry_tag_name ON tag_registry (tag_name);
CREATE INDEX IF NOT EXISTS idx_tag_registry_tag_name ON tag_registry (tag_name);
CREATE INDEX IF NOT EXISTS idx_dcs_model_vendor ON dcs_model (vendor_id);
CREATE INDEX IF NOT EXISTS idx_dcs_model_sort ON dcs_model (sort_order);
CREATE INDEX IF NOT EXISTS idx_plant_node_monitor_tag_id ON plant_node (monitor_tag_id);
CREATE INDEX IF NOT EXISTS idx_dcs_mode_mapping_model_raw ON dcs_mode_mapping (dcs_model_id, raw_mode_value);
CREATE UNIQUE INDEX IF NOT EXISTS uk_dcs_mode_mapping_model_mode ON dcs_mode_mapping (dcs_model_id, standard_mode) WHERE dcs_model_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uk_dcs_mode_mapping_default ON dcs_mode_mapping (standard_mode) WHERE dcs_model_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_node_id ON kpi_node_snapshot_daily (plant_node_id);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_status ON kpi_node_snapshot_daily (status);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_stat_date ON kpi_node_snapshot_daily (stat_date);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_daily_node_date ON kpi_node_snapshot_daily (plant_node_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_node_id ON kpi_node_snapshot_hourly (plant_node_id);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_node_ts ON kpi_node_snapshot_hourly (plant_node_id, ts_start);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_ts_status ON kpi_node_snapshot_hourly (ts_start, status, score);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_status ON kpi_node_snapshot_hourly (status);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_ts_start ON kpi_node_snapshot_hourly (ts_start);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_node_id ON kpi_node_snapshot_monthly (plant_node_id);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_node_month ON kpi_node_snapshot_monthly (plant_node_id, stat_month);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_status ON kpi_node_snapshot_monthly (status);
CREATE INDEX IF NOT EXISTS idx_kpi_node_snapshot_monthly_stat_month ON kpi_node_snapshot_monthly (stat_month);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_status ON loop_ledger (status);
CREATE UNIQUE INDEX IF NOT EXISTS uk_loop_ledger_tag_name ON loop_ledger (tag_name);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_unit_id ON loop_ledger (unit_id);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_tag_name ON loop_ledger (tag_name);
CREATE INDEX IF NOT EXISTS idx_loop_ledger_importance_level ON loop_ledger (importance_level);
CREATE INDEX IF NOT EXISTS ix_unit_kpi_summary_node_time ON unit_kpi_summary (node_id, snapshot_time);
CREATE INDEX IF NOT EXISTS idx_action_tracker_loop_id ON action_tracker (loop_id);
CREATE INDEX IF NOT EXISTS idx_action_tracker_action_status ON action_tracker (action_status);
CREATE INDEX IF NOT EXISTS ix_diagnosis_tag_loop_status ON diagnosis_tag (loop_id, status);
CREATE INDEX IF NOT EXISTS ix_diagnosis_tag_severity ON diagnosis_tag (severity, triggered_at);
CREATE INDEX IF NOT EXISTS idx_diagnosis_task_loop_id ON diagnosis_task (loop_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_task_status ON diagnosis_task (status);
CREATE INDEX IF NOT EXISTS idx_diagnosis_task_archived ON diagnosis_task (is_archived);
CREATE INDEX IF NOT EXISTS ix_kpi_snapshot_custom_loop_ts ON kpi_snapshot_custom (loop_id, ts_start);
CREATE INDEX IF NOT EXISTS ix_kpi_snapshot_custom_task ON kpi_snapshot_custom (task_id);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_loop_id ON kpi_snapshot_hourly (loop_id);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_status ON kpi_snapshot_hourly (status);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshot_ts_start ON kpi_snapshot_hourly (ts_start);
CREATE UNIQUE INDEX IF NOT EXISTS uk_loop_mode_mapping_loop_mode ON loop_mode_mapping (loop_id, mode_value);
CREATE INDEX IF NOT EXISTS idx_loop_mode_mapping_loop_id ON loop_mode_mapping (loop_id);
CREATE INDEX IF NOT EXISTS idx_loop_tag_mapping_tag_id ON loop_tag_mapping (tag_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_loop_tag_mapping_loop_role ON loop_tag_mapping (loop_id, tag_role);
CREATE INDEX IF NOT EXISTS idx_loop_tag_mapping_loop_id ON loop_tag_mapping (loop_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_result_task_id ON diagnosis_result (task_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_result_loop_id ON diagnosis_result (loop_id);
CREATE INDEX IF NOT EXISTS idx_diagnosis_result_diagnosed ON diagnosis_result (diagnosed_at);