-- =============================================================================
-- 数据库名: clpm
-- 脚本版本: v1.1
-- 创建日期: 2026-06-21
-- 对应 DDS 版本: DDS v3.0 (产品化架构重构版)
-- 设计依据: PRD v3.0, FDS v3.0, ADS v3.0, 关键算法设计说明 v1.0
-- 说明: 本脚本为 PostgreSQL 关系型业务域种子数据，包含：
--       1. 管理员账户（5 个角色用户）
--       2. 工厂节点（1 工厂 + 3 装置 + 5 工艺系统）
--       3. 性能指标配置（6 项核心 KPI，权重总和 100%）
--       4. 诊断指标配置（8 类诊断标签）
--       5. 引擎规则配置（3 项引擎参数）
--       6. 示例回路（3 条回路台账）
--       7. 示例 AAS Tag（18 条 Tag 注册记录）
--       8. 回路-Tag 关联（18 条映射记录）
--       9. KPI 快照示例（3 条）
--      10. 整定记录示例（1 条，Phase 2）
-- 前置条件: 已执行 01_schema.sql 完成表结构创建
-- 变更记录:
--   v1.0 2026-06-21: 初始版本（基础种子数据）
--   v1.1 2026-06-22: 算法设计同步种子数据更新（6大KPI + 8类诊断标签 + 新字段填充）
-- =============================================================================

-- =============================================================================
-- 1. 管理员账户 (sys_user)
-- =============================================================================
-- 密码哈希说明：
--   所有 5 个用户均使用同一密码 admin123（bcrypt 哈希，开发/测试环境用）
--   生产环境部署前请通过应用端修改密码。
--   哈希通过 app.core.security.hash_password('admin123') 生成（bcrypt cost=12）
-- =============================================================================

INSERT INTO sys_user (id, username, password_hash, display_name, email, role, is_active, created_at, updated_at) VALUES
('00000000-0000-0000-0000-000000000001', 'admin',       '$2b$12$EmVQ8NwGlB/O8L4vJ0XSluBfxYOlTwBer7vnNFuVL/0qmhSXlfy/u', '系统管理员',     'admin@clpm.local',       'ADMIN',       TRUE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000002', 'ic_engineer', '$2b$12$3KxnNHH3KmxeEE6AUmQOeuFEccnBLlHxaDBX5BIWCwvKPq1gqLrxy', '仪控工程师',     'ic_engineer@clpm.local', 'IC_ENGINEER', TRUE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000003', 'pe_engineer', '$2b$12$dLInICVCCkfdsIfs6jJnqeJfR0HDzFbv7yqBWboZQSLRknlQuhOKG', '工艺工程师',     'pe_engineer@clpm.local', 'PE_ENGINEER', TRUE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000004', 'sponsor',     '$2b$12$lpgnpJwE956RFjcYb4hyOubgVYhf0IDWs0xlBzbCU1RMuT1cmR0sC', '项目发起人',     'sponsor@clpm.local',     'SPONSOR',     TRUE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000005', 'expert',      '$2b$12$ai8B75As3GLsuFBHayAq2ufsMMmzezF.E9tg.058I/a30V7nTuiTG', '外部专家',       'expert@clpm.local',      'EXPERT',      TRUE, NOW(), NOW());

-- =============================================================================
-- 2. 工厂节点 (plant_node)
-- =============================================================================
-- 层级结构:
--   FACTORY  加氢联合车间 (HYU)
--     ├── UNIT 加氢精制 (HDS)
--     │     ├── EQUIPMENT HDS-RX 反应系统
--     │     └── EQUIPMENT HDS-FR 分馏系统
--     ├── UNIT 加氢裂化 (HDC)
--     │     ├── EQUIPMENT HDC-RX 反应系统
--     │     └── EQUIPMENT HDC-FR 分馏系统
--     └── UNIT S Zorb (SZB)
--           └── EQUIPMENT SZB-AD 吸附系统
-- =============================================================================

-- 2.1 工厂
INSERT INTO plant_node (id, name, type, parent_id, created_at, updated_at) VALUES
('00000000-0000-0000-0000-000000000101', '加氢联合车间', 'FACTORY', NULL, NOW(), NOW());

-- 2.2 装置
INSERT INTO plant_node (id, name, type, parent_id, created_at, updated_at) VALUES
('00000000-0000-0000-0000-000000000102', '加氢精制', 'UNIT', '00000000-0000-0000-0000-000000000101', NOW(), NOW()),
('00000000-0000-0000-0000-000000000103', '加氢裂化', 'UNIT', '00000000-0000-0000-0000-000000000101', NOW(), NOW()),
('00000000-0000-0000-0000-000000000104', 'S Zorb',   'UNIT', '00000000-0000-0000-0000-000000000101', NOW(), NOW());

-- 2.3 工艺系统
INSERT INTO plant_node (id, name, type, parent_id, created_at, updated_at) VALUES
('00000000-0000-0000-0000-000000000111', 'HDS-RX 反应系统', 'EQUIPMENT', '00000000-0000-0000-0000-000000000102', NOW(), NOW()),
('00000000-0000-0000-0000-000000000112', 'HDS-FR 分馏系统', 'EQUIPMENT', '00000000-0000-0000-0000-000000000102', NOW(), NOW()),
('00000000-0000-0000-0000-000000000113', 'HDC-RX 反应系统', 'EQUIPMENT', '00000000-0000-0000-0000-000000000103', NOW(), NOW()),
('00000000-0000-0000-0000-000000000114', 'HDC-FR 分馏系统', 'EQUIPMENT', '00000000-0000-0000-0000-000000000103', NOW(), NOW()),
('00000000-0000-0000-0000-000000000115', 'SZB-AD 吸附系统', 'EQUIPMENT', '00000000-0000-0000-0000-000000000104', NOW(), NOW());

-- =============================================================================
-- 3. 性能指标配置 (metric_config)
-- =============================================================================
-- 6 项核心 KPI（对齐 GB/T 44693.2-2024 附录 B/F）：
--   好值率/自控率/平稳率/准确率/振荡率/饱和率
-- 权重总和 = 10 + 10 + 30 + 15 + 20 + 15 = 100%（稳定型控制回路默认权重）
-- threshold 使用 JSONB 结构 {min, max, alert}
-- control_type 对齐算法设计说明 §4.7.3 默认权重模板
-- =============================================================================

INSERT INTO metric_config (id, metric_code, metric_name, formula, weight, threshold, control_type, is_enabled, updated_by, updated_at, version) VALUES
('00000000-0000-0000-0000-000000000401', 'GOOD_VALUE_RATE',  '好值率',  'count(pv_quality=Good) / count(*) * 100',                            10.00, '{"min": 80, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000402', 'AUTO_MODE_RATE',   '自控率',  'count(mode IN (Auto,Cascade,Remote)) / count(*) * 100',             10.00, '{"min": 90, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000403', 'STEADY_RATE',      '平稳率',  'max(0, (1 - osc_rate - k*std_norm) / (1 - osc_rate)) * 100',        30.00, '{"min": 85, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000404', 'ACCURACY_RATE',    '准确率',  'max(0, (1 - mean_abs_error / e_max)) * 100',                        15.00, '{"min": 80, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000405', 'OSCILLATION_RATE', '振荡率',  'min(S_A, S_B) * 100',                                                20.00, '{"min": 0, "max": 5, "alert": "warning"}'::jsonb,     'SLOW',   TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000406', 'SATURATION_RATE',  '饱和率',  'saturated_duration / total_duration * 100',                         15.00, '{"min": 0, "max": 5, "alert": "warning"}'::jsonb,     'STABLE', TRUE, 'admin', NOW(), 1);

-- =============================================================================
-- 4. 诊断指标配置 (diagnosis_config)
-- =============================================================================
-- 8 类诊断标签（对齐算法设计说明 §5.0 诊断标签体系）：
--   OSCILLATION/VALVE_STICTION/OVERAGGRESSIVE/OVERCONSERVATIVE/
--   EXTERNAL_DISTURBANCE/QUALITY_ABNORMAL/OUTPUT_SATURATION/MANUAL_REVIEW
-- calc_method 对齐算法设计说明 §5.1~§5.6 算法选型
-- threshold 使用 JSONB 结构 {min, max, alert}
-- =============================================================================

INSERT INTO diagnosis_config (id, diag_code, diag_name, algorithm_type, calc_method, params, threshold, is_enabled, updated_by, updated_at, version) VALUES
('00000000-0000-0000-0000-000000000501', 'OSCILLATION',         '振荡检测',           'IAE_FFT',         'IAE_ZERO_CROSSING',   '{"window_size": 1024, "overlap": 0.5, "snr_threshold": 5.0, "similarity_threshold": 0.4}'::json, '{"min": 0.4, "max": 1.0, "alert": "warning"}'::jsonb,  TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000502', 'VALVE_STICTION',      '阀门粘滞检测',       'SCATTER_FIT',     'CHOUDHURY_NGI_NLI',    '{"ngi_threshold": 0.001, "nli_threshold": 0.01, "stiction_threshold": 0.5, "r2_threshold": 0.7}'::json, '{"min": 0.5, "max": 100, "alert": "critical"}'::jsonb, TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000503', 'OVERAGGRESSIVE',      '参数过激检测',       'STEP_RESPONSE',   'EXPERT_RULE',          '{"overshoot_threshold": 25, "decay_ratio_threshold": 0.4, "harris_threshold": 0.4}'::json, '{"min": 25, "max": 100, "alert": "warning"}'::jsonb,    TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000504', 'OVERCONSERVATIVE',    '参数过保守检测',     'SETTLING_TIME',   'EXPERT_RULE',          '{"settling_ratio": 5.0, "iae_ratio": 2.0, "op_activity_min": 0.01}'::json, '{"min": 5.0, "max": 100, "alert": "warning"}'::jsonb,   TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000505', 'EXTERNAL_DISTURBANCE','外扰频繁检测',       'FREQ_ANALYSIS',   'KANO_STATISTICAL',     '{"window_size": 3600, "disturbance_threshold": 3, "freq_threshold": 5}'::json, '{"min": 5, "max": 100, "alert": "warning"}'::jsonb,     TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000506', 'QUALITY_ABNORMAL',    'PV 质量异常检测',    'QUALITY_CODE',    'EXPERT_RULE',          '{"bad_rate_threshold": 30, "uncertain_rate_threshold": 20, "freeze_duration": 300}'::json, '{"min": 20, "max": 100, "alert": "critical"}'::jsonb, TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000507', 'OUTPUT_SATURATION',   '输出饱和检测',       'OP_LIMIT_STAT',   'EXPERT_RULE',          '{"op_low": 0, "op_high": 100, "epsilon": 2}'::json, '{"min": 5, "max": 100, "alert": "warning"}'::jsonb,      TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000508', 'MANUAL_REVIEW',       '人工复核',           'MANUAL',          'EXPERT_RULE',          '{"confidence_min": 0, "confidence_max": 50}'::json, NULL,                                                  TRUE, 'admin', NOW(), 1);

-- =============================================================================
-- 5. 引擎规则配置 (engine_rule)
-- =============================================================================
-- 3 项引擎参数配置
-- =============================================================================

INSERT INTO engine_rule (id, rule_code, rule_name, rule_type, params, is_enabled, updated_by, updated_at) VALUES
('00000000-0000-0000-0000-000000000601', 'EVAL_CALC_CYCLE',     '评估计算周期', 'CALC_CYCLE', '{"cycle_minutes": 60}'::json,                           TRUE, 'admin', NOW()),
('00000000-0000-0000-0000-000000000602', 'DATA_FETCH_WINDOW',   '数据拉取窗口', 'DATA_FETCH', '{"window_days": 30, "sample_interval_seconds": 1}'::json, TRUE, 'admin', NOW()),
('00000000-0000-0000-0000-000000000603', 'SCHEDULE_CONCURRENCY','调度并发数',   'SCHEDULE',   '{"concurrency": 16}'::json,                             TRUE, 'admin', NOW());

-- =============================================================================
-- 6. 示例回路 (loop_ledger)
-- =============================================================================
-- 3 条示例回路：
--   L001: HDS-RX-TIC-101（加氢精制反应器入口温度，状态 READY）
--   L002: HDS-FR-FIC-201（加氢精制分馏塔进料流量，状态 READY）
--   L003: HDC-RX-TIC-301（加氢裂化反应器入口温度，状态 PARTIAL，缺 PID_* Tag）
-- =============================================================================

INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, created_at, updated_at, created_by) VALUES
('00000000-0000-0000-0000-000000000201', 'HDS-RX-TIC-101', 'R-101 反应器入口温度调节回路', '00000000-0000-0000-0000-000000000111', 1.00, TRUE,  NOW(), 'READY',   NOW(), NOW(), 'admin'),
('00000000-0000-0000-0000-000000000202', 'HDS-FR-FIC-201', 'E-201 分馏塔进料流量调节回路', '00000000-0000-0000-0000-000000000112', 1.00, TRUE,  NOW(), 'READY',   NOW(), NOW(), 'admin'),
('00000000-0000-0000-0000-000000000203', 'HDC-RX-TIC-301', 'R-301 反应器入口温度调节回路', '00000000-0000-0000-0000-000000000113', 1.00, TRUE,  NOW(), 'PARTIAL', NOW(), NOW(), 'admin');

-- =============================================================================
-- 7. 示例 AAS Tag (tag_registry)
-- =============================================================================
-- 18 条 Tag 注册记录：
--   L001 (7 条): T-HDS-001-PV / SP / OP / MODE / PID_P / PID_I / PID_D
--   L002 (7 条): T-HDS-002-PV / SP / OP / MODE / PID_P / PID_I / PID_D
--   L003 (4 条): T-HDC-003-PV / SP / OP / MODE（缺 PID_P / PID_I / PID_D）
-- =============================================================================

-- 7.1 L001 回路 Tag（7 条，全部已关联）
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked) VALUES
('00000000-0000-0000-0000-000000000301', 'T-HDS-001-PV',    'R-101 反应器入口温度 PV',     'PV',    358.50, 'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000302', 'T-HDS-001-SP',    'R-101 反应器入口温度 SP',     'SP',    360.00, 'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000303', 'T-HDS-001-OP',    'R-101 反应器入口温度 OP',     'OP',    62.30,  'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000304', 'T-HDS-001-MODE',  'R-101 反应器入口温度 MODE',   'MODE',  1.00,   'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000305', 'T-HDS-001-PID_P', 'R-101 反应器入口温度 PID_P',  'PID_P', 1.50,   'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000306', 'T-HDS-001-PID_I', 'R-101 反应器入口温度 PID_I',  'PID_I', 0.80,   'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000307', 'T-HDS-001-PID_D', 'R-101 反应器入口温度 PID_D',  'PID_D', 0.20,   'GOOD', NOW(), TRUE);

-- 7.2 L002 回路 Tag（7 条，全部已关联）
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked) VALUES
('00000000-0000-0000-0000-000000000308', 'T-HDS-002-PV',    'E-201 分馏塔进料流量 PV',     'PV',    85.20,  'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000309', 'T-HDS-002-SP',    'E-201 分馏塔进料流量 SP',     'SP',    85.00,  'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-00000000030a', 'T-HDS-002-OP',    'E-201 分馏塔进料流量 OP',     'OP',    48.50,  'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-00000000030b', 'T-HDS-002-MODE',  'E-201 分馏塔进料流量 MODE',   'MODE',  1.00,   'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-00000000030c', 'T-HDS-002-PID_P', 'E-201 分馏塔进料流量 PID_P',  'PID_P', 2.00,   'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-00000000030d', 'T-HDS-002-PID_I', 'E-201 分馏塔进料流量 PID_I',  'PID_I', 1.20,   'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-00000000030e', 'T-HDS-002-PID_D', 'E-201 分馏塔进料流量 PID_D',  'PID_D', 0.00,   'GOOD', NOW(), TRUE);

-- 7.3 L003 回路 Tag（4 条，仅 PV/SP/OP/MODE，缺 PID_*）
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked) VALUES
('00000000-0000-0000-0000-00000000030f', 'T-HDC-003-PV',    'R-301 反应器入口温度 PV',     'PV',    372.10, 'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000310', 'T-HDC-003-SP',    'R-301 反应器入口温度 SP',     'SP',    375.00, 'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000311', 'T-HDC-003-OP',    'R-301 反应器入口温度 OP',     'OP',    55.80,  'GOOD', NOW(), TRUE),
('00000000-0000-0000-0000-000000000312', 'T-HDC-003-MODE',  'R-301 反应器入口温度 MODE',   'MODE',  1.00,   'GOOD', NOW(), TRUE);

-- =============================================================================
-- 8. 回路-Tag 关联 (loop_tag_mapping)
-- =============================================================================
-- 18 条关联记录，对应上述 18 条 Tag
-- is_required: PV/SP/OP/MODE = TRUE，PID_P/PID_I/PID_D = FALSE
-- =============================================================================

-- 8.1 L001 回路-Tag 关联（7 条）
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES
('00000000-0000-0000-0000-000000000701', '00000000-0000-0000-0000-000000000201', '00000000-0000-0000-0000-000000000301', 'PV',    TRUE,  NOW()),
('00000000-0000-0000-0000-000000000702', '00000000-0000-0000-0000-000000000201', '00000000-0000-0000-0000-000000000302', 'SP',    TRUE,  NOW()),
('00000000-0000-0000-0000-000000000703', '00000000-0000-0000-0000-000000000201', '00000000-0000-0000-0000-000000000303', 'OP',    TRUE,  NOW()),
('00000000-0000-0000-0000-000000000704', '00000000-0000-0000-0000-000000000201', '00000000-0000-0000-0000-000000000304', 'MODE',  TRUE,  NOW()),
('00000000-0000-0000-0000-000000000705', '00000000-0000-0000-0000-000000000201', '00000000-0000-0000-0000-000000000305', 'PID_P', FALSE, NOW()),
('00000000-0000-0000-0000-000000000706', '00000000-0000-0000-0000-000000000201', '00000000-0000-0000-0000-000000000306', 'PID_I', FALSE, NOW()),
('00000000-0000-0000-0000-000000000707', '00000000-0000-0000-0000-000000000201', '00000000-0000-0000-0000-000000000307', 'PID_D', FALSE, NOW());

-- 8.2 L002 回路-Tag 关联（7 条）
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES
('00000000-0000-0000-0000-000000000708', '00000000-0000-0000-0000-000000000202', '00000000-0000-0000-0000-000000000308', 'PV',    TRUE,  NOW()),
('00000000-0000-0000-0000-000000000709', '00000000-0000-0000-0000-000000000202', '00000000-0000-0000-0000-000000000309', 'SP',    TRUE,  NOW()),
('00000000-0000-0000-0000-00000000070a', '00000000-0000-0000-0000-000000000202', '00000000-0000-0000-0000-00000000030a', 'OP',    TRUE,  NOW()),
('00000000-0000-0000-0000-00000000070b', '00000000-0000-0000-0000-000000000202', '00000000-0000-0000-0000-00000000030b', 'MODE',  TRUE,  NOW()),
('00000000-0000-0000-0000-00000000070c', '00000000-0000-0000-0000-000000000202', '00000000-0000-0000-0000-00000000030c', 'PID_P', FALSE, NOW()),
('00000000-0000-0000-0000-00000000070d', '00000000-0000-0000-0000-000000000202', '00000000-0000-0000-0000-00000000030d', 'PID_I', FALSE, NOW()),
('00000000-0000-0000-0000-00000000070e', '00000000-0000-0000-0000-000000000202', '00000000-0000-0000-0000-00000000030e', 'PID_D', FALSE, NOW());

-- 8.3 L003 回路-Tag 关联（4 条，仅 PV/SP/OP/MODE）
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES
('00000000-0000-0000-0000-00000000070f', '00000000-0000-0000-0000-000000000203', '00000000-0000-0000-0000-00000000030f', 'PV',   TRUE, NOW()),
('00000000-0000-0000-0000-000000000710', '00000000-0000-0000-0000-000000000203', '00000000-0000-0000-0000-000000000310', 'SP',   TRUE, NOW()),
('00000000-0000-0000-0000-000000000711', '00000000-0000-0000-0000-000000000203', '00000000-0000-0000-0000-000000000311', 'OP',   TRUE, NOW()),
('00000000-0000-0000-0000-000000000712', '00000000-0000-0000-0000-000000000203', '00000000-0000-0000-0000-000000000312', 'MODE', TRUE, NOW());

-- =============================================================================
-- 9. KPI 快照示例 (kpi_snapshot_hourly)
-- =============================================================================
-- 3 条示例快照，覆盖 6 大 KPI 字段（含 accuracy_rate/saturation_rate）
-- 评估窗口：2026-06-21 00:00:00 ~ 01:00:00
-- =============================================================================

INSERT INTO kpi_snapshot_hourly (id, loop_id, ts_start, ts_end, score, good_value_rate, auto_mode_rate, steady_rate, accuracy_rate, oscillation_rate, saturation_rate, status) VALUES
('00000000-0000-0000-0000-000000000801', '00000000-0000-0000-0000-000000000201', '2026-06-21 00:00:00', '2026-06-21 01:00:00', 88.50, 95.20, 98.00, 92.30, 90.10, 3.20,  1.50, 'SUCCESS'),
('00000000-0000-0000-0000-000000000802', '00000000-0000-0000-0000-000000000202', '2026-06-21 00:00:00', '2026-06-21 01:00:00', 82.30, 91.50, 95.00, 85.40, 88.20, 5.80,  2.10, 'SUCCESS'),
('00000000-0000-0000-0000-000000000803', '00000000-0000-0000-0000-000000000203', '2026-06-21 00:00:00', '2026-06-21 01:00:00', 65.80, 78.30, 70.00, 68.50, 72.40, 12.50, 8.30, 'PARTIAL');

-- =============================================================================
-- 10. 整定记录示例 (tuning_record) [Phase 2]
-- =============================================================================
-- 1 条示例整定记录，包含 fitting_score 字段
-- 模型类型 FOPDT，整定算法 IMC，状态 SIMULATED
-- =============================================================================

INSERT INTO tuning_record (id, loop_id, model_type, model_params, algorithm, recommended_pid, simulation_result, fitting_score, status, created_by, created_at) VALUES
('00000000-0000-0000-0000-000000000901', '00000000-0000-0000-0000-000000000201', 'FOPDT', '{"K": 1.20, "tau": 30.50, "theta": 5.00}'::json, 'IMC', '{"Kp": 5.08, "Ti": 33.00, "Td": 2.27}'::json, '{"overshoot": 4.50, "settling_time": 132, "itae": 1850, "rise_time": 18.5}'::json, 95.30, 'SIMULATED', 'admin', NOW());

-- =============================================================================
-- 脚本结束
-- =============================================================================
