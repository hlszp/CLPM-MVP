# CLPM 数据模型设计说明书 (DDS)

**文档状态**: 正式版
**当前版本**: v2.0
**发布日期**: 2026-06-19
**设计依据**: PRD (v2.2), FDS (v2.0), ADS (v2.0)

---

## 1. 设计原则

遵循 ADS (v2.0) 规定的“存算分离”原则，系统数据模型严格拆分为两大独立域：
1. **关系型业务域 (PostgreSQL)**：承载工厂拓扑模型、配置元数据、算法快照结果及轻量级状态追踪记录。要求强一致性 (ACID)。
2. **高频时序域 (TDengine)**：承载原始海量秒级运行数据。要求极高写入吞吐与降采样查询性能。

---

## 2. 关系型业务模型 (PostgreSQL)

### 2.1 工厂拓扑与台账 (Plant Model)

**表名: `plant_node` (工厂节点)**
| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 节点主键 | PK |
| name | VARCHAR(100) | 节点名称 (如: 常减压装置) | NOT NULL |
| type | VARCHAR(20) | 节点类型: `FACTORY`, `UNIT`, `EQUIPMENT` | NOT NULL |
| parent_id | UUID | 父节点 ID | FK -> plant_node.id |

**表名: `loop_ledger` (回路台账)**
| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 回路主键 | PK |
| tag_name | VARCHAR(100)| 唯一位号标识 (如: 101-FC-1023) | UNIQUE, NOT NULL |
| unit_id | UUID | 所属工艺单元 ID | FK -> plant_node.id |
| mapping_pv | VARCHAR(100)| PV 对应的时序点名 | NOT NULL |
| mapping_sp | VARCHAR(100)| SP 对应的时序点名 | NOT NULL |
| mapping_op | VARCHAR(100)| OP 对应的时序点名 | NOT NULL |
| mapping_mode | VARCHAR(100)| MODE 对应的时序点名 | NOT NULL |
| is_active | BOOLEAN | 是否启用全量评估计算 | DEFAULT TRUE |

### 2.2 评估快照与追踪 (Evaluation & Tracking)

**表名: `kpi_snapshot_hourly` (每小时性能评估快照)**
| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 快照主键 | PK |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id |
| ts_start | TIMESTAMP | 评估窗口起始时间 | NOT NULL |
| ts_end | TIMESTAMP | 评估窗口结束时间 | NOT NULL |
| score | DECIMAL(5,2) | 综合评分 (0-100) | |
| good_value_rate | DECIMAL(5,2) | 好值率 (%) | |
| auto_mode_rate | DECIMAL(5,2) | 自控率 (%) | |
| steady_rate | DECIMAL(5,2) | 平稳率 (%) | |
| oscillation_rate| DECIMAL(5,2) | 振荡率 (%) | |
| status | VARCHAR(20) | 计算状态: `SUCCESS`, `INCONCLUSIVE`, `PARTIAL` | NOT NULL |

**表名: `action_tracker` (轻量级异常追踪记录)**
| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| id | UUID | 追踪记录主键 | PK |
| loop_id | UUID | 关联回路 ID | FK -> loop_ledger.id |
| diagnosis_label| VARCHAR(100)| 自动预诊结论 (如: 疑似阀门粘滞) | |
| action_status | VARCHAR(20) | 处理状态: `PENDING`(待处理), `IN_PROGRESS`(处理中), `IGNORED`(已忽略), `RESOLVED`(已实施) | NOT NULL, DEFAULT 'PENDING' |
| evidence_url | VARCHAR(255)| 导出的《诊断建议书》PDF S3 存储路径 | |
| updated_by | VARCHAR(50) | 最后操作人 (仪控工程师) | |
| updated_at | TIMESTAMP | 状态变更时间戳 | |

---

## 3. 高频时序模型 (TDengine)

采用 TDengine 推荐的“一个设备一张表，一类设备一个超级表”设计模式。

### 3.1 超级表定义 (Super Table)

**超级表名: `st_loop_data`**
该超级表定义了所有控制回路时序数据的标准 Schema。

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| ts | TIMESTAMP | 采样时间戳 | 主键 (时间列) |
| pv | FLOAT | 过程变量测量值 | |
| sp | FLOAT | 设定值 | |
| op | FLOAT | 控制器输出值 (0-100) | |
| mode | TINYINT | 控制模式 (0=Manual, 1=Auto, 2=Cascade) | |
| quality | TINYINT | OPC 数据质量码 (0=Bad, 1=Good) | |

**超级表标签 (Tags)**
用于快速过滤与聚合查询。
| 标签名 | 类型 | 说明 |
|---|---|---|
| loop_id | BINARY(36) | 关联关系库的 loop_ledger.id |
| unit_id | BINARY(36) | 关联的单元 ID，用于按单元降采样聚合 |

### 3.2 子表实例化
系统每同步接入一条新回路，将自动执行建表操作：
`CREATE TABLE d_loop_101_fc_1023 USING st_loop_data TAGS ('uuid-xxx', 'uuid-yyy');`

---

## 4. 数据容错与清洗规则

1. **质量码过滤**：在写入 `kpi_snapshot_hourly` 前，计算引擎必须首先扫描 `quality` 字段。若某时间窗内 `quality=1 (Good)` 的记录占比低于配置阈值（默认 20%），则跳过各项 KPI 计算，直接将快照状态置为 `INCONCLUSIVE`，各 KPI 字段留空（NULL），**严禁写入 0 分**。
2. **时序数据留存期 (Retention)**：
   * TDengine 原始秒级数据默认保留周期配置为 `KEEP 365` (1年)。
   * PostgreSQL 中的 `kpi_snapshot_hourly` 快照记录永久保留，支撑 P2 规划中的 5 年任意查询及趋势回溯。
