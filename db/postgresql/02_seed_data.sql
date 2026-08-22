-- =============================================================================
-- 数据库名: clpm
-- 脚本版本: v1.5
-- 创建日期: 2026-06-21
-- 对应 DDS 版本: DDS v3.0 (产品化架构重构版)
-- 设计依据: PRD v3.0, FDS v3.0, ADS v3.0, 关键算法设计说明 v1.0
-- 说明: 本脚本为 PostgreSQL 关系型业务域种子数据，包含：
--       1. 管理员账户（5 个角色用户）
--       2. AAS Tag 注册记录（189 条，全部已关联回路）
--       3. 工厂节点（2 工厂 + 2 装置 + 3 单元，共 7 节点）
--       4. 性能指标配置（11 项 KPI 指标，含 r2b3c4d5e6f7 补齐的 3 项）
--       5. 诊断指标配置（8 类诊断标签）
--       6. 引擎规则配置（3 项引擎参数）
--       7. 回路台账（27 条回路，已清理测试回路）
--       8. 回路-Tag 关联（189 条映射记录）
--       9. 系统配置（AAS 连接默认值 + 实时回写开关）
--      10. 指标数据需求契约（26 条，DataPlanner 依赖此表查询数据）
--      11. 诊断专家规则（6 条 R01-R06，规则引擎化）
--      12. DCS 配置（5 品牌 + 5 型号 + 5 标准 MODE + 30 MODE 映射）
-- 数据来源: 开发环境生产配置（2026-08-03 同步至种子文件）
-- 前置条件: 已执行 01_schema.sql 完成表结构创建
-- 变更记录:
--   v1.0 2026-06-21: 初始版本（基础种子数据）
--   v1.1 2026-06-22: 算法设计同步种子数据更新（6大KPI + 8类诊断标签 + 新字段填充）
--   v1.2 2026-07-29: 快速率列名收敛为 ORM/head DDL 的 fast_rate
--   v1.3 2026-08-01: 种子数据对齐开发环境（2工厂/2装置/3单元 + 28回路 + 239Tag）；
--                    移除 KPI 快照与整定记录示例（运行时数据，非种子配置）；
--                    tag_registry 清理 current_value/quality 为 NULL（初始状态）
--   v1.4 2026-08-03: 整合 alembic 迁移脚本中散落的种子数据至部署包（修复 stamp head 跳过问题）；
--                    新增 clpm_metric_data_requirement（26 条指标契约，修复 E 不足根因）；
--                    新增 diagnosis_rule（6 条专家规则 R01-R06）；
--                    新增 DCS 配置（dcs_vendor/dcs_model/mode_definition/dcs_mode_mapping）；
--                    修正 metric_config：FAST_RESPONSE_RATE→FAST_RATE + 补齐 3 项缺失指标；
--                    精简回路测点：28→27 回路、239→189 测点（仅保留 7 位号全配置回路）；
--                    新增 sys_config 实时回写开关；所有 INSERT 添加 ON CONFLICT 确保幂等
--   v1.5 2026-08-03: 全表 ON CONFLICT 幂等化（修复升级部署重复执行报错）；
--                    旧表（sys_user/tag_registry/plant_node/loop_ledger/loop_tag_mapping）补 ON CONFLICT (id) DO NOTHING；
--                    配置表（metric_config/diagnosis_config/engine_rule）改用 ON CONFLICT (id) DO UPDATE SET 确保种子权威性；
--                    metric_config 修复 ON CONFLICT (metric_code) 无法捕获主键冲突导致 FAST_RESPONSE_RATE→FAST_RATE 重命名失败；
--                    扩展种子权威表：diagnosis_rule/dcs_vendor/dcs_model/mode_definition 从 DO NOTHING 升级为 ON CONFLICT (id) DO UPDATE SET；
--                    clpm_metric_data_requirement 从 ON CONFLICT DO NOTHING 升级为 ON CONFLICT (metric_code) DO UPDATE SET（修复契约更新被跳过）；
--                    dcs_mode_mapping 保持 ON CONFLICT DO NOTHING（部分唯一索引，仅 description 非键）
-- =============================================================================

-- =============================================================================
-- 1. 管理员账户 (sys_user)
-- =============================================================================
-- 密码哈希说明：
--   所有 5 个用户均使用同一密码 admin123（bcrypt 哈希，开发/测试环境用）
--   生产环境部署前请通过应用端修改密码。
--   哈希通过 app.core.security.hash_password('admin123') 生成（bcrypt cost=12）
-- 改密说明（2026-08-03 调整）：
--   must_change_password 统一置 FALSE，部署后可直接使用链路配置等写操作，
--   无需先修改密码。生产环境上线后建议通过个人中心修改默认密码。
-- =============================================================================

INSERT INTO sys_user (id, username, password_hash, display_name, email, role, is_active, must_change_password, created_at, updated_at) VALUES
('00000000-0000-0000-0000-000000000001', 'admin',       '$2b$12$EmVQ8NwGlB/O8L4vJ0XSluBfxYOlTwBer7vnNFuVL/0qmhSXlfy/u', '系统管理员',     'admin@clpm.local',       'ADMIN',       TRUE, FALSE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000002', 'ic_engineer', '$2b$12$3KxnNHH3KmxeEE6AUmQOeuFEccnBLlHxaDBX5BIWCwvKPq1gqLrxy', '仪控工程师',     'ic_engineer@clpm.local', 'IC_ENGINEER', TRUE, FALSE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000003', 'pe_engineer', '$2b$12$dLInICVCCkfdsIfs6jJnqeJfR0HDzFbv7yqBWboZQSLRknlQuhOKG', '工艺工程师',     'pe_engineer@clpm.local', 'PE_ENGINEER', TRUE, FALSE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000004', 'sponsor',     '$2b$12$lpgnpJwE956RFjcYb4hyOubgVYhf0IDWs0xlBzbCU1RMuT1cmR0sC', '项目发起人',     'sponsor@clpm.local',     'SPONSOR',     TRUE, FALSE, NOW(), NOW()),
('00000000-0000-0000-0000-000000000005', 'expert',      '$2b$12$ai8B75As3GLsuFBHayAq2ufsMMmzezF.E9tg.058I/a30V7nTuiTG', '外部专家',       'expert@clpm.local',      'EXPERT',      TRUE, FALSE, NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 1.5 通用字典项 (sys_dict_item) — 29 条（2026-08-20 字典化：三类可配置枚举）
-- =============================================================================
-- LOOP_TYPE 回路类型 / MEASURE_TYPE 测点类型 / TAG_TYPE 参数类型；
-- 用户自定义项（浓度/电流/转速/粘度）按 2026-08-21 库内导出原样保留；
-- 冲突目标 (dict_type, item_code)，幂等重放不会产生重复项。
-- =============================================================================

INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000016', 'LOOP_TYPE', 'TEMPERATURE', '温度', 1, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000017', 'LOOP_TYPE', 'PRESSURE', '压力', 2, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000018', 'LOOP_TYPE', 'LEVEL', '液位', 3, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000019', 'LOOP_TYPE', 'FLOW', '流量', 4, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000020', 'LOOP_TYPE', 'ANALYSIS', '分析', 5, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000021', 'LOOP_TYPE', 'SPEED', '速度', 6, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000022', 'LOOP_TYPE', 'OTHER', '其他', 7, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('930109a3-41ab-4cf3-88df-1303a80ae2cd', 'LOOP_TYPE', 'CURRENT', '电流', 90, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('da2ef769-249d-4f92-98cc-40927da6ef9c', 'LOOP_TYPE', 'SPEED_RPM', '转速', 91, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('9f529109-ff21-4b26-a0c3-07b8e7331848', 'LOOP_TYPE', 'VISCOSITY', '粘度', 92, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000001', 'MEASURE_TYPE', 'TEMPERATURE', '温度', 1, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000002', 'MEASURE_TYPE', 'PRESSURE', '压力', 2, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000003', 'MEASURE_TYPE', 'LEVEL', '液位', 3, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000004', 'MEASURE_TYPE', 'FLOW', '流量', 4, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000005', 'MEASURE_TYPE', 'ANALYSIS', '分析', 5, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000006', 'MEASURE_TYPE', 'SPEED', '速度', 6, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000007', 'MEASURE_TYPE', 'OTHER', '其他', 7, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('609df723-02dc-4608-abe8-7df8a3a4073a', 'MEASURE_TYPE', 'CONCENTRATION', '浓度', 80, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('4846fcac-4353-46f7-b94b-08c93723e8a1', 'MEASURE_TYPE', 'dianliu', '电流', 90, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('1aa4d7e6-c703-4c48-af65-83ce8fddf718', 'MEASURE_TYPE', 'rpm', '转速', 100, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('01d6ecae-a6fd-4fde-8e5e-3b21234a4393', 'MEASURE_TYPE', 'niandu', '粘度', 110, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000008', 'TAG_TYPE', 'PV', '测量值', 1, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000009', 'TAG_TYPE', 'SP', '设定值', 2, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000010', 'TAG_TYPE', 'OP', '输出值', 3, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000011', 'TAG_TYPE', 'MODE', '控制方式', 4, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000012', 'TAG_TYPE', 'PID_P', '比例', 5, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000013', 'TAG_TYPE', 'PID_I', '积分', 6, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000014', 'TAG_TYPE', 'PID_D', '微分', 7, TRUE, 'admin') ON CONFLICT (dict_type, item_code) DO NOTHING;
INSERT INTO sys_dict_item (id, dict_type, item_code, item_label, sort_order, is_enabled, updated_by) VALUES ('00000000-0000-0000-0000-000000000015', 'TAG_TYPE', 'OTHER', '其他', 8, TRUE, 'seed') ON CONFLICT (dict_type, item_code) DO NOTHING;

-- =============================================================================
-- 2. AAS Tag 注册记录 (tag_registry) — 189 条（全部已关联回路）
-- =============================================================================
-- 数据来源：开发环境生产配置（2026-08-01 导出）
-- 清理规则：current_value/quality 置 NULL（初始状态，待 SignalR 推送实时值）；
--           last_sync_at 置 NOW()（标记入库时间）；is_linked 保留开发环境关联状态
-- v1.4 精简：移除 50 条未关联回路的测试测点（HDC/HDS/SZB 前缀），仅保留 27 回路 × 7 位号 = 189 条
-- 依赖关系：tag_registry 无外键依赖，优先于 plant_node/loop_ledger 插入
--           （plant_node.monitor_tag_id / loop_ledger.modeattr_tag_id 引用本表）
-- =============================================================================

INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('22f0ae00-4762-498e-84f6-4c71e1b7430b', '41FIC20021_PIDA.MODE', 'T-101进料流量 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_41fic20021_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('66fac5db-ef96-447d-9962-154e8ca7a53c', '41FIC20021_PIDA.OP', 'T-101进料流量 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_41fic20021_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('1008fb32-98d9-4538-9b25-5a5c02b3ecb6', '41FIC20021_PIDA.PID_D', 'T-101进料流量 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20021_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('f75ba919-dbdc-4be2-8bee-dd8a1a2eb8a7', '41FIC20021_PIDA.PID_I', 'T-101进料流量 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20021_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('95d504bf-cd30-4dc0-ba34-aef7215350b8', '41FIC20021_PIDA.PID_P', 'T-101进料流量 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_41fic20021_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('28af6fa9-f0d4-4929-a833-052a7ab612cc', '41FIC20021_PIDA.PV', 'T-101进料流量 PV', 'PV', NULL, NULL, NOW(), true, 0, 1.0, 'kmol/s', 'FLOW', 'd_loop_41fic20021_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9249831d-3b6e-4576-8e49-c5e073e3f273', '41FIC20021_PIDA.SP', 'T-101进料流量 SP', 'SP', NULL, NULL, NOW(), true, 0, 1.0, 'kmol/s', 'FLOW', 'd_loop_41fic20021_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c458bf2e-17c2-41b7-86b2-6bd528156869', '41FIC20051_PIDA.MODE', 'V2008锁斗吹扫气流量 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_41fic20051_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('f51b320a-3464-47e0-9c09-744786fdcab9', '41FIC20051_PIDA.OP', 'V2008锁斗吹扫气流量 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_41fic20051_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b4b0ad4d-5c41-460d-ac45-69bbf871d294', '41FIC20051_PIDA.PID_D', 'V2008锁斗吹扫气流量 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20051_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9feff7be-fa8b-475e-bd8e-7a14c2ba2c1e', '41FIC20051_PIDA.PID_I', 'V2008锁斗吹扫气流量 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20051_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('d582ebd3-faf6-4161-a71b-aa774fc7819f', '41FIC20051_PIDA.PID_P', 'V2008锁斗吹扫气流量 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_41fic20051_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('684b2713-3460-460a-be55-c6a24aab0ccc', '41FIC20051_PIDA.PV', 'V2008锁斗吹扫气流量 PV', 'PV', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic20051_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('d8ca2277-036d-421f-be78-65288fba8be8', '41FIC20051_PIDA.SP', 'V2008锁斗吹扫气流量 SP', 'SP', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic20051_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('18ff52cd-2cce-44a7-9352-88a637d0dd37', '41FIC20074_PIDA.MODE', 'E2004A催化剂冷却风1 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_41fic20074_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('eab1a850-1fd4-49f7-9f38-2118eb4cd235', '41FIC20074_PIDA.OP', 'E2004A催化剂冷却风1 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_41fic20074_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('cd23456a-17c2-48e4-b265-e64be8c5d2dd', '41FIC20074_PIDA.PID_D', 'E2004A催化剂冷却风1 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20074_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('90fb22b3-cc7d-46d7-bf70-60019650bb62', '41FIC20074_PIDA.PID_I', 'E2004A催化剂冷却风1 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20074_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('02d12d59-6850-4e38-82d5-87d98aafb4ba', '41FIC20074_PIDA.PID_P', 'E2004A催化剂冷却风1 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_41fic20074_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('69eddb60-4736-41b9-b165-56ddbc130b0e', '41FIC20074_PIDA.PV', 'E2004A催化剂冷却风1 PV', 'PV', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic20074_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('99738bef-2547-4f86-8330-0e155f45ed83', '41FIC20074_PIDA.SP', 'E2004A催化剂冷却风1 SP', 'SP', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic20074_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9483c31f-cd76-42e5-a453-0ac77a66f99d', '41FIC20132_PIDA.MODE', 'E2004B催化剂冷却风2 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_41fic20132_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('fb7990d9-572d-4b79-9485-06702d2cfcc9', '41FIC20132_PIDA.OP', 'E2004B催化剂冷却风2 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_41fic20132_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b7b82788-13a9-4918-8fc6-2290361fd753', '41FIC20132_PIDA.PID_D', 'E2004B催化剂冷却风2 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20132_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('107ee56e-38a8-40a2-bdcf-225317ac3e01', '41FIC20132_PIDA.PID_I', 'E2004B催化剂冷却风2 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic20132_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('044d4a86-8f1a-47e6-9944-aa8689fa80a2', '41FIC20132_PIDA.PID_P', 'E2004B催化剂冷却风2 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_41fic20132_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('8c850c22-f033-4abb-87ab-d2da867ff11d', '41FIC20132_PIDA.PV', 'E2004B催化剂冷却风2 PV', 'PV', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic20132_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('5b873911-9d0d-49ed-b55c-e9c002bfa236', '41FIC20132_PIDA.SP', 'E2004B催化剂冷却风2 SP', 'SP', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic20132_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('27ebf468-bb48-479a-b667-ec5817213d94', '41FIC40504_PIDA.MODE', 'T-101回流流量 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_41fic40504_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('2694c4b1-6ad1-422d-a6f8-387e3aa8e9f8', '41FIC40504_PIDA.OP', 'T-101回流流量 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_41fic40504_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('fa7b4df2-7c00-4fc9-b6e5-0c484561c9a9', '41FIC40504_PIDA.PID_D', 'T-101回流流量 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic40504_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b5161954-06a4-4772-ac64-4fe7279c8c35', '41FIC40504_PIDA.PID_I', 'T-101回流流量 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic40504_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('1a4c9e1d-3224-4774-8bee-c88c6c6ab125', '41FIC40504_PIDA.PID_P', 'T-101回流流量 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_41fic40504_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9cc657c6-d6ca-4f41-90a1-21031cd9c905', '41FIC40504_PIDA.PV', 'T-101回流流量 PV', 'PV', NULL, NULL, NOW(), true, 0, 1.0, 'kmol/s', 'FLOW', 'd_loop_41fic40504_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b7c944db-3971-463e-bad0-4e530382e4cd', '41FIC40504_PIDA.SP', 'T-101回流流量 SP', 'SP', NULL, NULL, NOW(), true, 0, 1.0, 'kmol/s', 'FLOW', 'd_loop_41fic40504_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9879d6f3-e60a-42c1-8a6d-8baebe25ef25', '41FIC40519_PIDA.MODE', 'P4032A/B废水输送流量 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_41fic40519_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('d04dc912-cfbf-4bb1-97d0-4dc0d7719c89', '41FIC40519_PIDA.OP', 'P4032A/B废水输送流量 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_41fic40519_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b56563e6-228d-4d08-a248-dc2d4f1ae545', '41FIC40519_PIDA.PID_D', 'P4032A/B废水输送流量 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic40519_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('19d2523f-b0a7-4e78-b38f-04170be80958', '41FIC40519_PIDA.PID_I', 'P4032A/B废水输送流量 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_41fic40519_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('650c0457-e716-4376-9417-12f2d5f478f9', '41FIC40519_PIDA.PID_P', 'P4032A/B废水输送流量 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_41fic40519_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c885214b-efe4-4f9c-8e1f-9bac746f79ae', '41FIC40519_PIDA.PV', 'P4032A/B废水输送流量 PV', 'PV', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic40519_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('199d5751-8471-43b4-9bf7-d5dec8a1f14b', '41FIC40519_PIDA.SP', 'P4032A/B废水输送流量 SP', 'SP', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_41fic40519_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('674974a7-d86d-4b31-9c8d-e445055b6133', '41LIC20117_PIDA.MODE', 'V2010闪蒸罐液位 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'LEVEL', 'd_loop_41lic20117_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c547c6d3-b6f6-45c7-b696-5c98e2f0d4d3', '41LIC20117_PIDA.OP', 'V2010闪蒸罐液位 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic20117_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('0e94ab2e-bfd8-4dcc-ac51-c1ae7c345ab4', '41LIC20117_PIDA.PID_D', 'V2010闪蒸罐液位 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic20117_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a269e925-8f1f-4d65-923c-3acc4105a7f7', '41LIC20117_PIDA.PID_I', 'V2010闪蒸罐液位 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic20117_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('4626032e-51c2-48c8-9e49-c0e18cd9546e', '41LIC20117_PIDA.PID_P', 'V2010闪蒸罐液位 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'LEVEL', 'd_loop_41lic20117_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('ec237411-4bad-41e4-93cb-828234562b9e', '41LIC20117_PIDA.PV', 'V2010闪蒸罐液位 PV', 'PV', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic20117_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('3b9ec3a7-af53-4939-9ac5-d1dfd1d40d18', '41LIC20117_PIDA.SP', 'V2010闪蒸罐液位 SP', 'SP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic20117_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b1328d35-c5f3-4fca-a5dc-4f0fd34082b5', '41LIC40108_PIDA.MODE', 'T-101塔底液位 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'LEVEL', 'd_loop_41lic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('2f743047-6fd8-44f4-9c29-95b849b49b37', '41LIC40108_PIDA.OP', 'T-101塔底液位 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('2e9f3bab-941f-4f97-840c-b266e5499e34', '41LIC40108_PIDA.PID_D', 'T-101塔底液位 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c3acf680-ea99-4427-8507-3832f3038089', '41LIC40108_PIDA.PID_I', 'T-101塔底液位 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('bb3d4ee2-0494-4cf8-809e-24e87303a01d', '41LIC40108_PIDA.PID_P', 'T-101塔底液位 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'LEVEL', 'd_loop_41lic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('3173a1d2-372d-4bd6-a143-0cce88698c31', '41LIC40108_PIDA.PV', 'T-101塔底液位 PV', 'PV', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a5809dd6-6f38-42be-93c9-2602cfea6f2e', '41LIC40108_PIDA.SP', 'T-101塔底液位 SP', 'SP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9a915a8b-eabc-437e-844c-e2b9bbad8cd8', '41LIC40201_PIDA.MODE', 'E4013预切割塔冷却器液位 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'LEVEL', 'd_loop_41lic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('27777161-fa8c-4527-a65a-2085b4fc3521', '41LIC40201_PIDA.OP', 'E4013预切割塔冷却器液位 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a70f70b1-6d03-4a76-8251-dc62d40c1370', '41LIC40201_PIDA.PID_D', 'E4013预切割塔冷却器液位 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('1088f587-21f0-45c0-8528-5deccbcfee45', '41LIC40201_PIDA.PID_I', 'E4013预切割塔冷却器液位 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('218b3e37-5cb2-4f3a-9e39-4c05d628e829', '41LIC40201_PIDA.PID_P', 'E4013预切割塔冷却器液位 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'LEVEL', 'd_loop_41lic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c8725542-23b5-4e8b-8275-5c8ce88ebddb', '41LIC40201_PIDA.PV', 'E4013预切割塔冷却器液位 PV', 'PV', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b0165b63-a92a-4750-a9ed-a1fa696d1a51', '41LIC40201_PIDA.SP', 'E4013预切割塔冷却器液位 SP', 'SP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9b5bb38b-ed3a-4ac5-9fdb-02a4d3d005b8', '41LIC40309_PIDA.MODE', 'V4021 E4028凝液罐液位 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'LEVEL', 'd_loop_41lic40309_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('42c5de42-efd2-4220-a19d-a1ed947fcfc7', '41LIC40309_PIDA.OP', 'V4021 E4028凝液罐液位 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40309_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a7dbbd63-1510-4fdb-b1b7-c0bba2ae379d', '41LIC40309_PIDA.PID_D', 'V4021 E4028凝液罐液位 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40309_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('041b83a3-f4b8-4c90-ba8e-ea8757ad54e4', '41LIC40309_PIDA.PID_I', 'V4021 E4028凝液罐液位 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40309_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a8952106-8c04-4484-94bd-3f2ea910431c', '41LIC40309_PIDA.PID_P', 'V4021 E4028凝液罐液位 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'LEVEL', 'd_loop_41lic40309_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('05159296-d868-4fb0-87b1-f66dd5dd5aa2', '41LIC40309_PIDA.PV', 'V4021 E4028凝液罐液位 PV', 'PV', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40309_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('87259ccc-1aa6-4794-9733-b9ac054120b3', '41LIC40309_PIDA.SP', 'V4021 E4028凝液罐液位 SP', 'SP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40309_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('68c444ff-ee45-4467-97da-9640b3d614ca', '41LIC40404_PIDA.MODE', 'T-101回流罐液位 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'LEVEL', 'd_loop_41lic40404_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('cc7501f0-daaa-4ba2-a3c7-b10fb88dfccb', '41LIC40404_PIDA.OP', 'T-101回流罐液位 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40404_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('8e55e52b-ec88-461c-8487-31e6cc31df2d', '41LIC40404_PIDA.PID_D', 'T-101回流罐液位 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40404_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('5b14d674-d822-4ad5-b129-438bf4fcd831', '41LIC40404_PIDA.PID_I', 'T-101回流罐液位 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_41lic40404_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('27668240-7418-4991-a0ea-63374d062945', '41LIC40404_PIDA.PID_P', 'T-101回流罐液位 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'LEVEL', 'd_loop_41lic40404_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('49547e16-773e-406a-9baf-aa75484ab047', '41LIC40404_PIDA.PV', 'T-101回流罐液位 PV', 'PV', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40404_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('74e10766-3faa-4bcb-8d22-6c2456161b94', '41LIC40404_PIDA.SP', 'T-101回流罐液位 SP', 'SP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_41lic40404_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('8bdd746c-91a9-4ca9-8b20-c6f212590e3a', '41PIC20124_PIDA.MODE', 'C2002压缩机出口压力 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'PRESSURE', 'd_loop_41pic20124_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('ba725d7b-148d-4bc5-a73a-64480d3f3e16', '41PIC20124_PIDA.OP', 'C2002压缩机出口压力 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'PRESSURE', 'd_loop_41pic20124_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('dbb21816-25c5-4076-ae6c-a596c6cba795', '41PIC20124_PIDA.PID_D', 'C2002压缩机出口压力 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic20124_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('41af5d26-2b28-4e38-a0ad-f707d2ff5766', '41PIC20124_PIDA.PID_I', 'C2002压缩机出口压力 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic20124_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('6f2e0357-14c9-4470-b667-68767e33d874', '41PIC20124_PIDA.PID_P', 'C2002压缩机出口压力 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'PRESSURE', 'd_loop_41pic20124_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('bc09ccab-fca8-4ea2-ad93-acc9bfeced46', '41PIC20124_PIDA.PV', 'C2002压缩机出口压力 PV', 'PV', NULL, NULL, NOW(), true, 0, 1.5, 'MPa', 'PRESSURE', 'd_loop_41pic20124_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('72c8657d-113b-4eb1-8547-a91d69e8c281', '41PIC20124_PIDA.SP', 'C2002压缩机出口压力 SP', 'SP', NULL, NULL, NOW(), true, 0, 1.5, 'MPa', 'PRESSURE', 'd_loop_41pic20124_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('93832819-799d-4372-90a0-b5791da7c10a', '41PIC20137_PIDA.MODE', 'V2018凝液罐出口压力 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'PRESSURE', 'd_loop_41pic20137_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('76634060-3089-46d0-beaf-32f74ae5b823', '41PIC20137_PIDA.OP', 'V2018凝液罐出口压力 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'PRESSURE', 'd_loop_41pic20137_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('061f50d5-aa42-4483-bac5-e820ae5e39a2', '41PIC20137_PIDA.PID_D', 'V2018凝液罐出口压力 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic20137_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c56d3349-2790-48b7-9dce-f9b53ac54e09', '41PIC20137_PIDA.PID_I', 'V2018凝液罐出口压力 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic20137_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('45d87cd5-db9c-475e-b8b1-275ea7f04d19', '41PIC20137_PIDA.PID_P', 'V2018凝液罐出口压力 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'PRESSURE', 'd_loop_41pic20137_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c6e80e1a-fb1b-452f-bc50-15d88292ccb5', '41PIC20137_PIDA.PV', 'V2018凝液罐出口压力 PV', 'PV', NULL, NULL, NOW(), true, 0, 6, 'MPa', 'PRESSURE', 'd_loop_41pic20137_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('f8de89ae-9b5f-41d5-a0d7-a1bcb1a0cfb2', '41PIC20137_PIDA.SP', 'V2018凝液罐出口压力 SP', 'SP', NULL, NULL, NOW(), true, 0, 6, 'MPa', 'PRESSURE', 'd_loop_41pic20137_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('57d843de-913d-4567-a463-3546e045b26a', '41PIC40306_PIDA.MODE', 'E4023B乙炔加氢换热器压力 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'PRESSURE', 'd_loop_41pic40306_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('e882ca34-ad32-4fa8-a866-17809e45febb', '41PIC40306_PIDA.OP', 'E4023B乙炔加氢换热器压力 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'PRESSURE', 'd_loop_41pic40306_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('970b85b4-5371-4000-b266-2644741bc388', '41PIC40306_PIDA.PID_D', 'E4023B乙炔加氢换热器压力 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic40306_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('014f07c4-9744-4f81-85fe-0f9535cdcb1e', '41PIC40306_PIDA.PID_I', 'E4023B乙炔加氢换热器压力 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic40306_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('deb81842-3f5b-4cb6-9475-536d098f6b45', '41PIC40306_PIDA.PID_P', 'E4023B乙炔加氢换热器压力 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'PRESSURE', 'd_loop_41pic40306_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('6d03c03e-5a23-4386-aefd-316de62754df', '41PIC40306_PIDA.PV', 'E4023B乙炔加氢换热器压力 PV', 'PV', NULL, NULL, NOW(), true, 0, 6, 'MPa', 'PRESSURE', 'd_loop_41pic40306_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('e9cbfe31-85c8-4e9a-a81d-b9a2525d5a78', '41PIC40306_PIDA.SP', 'E4023B乙炔加氢换热器压力 SP', 'SP', NULL, NULL, NOW(), true, 0, 6, 'MPa', 'PRESSURE', 'd_loop_41pic40306_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('411b8466-cd48-4f99-819a-c4169b34acec', '41PIC40320_PIDA.MODE', 'T-101塔顶压力 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'PRESSURE', 'd_loop_41pic40320_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('00994deb-47fd-43ea-b5f7-bcc0db8896be', '41PIC40320_PIDA.OP', 'T-101塔顶压力 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'PRESSURE', 'd_loop_41pic40320_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('cbb070c2-2084-42a8-b23e-85cd321ae2b6', '41PIC40320_PIDA.PID_D', 'T-101塔顶压力 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic40320_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('78415ebb-3ebd-4c1c-9a66-3cc6c9af42d5', '41PIC40320_PIDA.PID_I', 'T-101塔顶压力 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic40320_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('65dcb2ee-4bd2-49d6-b9b7-e8c935cd19e4', '41PIC40320_PIDA.PID_P', 'T-101塔顶压力 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'PRESSURE', 'd_loop_41pic40320_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b47ecd3c-9b7f-4331-971e-b6f5b95f27ed', '41PIC40320_PIDA.PV', 'T-101塔顶压力 PV', 'PV', NULL, NULL, NOW(), true, 0, 2.0, 'MPa', 'PRESSURE', 'd_loop_41pic40320_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b35a5361-d5ce-4845-a57b-f8cc8032722d', '41PIC40320_PIDA.SP', 'T-101塔顶压力 SP', 'SP', NULL, NULL, NOW(), true, 0, 2.0, 'MPa', 'PRESSURE', 'd_loop_41pic40320_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('f60fd5f8-8a17-4f97-9fa1-de89bcc3115e', '41PIC40506_PIDA.MODE', 'V4061 C4+缓冲罐压力 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'PRESSURE', 'd_loop_41pic40506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('e8efef6c-887a-4169-a82a-4e769e5d5cf2', '41PIC40506_PIDA.OP', 'V4061 C4+缓冲罐压力 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'PRESSURE', 'd_loop_41pic40506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a1a79505-620b-4ce6-badb-6eca794c6229', '41PIC40506_PIDA.PID_D', 'V4061 C4+缓冲罐压力 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic40506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('bc2b2ad9-063b-4fe9-9fca-ff03ed98d0ea', '41PIC40506_PIDA.PID_I', 'V4061 C4+缓冲罐压力 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_41pic40506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('706eaac0-3084-4d31-a17b-e4e8c948df06', '41PIC40506_PIDA.PID_P', 'V4061 C4+缓冲罐压力 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'PRESSURE', 'd_loop_41pic40506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('bb10480d-5155-4b27-9a78-ff112c1454ce', '41PIC40506_PIDA.PV', 'V4061 C4+缓冲罐压力 PV', 'PV', NULL, NULL, NOW(), true, 0, 4, 'MPa', 'PRESSURE', 'd_loop_41pic40506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a80331f3-1638-4841-bfa8-ec7251dc9663', '41PIC40506_PIDA.SP', 'V4061 C4+缓冲罐压力 SP', 'SP', NULL, NULL, NOW(), true, 0, 4, 'MPa', 'PRESSURE', 'd_loop_41pic40506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('6fb92b1e-66c3-49e1-84dd-42abde40e3f1', '41TIC20006_PIDA.MODE', '界外换热器出口温度 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'TEMPERATURE', 'd_loop_41tic20006_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('2994b155-0550-40bf-ab8f-073db5fbc7a1', '41TIC20006_PIDA.OP', '界外换热器出口温度 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'TEMPERATURE', 'd_loop_41tic20006_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('7450f955-2c8f-4e85-bee2-70e02eaa4bb7', '41TIC20006_PIDA.PID_D', '界外换热器出口温度 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_41tic20006_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('97651679-a59a-4387-b4e3-23ffc2c21375', '41TIC20006_PIDA.PID_I', '界外换热器出口温度 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_41tic20006_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('ffab1f34-0f7d-4256-912a-14fdeab2581c', '41TIC20006_PIDA.PID_P', '界外换热器出口温度 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'TEMPERATURE', 'd_loop_41tic20006_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('0997dfa3-42d0-4890-a5ac-ce8ef6a0dd5a', '41TIC20006_PIDA.PV', '界外换热器出口温度 PV', 'PV', NULL, NULL, NOW(), true, 0, 150, '℃', 'TEMPERATURE', 'd_loop_41tic20006_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('f3710810-ac8b-4635-8ded-eb543e777ee8', '41TIC20006_PIDA.SP', '界外换热器出口温度 SP', 'SP', NULL, NULL, NOW(), true, 0, 150, '℃', 'TEMPERATURE', 'd_loop_41tic20006_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b732d817-a093-4557-802a-3ae130da27e4', '41TIC40201_PIDA.MODE', 'E4013冷却器出口温度 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'TEMPERATURE', 'd_loop_41tic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('1ccea1ef-cb37-4ab1-8ba9-6c008484a78e', '41TIC40201_PIDA.OP', 'E4013冷却器出口温度 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'TEMPERATURE', 'd_loop_41tic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('bf6a76d6-57c0-49c2-99a0-1d646cd68b5a', '41TIC40201_PIDA.PID_D', 'E4013冷却器出口温度 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_41tic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('029f40b8-424b-4cff-86a4-90a49839feb9', '41TIC40201_PIDA.PID_I', 'E4013冷却器出口温度 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_41tic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('1f2b7590-cf24-4f1f-9999-759f9f0e0fef', '41TIC40201_PIDA.PID_P', 'E4013冷却器出口温度 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'TEMPERATURE', 'd_loop_41tic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('bdcb00fe-b42c-41fe-814e-86613e25112f', '41TIC40201_PIDA.PV', 'E4013冷却器出口温度 PV', 'PV', NULL, NULL, NOW(), true, 0, 400, '℃', 'TEMPERATURE', 'd_loop_41tic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('3f3684c3-a55b-4af9-bccc-0bb9a0e9ae2d', '41TIC40201_PIDA.SP', 'E4013冷却器出口温度 SP', 'SP', NULL, NULL, NOW(), true, 0, 400, '℃', 'TEMPERATURE', 'd_loop_41tic40201_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('04b736d8-11f4-4a57-9d41-10ef3c43a2e7', '80FIC11906_PIDA.MODE', 'V123→V122 BAL循环气流量 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_80fic11906_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('6ebe52d9-f7e8-4f15-bd4f-4faffa6e74ff', '80FIC11906_PIDA.OP', 'V123→V122 BAL循环气流量 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_80fic11906_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('6ddea1ad-4c0c-4ea3-9c8c-67276247b7f6', '80FIC11906_PIDA.PID_D', 'V123→V122 BAL循环气流量 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_80fic11906_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('dfe6eae3-a051-4171-b152-4d96428b51d5', '80FIC11906_PIDA.PID_I', 'V123→V122 BAL循环气流量 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_80fic11906_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('3951c9c8-4ea7-4ccb-a9ab-576aa10bde01', '80FIC11906_PIDA.PID_P', 'V123→V122 BAL循环气流量 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_80fic11906_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('780ed6e2-6f12-442c-945e-b5e44dc06a59', '80FIC11906_PIDA.PV', 'V123→V122 BAL循环气流量 PV', 'PV', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_80fic11906_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('e046ce7d-1161-4b06-a545-83c3f6e5bf51', '80FIC11906_PIDA.SP', 'V123→V122 BAL循环气流量 SP', 'SP', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_80fic11906_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b967a98c-5f24-46a0-981e-182ca91aa1dd', '80FIC31402_PIDA.MODE', '装置边界外循环流量 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'FLOW', 'd_loop_80fic31402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('22057539-c7aa-40d6-82d7-4cb8f9629cfc', '80FIC31402_PIDA.OP', '装置边界外循环流量 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'FLOW', 'd_loop_80fic31402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('4f7097e4-b625-4bb0-ae9b-1fd7de9130df', '80FIC31402_PIDA.PID_D', '装置边界外循环流量 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_80fic31402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c8e85c1b-9626-49ed-ad25-b39f984a7edb', '80FIC31402_PIDA.PID_I', '装置边界外循环流量 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'FLOW', 'd_loop_80fic31402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('1e8240e3-bb27-48d2-873a-cf9427f91858', '80FIC31402_PIDA.PID_P', '装置边界外循环流量 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'FLOW', 'd_loop_80fic31402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('0fde44a1-a439-4e56-aef0-83af3f736a87', '80FIC31402_PIDA.PV', '装置边界外循环流量 PV', 'PV', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_80fic31402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a6792ebf-e1a7-4a96-be46-02ab1ff9f68a', '80FIC31402_PIDA.SP', '装置边界外循环流量 SP', 'SP', NULL, NULL, NOW(), true, 0, 200, 't/h', 'FLOW', 'd_loop_80fic31402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('ff6693bb-6eaa-41b7-9d82-9a4eefbb39c8', '80LIC10603_PIDA.MODE', 'R102羰基合成反应器液位 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'LEVEL', 'd_loop_80lic10603_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('3391dc82-5ff1-4f7a-a793-1471d1e185ce', '80LIC10603_PIDA.OP', 'R102羰基合成反应器液位 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_80lic10603_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('0ee35351-cef2-4ae6-a01b-f780a826a2a4', '80LIC10603_PIDA.PID_D', 'R102羰基合成反应器液位 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_80lic10603_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('5554f290-5ac9-41e7-ab4c-5fa8e1eb58c1', '80LIC10603_PIDA.PID_I', 'R102羰基合成反应器液位 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_80lic10603_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('96121933-8630-47d5-9623-22d7cc3e72d9', '80LIC10603_PIDA.PID_P', 'R102羰基合成反应器液位 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'LEVEL', 'd_loop_80lic10603_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('b4200702-65fd-4017-8a93-c994b319c1fb', '80LIC10603_PIDA.PV', 'R102羰基合成反应器液位 PV', 'PV', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_80lic10603_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('337bedd8-4f6f-4942-b297-77f30aa91a16', '80LIC10603_PIDA.SP', 'R102羰基合成反应器液位 SP', 'SP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_80lic10603_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9c322b14-da11-4a73-93c6-c51812571474', '80LIC10801_PIDA.MODE', 'V107低压蒸发罐液位 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'LEVEL', 'd_loop_80lic10801_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('7a9b2f23-aec3-431c-818f-ef79c4b19970', '80LIC10801_PIDA.OP', 'V107低压蒸发罐液位 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_80lic10801_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('d92da3bb-7d73-400f-bda5-e50d617faa4c', '80LIC10801_PIDA.PID_D', 'V107低压蒸发罐液位 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_80lic10801_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('ade948b0-016e-4e2a-ba7a-fcf416d3dc8b', '80LIC10801_PIDA.PID_I', 'V107低压蒸发罐液位 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'LEVEL', 'd_loop_80lic10801_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c2168c04-7d88-4d1b-a145-cc842f979319', '80LIC10801_PIDA.PID_P', 'V107低压蒸发罐液位 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'LEVEL', 'd_loop_80lic10801_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('553d4827-221e-4d4d-a993-f14ce1790f7f', '80LIC10801_PIDA.PV', 'V107低压蒸发罐液位 PV', 'PV', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_80lic10801_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('40cab1d0-2f44-4851-b8e9-98b44bebc4e5', '80LIC10801_PIDA.SP', 'V107低压蒸发罐液位 SP', 'SP', NULL, NULL, NOW(), true, 0, 100, '%', 'LEVEL', 'd_loop_80lic10801_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('e7770f7a-a522-4f46-95aa-15d68f09471f', '80TIC10303_PIDA.MODE', 'T-101塔顶温度 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'TEMPERATURE', 'd_loop_80tic10303_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('9c2523b1-02b1-44fc-862a-b2834f8a909c', '80TIC10303_PIDA.OP', 'T-101塔顶温度 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'TEMPERATURE', 'd_loop_80tic10303_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('4e9943cc-87d8-45c0-a285-48d0b7c3aafb', '80TIC10303_PIDA.PID_D', 'T-101塔顶温度 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_80tic10303_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('03b052c3-0465-41fb-a6d9-7eeeaf2465b1', '80TIC10303_PIDA.PID_I', 'T-101塔顶温度 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_80tic10303_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('60f33ccd-0530-457c-be60-9f67ea3e103f', '80TIC10303_PIDA.PID_P', 'T-101塔顶温度 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'TEMPERATURE', 'd_loop_80tic10303_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('31299816-1c8d-47a2-b950-e7b7ecae2e7b', '80TIC10303_PIDA.PV', 'T-101塔顶温度 PV', 'PV', NULL, NULL, NOW(), true, 0, 80, '℃', 'TEMPERATURE', 'd_loop_80tic10303_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('fad88f66-3a92-4ed5-88b2-1624161363c2', '80TIC10303_PIDA.SP', 'T-101塔顶温度 SP', 'SP', NULL, NULL, NOW(), true, 0, 80, '℃', 'TEMPERATURE', 'd_loop_80tic10303_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('a3e3e13e-0b55-4121-b1b6-c96a538d4494', '80TIC10402_PIDA.MODE', 'E105→V104合成气温度 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'TEMPERATURE', 'd_loop_80tic10402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('f6bedd11-7f5d-47e1-b9b9-d03be3ac13e1', '80TIC10402_PIDA.OP', 'E105→V104合成气温度 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'TEMPERATURE', 'd_loop_80tic10402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('1c77c14e-58de-4a2d-a2c4-1588aa4d9920', '80TIC10402_PIDA.PID_D', 'E105→V104合成气温度 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_80tic10402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('20f23908-3f2a-4c8b-b943-210a3ea90c46', '80TIC10402_PIDA.PID_I', 'E105→V104合成气温度 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_80tic10402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('6907b506-2cc8-4c1b-9895-56e35cc0863d', '80TIC10402_PIDA.PID_P', 'E105→V104合成气温度 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'TEMPERATURE', 'd_loop_80tic10402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('f4c0c393-26f2-4305-9136-f2fe7fa842f2', '80TIC10402_PIDA.PV', 'E105→V104合成气温度 PV', 'PV', NULL, NULL, NOW(), true, 0, 200, '℃', 'TEMPERATURE', 'd_loop_80tic10402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('dfa39406-79e0-4217-bb03-b531f27b188a', '80TIC10402_PIDA.SP', 'E105→V104合成气温度 SP', 'SP', NULL, NULL, NOW(), true, 0, 200, '℃', 'TEMPERATURE', 'd_loop_80tic10402_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('17d7f5cb-6265-4d5f-9e28-d6b207696d1a', '80TIC10506_PIDA.MODE', 'T-101塔底温度 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'TEMPERATURE', 'd_loop_80tic10506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('be2b394c-ea9e-4437-bdb8-c6e883fe7b4e', '80TIC10506_PIDA.OP', 'T-101塔底温度 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'TEMPERATURE', 'd_loop_80tic10506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('52f065b4-eb4f-45b3-9d41-e52a2be8692c', '80TIC10506_PIDA.PID_D', 'T-101塔底温度 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_80tic10506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('8e3e4bfc-3868-4c49-9ac5-a13b0f1a0d88', '80TIC10506_PIDA.PID_I', 'T-101塔底温度 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'TEMPERATURE', 'd_loop_80tic10506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('120b1a0f-b3ff-4cd7-bbab-d35579c87615', '80TIC10506_PIDA.PID_P', 'T-101塔底温度 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'TEMPERATURE', 'd_loop_80tic10506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('d1bf7d65-92d1-414e-b29f-08bd491d2195', '80TIC10506_PIDA.PV', 'T-101塔底温度 PV', 'PV', NULL, NULL, NOW(), true, 20, 100, '℃', 'TEMPERATURE', 'd_loop_80tic10506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('5f7a94d2-906c-4fe4-8516-743c6799339a', '80TIC10506_PIDA.SP', 'T-101塔底温度 SP', 'SP', NULL, NULL, NOW(), true, 20, 100, '℃', 'TEMPERATURE', 'd_loop_80tic10506_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('ec140e13-b2f1-4e71-9f4b-b9248bbd57a1', '80TIC40108_PIDA.MODE', 'T-101塔顶轻组分分析 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'ANALYSIS', 'd_loop_80tic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c359487b-06e2-41b2-9084-660ce58382f8', '80TIC40108_PIDA.OP', 'T-101塔顶轻组分分析 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'ANALYSIS', 'd_loop_80tic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('38c4e505-e0d7-4143-ae28-c8364d62d6c5', '80TIC40108_PIDA.PID_D', 'T-101塔顶轻组分分析 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'ANALYSIS', 'd_loop_80tic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('42e51e5f-553e-4962-9618-0ff560ab5ed8', '80TIC40108_PIDA.PID_I', 'T-101塔顶轻组分分析 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'ANALYSIS', 'd_loop_80tic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('24f676c1-b163-4568-b916-3899b47ff93e', '80TIC40108_PIDA.PID_P', 'T-101塔顶轻组分分析 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'ANALYSIS', 'd_loop_80tic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('54446457-d881-407a-93b2-42544c703cbb', '80TIC40108_PIDA.PV', 'T-101塔顶轻组分分析 PV', 'PV', NULL, NULL, NOW(), true, 0, 1.0, 'mol/mol', 'ANALYSIS', 'd_loop_80tic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('ccf7ffe6-26e0-4a97-ad43-ced16037e9d3', '80TIC40108_PIDA.SP', 'T-101塔顶轻组分分析 SP', 'SP', NULL, NULL, NOW(), true, 0, 1.0, 'mol/mol', 'ANALYSIS', 'd_loop_80tic40108_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('36514c9d-1bce-4ea5-a211-488296680d3c', '90PIC51212A_PIDA.MODE', 'TK521A辛醇罐顶部压力 MODE', 'MODE', NULL, NULL, NOW(), true, 0, 10, '', 'PRESSURE', 'd_loop_90pic51212a_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('55dd8518-5eb0-4916-bfd9-9277d304652b', '90PIC51212A_PIDA.OP', 'TK521A辛醇罐顶部压力 OP', 'OP', NULL, NULL, NOW(), true, 0, 100, '%', 'PRESSURE', 'd_loop_90pic51212a_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('c4ca0b91-5d4b-45e9-bba2-7567d9bbf659', '90PIC51212A_PIDA.PID_D', 'TK521A辛醇罐顶部压力 PID_D', 'PID_D', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_90pic51212a_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('cc3717c9-b0ba-4518-bcba-7cc72204c2b9', '90PIC51212A_PIDA.PID_I', 'TK521A辛醇罐顶部压力 PID_I', 'PID_I', NULL, NULL, NOW(), true, 0, 1000, 's', 'PRESSURE', 'd_loop_90pic51212a_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('2345ec41-7c6f-4649-9686-7ed86654f080', '90PIC51212A_PIDA.PID_P', 'TK521A辛醇罐顶部压力 PID_P', 'PID_P', NULL, NULL, NOW(), true, 0, 100, '', 'PRESSURE', 'd_loop_90pic51212a_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('099704d3-8318-4820-ae11-be9b64b4cf4a', '90PIC51212A_PIDA.PV', 'TK521A辛醇罐顶部压力 PV', 'PV', NULL, NULL, NOW(), true, 0, 5, 'MPa', 'PRESSURE', 'd_loop_90pic51212a_pida') ON CONFLICT (id) DO NOTHING;
INSERT INTO tag_registry (id, tag_name, tag_description, tag_type, current_value, quality, last_sync_at, is_linked, range_min, range_max, unit, measure_type, tdengine_tag_id) VALUES ('aa2f4027-b066-4368-b5ff-58ff6799bfa1', '90PIC51212A_PIDA.SP', 'TK521A辛醇罐顶部压力 SP', 'SP', NULL, NULL, NOW(), true, 0, 5, 'MPa', 'PRESSURE', 'd_loop_90pic51212a_pida') ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 3. 工厂节点 (plant_node) — 7 节点
-- =============================================================================
-- 层级结构（对齐 CLPM-engine/PLANT_LOOP_MAPPING.md，2026-08-21）:
--   FACTORY  EO 工厂
--     └── AREA  EO 装置
--           ├── UNIT 精馏塔单元     col_t101   8 PLANT + 2 LTI = 10 回路
--           ├── UNIT 醛化反应单元   rx_r101    5 回路
--           ├── UNIT 急冷分离单元   sep_quench 6 回路
--           └── UNIT 脱甲烷精馏单元 sep_demeth 6 回路
--   FACTORY  致联工厂（预留，未挂回路）
-- 依赖关系：parent_id 自引用（FACTORY 先于 AREA/UNIT）；回路 unit_id 挂 UNIT
-- =============================================================================

INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled, created_at, updated_at) VALUES ('35af8328-e72d-414f-bfc9-b0c0caad71ee', 'EO 工厂', 'FACTORY', NULL, true, NOW(), NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled, created_at, updated_at) VALUES ('2181a13b-45eb-4306-9d19-c143f1b3ed11', '致联工厂', 'FACTORY', NULL, false, NOW(), NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled, created_at, updated_at) VALUES ('ff26e255-dcb9-45b4-8b79-a0d1c8c51fae', 'EO 装置', 'AREA', '35af8328-e72d-414f-bfc9-b0c0caad71ee', true, NOW(), NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled, created_at, updated_at) VALUES ('e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', '精馏塔单元', 'UNIT', 'ff26e255-dcb9-45b4-8b79-a0d1c8c51fae', true, NOW(), NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled, created_at, updated_at) VALUES ('07f43143-4f47-4f31-869c-bcdae8ecd865', '醛化反应单元', 'UNIT', 'ff26e255-dcb9-45b4-8b79-a0d1c8c51fae', true, NOW(), NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled, created_at, updated_at) VALUES ('ad6a0993-0e83-4645-87f8-edecd2c85356', '急冷分离单元', 'UNIT', 'ff26e255-dcb9-45b4-8b79-a0d1c8c51fae', true, NOW(), NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO plant_node (id, name, type, parent_id, is_kpi_enabled, created_at, updated_at) VALUES ('3353a2b2-2d4f-4907-9964-fb2aac837352', '脱甲烷精馏单元', 'UNIT', 'ff26e255-dcb9-45b4-8b79-a0d1c8c51fae', true, NOW(), NOW()) ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 4. 性能指标配置 (metric_config)
-- =============================================================================
-- 8 项核心 KPI（对齐 GB/T 44693.2-2024 附录 B/F）：
--   好值率/自控率/有效自控率/平稳率/准确率/快速率/振荡率/饱和率
-- 国标 4 分项指标加法关系：P = (λA·A + λF·F + λS·S + λR·R) / (λA+λF+λS+λR)
--   参与评分的 4 指标：准确率(A)/快速率(F)/平稳率(S)/有效自控率(R)
--   仅显示不参与评分：好值率/自控率/振荡率/饱和率（weight=0）
-- 参与评分的 4 指标权重总和 = 30 + 20 + 30 + 20 = 100%（稳定型控制回路默认权重）
-- threshold 使用 JSONB 结构 {min, max, alert}
-- control_type 对齐算法设计说明 §4.7.3 默认权重模板
-- =============================================================================

INSERT INTO metric_config (id, metric_code, metric_name, formula, weight, threshold, control_type, is_enabled, updated_by, updated_at, version) VALUES
('00000000-0000-0000-0000-000000000401', 'GOOD_VALUE_RATE',    '好值率',     'count(pv_quality=Good) / count(*) * 100',                            0.00,  '{"min": 80, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000402', 'AUTO_MODE_RATE',     '自控率',     'count(mode IN (Auto,Cascade,Remote)) / count(*) * 100',             0.00,  '{"min": 90, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000403', 'STEADY_RATE',        '平稳率',     'max(0, (1 - osc_rate - k*std_norm) / (1 - osc_rate)) * 100',        30.00, '{"min": 85, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000404', 'ACCURACY_RATE',      '准确率',     'max(0, (1 - mean_abs_error / e_max)) * 100',                        30.00, '{"min": 80, "max": 100, "alert": "warning"}'::jsonb,  'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000405', 'OSCILLATION_RATE',   '振荡率',     'min(S_A, S_B) * 100',                                                0.00,  '{"min": 0, "max": 5, "alert": "warning"}'::jsonb,     'SLOW',   TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000406', 'SATURATION_RATE',    '饱和率',     'saturated_duration / total_duration * 100',                          0.00,  '{"min": 0, "max": 5, "alert": "warning"}'::jsonb,     'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000407', 'FAST_RATE',          '快速率',     'min(ideal_settling / actual_settling, 1.0) * 100',                   20.00, '{"min": 80, "max": 100, "alert": "warning"}'::jsonb,  'FAST',   TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000408', 'EFFECTIVE_AUTO_RATE','有效自控率', 'count(auto AND op NOT saturated AND pv_quality=Good) / count(*) * 100', 20.00, '{"min": 90, "max": 100, "alert": "warning"}'::jsonb, 'STABLE', TRUE, 'admin', NOW(), 1),
-- r2b3c4d5e6f7 补齐 3 个缺失指标（对齐 DDS v4.1 列名）
('00000000-0000-0000-0000-000000000409', 'STICTION_INDEX',     '粘滞指数',   'cross_correlation_based_stiction_detection',                          0.00, '{"min": 0, "max": 0.5, "alert": "warning"}'::jsonb,   'STABLE', TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-00000000040a', 'SETTLING_TIME',      '稳态时间',   'arma_green_function_settling_time',                                   0.00, '{"min": 0, "max": 60, "alert": "warning"}'::jsonb,    'FAST',   TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-00000000040b', 'OUTPUT_TRIP_INDEX',  '输出行程指数','std(op_diff) / range',                                               0.00, '{"min": 0, "max": 0.5, "alert": "warning"}'::jsonb,   'STABLE', TRUE, 'admin', NOW(), 1)
ON CONFLICT (id) DO UPDATE SET
    metric_code   = EXCLUDED.metric_code,
    metric_name   = EXCLUDED.metric_name,
    formula       = EXCLUDED.formula,
    weight        = EXCLUDED.weight,
    threshold     = EXCLUDED.threshold,
    control_type  = EXCLUDED.control_type,
    is_enabled    = EXCLUDED.is_enabled,
    updated_by    = EXCLUDED.updated_by,
    updated_at    = EXCLUDED.updated_at,
    version       = EXCLUDED.version;

-- =============================================================================
-- 5. 诊断指标配置 (diagnosis_config)
-- =============================================================================
-- 8 类诊断标签（对齐算法设计说明 §5.0 诊断标签体系）：
--   OSCILLATION/VALVE_STICTION/OVERAGGRESSIVE/OVERCONSERVATIVE/
--   EXTERNAL_DISTURBANCE/QUALITY_ABNORMAL/OUTPUT_SATURATION/MANUAL_REVIEW
-- calc_method 对齐算法设计说明 §5.1~§5.6 算法选型
-- threshold 使用 JSONB 结构，键名对齐 diagnosis_engine._get_threshold 实际读取键：
--   OSCILLATION:       {similarity_threshold, min_zero_crossings}
--   QUALITY_ABNORMAL:  {q001_consecutive_bad, q002_bad_rate, q003_uncertain_rate,
--                       q004_bad_duration, q005_min_bad, q005_max_bad}
--   OUTPUT_SATURATION: {op_high_limit, op_low_limit, saturation_epsilon}
--   其余标签算法暂未从配置读取阈值（代码内默认值），threshold 置 NULL
-- =============================================================================

INSERT INTO diagnosis_config (id, diag_code, diag_name, algorithm_type, calc_method, params, threshold, is_enabled, updated_by, updated_at, version) VALUES
('00000000-0000-0000-0000-000000000501', 'OSCILLATION',         '振荡检测',           'IAE_FFT',         'IAE_ZERO_CROSSING',   '{"window_size": 1024, "overlap": 0.5, "snr_threshold": 5.0, "similarity_threshold": 0.4}'::json, '{"similarity_threshold": 0.4, "min_zero_crossings": 3}'::jsonb,  TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000502', 'VALVE_STICTION',      '阀门粘滞检测',       'SCATTER_FIT',     'CHOUDHURY_NGI_NLI',    '{"ngi_threshold": 0.001, "nli_threshold": 0.01, "stiction_threshold": 0.5, "r2_threshold": 0.7}'::json, NULL, TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000503', 'OVERAGGRESSIVE',      '参数过激检测',       'STEP_RESPONSE',   'EXPERT_RULE',          '{"overshoot_threshold": 25, "decay_ratio_threshold": 0.4, "harris_threshold": 0.4}'::json, NULL,    TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000504', 'OVERCONSERVATIVE',    '参数过保守检测',     'SETTLING_TIME',   'EXPERT_RULE',          '{"settling_ratio": 5.0, "iae_ratio": 2.0, "op_activity_min": 0.01}'::json, NULL,   TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000505', 'EXTERNAL_DISTURBANCE','外扰频繁检测',       'FREQ_ANALYSIS',   'KANO_STATISTICAL',     '{"window_size": 3600, "disturbance_threshold": 3, "freq_threshold": 5}'::json, NULL,     TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000506', 'QUALITY_ABNORMAL',    'PV 质量异常检测',    'QUALITY_CODE',    'EXPERT_RULE',          '{"bad_rate_threshold": 30, "uncertain_rate_threshold": 20, "freeze_duration": 300}'::json, '{"q001_consecutive_bad": 10, "q002_bad_rate": 0.1, "q003_uncertain_rate": 0.2, "q004_bad_duration": 5, "q005_min_bad": 3, "q005_max_bad": 10}'::jsonb, TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000507', 'OUTPUT_SATURATION',   '输出饱和检测',       'OP_LIMIT_STAT',   'EXPERT_RULE',          '{"op_low": 0, "op_high": 100, "epsilon": 2}'::json, '{"op_high_limit": 100.0, "op_low_limit": 0.0, "saturation_epsilon": 2.0}'::jsonb,      TRUE, 'admin', NOW(), 1),
('00000000-0000-0000-0000-000000000508', 'MANUAL_REVIEW',       '人工复核',           'MANUAL',          'EXPERT_RULE',          '{"confidence_min": 0, "confidence_max": 50}'::json, NULL,                                                  TRUE, 'admin', NOW(), 1)
ON CONFLICT (id) DO UPDATE SET
    diag_code       = EXCLUDED.diag_code,
    diag_name       = EXCLUDED.diag_name,
    algorithm_type  = EXCLUDED.algorithm_type,
    calc_method     = EXCLUDED.calc_method,
    params          = EXCLUDED.params,
    threshold       = EXCLUDED.threshold,
    is_enabled      = EXCLUDED.is_enabled,
    updated_by      = EXCLUDED.updated_by,
    updated_at      = EXCLUDED.updated_at,
    version         = EXCLUDED.version;

-- =============================================================================
-- 6. 引擎规则配置 (engine_rule)
-- =============================================================================
-- 3 项引擎参数配置
-- =============================================================================

INSERT INTO engine_rule (id, rule_code, rule_name, rule_type, params, is_enabled, updated_by, updated_at) VALUES
('00000000-0000-0000-0000-000000000601', 'EVAL_CALC_CYCLE',     '评估计算周期', 'CALC_CYCLE', '{"cycle_minutes": 60}'::json,                           TRUE, 'admin', NOW()),
('00000000-0000-0000-0000-000000000602', 'DATA_FETCH_WINDOW',   '数据拉取窗口', 'DATA_FETCH', '{"window_days": 30, "sample_interval_seconds": 1}'::json, TRUE, 'admin', NOW()),
('00000000-0000-0000-0000-000000000603', 'SCHEDULE_CONCURRENCY','调度并发数',   'SCHEDULE',   '{"concurrency": 16}'::json,                             TRUE, 'admin', NOW())
ON CONFLICT (id) DO UPDATE SET
    rule_code  = EXCLUDED.rule_code,
    rule_name  = EXCLUDED.rule_name,
    rule_type  = EXCLUDED.rule_type,
    params     = EXCLUDED.params,
    is_enabled = EXCLUDED.is_enabled,
    updated_by = EXCLUDED.updated_by,
    updated_at = EXCLUDED.updated_at;

-- =============================================================================
-- 7. 回路台账 (loop_ledger) — 27 条
-- =============================================================================
-- 数据来源：开发环境生产配置（2026-08-01 导出）
-- 清理规则：last_aas_sync_at 置 NULL（初始状态，待首次 AAS 同步）
-- 依赖关系：unit_id 引用 plant_node；modeattr_tag_id 引用 tag_registry
-- =============================================================================

INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('dcd77662-ddf5-4643-befe-18b4a58b0622', '41FIC20021_PIDA', 'T-101进料流量', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'FLOW', 'STABLE', NOW(), NOW(), 'import_script', NULL, '', 2, true, NULL, NULL, 10, 90) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('1aec206c-d1af-4881-8f43-935ae95c2279', '41FIC20051_PIDA', 'V2008锁斗吹扫气流量', 'ad6a0993-0e83-4645-87f8-edecd2c85356', 1.00, true, NULL, 'READY', 'FLOW', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('1194cef5-e7ce-40dd-9152-ed22f5f5c629', '41FIC20074_PIDA', 'E2004A催化剂冷却风1', 'ad6a0993-0e83-4645-87f8-edecd2c85356', 1.00, true, NULL, 'READY', 'FLOW', 'FAST', NOW(), NOW(), 'import_script', NULL, '', 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', '41FIC20132_PIDA', 'E2004B催化剂冷却风2', 'ad6a0993-0e83-4645-87f8-edecd2c85356', 1.00, true, NULL, 'READY', 'FLOW', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('640a0ce1-64da-4fbb-8f7f-7305542754a9', '41FIC40504_PIDA', 'T-101回流流量', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'FLOW', 'FAST', NOW(), NOW(), 'import_script', NULL, '', 1, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('82027a76-42c7-4cf0-852a-cf2402accfb0', '41FIC40519_PIDA', 'P4032A/B废水输送流量', '3353a2b2-2d4f-4907-9964-fb2aac837352', 1.00, true, NULL, 'READY', 'FLOW', 'FAST', NOW(), NOW(), 'import_script', NULL, '', 2, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', '41LIC20117_PIDA', 'V2010闪蒸罐液位', 'ad6a0993-0e83-4645-87f8-edecd2c85356', 1.00, true, NULL, 'READY', 'LEVEL', 'STABLE', NOW(), NOW(), 'import_script', NULL, '', 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('c4073df9-5983-45a5-aaf3-171dcbe26361', '41LIC40108_PIDA', 'T-101塔底液位', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'LEVEL', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('f69922dd-85df-45a8-be32-0852ee90c23b', '41LIC40201_PIDA', 'E4013预切割塔冷却器液位', '3353a2b2-2d4f-4907-9964-fb2aac837352', 1.00, true, NULL, 'READY', 'LEVEL', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('57715824-a786-47f9-91aa-984c84a151cd', '41LIC40309_PIDA', 'V4021 E4028凝液罐液位', '3353a2b2-2d4f-4907-9964-fb2aac837352', 1.00, true, NULL, 'READY', 'LEVEL', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('436dea56-63c3-44a0-b073-5f3dbf52d165', '41LIC40404_PIDA', 'T-101回流罐液位', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'LEVEL', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('147aaba4-2684-4cc6-b284-2c5f94161fb1', '41PIC20124_PIDA', 'C2002压缩机出口压力', 'ad6a0993-0e83-4645-87f8-edecd2c85356', 1.00, true, NULL, 'READY', 'PRESSURE', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('267a4306-e63b-4296-9643-5a6b4ed4547a', '41PIC20137_PIDA', 'V2018凝液罐出口压力', 'ad6a0993-0e83-4645-87f8-edecd2c85356', 1.00, true, NULL, 'READY', 'PRESSURE', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('bc753093-1e36-4c1c-8a70-8db4529c758c', '41PIC40306_PIDA', 'E4023B乙炔加氢换热器压力', '3353a2b2-2d4f-4907-9964-fb2aac837352', 1.00, true, NULL, 'READY', 'PRESSURE', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('d182c4a1-f5d2-4e73-925f-4ec3c44fe372', '41PIC40320_PIDA', 'T-101塔顶压力', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'PRESSURE', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('4197274e-3743-4c67-808e-c563e8db3a31', '41PIC40506_PIDA', 'V4061 C4+缓冲罐压力', '3353a2b2-2d4f-4907-9964-fb2aac837352', 1.00, true, NULL, 'READY', 'PRESSURE', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('859b45a4-a24e-4dc4-b060-c2835832a2b4', '41TIC20006_PIDA', '界外换热器出口温度', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'TEMPERATURE', 'STABLE', NOW(), NOW(), 'import_script', NULL, '', 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('474c1e51-35d2-4ff0-af95-7f4168847326', '41TIC40201_PIDA', 'E4013冷却器出口温度', '3353a2b2-2d4f-4907-9964-fb2aac837352', 1.00, true, NULL, 'READY', 'TEMPERATURE', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('4aa99c40-201b-48f4-9116-327821248b39', '80FIC11906_PIDA', 'V123→V122 BAL循环气流量', '07f43143-4f47-4f31-869c-bcdae8ecd865', 1.00, true, NULL, 'READY', 'FLOW', 'FAST', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', '80FIC31402_PIDA', '装置边界外循环流量', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'FLOW', 'FAST', NOW(), NOW(), 'import_script', NULL, '', 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', '80LIC10603_PIDA', 'R102羰基合成反应器液位', '07f43143-4f47-4f31-869c-bcdae8ecd865', 1.00, true, NULL, 'READY', 'LEVEL', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('e941bbce-cdf1-4db5-90fb-464cec88918e', '80LIC10801_PIDA', 'V107低压蒸发罐液位', '07f43143-4f47-4f31-869c-bcdae8ecd865', 1.00, true, NULL, 'READY', 'LEVEL', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', '80TIC10303_PIDA', 'T-101塔顶温度', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'TEMPERATURE', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('7d2834a4-e02f-41e1-ad48-a757201cb174', '80TIC10402_PIDA', 'E105→V104合成气温度', '07f43143-4f47-4f31-869c-bcdae8ecd865', 1.00, true, NULL, 'READY', 'TEMPERATURE', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('5d218e6c-832b-4cb4-97df-d922cae5c520', '80TIC10506_PIDA', 'T-101塔底温度', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'TEMPERATURE', 'STABLE', NOW(), NOW(), 'import_script', NULL, NULL, 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', '80TIC40108_PIDA', 'T-101塔顶轻组分分析', 'e8f9a0b1-2c3d-4e5f-8a9b-0c1d2e3f4a5b', 1.00, true, NULL, 'READY', 'ANALYSIS', 'STABLE', NOW(), NOW(), 'import_script', NULL, '', 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_ledger (id, tag_name, description, unit_id, score_weight, is_active, last_aas_sync_at, status, loop_type, control_type, created_at, updated_at, created_by, score_weights, remark, importance_level, include_in_evaluation, modeattr_tag_id, data_retention_days, op_output_lower_limit, op_output_upper_limit) VALUES ('0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', '90PIC51212A_PIDA', 'TK521A辛醇罐顶部压力', '07f43143-4f47-4f31-869c-bcdae8ecd865', 1.00, true, NULL, 'READY', 'PRESSURE', 'FAST', NOW(), NOW(), 'import_script', NULL, '', 3, true, NULL, NULL, NULL, NULL) ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 8. 回路-Tag 关联 (loop_tag_mapping) — 189 条
-- =============================================================================
-- 数据来源：开发环境生产配置（2026-08-01 导出）
-- 依赖关系：loop_id 引用 loop_ledger；tag_id 引用 tag_registry
-- is_required: PV/SP/OP/MODE = TRUE，PID_P/PID_I/PID_D = FALSE
-- =============================================================================

INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6b15da14-6abe-4913-96fb-d0c7cff761af', '0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', '36514c9d-1bce-4ea5-a211-488296680d3c', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('2ce9c98c-2fe1-4737-95e7-8529b858dd50', '0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', '55dd8518-5eb0-4916-bfd9-9277d304652b', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('dd93dd85-fb27-47c0-8949-b1a6c1127f0b', '0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', 'c4ca0b91-5d4b-45e9-bba2-7567d9bbf659', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('4121a230-0f07-4708-8c98-7694f02dd17c', '0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', 'cc3717c9-b0ba-4518-bcba-7cc72204c2b9', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('02ba5904-607a-4dcd-92cd-2202896debb2', '0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', '2345ec41-7c6f-4649-9686-7ed86654f080', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d0bd8075-29cc-412b-b59d-2402de897709', '0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', '099704d3-8318-4820-ae11-be9b64b4cf4a', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f78c637e-baf0-4466-8c41-d685d675ceee', '0b68bb0b-af84-4e52-96ed-96f5e7d3eda8', 'aa2f4027-b066-4368-b5ff-58ff6799bfa1', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f79a3420-5a4e-4d09-a926-62b547f49c54', '1194cef5-e7ce-40dd-9152-ed22f5f5c629', '18ff52cd-2cce-44a7-9352-88a637d0dd37', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('5cc26951-55dc-4df4-a325-390a7170d058', '1194cef5-e7ce-40dd-9152-ed22f5f5c629', 'eab1a850-1fd4-49f7-9f38-2118eb4cd235', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a239e785-2d24-40d4-8460-4203fed0a0ef', '1194cef5-e7ce-40dd-9152-ed22f5f5c629', 'cd23456a-17c2-48e4-b265-e64be8c5d2dd', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('30f519c7-3f20-45a2-b701-9fcc82311888', '1194cef5-e7ce-40dd-9152-ed22f5f5c629', '90fb22b3-cc7d-46d7-bf70-60019650bb62', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('741fa11f-97d1-4340-8374-f5a16123d949', '1194cef5-e7ce-40dd-9152-ed22f5f5c629', '02d12d59-6850-4e38-82d5-87d98aafb4ba', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('e8f6dd18-eb7d-41b3-b1f6-962e2f80ff00', '1194cef5-e7ce-40dd-9152-ed22f5f5c629', '69eddb60-4736-41b9-b165-56ddbc130b0e', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6614afe2-1acf-4baf-a607-a9e1431519f6', '1194cef5-e7ce-40dd-9152-ed22f5f5c629', '99738bef-2547-4f86-8330-0e155f45ed83', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('7d846bad-0cac-497e-952b-0fd25a8d7d35', '147aaba4-2684-4cc6-b284-2c5f94161fb1', '8bdd746c-91a9-4ca9-8b20-c6f212590e3a', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a33c019b-b045-4fce-8847-a316a56491aa', '147aaba4-2684-4cc6-b284-2c5f94161fb1', 'ba725d7b-148d-4bc5-a73a-64480d3f3e16', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('3f6cf29a-a1a5-4706-90de-73be591b11c2', '147aaba4-2684-4cc6-b284-2c5f94161fb1', 'dbb21816-25c5-4076-ae6c-a596c6cba795', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a60291d5-8f22-4c0b-bcea-a19f9ef5db67', '147aaba4-2684-4cc6-b284-2c5f94161fb1', '41af5d26-2b28-4e38-a0ad-f707d2ff5766', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('e1cb4436-dd2d-4042-b7c2-e8bf7d2c7b1a', '147aaba4-2684-4cc6-b284-2c5f94161fb1', '6f2e0357-14c9-4470-b667-68767e33d874', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('49c83be6-2960-4c9d-b133-ef597db0e459', '147aaba4-2684-4cc6-b284-2c5f94161fb1', 'bc09ccab-fca8-4ea2-ad93-acc9bfeced46', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c6791384-3dee-4639-8331-1941eda9f37d', '147aaba4-2684-4cc6-b284-2c5f94161fb1', '72c8657d-113b-4eb1-8547-a91d69e8c281', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('134ea1f3-2cf6-492e-85a6-1be406213cd6', '1aec206c-d1af-4881-8f43-935ae95c2279', 'c458bf2e-17c2-41b7-86b2-6bd528156869', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('8eb48fd0-c05c-4d4e-89b8-ffabc63b9343', '1aec206c-d1af-4881-8f43-935ae95c2279', 'f51b320a-3464-47e0-9c09-744786fdcab9', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('7247c1e9-b2ed-4e26-a04a-ea91554b5f34', '1aec206c-d1af-4881-8f43-935ae95c2279', 'b4b0ad4d-5c41-460d-ac45-69bbf871d294', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('744fc581-d8db-4b2a-af64-49686de7ea49', '1aec206c-d1af-4881-8f43-935ae95c2279', '9feff7be-fa8b-475e-bd8e-7a14c2ba2c1e', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('2ae0523e-e06a-472a-8f5e-bdffd53f9595', '1aec206c-d1af-4881-8f43-935ae95c2279', 'd582ebd3-faf6-4161-a71b-aa774fc7819f', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('eb7d8a93-0771-4c02-8f00-44c682014c71', '1aec206c-d1af-4881-8f43-935ae95c2279', '684b2713-3460-460a-be55-c6a24aab0ccc', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('0d322f3d-cc95-4e17-bd39-55698406dac0', '1aec206c-d1af-4881-8f43-935ae95c2279', 'd8ca2277-036d-421f-be78-65288fba8be8', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('08321fd8-2cc9-42da-b8db-544dd1c2f8a0', '267a4306-e63b-4296-9643-5a6b4ed4547a', '93832819-799d-4372-90a0-b5791da7c10a', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('1c697aa9-359c-4a37-b12a-437d3fc2d95a', '267a4306-e63b-4296-9643-5a6b4ed4547a', '76634060-3089-46d0-beaf-32f74ae5b823', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c1ffa1cb-f044-4e02-8a86-79f199c52f77', '267a4306-e63b-4296-9643-5a6b4ed4547a', '061f50d5-aa42-4483-bac5-e820ae5e39a2', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('4efb9b04-60a8-41e2-aa7f-7a8e80999af5', '267a4306-e63b-4296-9643-5a6b4ed4547a', 'c56d3349-2790-48b7-9dce-f9b53ac54e09', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('1ff83636-7acd-4acd-a3d9-1ff7ad8f3f34', '267a4306-e63b-4296-9643-5a6b4ed4547a', '45d87cd5-db9c-475e-b8b1-275ea7f04d19', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('7cdc874c-6549-49fd-bc66-d04790922026', '267a4306-e63b-4296-9643-5a6b4ed4547a', 'c6e80e1a-fb1b-452f-bc50-15d88292ccb5', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('723aff8c-0f7c-4a2e-b91a-104a6a214d7b', '267a4306-e63b-4296-9643-5a6b4ed4547a', 'f8de89ae-9b5f-41d5-a0d7-a1bcb1a0cfb2', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6758aebb-5479-40a4-a7a5-557a7df3371d', '3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', 'e7770f7a-a522-4f46-95aa-15d68f09471f', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('345e83cc-e2b9-4320-8abe-fa7273195ccc', '3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', '9c2523b1-02b1-44fc-862a-b2834f8a909c', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('ff0d6eca-0ceb-4377-a214-6c3c842e91ec', '3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', '4e9943cc-87d8-45c0-a285-48d0b7c3aafb', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('8d5d7c08-51ec-47d1-a4a9-ec928d52d9fb', '3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', '03b052c3-0465-41fb-a6d9-7eeeaf2465b1', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f7750bfb-6848-4171-a0d5-c37b2fed5561', '3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', '60f33ccd-0530-457c-be60-9f67ea3e103f', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('302e2721-c2d6-4eab-aeba-bf28f4bb0a7d', '3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', '31299816-1c8d-47a2-b950-e7b7ecae2e7b', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f3fffbbf-4881-4bc4-a16f-99f4585f46ad', '3faa375f-39b1-4214-80c1-7dc2cdb7b2b7', 'fad88f66-3a92-4ed5-88b2-1624161363c2', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c33704c9-61f2-4a70-8260-0b0cd39735f1', '4197274e-3743-4c67-808e-c563e8db3a31', 'f60fd5f8-8a17-4f97-9fa1-de89bcc3115e', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('4b11a218-31e2-4048-893e-adfa09484a3e', '4197274e-3743-4c67-808e-c563e8db3a31', 'e8efef6c-887a-4169-a82a-4e769e5d5cf2', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('3d65ecab-29b6-4b2f-8ade-57858e676e09', '4197274e-3743-4c67-808e-c563e8db3a31', 'a1a79505-620b-4ce6-badb-6eca794c6229', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('15383622-fa68-4d57-973a-048c81b37512', '4197274e-3743-4c67-808e-c563e8db3a31', 'bc2b2ad9-063b-4fe9-9fca-ff03ed98d0ea', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c66a8249-c667-43b9-8c78-61541ec6ba3b', '4197274e-3743-4c67-808e-c563e8db3a31', '706eaac0-3084-4d31-a17b-e4e8c948df06', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('67dbb8ac-4214-4734-b070-d35334e3368e', '4197274e-3743-4c67-808e-c563e8db3a31', 'bb10480d-5155-4b27-9a78-ff112c1454ce', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('109eea6c-a7bb-4fb7-8573-9052277cbcf0', '4197274e-3743-4c67-808e-c563e8db3a31', 'a80331f3-1638-4841-bfa8-ec7251dc9663', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('99999423-19bb-450e-bc56-c6b4074087df', '4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', 'b967a98c-5f24-46a0-981e-182ca91aa1dd', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6d437f40-57b1-4e34-9ce5-6a2b47a12d91', '4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', '22057539-c7aa-40d6-82d7-4cb8f9629cfc', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('9039c389-3d3c-492d-9820-595b472ba669', '4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', '4f7097e4-b625-4bb0-ae9b-1fd7de9130df', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c3572499-f2a9-4b0d-94b0-e83e4b8887bb', '4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', 'c8e85c1b-9626-49ed-ad25-b39f984a7edb', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('8fc2595e-b4aa-4689-85f7-7bbd362a6cb6', '4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', '1e8240e3-bb27-48d2-873a-cf9427f91858', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('134ef664-81f3-4751-836f-ab1913f48dbb', '4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', '0fde44a1-a439-4e56-aef0-83af3f736a87', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f9bd3372-ae94-4c91-99f5-ae521a52425f', '4301b744-4b15-4bd2-b2a1-45f7cbbb5b8a', 'a6792ebf-e1a7-4a96-be46-02ab1ff9f68a', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('e1047c15-9b45-462e-9965-535a277670fe', '436dea56-63c3-44a0-b073-5f3dbf52d165', '68c444ff-ee45-4467-97da-9640b3d614ca', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('ba9cc365-2414-4414-83cd-0df0114400c4', '436dea56-63c3-44a0-b073-5f3dbf52d165', 'cc7501f0-daaa-4ba2-a3c7-b10fb88dfccb', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c3df26b1-fc8d-4e18-9181-149581ba6d7d', '436dea56-63c3-44a0-b073-5f3dbf52d165', '8e55e52b-ec88-461c-8487-31e6cc31df2d', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6693a888-a42f-4b11-a96d-786a53b81d9f', '436dea56-63c3-44a0-b073-5f3dbf52d165', '5b14d674-d822-4ad5-b129-438bf4fcd831', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('616d6cd1-9676-4472-9d54-c6e0c200cb4c', '436dea56-63c3-44a0-b073-5f3dbf52d165', '27668240-7418-4991-a0ea-63374d062945', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('319be314-4074-42bd-aa24-846cb6845d2c', '436dea56-63c3-44a0-b073-5f3dbf52d165', '49547e16-773e-406a-9baf-aa75484ab047', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('14b46248-4662-4e61-bced-c88b0b04ab6e', '436dea56-63c3-44a0-b073-5f3dbf52d165', '74e10766-3faa-4bcb-8d22-6c2456161b94', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('521b9b85-08d7-4294-868e-f194b22dea78', '474c1e51-35d2-4ff0-af95-7f4168847326', 'b732d817-a093-4557-802a-3ae130da27e4', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('588e7931-c648-4dfb-9c5e-f05309f3ca90', '474c1e51-35d2-4ff0-af95-7f4168847326', '1ccea1ef-cb37-4ab1-8ba9-6c008484a78e', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('9c998bd9-b2da-46e5-ae01-e7fbcc8daa00', '474c1e51-35d2-4ff0-af95-7f4168847326', 'bf6a76d6-57c0-49c2-99a0-1d646cd68b5a', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('95243209-e8b4-4b04-980c-79ae357ed8f3', '474c1e51-35d2-4ff0-af95-7f4168847326', '029f40b8-424b-4cff-86a4-90a49839feb9', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f4b49d38-bb50-4744-92eb-4e520d69ff50', '474c1e51-35d2-4ff0-af95-7f4168847326', '1f2b7590-cf24-4f1f-9999-759f9f0e0fef', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('20d78d10-1034-4913-8f42-0144917547aa', '474c1e51-35d2-4ff0-af95-7f4168847326', 'bdcb00fe-b42c-41fe-814e-86613e25112f', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('17749aee-0040-4401-b31f-d184e46686c1', '474c1e51-35d2-4ff0-af95-7f4168847326', '3f3684c3-a55b-4af9-bccc-0bb9a0e9ae2d', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('ebe32ff1-259e-4d7a-9f35-258c4ab5dcea', '4aa99c40-201b-48f4-9116-327821248b39', '04b736d8-11f4-4a57-9d41-10ef3c43a2e7', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d6f6269c-6969-482a-884a-273d71c84136', '4aa99c40-201b-48f4-9116-327821248b39', '6ebe52d9-f7e8-4f15-bd4f-4faffa6e74ff', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d05aaf4e-3a91-470c-aaf8-ebb8099fc186', '4aa99c40-201b-48f4-9116-327821248b39', '6ddea1ad-4c0c-4ea3-9c8c-67276247b7f6', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('0a37a0fa-30f5-40ad-aea8-a0455dc399b2', '4aa99c40-201b-48f4-9116-327821248b39', 'dfe6eae3-a051-4171-b152-4d96428b51d5', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('9511b401-9004-473e-9066-371dfb327864', '4aa99c40-201b-48f4-9116-327821248b39', '3951c9c8-4ea7-4ccb-a9ab-576aa10bde01', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d6474719-3136-4793-98c1-24db8e3fb80a', '4aa99c40-201b-48f4-9116-327821248b39', '780ed6e2-6f12-442c-945e-b5e44dc06a59', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('8adb1b77-e094-4cb3-a7a6-9e99dcc90191', '4aa99c40-201b-48f4-9116-327821248b39', 'e046ce7d-1161-4b06-a545-83c3f6e5bf51', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('562a366d-f6b7-4e12-8f4a-f600d8a58dc6', '57715824-a786-47f9-91aa-984c84a151cd', '9b5bb38b-ed3a-4ac5-9fdb-02a4d3d005b8', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('7125952d-2bb0-435e-8071-07034039e907', '57715824-a786-47f9-91aa-984c84a151cd', '42c5de42-efd2-4220-a19d-a1ed947fcfc7', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('03ce7d9e-9286-4759-9940-6f6b5be54312', '57715824-a786-47f9-91aa-984c84a151cd', 'a7dbbd63-1510-4fdb-b1b7-c0bba2ae379d', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('844b47f3-3fb7-4562-a857-a96447b244a3', '57715824-a786-47f9-91aa-984c84a151cd', '041b83a3-f4b8-4c90-ba8e-ea8757ad54e4', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('5b31e582-398f-431b-926c-e9cc3875bdb9', '57715824-a786-47f9-91aa-984c84a151cd', 'a8952106-8c04-4484-94bd-3f2ea910431c', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c26a2298-68d6-48de-be67-214b1a3959ef', '57715824-a786-47f9-91aa-984c84a151cd', '05159296-d868-4fb0-87b1-f66dd5dd5aa2', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('21394603-e2e4-4ab1-a486-60fe1f7648ee', '57715824-a786-47f9-91aa-984c84a151cd', '87259ccc-1aa6-4794-9733-b9ac054120b3', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('52d85e06-a1a6-4899-bba6-6820392b2664', '5d218e6c-832b-4cb4-97df-d922cae5c520', '17d7f5cb-6265-4d5f-9e28-d6b207696d1a', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('39adb395-46ee-41dd-80ec-7fb727e47809', '5d218e6c-832b-4cb4-97df-d922cae5c520', 'be2b394c-ea9e-4437-bdb8-c6e883fe7b4e', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d07df4f2-bbcf-402b-9758-30151bf3454f', '5d218e6c-832b-4cb4-97df-d922cae5c520', '52f065b4-eb4f-45b3-9d41-e52a2be8692c', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('13e60a57-5d10-46e1-b481-a12f16f44b4e', '5d218e6c-832b-4cb4-97df-d922cae5c520', '8e3e4bfc-3868-4c49-9ac5-a13b0f1a0d88', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('43467778-f1f1-45a9-9461-ef261d972fa7', '5d218e6c-832b-4cb4-97df-d922cae5c520', '120b1a0f-b3ff-4cd7-bbab-d35579c87615', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a7eff465-b251-40a6-8fed-939c9e71f0a8', '5d218e6c-832b-4cb4-97df-d922cae5c520', 'd1bf7d65-92d1-414e-b29f-08bd491d2195', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('51b866a3-d03f-4e66-b477-184bc6e60680', '5d218e6c-832b-4cb4-97df-d922cae5c520', '5f7a94d2-906c-4fe4-8516-743c6799339a', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('2fdd2c4e-b67a-4beb-accf-f65396e453e2', '640a0ce1-64da-4fbb-8f7f-7305542754a9', '27ebf468-bb48-479a-b667-ec5817213d94', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('61968704-21c8-4b28-ae23-b2e29c46ee3b', '640a0ce1-64da-4fbb-8f7f-7305542754a9', '2694c4b1-6ad1-422d-a6f8-387e3aa8e9f8', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('1c8ff273-9c6f-4c5d-8ebb-3560d1f09358', '640a0ce1-64da-4fbb-8f7f-7305542754a9', 'fa7b4df2-7c00-4fc9-b6e5-0c484561c9a9', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('ba00d66c-9c31-459b-9648-177d7c0cda3d', '640a0ce1-64da-4fbb-8f7f-7305542754a9', 'b5161954-06a4-4772-ac64-4fe7279c8c35', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a84a926b-127f-4743-a945-a4937f057d7e', '640a0ce1-64da-4fbb-8f7f-7305542754a9', '1a4c9e1d-3224-4774-8bee-c88c6c6ab125', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('cc267053-6607-49a3-8745-5f7230a257bf', '640a0ce1-64da-4fbb-8f7f-7305542754a9', '9cc657c6-d6ca-4f41-90a1-21031cd9c905', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a8353747-65ce-475f-b14d-d763662b9889', '640a0ce1-64da-4fbb-8f7f-7305542754a9', 'b7c944db-3971-463e-bad0-4e530382e4cd', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('ea1bcadd-e6fb-4be3-9f10-cc1531324420', '7d2834a4-e02f-41e1-ad48-a757201cb174', 'a3e3e13e-0b55-4121-b1b6-c96a538d4494', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('8ba64f6a-479a-4d74-93ce-264e473b9db6', '7d2834a4-e02f-41e1-ad48-a757201cb174', 'f6bedd11-7f5d-47e1-b9b9-d03be3ac13e1', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c8bbd030-bdc4-44e1-b1bd-75e6d24cddd8', '7d2834a4-e02f-41e1-ad48-a757201cb174', '1c77c14e-58de-4a2d-a2c4-1588aa4d9920', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('0118062a-9277-4b67-9d52-c68d5df89056', '7d2834a4-e02f-41e1-ad48-a757201cb174', '20f23908-3f2a-4c8b-b943-210a3ea90c46', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('e0a1b83c-9486-4249-8bfc-bfb69f07e0cc', '7d2834a4-e02f-41e1-ad48-a757201cb174', '6907b506-2cc8-4c1b-9895-56e35cc0863d', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('3ac49b1f-5abd-486a-8501-e5e6aa275de0', '7d2834a4-e02f-41e1-ad48-a757201cb174', 'f4c0c393-26f2-4305-9136-f2fe7fa842f2', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('485f4311-0106-4324-9c89-6c14065ad802', '7d2834a4-e02f-41e1-ad48-a757201cb174', 'dfa39406-79e0-4217-bb03-b531f27b188a', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('05535157-2683-4a71-bd89-79e7bd672c66', '82027a76-42c7-4cf0-852a-cf2402accfb0', '9879d6f3-e60a-42c1-8a6d-8baebe25ef25', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('de06f3bf-62b2-4913-96bd-139fba2ecad2', '82027a76-42c7-4cf0-852a-cf2402accfb0', 'd04dc912-cfbf-4bb1-97d0-4dc0d7719c89', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c1984906-dc68-42eb-9bf7-91a15de99293', '82027a76-42c7-4cf0-852a-cf2402accfb0', 'b56563e6-228d-4d08-a248-dc2d4f1ae545', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('710b04eb-2187-4f3e-903e-85670af396db', '82027a76-42c7-4cf0-852a-cf2402accfb0', '19d2523f-b0a7-4e78-b38f-04170be80958', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('336a8531-8829-4d6c-ad0f-daf81413db5e', '82027a76-42c7-4cf0-852a-cf2402accfb0', '650c0457-e716-4376-9417-12f2d5f478f9', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('b2bccce9-2975-42a7-9afd-7b2d8c536250', '82027a76-42c7-4cf0-852a-cf2402accfb0', 'c885214b-efe4-4f9c-8e1f-9bac746f79ae', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6ab32c1c-7c6c-46ac-a666-fef2084ba358', '82027a76-42c7-4cf0-852a-cf2402accfb0', '199d5751-8471-43b4-9bf7-d5dec8a1f14b', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('504b9428-e327-41c5-9089-661e14393fae', '859b45a4-a24e-4dc4-b060-c2835832a2b4', '6fb92b1e-66c3-49e1-84dd-42abde40e3f1', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6b3b31c4-d1f2-4e5b-a607-e571a8fcb047', '859b45a4-a24e-4dc4-b060-c2835832a2b4', '2994b155-0550-40bf-ab8f-073db5fbc7a1', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('75f2c50f-011a-4f39-aa5d-25da790d9794', '859b45a4-a24e-4dc4-b060-c2835832a2b4', '7450f955-2c8f-4e85-bee2-70e02eaa4bb7', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('9d841cdc-99b1-4e0d-aa1d-808a6db2b9a5', '859b45a4-a24e-4dc4-b060-c2835832a2b4', '97651679-a59a-4387-b4e3-23ffc2c21375', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('29fc872c-b9ac-4ffa-8e2b-d8d21f5d90c1', '859b45a4-a24e-4dc4-b060-c2835832a2b4', 'ffab1f34-0f7d-4256-912a-14fdeab2581c', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c7018fc7-7c28-4052-ae3d-aa63e81ddd76', '859b45a4-a24e-4dc4-b060-c2835832a2b4', '0997dfa3-42d0-4890-a5ac-ce8ef6a0dd5a', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d67caae7-ae89-449b-a7f4-61ac96ede217', '859b45a4-a24e-4dc4-b060-c2835832a2b4', 'f3710810-ac8b-4635-8ded-eb543e777ee8', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d373a3e0-993c-49ce-817d-5df8831a5b72', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', '674974a7-d86d-4b31-9c8d-e445055b6133', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('4be5fee9-24ae-4144-80e8-fb271c7fac23', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', 'c547c6d3-b6f6-45c7-b696-5c98e2f0d4d3', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('75c8768b-ee24-4f57-bd4b-298ac94f1c79', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', '0e94ab2e-bfd8-4dcc-ac51-c1ae7c345ab4', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('db776412-3c93-4e01-b775-e185f8451328', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', 'a269e925-8f1f-4d65-923c-3acc4105a7f7', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a2a72622-9354-4d91-a029-3b546653b320', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', '4626032e-51c2-48c8-9e49-c0e18cd9546e', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c226014b-11c9-4052-80e4-93bcf559fe47', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', 'ec237411-4bad-41e4-93cb-828234562b9e', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('549b0b78-3e0c-494d-9e43-80be2390c414', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', '3b9ec3a7-af53-4939-9ac5-d1dfd1d40d18', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('5c19b395-c1bc-4032-97e0-0a128b8b7d8f', '9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', '9483c31f-cd76-42e5-a453-0ac77a66f99d', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('42046143-3042-40cf-bed1-9e7472b6484e', '9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', 'fb7990d9-572d-4b79-9485-06702d2cfcc9', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('052acc96-0753-4d0d-8f16-57d78db37ff9', '9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', 'b7b82788-13a9-4918-8fc6-2290361fd753', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('626b4646-2d01-4031-bffc-4b6f3732e10f', '9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', '107ee56e-38a8-40a2-bdcf-225317ac3e01', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f3e35344-39ef-4ce1-bb78-db5821948775', '9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', '044d4a86-8f1a-47e6-9944-aa8689fa80a2', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('656f0eb8-6fa9-40bb-9325-57afb09b242e', '9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', '8c850c22-f033-4abb-87ab-d2da867ff11d', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a247b91d-6a22-4de9-96f2-6a087ffdeddd', '9b9b82ad-0a89-4a0c-8725-5cd5aef485a3', '5b873911-9d0d-49ed-b55c-e9c002bfa236', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('3db85057-87c6-4607-b23d-ca66b050ff7b', 'bc753093-1e36-4c1c-8a70-8db4529c758c', '57d843de-913d-4567-a463-3546e045b26a', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('bf535878-0f50-44fa-af30-eeedeaf4e627', 'bc753093-1e36-4c1c-8a70-8db4529c758c', 'e882ca34-ad32-4fa8-a866-17809e45febb', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('46677a8e-4f20-459a-8106-a9be66e0898d', 'bc753093-1e36-4c1c-8a70-8db4529c758c', '970b85b4-5371-4000-b266-2644741bc388', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('7a9f9e57-1b9e-4168-afa2-52d7b078e283', 'bc753093-1e36-4c1c-8a70-8db4529c758c', '014f07c4-9744-4f81-85fe-0f9535cdcb1e', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f1fda2f8-4d48-4b47-9973-0df888fd32ab', 'bc753093-1e36-4c1c-8a70-8db4529c758c', 'deb81842-3f5b-4cb6-9475-536d098f6b45', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f0669572-34e5-4c8b-91da-bb66521255c3', 'bc753093-1e36-4c1c-8a70-8db4529c758c', '6d03c03e-5a23-4386-aefd-316de62754df', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('cd1c0df3-5917-4de9-bce2-112d0cfafef1', 'bc753093-1e36-4c1c-8a70-8db4529c758c', 'e9cbfe31-85c8-4e9a-a81d-b9a2525d5a78', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('468a1796-4114-4132-9dd3-ad6fdada8664', 'bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', 'ec140e13-b2f1-4e71-9f4b-b9248bbd57a1', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c020b78d-e543-491b-a560-a86b129b7c78', 'bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', 'c359487b-06e2-41b2-9084-660ce58382f8', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('69750a6a-5055-4c9d-9037-6776f5af256e', 'bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', '38c4e505-e0d7-4143-ae28-c8364d62d6c5', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('0e71259b-746e-4dab-97d9-673d6e415320', 'bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', '42e51e5f-553e-4962-9618-0ff560ab5ed8', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('2834d316-3235-41da-a88e-69b30600a251', 'bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', '24f676c1-b163-4568-b916-3899b47ff93e', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('580f42a9-333f-4904-8efc-86ea78ea2b79', 'bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', '54446457-d881-407a-93b2-42544c703cbb', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('178463bd-fb17-4da0-8052-5eff063296f1', 'bde5d6dc-a0bd-4d29-b99d-81a06e5b1a1d', 'ccf7ffe6-26e0-4a97-ad43-ced16037e9d3', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('fe5754e8-02c1-4f19-b027-166f0a7846ee', 'c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', 'ff6693bb-6eaa-41b7-9d82-9a4eefbb39c8', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('27bbde08-32ff-4563-baba-d5e4db412ed5', 'c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', '3391dc82-5ff1-4f7a-a793-1471d1e185ce', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('9fb29378-a75b-4cae-9c90-b640a92bdcd0', 'c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', '0ee35351-cef2-4ae6-a01b-f780a826a2a4', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('231270d1-d825-47ab-b9af-93e6c9e4724f', 'c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', '5554f290-5ac9-41e7-ab4c-5fa8e1eb58c1', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('f96f7dc3-a883-4e0d-8dcf-af3ef704799d', 'c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', '96121933-8630-47d5-9623-22d7cc3e72d9', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('b2877022-afeb-44e7-9f78-1d41fb3d6f19', 'c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', 'b4200702-65fd-4017-8a93-c994b319c1fb', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d4658a6b-8f45-495d-bec3-c22c45f31eb0', 'c3f3bc9b-a58c-4ca1-b9aa-3b2894a09975', '337bedd8-4f6f-4942-b297-77f30aa91a16', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('e0d459e5-39e1-4b55-a932-3a88374d246b', 'c4073df9-5983-45a5-aaf3-171dcbe26361', 'b1328d35-c5f3-4fca-a5dc-4f0fd34082b5', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('718dad16-7c3f-47d0-afae-19839ccdb2d9', 'c4073df9-5983-45a5-aaf3-171dcbe26361', '2f743047-6fd8-44f4-9c29-95b849b49b37', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('1e70b956-c1b7-4959-ad21-182defc0ddfa', 'c4073df9-5983-45a5-aaf3-171dcbe26361', '2e9f3bab-941f-4f97-840c-b266e5499e34', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('b7c6cf21-4d21-4851-a907-f2d33ca88fdb', 'c4073df9-5983-45a5-aaf3-171dcbe26361', 'c3acf680-ea99-4427-8507-3832f3038089', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('8dea8850-adaa-44ca-b862-4325ac8be889', 'c4073df9-5983-45a5-aaf3-171dcbe26361', 'bb3d4ee2-0494-4cf8-809e-24e87303a01d', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('0ec8b5b2-983d-42dd-afd8-4c153be5a9e0', 'c4073df9-5983-45a5-aaf3-171dcbe26361', '3173a1d2-372d-4bd6-a143-0cce88698c31', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('cbda5a77-fe11-4c69-84b1-1cb8160f05b0', 'c4073df9-5983-45a5-aaf3-171dcbe26361', 'a5809dd6-6f38-42be-93c9-2602cfea6f2e', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('cd05ccf8-0b6e-4c0c-b83a-5bdb2b37bfa7', 'd182c4a1-f5d2-4e73-925f-4ec3c44fe372', '411b8466-cd48-4f99-819a-c4169b34acec', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('13e85af1-d552-4cea-b898-bf686e852aae', 'd182c4a1-f5d2-4e73-925f-4ec3c44fe372', '00994deb-47fd-43ea-b5f7-bcc0db8896be', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('bed07c1b-cfef-43cf-ac89-1f61ada8ee47', 'd182c4a1-f5d2-4e73-925f-4ec3c44fe372', 'cbb070c2-2084-42a8-b23e-85cd321ae2b6', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6f95b869-29cc-4566-8316-cc55136edc5e', 'd182c4a1-f5d2-4e73-925f-4ec3c44fe372', '78415ebb-3ebd-4c1c-9a66-3cc6c9af42d5', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('3cbe9254-e8a2-4bab-bee6-aefa4e790272', 'd182c4a1-f5d2-4e73-925f-4ec3c44fe372', '65dcb2ee-4bd2-49d6-b9b7-e8c935cd19e4', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('36fc27ed-97d4-44ed-a6c2-234a8625cbef', 'd182c4a1-f5d2-4e73-925f-4ec3c44fe372', 'b47ecd3c-9b7f-4331-971e-b6f5b95f27ed', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('29ac4f43-1949-4bca-9e8d-824511862d42', 'd182c4a1-f5d2-4e73-925f-4ec3c44fe372', 'b35a5361-d5ce-4845-a57b-f8cc8032722d', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('64bd9375-3ef6-4651-af68-f4a3d8337ba6', 'dcd77662-ddf5-4643-befe-18b4a58b0622', '22f0ae00-4762-498e-84f6-4c71e1b7430b', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('35bba590-38b9-4242-8d16-09fd8580f646', 'dcd77662-ddf5-4643-befe-18b4a58b0622', '66fac5db-ef96-447d-9962-154e8ca7a53c', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c30189b3-3e8c-4253-888d-f8dfcc537b3d', 'dcd77662-ddf5-4643-befe-18b4a58b0622', '1008fb32-98d9-4538-9b25-5a5c02b3ecb6', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c7186818-0681-4a3d-b803-ab7ee18480cf', 'dcd77662-ddf5-4643-befe-18b4a58b0622', 'f75ba919-dbdc-4be2-8bee-dd8a1a2eb8a7', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('1c20ab7e-27f8-4f34-a475-2d680dfc0b21', 'dcd77662-ddf5-4643-befe-18b4a58b0622', '95d504bf-cd30-4dc0-ba34-aef7215350b8', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('6b6b1434-6c31-431d-abd7-ed5c5fcc7342', 'dcd77662-ddf5-4643-befe-18b4a58b0622', '28af6fa9-f0d4-4929-a833-052a7ab612cc', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('93f1de76-c257-4953-9bda-858dd2a7da44', 'dcd77662-ddf5-4643-befe-18b4a58b0622', '9249831d-3b6e-4576-8e49-c5e073e3f273', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('618c19cc-7fe3-4b59-a562-db5ae4ba9ef2', 'e941bbce-cdf1-4db5-90fb-464cec88918e', '9c322b14-da11-4a73-93c6-c51812571474', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('a6937392-7056-43cf-9f93-6a021ddb3ac1', 'e941bbce-cdf1-4db5-90fb-464cec88918e', '7a9b2f23-aec3-431c-818f-ef79c4b19970', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('dbf418bf-6ec8-41f3-a091-4ab449471616', 'e941bbce-cdf1-4db5-90fb-464cec88918e', 'd92da3bb-7d73-400f-bda5-e50d617faa4c', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('0f47041d-ec81-49f8-9523-b33630dd59e9', 'e941bbce-cdf1-4db5-90fb-464cec88918e', 'ade948b0-016e-4e2a-ba7a-fcf416d3dc8b', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('3d700d29-f6eb-4878-adc3-b79f817a2ce8', 'e941bbce-cdf1-4db5-90fb-464cec88918e', 'c2168c04-7d88-4d1b-a145-cc842f979319', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('02a96ebe-68f0-48b1-91dc-7fa824120544', 'e941bbce-cdf1-4db5-90fb-464cec88918e', '553d4827-221e-4d4d-a993-f14ce1790f7f', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('2801806f-7a59-4562-a1fb-7a5f8da2002a', 'e941bbce-cdf1-4db5-90fb-464cec88918e', '40cab1d0-2f44-4851-b8e9-98b44bebc4e5', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('d5010482-62b1-4c99-8d10-6faa5613f81a', 'f69922dd-85df-45a8-be32-0852ee90c23b', '9a915a8b-eabc-437e-844c-e2b9bbad8cd8', 'MODE', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('c9b2732f-3eb3-484e-b7fb-457bf416dabc', 'f69922dd-85df-45a8-be32-0852ee90c23b', '27777161-fa8c-4527-a65a-2085b4fc3521', 'OP', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('e15c3e90-02ee-447e-a262-16607fe0d63e', 'f69922dd-85df-45a8-be32-0852ee90c23b', 'a70f70b1-6d03-4a76-8251-dc62d40c1370', 'PID_D', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('4d8af596-257f-4703-abf6-5aba77f4a35c', 'f69922dd-85df-45a8-be32-0852ee90c23b', '1088f587-21f0-45c0-8528-5deccbcfee45', 'PID_I', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('4aee6ca7-0dc6-4088-a7f6-b6f50d78118c', 'f69922dd-85df-45a8-be32-0852ee90c23b', '218b3e37-5cb2-4f3a-9e39-4c05d628e829', 'PID_P', false, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('55515368-50d8-45ef-8759-da801121f2d6', 'f69922dd-85df-45a8-be32-0852ee90c23b', 'c8725542-23b5-4e8b-8275-5c8ce88ebddb', 'PV', true, NOW()) ON CONFLICT (id) DO NOTHING;
INSERT INTO loop_tag_mapping (id, loop_id, tag_id, tag_role, is_required, created_at) VALUES ('be4637e3-4900-4185-bfe8-15c83af9cbc8', 'f69922dd-85df-45a8-be32-0852ee90c23b', 'b0165b63-a92a-4750-a9ed-a1fa696d1a51', 'SP', true, NOW()) ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 9. 系统配置 (sys_config) [S2-LOOP-003 新增]
-- =============================================================================
-- AAS 连接配置默认值 + 实时数据回写开关（运行时可由管理员通过 UI 链路配置页面修改）
-- =============================================================================

INSERT INTO sys_config (key, value, description, updated_by, updated_at) VALUES
('aas.endpoint', 'opc.tcp://localhost:4840', 'AAS OPC UA 端点', 'system', NOW()),
('aas.sync_interval_seconds', '300', 'AAS 同步周期（秒）', 'system', NOW()),
('aas.sync_enabled', 'true', 'AAS 同步启停状态', 'system', NOW()),
('aas.security_mode', 'None', 'AAS 安全模式：None/Sign/SignAndEncrypt', 'system', NOW()),
('datasource.type', 'remote_api', '历史数据源类型 tdengine/remote_api', 'system', NOW()),
('datasource.network_mode', 'lan', '网络模式 lan（局域网直连）/wan（公网走 Tailscale）', 'system', NOW()),
('datasource.history_api_url', 'http://192.168.100.2:81/api/services/v1/HistoryData/Get', '外部历史数据 API 地址', 'system', NOW()),
('datasource.history_api_token', '', '外部历史数据 API 鉴权 Token', 'system', NOW()),
('datasource.history_api_timeout', '30', '外部历史数据 API 超时（秒）', 'system', NOW()),
('datasource.signalr_hub_url', 'ws://192.168.100.2:81/signalr/realValueForClpmHub', '实时数据 SignalR Hub URL', 'system', NOW()),
('datasource.signalr_enabled', 'true', 'SignalR 实时订阅开关（后端重启生效）', 'system', NOW()),
('datasource.signalr_reconnect_interval', '5', 'SignalR 断线重连间隔（秒）', 'system', NOW()),
('datasource.realtime_writeback_enabled', 'true', '实时数据写回 TDengine 开关（链路配置页面运行时切换）', 'system', NOW()),
-- P3-04: LLM 配置（自然语言诊断解读），默认关闭，管理员在系统管理→LLM 配置页启用
('llm.enabled', 'false', 'LLM 解读开关（系统管理→LLM 配置页修改）', 'system', NOW()),
('llm.endpoint', '', 'LLM BaseURL（API 根地址，不含 /v1，如 https://api.openai.com）', 'system', NOW()),
('llm.api_key', '', 'LLM API Key（GET 返回时脱敏）', 'system', NOW()),
('llm.model', '', 'LLM 模型名（如 gpt-4o / deepseek-chat / qwen-plus）', 'system', NOW()),
('llm.timeout', '30', 'LLM 请求超时秒数', 'system', NOW()),
('llm.max_tokens', '4096', 'LLM 最大输出 token 数（推理模型建议 ≥4096）', 'system', NOW())
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- 10. 指标数据需求契约 (clpm_metric_data_requirement) — 26 条
-- =============================================================================
-- DataPlanner 依赖此表决定每个指标从 TDengine 读取哪些位号、采样策略、质量策略等。
-- 此表为空时 DataPlanner 无法生成 MetricDataBundle，所有指标判为 E 级可信度（INCONCLUSIVE）。
-- 数据来源：合成 k2f3a4b5c6d7(12条) + x4c5d6e7f8a9(删5旧增5新+修effective_auto_rate) + c588a06c1c05(14条Phase1)
-- 命名规范：metric_code 使用小写 calculator code（与 CALCULATOR_REGISTRY 对齐），
--           与 metric_config 的大写 DB 列名通过 _DB_TO_CALCULATOR_METRIC_CODE 双向映射
-- =============================================================================

INSERT INTO clpm_metric_data_requirement
    (metric_code, metric_name, tag_group, tags, sampling_strategy,
     quality_policy, mask_expression, aggregation_policy, depends_on) VALUES
-- ===== 基础 12 条（k2f3a4b5c6d7 + x4c5d6e7f8a9 修正后最终态）=====
('accuracy_rate', '准确率', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
('fast_rate', '快速率', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
 'LAST', '["settling_time","ideal_settling_time"]'),
('stability_rate', '稳定率', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid',
 'LAST', '["oscillation_rate"]'),
('effective_auto_rate', '有效自控率', 'MODE_HF', '["mode","op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'mode_valid && op_valid', 'LAST', NULL),
('good_value_rate', '好值率', 'QUALITY_HF', '["pv_quality"]',
 'FIXED_1S', 'KEEP_ALL', NULL, NULL, NULL),
('oscillation_rate', '振荡率', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
('saturation_rate', '饱和率', 'MODE_HF', '["mode","op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'mode_valid && op_valid', 'LAST', NULL),
('stiction_index', '粘滞指数', 'PVOP_HF', '["pv","op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
('output_trip_index', '输出行程指数', 'OP_HF', '["op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid && consecutive_valid', 'LAST', NULL),
('auto_mode_rate', '自控率', 'MODE_HF', '["mode"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'mode_valid', 'LAST', NULL),
('settling_time', '稳态时间', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
('ideal_settling_time', '理想稳态时间', 'CONFIG', '[]',
 'NONE', 'NONE', NULL, NULL, NULL),
-- ===== Phase 1 新增 14 条（c588a06c1c05）=====
('instrument_fault_rate', '仪表故障率', 'BASE', '["pv"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid', 'LAST', NULL),
('pv_mean', 'PV均值', 'BASE', '["pv"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid', 'LAST', NULL),
('pv_std', 'PV标准差', 'BASE', '["pv"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid', 'LAST', NULL),
('sp_mean', '设定值均值', 'BASE', '["sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'sp_valid', 'LAST', NULL),
('sp_std', '设定值标准差', 'BASE', '["sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'sp_valid', 'LAST', NULL),
('op_mean', '输出均值', 'OP_HF', '["op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
('op_std', '输出标准差', 'OP_HF', '["op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
('error_mean', '偏差均值', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
('error_std', '偏差标准差', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
('valve_linearity', '阀门线性度', 'PVOP_HF', '["pv","op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
('valve_nonlinearity', '阀门非线性度', 'PVOP_HF', '["pv","op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && op_valid', 'LAST', NULL),
('valve_operating_range', '阀门运行区间', 'OP_HF', '["op"]',
 'FIXED_1S', 'KEEP_ALL_WITH_VALIDITY', 'op_valid', 'LAST', NULL),
('setpoint_crossing_count', '设定值穿越次数', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST', NULL),
('oscillation_amplitude', '振荡幅值', 'BASE', '["pv","sp"]',
 'BY_CONTROL_TYPE', 'KEEP_ALL_WITH_VALIDITY', 'pv_valid && sp_valid', 'LAST',
 '["oscillation_rate"]')
ON CONFLICT (metric_code) DO UPDATE SET
        metric_code         = EXCLUDED.metric_code,
        metric_name         = EXCLUDED.metric_name,
        tag_group           = EXCLUDED.tag_group,
        tags                = EXCLUDED.tags,
        sampling_strategy   = EXCLUDED.sampling_strategy,
        quality_policy      = EXCLUDED.quality_policy,
        mask_expression     = EXCLUDED.mask_expression,
        aggregation_policy  = EXCLUDED.aggregation_policy,
        depends_on          = EXCLUDED.depends_on;

-- =============================================================================
-- 11. 诊断专家规则 (diagnosis_rule) — 6 条 R01-R06
-- =============================================================================
-- C2 规则引擎化：将硬编码的 R01-R06 专家规则迁入数据库表，
-- 运行时用 simpleeval 安全沙箱求值条件表达式（FDS §5.4.6）。
-- 按 priority 升序执行；数据来源：c3d4e5f6g7h8 迁移脚本
-- =============================================================================

INSERT INTO diagnosis_rule (id, rule_code, rule_name, priority, condition_expr, action_type, action_params, is_enabled, version) VALUES
('00000000-0000-0000-0000-000000000704', 'R04', '质量异常严重时仅保留质量标签', 5,
 'has("QUALITY_ABNORMAL") and feature("bad_quality_rate") > 0.5',
 'FILTER_ONLY', '{"keep": "QUALITY_ABNORMAL"}'::jsonb, true, 1),
('00000000-0000-0000-0000-000000000701', 'R01', '粘滞根因优先于振荡', 10,
 'has("OSCILLATION") and has("VALVE_STICTION") and confidence("VALVE_STICTION") > 0.5',
 'REMOVE_LABEL', '{"label": "OSCILLATION"}'::jsonb, true, 1),
('00000000-0000-0000-0000-000000000702', 'R02', '过激整定根因优先于振荡', 20,
 'has("OSCILLATION") and has("OVERAGGRESSIVE") and not has("VALVE_STICTION")',
 'REMOVE_LABEL', '{"label": "OSCILLATION"}'::jsonb, true, 1),
('00000000-0000-0000-0000-000000000703', 'R03', '过激与过保守互斥保留高置信度', 30,
 'has("OVERAGGRESSIVE") and has("OVERCONSERVATIVE")',
 'KEEP_HIGHEST', '{"labels": ["OVERAGGRESSIVE", "OVERCONSERVATIVE"]}'::jsonb, true, 1),
('00000000-0000-0000-0000-000000000705', 'R05', '所有算法低置信度时添加人工复核', 40,
 'count() > 0 and max_confidence() < 0.5',
 'ADD_LABEL', '{"label": "MANUAL_REVIEW", "confidence": 0.5}'::jsonb, true, 1),
('00000000-0000-0000-0000-000000000706', 'R06', '按标签优先级排序', 90,
 'True',
 'SORT_PRIORITY', '{"priority_map": {"QUALITY_ABNORMAL": 1, "VALVE_STICTION": 2, "OVERAGGRESSIVE": 3, "OVERCONSERVATIVE": 4, "OUTPUT_SATURATION": 5, "OSCILLATION": 6, "EXTERNAL_DISTURBANCE": 7, "MANUAL_REVIEW": 99}}'::jsonb, true, 1)
ON CONFLICT (id) DO UPDATE SET
        rule_code      = EXCLUDED.rule_code,
        rule_name      = EXCLUDED.rule_name,
        priority       = EXCLUDED.priority,
        condition_expr = EXCLUDED.condition_expr,
        action_type    = EXCLUDED.action_type,
        action_params  = EXCLUDED.action_params,
        is_enabled     = EXCLUDED.is_enabled,
        version        = EXCLUDED.version;

-- =============================================================================
-- 12. DCS 配置（dcs_vendor / dcs_model / mode_definition / dcs_mode_mapping）
-- =============================================================================
-- 配置驱动的 MODE 映射（v6p1dcs001）：
--   dcs_vendor: 5 家主流 DCS 厂商（和利时/中控/霍尼韦尔/横河/艾默生）
--   dcs_model: 每品牌 1 个主流型号（MACS/ECS-700/Experion/CENTUM/DeltaV）
--   mode_definition: 5 行标准 MODE 定义（0-4，替代硬编码 AUTO_MODES）
--   dcs_mode_mapping: 30 行映射矩阵（5 本系统默认 + 5 型号 × 5 MODE）
-- 固定 UUID 确保 FK 关联可重建；数据来源：v6p1dcs001 迁移脚本
-- =============================================================================

-- 12a. dcs_vendor（5 条）
INSERT INTO dcs_vendor (id, code, name, name_en, description, sort_order, is_active) VALUES
('00000000-0000-0000-0000-000000000d01', 'hollysys',  '和利时',   'HollySys',  '北京和利时系统工程股份有限公司', 1, true),
('00000000-0000-0000-0000-000000000d02', 'supcon',    '中控',     'SUPCON',    '浙江中控技术股份有限公司',       2, true),
('00000000-0000-0000-0000-000000000d03', 'honeywell', '霍尼韦尔', 'Honeywell', '霍尼韦尔国际公司',               3, true),
('00000000-0000-0000-0000-000000000d04', 'yokogawa',  '横河',     'Yokogawa',  '横河电机株式会社',               4, true),
('00000000-0000-0000-0000-000000000d05', 'emerson',   '艾默生',   'Emerson',   '艾默生电气公司',                 5, true)
ON CONFLICT (id) DO UPDATE SET
        code        = EXCLUDED.code,
        name        = EXCLUDED.name,
        name_en     = EXCLUDED.name_en,
        description = EXCLUDED.description,
        sort_order  = EXCLUDED.sort_order,
        is_active   = EXCLUDED.is_active;

-- 12b. dcs_model（5 条，vendor_id 引用 dcs_vendor 固定 UUID）
INSERT INTO dcs_model (id, vendor_id, code, name, description, sort_order, is_active) VALUES
('00000000-0000-0000-0000-000000000d11', '00000000-0000-0000-0000-000000000d01', 'hollysys-macs',      'MACS 系统',    '和利时 MACS V 集散控制系统',      1, true),
('00000000-0000-0000-0000-000000000d12', '00000000-0000-0000-0000-000000000d02', 'supcon-ecs700',      'ECS-700',      '中控 ECS-700 集散控制系统',       2, true),
('00000000-0000-0000-0000-000000000d13', '00000000-0000-0000-0000-000000000d03', 'honeywell-experion', 'Experion PKS', '霍尼韦尔 Experion 过程知识系统',  3, true),
('00000000-0000-0000-0000-000000000d14', '00000000-0000-0000-0000-000000000d04', 'yokogawa-centum',    'CENTUM CS3000','横河 CENTUM CS3000 集散控制系统', 4, true),
('00000000-0000-0000-0000-000000000d15', '00000000-0000-0000-0000-000000000d05', 'emerson-deltav',     'DeltaV',       '艾默生 DeltaV 集散控制系统',      5, true)
ON CONFLICT (id) DO UPDATE SET
        vendor_id   = EXCLUDED.vendor_id,
        code        = EXCLUDED.code,
        name        = EXCLUDED.name,
        description = EXCLUDED.description,
        sort_order  = EXCLUDED.sort_order,
        is_active   = EXCLUDED.is_active;

-- 12c. mode_definition（5 行标准 MODE 定义）
INSERT INTO mode_definition (id, standard_mode, label_zh, label_en, is_auto, color, sort_order, description) VALUES
('00000000-0000-0000-0000-000000000d21', 0, '手动', 'MANUAL', false, '#d4380d', 0, '操作员直接操作 OP'),
('00000000-0000-0000-0000-000000000d22', 1, '自动', 'AUTO',   true,  '#52c41a', 1, '单回路 PID 自动控制'),
('00000000-0000-0000-0000-000000000d23', 2, '串级', 'CAS',    true,  '#1890ff', 2, '主-副回路串级控制'),
('00000000-0000-0000-0000-000000000d24', 3, '远程', 'REMOTE', true,  '#722ed1', 3, 'SCADA/上位机远程设定'),
('00000000-0000-0000-0000-000000000d25', 4, '先控', 'APC',    true,  '#13c2c2', 4, '先进过程控制（MPC 等）')
ON CONFLICT (id) DO UPDATE SET
        standard_mode = EXCLUDED.standard_mode,
        label_zh      = EXCLUDED.label_zh,
        label_en      = EXCLUDED.label_en,
        is_auto       = EXCLUDED.is_auto,
        color         = EXCLUDED.color,
        sort_order    = EXCLUDED.sort_order,
        description   = EXCLUDED.description;

-- 12d. dcs_mode_mapping（30 条：5 本系统默认 + 25 型号映射）
-- 本系统默认映射（dcs_model_id=NULL，1:1 映射）
INSERT INTO dcs_mode_mapping (dcs_model_id, standard_mode, raw_mode_value, description) VALUES
(NULL, 0, 0, '本系统默认映射（1:1）'),
(NULL, 1, 1, '本系统默认映射（1:1）'),
(NULL, 2, 2, '本系统默认映射（1:1）'),
(NULL, 3, 3, '本系统默认映射（1:1）'),
(NULL, 4, 4, '本系统默认映射（1:1）')
ON CONFLICT DO NOTHING;

-- 各型号默认映射（1:1，可后续按实际 DCS 调整）
INSERT INTO dcs_mode_mapping (dcs_model_id, standard_mode, raw_mode_value, description) VALUES
('00000000-0000-0000-0000-000000000d11', 0, 0, 'hollysys-macs 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d11', 1, 1, 'hollysys-macs 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d11', 2, 2, 'hollysys-macs 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d11', 3, 3, 'hollysys-macs 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d11', 4, 4, 'hollysys-macs 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d12', 0, 0, 'supcon-ecs700 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d12', 1, 1, 'supcon-ecs700 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d12', 2, 2, 'supcon-ecs700 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d12', 3, 3, 'supcon-ecs700 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d12', 4, 4, 'supcon-ecs700 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d13', 0, 0, 'honeywell-experion 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d13', 1, 1, 'honeywell-experion 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d13', 2, 2, 'honeywell-experion 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d13', 3, 3, 'honeywell-experion 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d13', 4, 4, 'honeywell-experion 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d14', 0, 0, 'yokogawa-centum 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d14', 1, 1, 'yokogawa-centum 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d14', 2, 2, 'yokogawa-centum 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d14', 3, 3, 'yokogawa-centum 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d14', 4, 4, 'yokogawa-centum 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d15', 0, 0, 'emerson-deltav 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d15', 1, 1, 'emerson-deltav 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d15', 2, 2, 'emerson-deltav 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d15', 3, 3, 'emerson-deltav 默认映射（1:1，可按实际 DCS 调整）'),
('00000000-0000-0000-0000-000000000d15', 4, 4, 'emerson-deltav 默认映射（1:1，可按实际 DCS 调整）')
ON CONFLICT DO NOTHING;

-- =============================================================================
-- 诊断阈值模板（P3-02：按回路类型差异化预置，6 种 loop_type × 19 条模板）
-- 幂等：ON CONFLICT DO NOTHING，不覆盖用户已有覆盖
-- OTHER 类型不预置（无覆盖时自动回退全局默认）
-- =============================================================================
INSERT INTO diagnosis_threshold_override (id, diag_code, scope_type, scope_id, threshold, version, updated_by, updated_at) VALUES
-- FLOW（流量）：响应快、噪声大 → 振荡阈值严、饱和阈值宽、过激阈值严
('11111111-0000-0000-0000-000000000001', 'OSCILLATION',        'loop_type', 'FLOW',         '{"similarity_threshold": 0.35, "min_zero_crossings": 5, "fft_min_zero_crossings": 6}'::jsonb, 1, 'system', NOW()),
('11111111-0000-0000-0000-000000000002', 'OUTPUT_SATURATION',  'loop_type', 'FLOW',         '{"saturation_epsilon": 3.0}'::jsonb, 1, 'system', NOW()),
('11111111-0000-0000-0000-000000000003', 'OVERAGGRESSIVE',     'loop_type', 'FLOW',         '{"step_overshoot_threshold": 0.20}'::jsonb, 1, 'system', NOW()),
('11111111-0000-0000-0000-000000000004', 'OVERCONSERVATIVE',   'loop_type', 'FLOW',         '{"slow_expected_tau_seconds": 10.0, "slow_response_ratio_threshold": 1.5}'::jsonb, 1, 'system', NOW()),
-- TEMPERATURE（温度）：响应慢、惯性大 → 振荡阈值宽、过激阈值宽
('22222222-0000-0000-0000-000000000001', 'OSCILLATION',        'loop_type', 'TEMPERATURE',  '{"similarity_threshold": 0.45, "min_zero_crossings": 3, "fft_min_cycles": 1.5}'::jsonb, 1, 'system', NOW()),
('22222222-0000-0000-0000-000000000002', 'OVERAGGRESSIVE',     'loop_type', 'TEMPERATURE',  '{"step_overshoot_threshold": 0.30}'::jsonb, 1, 'system', NOW()),
('22222222-0000-0000-0000-000000000003', 'OVERCONSERVATIVE',   'loop_type', 'TEMPERATURE',  '{"slow_expected_tau_seconds": 600.0, "slow_response_ratio_threshold": 2.5}'::jsonb, 1, 'system', NOW()),
-- PRESSURE（压力）：响应较快 → 振荡阈值稍严
('33333333-0000-0000-0000-000000000001', 'OSCILLATION',        'loop_type', 'PRESSURE',     '{"similarity_threshold": 0.40, "min_zero_crossings": 4}'::jsonb, 1, 'system', NOW()),
('33333333-0000-0000-0000-000000000002', 'OVERCONSERVATIVE',   'loop_type', 'PRESSURE',     '{"slow_expected_tau_seconds": 30.0, "slow_response_ratio_threshold": 2.0}'::jsonb, 1, 'system', NOW()),
-- LEVEL（液位）：积分特性 → 振荡阈值严、饱和阈值严
('44444444-0000-0000-0000-000000000001', 'OSCILLATION',        'loop_type', 'LEVEL',        '{"similarity_threshold": 0.35, "min_zero_crossings": 5}'::jsonb, 1, 'system', NOW()),
('44444444-0000-0000-0000-000000000002', 'OUTPUT_SATURATION',  'loop_type', 'LEVEL',        '{"saturation_epsilon": 1.5}'::jsonb, 1, 'system', NOW()),
('44444444-0000-0000-0000-000000000003', 'OVERCONSERVATIVE',   'loop_type', 'LEVEL',        '{"slow_expected_tau_seconds": 120.0, "slow_response_ratio_threshold": 2.0}'::jsonb, 1, 'system', NOW()),
-- ANALYSIS（分析）：响应最慢 → 振荡阈值最宽、过激阈值最宽
('55555555-0000-0000-0000-000000000001', 'OSCILLATION',        'loop_type', 'ANALYSIS',     '{"similarity_threshold": 0.50, "min_zero_crossings": 3, "fft_min_cycles": 1.0}'::jsonb, 1, 'system', NOW()),
('55555555-0000-0000-0000-000000000002', 'OVERAGGRESSIVE',     'loop_type', 'ANALYSIS',     '{"step_overshoot_threshold": 0.35}'::jsonb, 1, 'system', NOW()),
('55555555-0000-0000-0000-000000000003', 'OVERCONSERVATIVE',   'loop_type', 'ANALYSIS',     '{"slow_expected_tau_seconds": 900.0, "slow_response_ratio_threshold": 3.0}'::jsonb, 1, 'system', NOW()),
-- SPEED（转速）：响应快、精度高 → 振荡阈值严、质量阈值严、过激阈值严
('66666666-0000-0000-0000-000000000001', 'OSCILLATION',        'loop_type', 'SPEED',        '{"similarity_threshold": 0.35, "min_zero_crossings": 5, "fft_min_zero_crossings": 6}'::jsonb, 1, 'system', NOW()),
('66666666-0000-0000-0000-000000000002', 'QUALITY_ABNORMAL',   'loop_type', 'SPEED',        '{"q002_bad_rate": 0.05}'::jsonb, 1, 'system', NOW()),
('66666666-0000-0000-0000-000000000003', 'OVERAGGRESSIVE',     'loop_type', 'SPEED',        '{"step_overshoot_threshold": 0.20}'::jsonb, 1, 'system', NOW()),
('66666666-0000-0000-0000-000000000004', 'OVERCONSERVATIVE',   'loop_type', 'SPEED',        '{"slow_expected_tau_seconds": 5.0, "slow_response_ratio_threshold": 1.5}'::jsonb, 1, 'system', NOW())
ON CONFLICT (diag_code, scope_type, scope_id) DO NOTHING;

-- =============================================================================
-- 15. 智能预警规则引擎种子规则（5 条示例 + 订阅关系）
-- 结合 27 条回路种子数据，覆盖 4 种规则类型与 3 种订阅范围：
--   ① THRESHOLD/ALL  控制器输出饱和（OP≥95%，10min）→ ERROR
--   ② THRESHOLD/LOOP V-2010 凝液闪蒸罐液位高高限（41LIC20117 PV≥90%，5min）→ CRITICAL
--   ③ CONFIDENCE/ALL 数据可信度降级（等级劣于 C）→ WARN
--   ④ COMPOSITE/LOOP V-4002 回流罐液位高且调节阀饱和（41LIC40108 PV≥85% AND OP≥95%，5min）→ CRITICAL
--   ⑤ DRIFT/ALL      过程变量均值漂移（30min 均值偏离历史基线 20%，10min）→ WARN
--     （DRIFT 求值为 Phase 2 能力，Phase 1 仅录入展示）
-- 说明：ALL 范围订阅取首个活跃回路作占位 loop_id（外键完整性），求值时按 scope_type=ALL 展开到全部活跃回路。
-- 幂等：固定 UUID + ON CONFLICT (id) DO NOTHING。
-- =============================================================================
INSERT INTO alert_rule (id, rule_code, rule_name, rule_type, dsl, description, priority, is_enabled, version, created_by, created_at) VALUES
-- ① 控制器输出饱和（全回路）
('00000000-0000-0000-0000-0a1a00000001', 'OP_SATURATION', '控制器输出饱和报警', 'THRESHOLD',
 '{"ruleType":"THRESHOLD","scope":{"loopSelector":{"type":"ALL"}},"condition":{"metric":"OP","operator":">=","value":95},"durationSeconds":600,"cooldownSeconds":1800,"severity":"ERROR","actions":[{"type":"CREATE_EVENT"}],"priority":100,"dedupKey":"${loop_id}+${rule_id}"}'::jsonb,
 '控制器输出（OP）持续 ≥95% 达 10 分钟，提示调节阀接近全开，存在饱和失效风险。适用于全部回路。',
 100, true, 1, 'system', NOW()),
-- ② V-2010 凝液闪蒸罐液位高高限（指定回路 41LIC20117）
('00000000-0000-0000-0000-0a1a00000002', 'LIC20117_PV_HIGH', 'V-2010 凝液闪蒸罐液位高高限', 'THRESHOLD',
 '{"ruleType":"THRESHOLD","scope":{"loopSelector":{"type":"LOOP","value":"8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5"}},"condition":{"metric":"PV","operator":">=","value":90},"durationSeconds":300,"cooldownSeconds":1800,"severity":"CRITICAL","actions":[{"type":"CREATE_EVENT"}],"priority":50,"dedupKey":"${loop_id}+${rule_id}"}'::jsonb,
 'V-2010 LP 洁净凝液闪蒸罐液位（41LIC20117，量程 0-100%）持续 ≥90% 达 5 分钟，触发高高限紧急预警。',
 50, true, 1, 'system', NOW()),
-- ③ 数据可信度降级（全回路）
('00000000-0000-0000-0000-0a1a00000003', 'CONFIDENCE_DEGRADED', '数据可信度降级告警', 'CONFIDENCE',
 '{"ruleType":"CONFIDENCE","scope":{"loopSelector":{"type":"ALL"}},"condition":{"maxLevel":"C"},"durationSeconds":0,"cooldownSeconds":3600,"severity":"WARN","actions":[{"type":"CREATE_EVENT"}],"priority":150,"dedupKey":"${loop_id}+${rule_id}"}'::jsonb,
 '回路可信度等级劣于 C（即 D 或 E）时触发，提示数据质量不足、评估结果不确定，需排查数据采集链路。',
 150, true, 1, 'system', NOW()),
-- ④ 液位高且调节阀饱和（组合条件，指定回路 41LIC40108）
('00000000-0000-0000-0000-0a1a00000004', 'LEVEL_HIGH_OP_SAT', '液位高且调节阀饱和', 'COMPOSITE',
 '{"ruleType":"COMPOSITE","scope":{"loopSelector":{"type":"LOOP","value":"c4073df9-5983-45a5-aaf3-171dcbe26361"}},"condition":{"logic":"AND","operands":[{"type":"THRESHOLD","metric":"PV","operator":">=","value":85},{"type":"THRESHOLD","metric":"OP","operator":">=","value":95}]},"durationSeconds":300,"cooldownSeconds":1800,"severity":"CRITICAL","actions":[{"type":"CREATE_EVENT"}],"priority":30,"dedupKey":"${loop_id}+${rule_id}"}'::jsonb,
 'V-4002 低压脱丙烷塔回流罐液位（41LIC40108，量程 0-100%）≥85% 且控制器输出 ≥95% 持续 5 分钟，提示调节已接近失效，存在失控风险。',
 30, true, 1, 'system', NOW()),
-- ⑤ 过程变量均值漂移（全回路，DRIFT Phase 2 求值）
('00000000-0000-0000-0000-0a1a00000005', 'PV_MEAN_DRIFT', '过程变量均值漂移', 'DRIFT',
 '{"ruleType":"DRIFT","scope":{"loopSelector":{"type":"ALL"}},"condition":{"metric":"PV","statistic":"MEAN","windowSeconds":1800,"baseline":{"type":"HISTORICAL"},"deviationThreshold":20,"deviationType":"RELATIVE"},"durationSeconds":600,"cooldownSeconds":3600,"severity":"WARN","actions":[{"type":"CREATE_EVENT"}],"priority":200,"dedupKey":"${loop_id}+${rule_id}"}'::jsonb,
 '过程变量（PV）30 分钟窗口均值偏离历史基线 ≥20% 持续 10 分钟，提示工况发生漂移。DRIFT 规则求值为 Phase 2 能力，Phase 1 仅录入展示。',
 200, true, 1, 'system', NOW())
ON CONFLICT (id) DO NOTHING;

-- 订阅关系（ALL 范围以首个活跃回路 41FIC20021_PIDA 为占位 loop_id）
INSERT INTO alert_rule_subscription (id, rule_id, loop_id, scope_type, scope_value, is_active, created_by, created_at) VALUES
('00000000-0000-0000-0000-0a1b00000001', '00000000-0000-0000-0000-0a1a00000001', 'dcd77662-ddf5-4643-befe-18b4a58b0622', 'ALL', NULL, true, 'system', NOW()),
('00000000-0000-0000-0000-0a1b00000002', '00000000-0000-0000-0000-0a1a00000002', '8c3ba471-2b9e-4f94-a387-9e7c82b1f9f5', 'LOOP', NULL, true, 'system', NOW()),
('00000000-0000-0000-0000-0a1b00000003', '00000000-0000-0000-0000-0a1a00000003', 'dcd77662-ddf5-4643-befe-18b4a58b0622', 'ALL', NULL, true, 'system', NOW()),
('00000000-0000-0000-0000-0a1b00000004', '00000000-0000-0000-0000-0a1a00000004', 'c4073df9-5983-45a5-aaf3-171dcbe26361', 'LOOP', NULL, true, 'system', NOW()),
('00000000-0000-0000-0000-0a1b00000005', '00000000-0000-0000-0000-0a1a00000005', 'dcd77662-ddf5-4643-befe-18b4a58b0622', 'ALL', NULL, true, 'system', NOW())
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 脚本结束
-- =============================================================================
