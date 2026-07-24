# CLPM 接口设计规范说明书 (IDS)

**文档状态**: 正式版
**当前版本**: v6.0 (实现契约对齐与 3+1+8 体系统一版)
**发布日期**: 2026-07-06
**设计依据**: PRD (v3.1), FDS (v5.1), ADS (v4.0), DDS (v4.1), UIUX (v5.3), 实现契约 (v1.0), 关键算法设计说明 v2.0, GB/T 44693.2-2024

---

## 0. 文档变更记录

| 版本 | 日期 | 变更说明 | 作者 |
|---|---|---|---|
| v1.0 | 2026-06-16 | 初始版本（基于旧版 PRS 拆解） | 产品团队 |
| v2.0 | 2026-06-19 | 全面重构：基于 PRD v2.2 重新设计，移除繁重工单审批流，重塑为"自动评估+轻量跟踪"架构，定义性能看板、波形查询、异常跟踪三类核心 API。 | 系统设计团队 |
| v3.0 | 2026-06-20 | 产品化架构重构：①对齐 6 模块 + 1 门户结构（工作台/回路管理/性能评估/诊断中心/回路整定/系统管理）；②引入 AAS Tag 模型（7 个 OPC tag：PV/SP/OP/MODE/PID_P/PID_I/PID_D），PID 参数从 tag 只读，数据质量主要针对 PV；③新增工作台、回路管理（含 AAS 同步/回路 CRUD/tag 关联/回路监控）、回路整定（Phase 2 占位）、系统管理 API 组；④扩展性能评估与诊断中心 API（指标配置/引擎规则/统计报表）；⑤波形 API 响应增加 `pv_quality` 数组，明确仅 PV 携带质量码；⑥补充新错误码（ERR_TAG_NOT_FOUND/ERR_LOOP_TAG_REQUIRED/ERR_METRIC_WEIGHT_SUM/ERR_CONFIG_FORBIDDEN）。 | 系统设计团队 |
| v3.1 | 2026-06-21 | 认证授权与统一响应规范补充：①新增 §5 认证与授权 API（登录/登出/Token 刷新/获取当前用户/修改密码），定义 JWT Bearer Token 方案、Access/Refresh Token 双 Token 机制、黑名单策略、权限列表枚举；②新增 §6 统一响应规范（成功/错误/分页/异步任务响应 envelope 格式、HTTP 状态码使用规则、4 位业务错误码分段定义、前端 Axios 拦截器对接规范）；③补充 ERR_TOKEN_EXPIRED/ERR_TOKEN_INVALID/ERR_INVALID_CREDENTIALS/ERR_ACCOUNT_DISABLED/ERR_TOO_MANY_ATTEMPTS/ERR_PASSWORD_SAME/ERR_USER_NOT_FOUND/ERR_USER_DUPLICATE 等认证相关错误码。 | 系统设计团队 |
| v3.2 | 2026-06-22 | 算法对齐与算法服务接口补充（依据《关键算法设计说明》v1.0）：①统一 6 大 KPI 清单（good_value_rate/auto_mode_rate/steady_rate/accuracy_rate/oscillation_rate/saturation_rate），所有 KPI 接口响应包含全部 6 个 KPI 字段 + score + status（GOOD/WARNING/POOR/INCONCLUSIVE）+ algorithm_version；②统一诊断标签为 8 类（OSCILLATION/VALVE_STICTION/OVERAGGRESSIVE/OVERCONSERVATIVE/EXTERNAL_DISTURBANCE/QUALITY_ABNORMAL/OUTPUT_SATURATION/MANUAL_REVIEW），诊断结果结构含 label/confidence/evidence/algorithm，新增 fused_confidence（Dempster-Shafer 融合置信度）；③整定接口新增 fitting_score 字段、method 枚举（IMC/LAMBDA/ZIEGLER_NICHOLS/COHEN_COON/SIMC），响应包含 model_params/pid_params/simulation_result/fitting_score；④新增 §2.7 算法服务接口（4 个异步 API：KPI 计算/诊断分析/整定计算/任务查询）；⑤新增 §2.8 指标配置接口与 §2.9 诊断配置接口（批量 GET/PUT，calc_method/threshold JSONB/control_type）；⑥对齐 C1-C7 跨文档差距修正。 | 系统设计团队 |
| v4.0 | 2026-06-26 | 数据质量增强与算法服务扩展（依据《关键算法设计说明》v2.0）：①§2.4.5 历史数据接口扩展，新增 tagGroup（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF）/qualityPolicy（KEEP_ALL_WITH_VALIDITY/KEEP_ALL）/aggregationPolicy（LAST/MEAN/MAX）参数，数据点增加 valid 标记；②§2.7.1 KPI 计算接口增加数据血缘（sampling_freq/quality_policy/tag_group/valid_rate）与 confidence_level（A/B/C/D/E），score 增加 data_lineage JSON 对象；③新增 §2.7.5 DataPlanner 内部接口（plan/bundle，仅供算法服务调用，不对外暴露）；④新增 §2.7.6 任务管理接口（standard/custom 评估任务触发、任务状态/列表查询）；⑤§2.4 新增诊断标签接口（标签列表/回路标签/标签处理）；⑥§4.4 PV 质量码处理约定从"Bad 对应 pv=null"改为"保留所有点，Bad 对应 valid=false"，波形 Good 实线/Bad 灰色虚线/Uncertain 黄色虚线，引入 Metric Validity Mask；⑦§2.8 指标配置从 6 大 KPI 升级为 3+1+8 结构（3 核心指标 + 1 投用指标 + 8 辅助诊断指标），核心指标权重配置、投用指标作为折扣因子、辅助诊断指标不参与权重配置。 | 系统设计团队 |
| v5.0 | 2026-07-04 | 对齐 FDS v5.1 与 DDS v4.1：①引用文档版本同步更新至 FDS v5.1/DDS v4.1/ADS v4.0/UIUX v5.3；②新增对实现契约 v1.0 的引用；③补全 metric_config 表 category/is_discount_factor/grading_thresholds 字段；④补全 loop_ledger 表 control_type/importance_level/include_in_evaluation 字段；⑤补全 unit_kpi_summary 表 excluded_loops/status 字段；⑥数据血缘结构修正为 5 独立字段+data_lineage JSONB（对齐 DDS v6.0 §3.5）；⑦kpi_snapshot_custom.stability_rate 修正为 steady_rate。 | 系统设计团队 |
| v6.0 | 2026-07-06 | 实现契约对齐与 3+1+8 体系统一：①统一 API 路径与代码一致（`/api/v1/system/users`→`/api/v1/users`、`/api/v1/system/audit`→`/api/v1/audit-logs`、`/api/v1/system/reports`→`/api/v1/reports`、`/api/v1/tracker/*`→`/api/v1/diagnosis/tracker/*`）；②统一角色枚举为 5 角色（EXECUTOR→IC_ENGINEER、COLLABORATOR→PE_ENGINEER、VIEWER→SPONSOR，补全 ADMIN/EXPERT）；③统一状态机枚举（Action Tracker: PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED；Loop: READY/PARTIAL/INACTIVE；Tuning: DRAFT/RUNNING/COMPLETED/ROLLED_BACK；KPI 快照: SUCCESS/PARTIAL/INCONCLUSIVE；PV Quality: GOOD/BAD/UNCERTAIN）；④统一 KPI 体系为 3+1+8（修复 §2.1/§2.3 与 §2.8 内部矛盾），补全 effective_auto_rate 作为折扣因子 R；⑤统一 scoreWeights 结构为 3 核心指标权重（accuracy/fast/steady，权重和=100）+ R 折扣因子（不纳入权重和）；⑥可信度 D 级阈值统一为 20%~60%（对齐 FDS v5.1）；⑦补全 ERR_DISCOUNT_FACTOR_READONLY/ERR_AUXILIARY_METRIC_WEIGHT_FORBIDDEN 错误码；⑧新增对 GB/T 44693.2-2024 国标的引用；⑨新增"数据模型映射"与"时序数据存储"通用约定小节。 | 系统设计团队 |

---

## 1. 接口设计原则

本规范定义了前后端 (BFF) 之间及系统与外部系统交互的核心 API 契约，严格遵循以下原则：

* **RESTful 风格**：资源导向设计，标准 HTTP 方法 (GET, POST, PUT, PATCH, DELETE)。资源路径统一以 `/api/v1/` 为前缀。
* **防超载设计**：对于时序波形数据的查询，必须强制提供时间窗参数，并默认执行降采样（LTTB）。单次返回数据点数不得超过 `maxPoints` 上限。
* **安全与权限**：所有接口需在 Header 中携带 `Authorization: Bearer <JWT>`，网关层执行 RBAC 校验。角色枚举对齐实现契约 v2.0 §4.5，共 5 个角色：`ADMIN`（系统管理员）/`IC_ENGINEER`（仪控工程师）/`PE_ENGINEER`（工艺/设备工程师）/`EXPERT`（外部专家）/`SPONSOR`（生产技术 Sponsor）。权限层级分为：查看层（SPONSOR）、协同层（PE_ENGINEER）、执行层（IC_ENGINEER）、管理层（ADMIN）、服务层（EXPERT）。
* **绝对只读边界**：系统与 DCS/OPC 之间仅建立单向只读连接，API 设计中**严禁**出现任何针对 DCS OPC 节点的 `Write` / `Set` 语义接口。
* **不掩盖数据缺失**：数据不足/无效时通过 `INCONCLUSIVE` 状态显式反馈，严禁以 0 分或空值掩盖。
* **产品化配置 API 原则**：
  * **配置即资源**：性能指标、诊断指标、引擎规则等均抽象为独立 RESTful 资源，支持 GET（查看）/ PUT（更新）/ 启停切换。
  * **配置即时生效**：配置变更 API 调用成功后即时生效，无需重启服务。
  * **配置变更审计**：所有配置变更必须记录审计日志（操作人/时间/变更前后值），审计日志不可物理删除。
  * **配置双重校验**：权重总和、阈值范围、参数合法性等校验在前端实时反馈，后端二次校验，不满足约束时返回明确错误码（如 `ERR_METRIC_WEIGHT_SUM`）。
  * **配置越权拦截**：仅系统管理员可执行配置类 API，越权操作返回 `ERR_CONFIG_FORBIDDEN`。
* **PV 质量码处理原则**：数据质量主要针对 PV 值。PV tag 携带质量码（Good/Bad/Uncertain），SP/OP/MODE/PID_* 不携带质量码。波形 API 响应中 `pv_quality` 数组与 `pv` 数组等长，KPI 好值率基于 PV 质量码统计。

---

## 2. 核心业务接口契约

### 2.1 工作台 API (Dashboard)

工作台为全角色日常作业门户入口，聚合性能评估、诊断中心、异常跟踪多模块数据，仅提供只读聚合接口。

#### 2.1.1 获取性能总览首页数据 (Get Dashboard Overview)

* **URL**: `GET /api/v1/dashboard/overview`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按指定工厂/装置/单元过滤，不传则返回全厂数据
  * `timeWindow` (String, required): 时间窗，枚举值 `today`, `yesterday`, `last_7_days`, `last_30_days`
  * `granularity` (String, default=`day`): 统计粒度，枚举值 `day`, `week`, `month`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "filterScope": {
        "plantNodeId": "uuid-xxx",
        "plantNodeName": "常减压装置",
        "timeWindow": "today",
        "granularity": "day"
      },
      "kpiCards": [
        {
          "metricKey": "accuracy_rate",
          "metricName": "准确率",
          "category": "CORE",
          "value": 88.1,
          "unit": "%",
          "delta": 0.3,
          "trend": "UP",
          "miniTrend": [87.5, 87.8, 88.0, 88.1],
          "status": "GOOD"
        },
        {
          "metricKey": "fast_rate",
          "metricName": "快速率",
          "category": "CORE",
          "value": 76.5,
          "unit": "%",
          "delta": 0.8,
          "trend": "UP",
          "miniTrend": [75.0, 75.5, 76.0, 76.5],
          "status": "FAIR"
        },
        {
          "metricKey": "steady_rate",
          "metricName": "稳定率",
          "category": "CORE",
          "value": 85.3,
          "unit": "%",
          "delta": -0.8,
          "trend": "DOWN",
          "miniTrend": [86.1, 85.9, 85.5, 85.3],
          "status": "WARNING"
        },
        {
          "metricKey": "effective_auto_rate",
          "metricName": "有效自控率",
          "category": "COMMISSIONING",
          "isDiscountFactor": true,
          "value": 92.5,
          "unit": "%",
          "delta": 1.2,
          "trend": "UP",
          "miniTrend": [91.0, 91.5, 92.0, 92.5],
          "status": "GOOD"
        },
        {
          "metricKey": "score",
          "metricName": "综合评分",
          "value": 78.6,
          "unit": "",
          "delta": 2.1,
          "trend": "UP",
          "miniTrend": [76.5, 77.0, 78.0, 78.6],
          "status": "WARNING"
        }
      ],
      "kpiSummary": {
        "accuracy_rate": 88.1,
        "fast_rate": 76.5,
        "steady_rate": 85.3,
        "effective_auto_rate": 92.5,
        "discountFactor": 0.925,
        "score": 78.6,
        "status": "WARNING",
        "snapshotStatus": "SUCCESS",
        "algorithm_version": "KPI_CALC_v1.0"
      },
      "badActors": [
        {
          "loopId": "uuid-xxx",
          "tagName": "101-FC-1023",
          "score": 45.2,
          "steadyRate": 60.5,
          "status": "POOR",
          "preDiagnosis": "疑似阀门粘滞"
        }
      ],
      "loopTrendSummary": {
        "loopId": "uuid-xxx",
        "tagName": "101-FC-1023",
        "latestPv": 50.2,
        "latestSp": 50.0,
        "latestOp": 45.8,
        "latestMode": 1,
        "trend": {
          "timestamps": [1623912000000, 1623912900000],
          "pv": [50.1, 50.2],
          "sp": [50.0, 50.0],
          "op": [45.5, 45.8]
        }
      },
      "combinedTrend": {
        "granularity": "day",
        "timestamps": ["2026-06-14", "2026-06-15", "2026-06-16", "2026-06-17"],
        "effectiveAutoRate": [91.0, 91.5, 92.0, 92.5],
        "steadyRate": [86.1, 85.9, 85.5, 85.3]
      }
    }
  }
  ```
* **说明**：KPI 卡片采用 3+1+8 体系（对齐 §2.8 与 FDS v6.0 §1.3）：固定返回 3 项核心指标（accuracy_rate/fast_rate/steady_rate）+ 1 项投用指标（effective_auto_rate，作为折扣因子 R）+ 综合评分（score），辅助诊断指标通过 §2.3.1 单独查询；每卡 `status` 枚举值为 5 级性能定级（`EXCELLENT`/`GOOD`/`FAIR`/`WARNING`/`POOR`，对齐 FDS v6.0 §1.3）；`kpiSummary` 汇总 3 核心指标 + 1 投用指标 + `discountFactor`（折扣因子，0-1）+ `score`（综合评分 = 核心指标加权得分 × 折扣因子）+ `status`（5 级性能定级）+ `snapshotStatus`（KPI 快照状态：SUCCESS/PARTIAL/INCONCLUSIVE）+ `algorithm_version`；`badActors` 默认返回 Top 10 低效回路；`loopTrendSummary` 返回选中回路近 24h 趋势摘要；数据为空时对应字段返回 `null` 或空数组，前端展示"--"。

---

### 2.2 回路管理 API (Loop Management)

回路是系统核心实体。AAS 同步的是 **tag 位号**（非回路实体），回路由用户在 CLPM 系统中创建并关联 tag。本组 API 覆盖 AAS Tag 同步、回路台账 CRUD、Tag 关联管理、回路监控四类子能力。

#### 2.2.1 获取工厂层级树 (List Plant Nodes)

* **URL**: `GET /api/v1/plant-nodes`
* **权限**: 执行层及以上（仪控工程师/系统管理员）
* **Query Parameters**:
  * `parentId` (UUID, optional): 父节点 ID，不传则返回顶层节点及其完整子树
* **Response (200 OK)**:
  ```json
  {
    "data": [
      {
        "id": "uuid-xxx",
        "name": "常减压装置",
        "type": "FACTORY",
        "parentId": null,
        "children": [
          {
            "id": "uuid-yyy",
            "name": "常减压装置-单元A",
            "type": "UNIT",
            "parentId": "uuid-xxx",
            "children": [
              {
                "id": "uuid-zzz",
                "name": "塔顶温度调节",
                "type": "EQUIPMENT",
                "parentId": "uuid-yyy",
                "children": []
              }
            ]
          }
        ]
      }
    ]
  }
  ```
* **说明**：返回工厂 → 装置 → 单元的多级层级树；`type` 枚举值为 `FACTORY`/`UNIT`/`EQUIPMENT`；`parentId` 为 `null` 表示顶层节点；递归返回所有子节点。

#### 2.2.2 创建工厂节点 (Create Plant Node)

* **URL**: `POST /api/v1/plant-nodes`
* **权限**: 管理层（系统管理员）
* **Request Body**:
  ```json
  {
    "name": "常减压装置-单元B",
    "type": "UNIT",
    "parentId": "uuid-xxx"
  }
  ```
* **Response (201 Created)**:
  ```json
  {
    "data": {
      "id": "uuid-new",
      "name": "常减压装置-单元B",
      "type": "UNIT",
      "parentId": "uuid-xxx"
    }
  }
  ```
* **说明**：`type` 枚举值为 `FACTORY`/`UNIT`/`EQUIPMENT`；创建 `FACTORY` 类型节点时 `parentId` 为 `null`；节点名称在同一父节点下唯一；创建操作记录审计日志。

#### 2.2.3 更新工厂节点 (Update Plant Node)

* **URL**: `PUT /api/v1/plant-nodes/{nodeId}`
* **权限**: 管理层（系统管理员）
* **Path Parameters**:
  * `nodeId` (UUID, required): 工厂节点 ID
* **Request Body**:
  ```json
  {
    "name": "常减压装置-单元B（更新名称）"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "success": true
    }
  }
  ```
* **说明**：仅支持更新节点名称，节点类型与父子关系不可变更；更新操作记录审计日志。

#### 2.2.4 删除工厂节点 (Delete Plant Node)

* **URL**: `DELETE /api/v1/plant-nodes/{nodeId}`
* **权限**: 管理层（系统管理员）
* **Path Parameters**:
  * `nodeId` (UUID, required): 工厂节点 ID
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "success": true
    }
  }
  ```
* **说明**：节点存在子节点或关联回路时禁止删除，返回 400 错误并提示"该节点存在子节点或关联回路，无法删除"；删除操作记录审计日志。

#### 2.2.5 获取 AAS 同步的 Tag 列表 (List AAS Tags)

* **URL**: `GET /api/v1/aas/tags`
* **权限**: 执行层及以上（仪控工程师/系统管理员）
* **Query Parameters**:
  * `keyword` (String, optional): 按 tag 名/描述模糊查询
  * `quality` (String, optional): 按质量码筛选，枚举值 `Good`, `Bad`, `Uncertain`
  * `associated` (Boolean, optional): 是否已关联回路，`true` 仅返回已关联，`false` 仅返回未关联
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "tagId": "uuid-xxx",
          "tagName": "101-FC-1023.PV",
          "description": "常减压装置进料流量 PV",
          "currentValue": 50.2,
          "quality": "Good",
          "lastSyncAt": "2026-06-20T10:00:00Z",
          "associatedLoopId": "uuid-yyy",
          "associatedLoopTagName": "101-FC-1023"
        }
      ],
      "total": 10234,
      "page": 1,
      "pageSize": 20,
      "lastSyncAt": "2026-06-20T10:00:00Z",
      "syncStatus": "SUCCESS"
    }
  }
  ```
* **说明**：`quality` 字段仅对 PV 类型 tag 有意义，非 PV tag 该字段为 `null`。

#### 2.2.6 触发 AAS Tag 手动同步 (Trigger AAS Sync)

* **URL**: `POST /api/v1/aas/sync`
* **权限**: 执行层及以上（仪控工程师/系统管理员）
* **Request Body**: 无（采用默认同步配置）
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx"
    }
  }
  ```
* **说明**：触发后台异步任务，从 AAS 拉取所有 OPC tag 位号信息（tag 名/描述/当前值/数据质量）写入 `tag_registry` 表。同步失败时保留上一周期有效数据并告警，不阻塞回路监控页面读取。

#### 2.2.7 获取回路列表 (List Loops)

* **URL**: `GET /api/v1/loops`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按装置/单元筛选
  * `controlMode` (String, optional): 按控制方式筛选，枚举值 `Manual`, `Auto`, `Cascade`
  * `isActive` (Boolean, optional): 按启用状态筛选
  * `status` (String, optional): 按回路状态筛选，枚举值 `READY`, `PARTIAL`, `INACTIVE`
  * `keyword` (String, optional): 按回路位号/描述模糊查询
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "loopId": "uuid-xxx",
          "tagName": "101-FC-1023",
          "description": "常减压装置进料流量控制",
          "unitId": "uuid-yyy",
          "unitName": "常减压装置-单元A",
          "controlMode": "Auto",
          "isActive": true,
          "status": "READY",
          "score": 78.6,
          "lastScoreAt": "2026-06-20T09:00:00Z",
          "tagMappingStatus": {
            "pv": true,
            "sp": true,
            "op": true,
            "mode": true,
            "pid_p": true,
            "pid_i": true,
            "pid_d": false
          }
        }
      ],
      "total": 156,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：`controlMode` 从 MODE tag 只读读取；`tagMappingStatus` 标识 7 个 OPC tag 的关联完整性，PV/SP/OP/MODE 任一缺失时 `status` 为 `PARTIAL`；`status` 枚举值对齐实现契约 v2.0 §4.6：`READY`（就绪，必填 tag 完整）/`PARTIAL`（部分配置，必填 tag 缺失）/`INACTIVE`（已停用，isActive=false）。

#### 2.2.8 创建回路 (Create Loop)

* **URL**: `POST /api/v1/loops`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN）
* **Request Body**:
  ```json
  {
    "tagName": "101-FC-1024",
    "description": "常减压装置塔顶温度控制",
    "unitId": "uuid-yyy",
    "controlType": "STABLE",
    "importanceLevel": "MEDIUM",
    "includeInEvaluation": true,
    "scoreWeights": {
      "accuracy_rate": 40,
      "fast_rate": 30,
      "steady_rate": 30
    },
    "isActive": true,
    "remark": "新增回路，待关联 tag"
  }
  ```
* **Response (201 Created)**:
  ```json
  {
    "data": {
      "loopId": "uuid-new",
      "tagName": "101-FC-1024",
      "description": "常减压装置塔顶温度控制",
      "unitId": "uuid-yyy",
      "controlType": "STABLE",
      "importanceLevel": "MEDIUM",
      "includeInEvaluation": true,
      "status": "PARTIAL",
      "isActive": true,
      "scoreWeights": {
        "accuracy_rate": 40,
        "fast_rate": 30,
        "steady_rate": 30
      },
      "commissioningMetric": {
        "metricKey": "effective_auto_rate",
        "isDiscountFactor": true,
        "discountFactor": 1.0
      },
      "remark": "新增回路，待关联 tag",
      "createdAt": "2026-06-20T10:00:00Z",
      "createdBy": "zhang.san"
    }
  }
  ```
* **说明**：回路位号在所属单元内唯一；新建回路默认状态为 `PARTIAL`（待关联 tag）；`scoreWeights` 仅含 3 项核心指标（accuracy_rate/fast_rate/steady_rate，权重和=100），投用指标 `effective_auto_rate` 作为折扣因子 R 不纳入权重和（对齐 §2.8 3+1+8 体系）；`scoreWeights` 可不传，缺省继承自性能指标配置默认值，权重总和须为 100%，否则返回 `ERR_METRIC_WEIGHT_SUM`；`controlType` 枚举值 `STABLE`/`SLOW`/`FAST`/`LOGIC`；`importanceLevel` 枚举值 `HIGH`/`MEDIUM`/`LOW`；`includeInEvaluation` 为 `false` 时该回路不参与装置级 KPI 汇总。v6.1 新增 `complexLoopGroupId`/`complexRole` 可选字段（通常通过 §2.2.16 批量分组 API 建立，不建议在创建回路时直接指定）。

#### 2.2.9 获取回路详情 (Get Loop Detail)

* **URL**: `GET /api/v1/loops/{loopId}`
* **权限**: 查看层及以上（所有角色可访问）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "basicInfo": {
        "loopId": "uuid-xxx",
        "tagName": "101-FC-1023",
        "description": "常减压装置进料流量控制",
        "unitId": "uuid-yyy",
        "unitName": "常减压装置-单元A",
        "controlType": "STABLE",
        "importanceLevel": "HIGH",
        "includeInEvaluation": true,
        "isActive": true,
        "status": "READY",
        "scoreWeights": {
          "accuracy_rate": 40,
          "fast_rate": 30,
          "steady_rate": 30
        },
        "commissioningMetric": {
          "metricKey": "effective_auto_rate",
          "isDiscountFactor": true,
          "discountFactor": 0.92
        },
        "remark": "重点监控回路",
        "createdAt": "2026-06-01T10:00:00Z",
        "createdBy": "zhang.san",
        "updatedAt": "2026-06-19T15:30:00Z",
        "updatedBy": "li.si"
      },
      "tagMapping": {
        "pv": { "tagId": "uuid-pv", "tagName": "101-FC-1023.PV", "required": true, "associated": true },
        "sp": { "tagId": "uuid-sp", "tagName": "101-FC-1023.SP", "required": true, "associated": true },
        "op": { "tagId": "uuid-op", "tagName": "101-FC-1023.OP", "required": true, "associated": true },
        "mode": { "tagId": "uuid-mode", "tagName": "101-FC-1023.MODE", "required": true, "associated": true },
        "pid_p": { "tagId": "uuid-pp", "tagName": "101-FC-1023.PID_P", "required": false, "associated": true },
        "pid_i": { "tagId": "uuid-pi", "tagName": "101-FC-1023.PID_I", "required": false, "associated": true },
        "pid_d": { "tagId": null, "tagName": null, "required": false, "associated": false }
      },
      "runtimeParams": {
        "controlMode": "Auto",
        "pidP": 1.2,
        "pidI": 0.5,
        "pidD": 0.0,
        "readAt": "2026-06-20T10:00:00Z"
      },
      "aasSyncStatus": {
        "lastSyncAt": "2026-06-20T09:55:00Z",
        "associatedTagCount": 6
      }
    }
  }
  ```
* **说明**：`runtimeParams`（控制方式/PID 参数）从对应 tag 只读读取，不可手动编辑；`tagMapping` 标识 7 个 OPC tag 的关联状态及必填属性。

#### 2.2.10 更新回路 (Update Loop)

* **URL**: `PUT /api/v1/loops/{loopId}`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Request Body**:
  ```json
  {
    "description": "常减压装置进料流量控制（更新描述）",
    "scoreWeights": {
      "accuracy_rate": 45,
      "fast_rate": 25,
      "steady_rate": 30
    },
    "isActive": true,
    "remark": "更新备注：权重调整"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "description": "常减压装置进料流量控制（更新描述）",
      "scoreWeights": {
        "accuracy_rate": 45,
        "fast_rate": 25,
        "steady_rate": 30
      },
      "isActive": true,
      "remark": "更新备注：权重调整",
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "zhang.san"
    }
  }
  ```
* **说明**：仅支持更新扩展配置（描述/评分权重/启用状态/备注），回路位号与所属单元不可变更；`scoreWeights` 仅含 3 项核心指标（accuracy_rate/fast_rate/steady_rate，权重和=100），权重总和须为 100%，否则返回 `ERR_METRIC_WEIGHT_SUM`；变更记录审计日志。

#### 2.2.11 删除回路 (Delete Loop)

* **URL**: `DELETE /api/v1/loops/{loopId}`
* **权限**: 管理层（ADMIN）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "deleted": true,
      "deletedAt": "2026-06-20T10:30:00Z"
    }
  }
  ```
* **说明**：删除回路时同步解除所有 tag 关联关系；历史 KPI 快照与诊断记录保留（按合规要求），仅标记回路为已删除；删除操作记录审计日志。

#### 2.2.12 获取回路关联的 Tag 列表 (Get Loop Tags)

* **URL**: `GET /api/v1/loops/{loopId}/tags`
* **权限**: 查看层及以上（所有角色可访问）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "tagName": "101-FC-1023",
      "status": "Ready",
      "tags": [
        {
          "role": "PV",
          "tagId": "uuid-pv",
          "tagName": "101-FC-1023.PV",
          "description": "过程变量测量值",
          "required": true,
          "associated": true,
          "currentValue": 50.2,
          "quality": "Good",
          "lastSyncAt": "2026-06-20T09:55:00Z"
        },
        {
          "role": "SP",
          "tagId": "uuid-sp",
          "tagName": "101-FC-1023.SP",
          "description": "设定值",
          "required": true,
          "associated": true,
          "currentValue": 50.0,
          "quality": null,
          "lastSyncAt": "2026-06-20T09:55:00Z"
        },
        {
          "role": "OP",
          "tagId": "uuid-op",
          "tagName": "101-FC-1023.OP",
          "description": "控制器输出值",
          "required": true,
          "associated": true,
          "currentValue": 45.8,
          "quality": null,
          "lastSyncAt": "2026-06-20T09:55:00Z"
        },
        {
          "role": "MODE",
          "tagId": "uuid-mode",
          "tagName": "101-FC-1023.MODE",
          "description": "控制模式",
          "required": true,
          "associated": true,
          "currentValue": 1,
          "quality": null,
          "lastSyncAt": "2026-06-20T09:55:00Z"
        },
        {
          "role": "PID_P",
          "tagId": "uuid-pp",
          "tagName": "101-FC-1023.PID_P",
          "description": "比例参数",
          "required": false,
          "associated": true,
          "currentValue": 1.2,
          "quality": null,
          "lastSyncAt": "2026-06-20T09:55:00Z"
        },
        {
          "role": "PID_I",
          "tagId": "uuid-pi",
          "tagName": "101-FC-1023.PID_I",
          "description": "积分参数",
          "required": false,
          "associated": true,
          "currentValue": 0.5,
          "quality": null,
          "lastSyncAt": "2026-06-20T09:55:00Z"
        },
        {
          "role": "PID_D",
          "tagId": null,
          "tagName": null,
          "description": null,
          "required": false,
          "associated": false,
          "currentValue": null,
          "quality": null,
          "lastSyncAt": null
        }
      ]
    }
  }
  ```
* **说明**：`role` 枚举值为 `PV`/`SP`/`OP`/`MODE`/`PID_P`/`PID_I`/`PID_D`；`quality` 仅 PV 角色有值，其他角色为 `null`。

#### 2.2.13 更新回路 Tag 关联 (Update Loop Tag Mapping)

* **URL**: `PUT /api/v1/loops/{loopId}/tags`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Request Body**:
  ```json
  {
    "pv": "uuid-pv",
    "sp": "uuid-sp",
    "op": "uuid-op",
    "mode": "uuid-mode",
    "pid_p": "uuid-pp",
    "pid_i": "uuid-pi",
    "pid_d": null
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "status": "READY",
      "tags": [
        { "role": "PV", "tagId": "uuid-pv", "tagName": "101-FC-1023.PV", "required": true, "associated": true },
        { "role": "SP", "tagId": "uuid-sp", "tagName": "101-FC-1023.SP", "required": true, "associated": true },
        { "role": "OP", "tagId": "uuid-op", "tagName": "101-FC-1023.OP", "required": true, "associated": true },
        { "role": "MODE", "tagId": "uuid-mode", "tagName": "101-FC-1023.MODE", "required": true, "associated": true },
        { "role": "PID_P", "tagId": "uuid-pp", "tagName": "101-FC-1023.PID_P", "required": false, "associated": true },
        { "role": "PID_I", "tagId": "uuid-pi", "tagName": "101-FC-1023.PID_I", "required": false, "associated": true },
        { "role": "PID_D", "tagId": null, "tagName": null, "required": false, "associated": false }
      ],
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "zhang.san"
    }
  }
  ```
* **说明**：
  * 请求体为 7 个 tag 角色与 `tag_registry` 中 tagId 的映射，未关联的角色传 `null`。
  * PV/SP/OP/MODE 为必填 tag，缺失时回路状态变更为 `PARTIAL` 并标红提示，但 API 调用本身成功（不报错），由前端根据 `status` 提示。
  * 若 tagId 在 `tag_registry` 中不存在，返回 `ERR_TAG_NOT_FOUND`。
  * 若请求体中未提供任何必填 tag（pv/sp/op/mode 全为 null 或缺失），返回 `ERR_LOOP_TAG_REQUIRED`。
  * Tag 关联变更记录审计日志。

#### 2.2.14 获取回路运行态数据 (Get Loop Monitor Data)

* **URL**: `GET /api/v1/loops/{loopId}/monitor`
* **权限**: 查看层及以上（所有角色可访问）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Query Parameters**:
  * `trendWindow` (String, default=`last_24_hours`): 趋势数据时间窗，枚举值 `last_1_hour`, `last_24_hours`, `last_7_days`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "tagName": "101-FC-1023",
      "currentValues": {
        "pv": 50.2,
        "sp": 50.0,
        "op": 45.8,
        "mode": 1,
        "modeLabel": "Auto",
        "pvQuality": "Good",
        "readAt": "2026-06-20T10:00:00Z"
      },
      "runtimeParams": {
        "controlMode": "Auto",
        "pidP": 1.2,
        "pidI": 0.5,
        "pidD": 0.0
      },
      "trend": {
        "timestamps": [1623912000000, 1623912900000, 1623913800000],
        "pv": [50.1, 50.2, 50.1],
        "sp": [50.0, 50.0, 50.0],
        "op": [45.5, 45.8, 45.7],
        "mode": [1, 1, 1],
        "pvQuality": ["Good", "Good", "Bad"]
      },
      "kpiSummary": {
        "accuracy_rate": 88.1,
        "fast_rate": 76.5,
        "steady_rate": 85.3,
        "effective_auto_rate": 92.5,
        "score": 78.6,
        "status": "WARNING",
        "snapshotStatus": "SUCCESS",
        "algorithm_version": "KPI_CALC_v1.0",
        "calculatedAt": "2026-06-20T09:00:00Z"
      }
    }
  }
  ```
* **说明**：`currentValues` 为当前最新值快照；`trend` 为趋势数据，`pvQuality` 数组与 `pv` 数组等长，仅 PV 携带质量码；`kpiSummary` 包含 3 项核心指标（accuracy_rate/fast_rate/steady_rate）+ 1 项投用指标（effective_auto_rate，作为折扣因子 R）+ `score`（综合评分，0-100）+ `status`（5 级性能定级：EXCELLENT/GOOD/FAIR/WARNING/POOR）+ `snapshotStatus`（KPI 快照状态：SUCCESS/PARTIAL/INCONCLUSIVE，对齐实现契约 v2.0 §4.6）+ `algorithm_version`。回路处于 `INACTIVE` 或数据不足时，`snapshotStatus` 为 `INCONCLUSIVE`，波形区灰色虚线断线。

#### 2.2.15 回路监控列表 (List Loop Monitor)

* **URL**: `GET /api/v1/loops/monitor`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按装置/单元筛选
  * `view` (String, default=`list`): 视图模式，枚举值 `list`（列表视图）, `card`（卡片视图）
  * `keyword` (String, optional): 按回路位号/描述模糊查询
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "view": "list",
      "items": [
        {
          "loopId": "uuid-xxx",
          "tagName": "101-FC-1023",
          "description": "常减压装置进料流量控制",
          "unitName": "常减压装置-单元A",
          "pvRange": { "min": 0.0, "max": 100.0 },
          "pvUnit": "℃",
          "opRange": { "min": 0.0, "max": 100.0 },
          "opUnit": "%",
          "currentValues": {
            "pv": 50.2,
            "sp": 50.0,
            "op": 45.8,
            "mode": 1,
            "modeLabel": "Auto",
            "pvQuality": "Good"
          },
          "controlMode": "Auto",
          "score": 78.6,
          "status": "READY",
          "isActive": true,
          "readAt": "2026-06-20T10:00:00Z"
        }
      ],
      "total": 156,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：列表视图与卡片视图返回数据结构一致，仅前端渲染方式不同；回路处于 `PARTIAL` 时卡片置灰，悬浮提示缺失项；`status` 枚举值对齐实现契约 v2.0 §4.6：`READY`/`PARTIAL`/`INACTIVE`。
  v6.1 新增 `pvRange`/`pvUnit`/`opRange`/`opUnit` 字段（从关联 Tag 引用，不冗余存储），用于在列表中展示量程和单位。
  v6.1 新增 `complexLoopGroupId`/`complexRole` 字段（可空），用于回路列表展示分组状态。

#### 2.2.16 批量建立复杂回路分组 (Batch Group Loops) [v6.1 新增]

* **URL**: `POST /api/v1/loops/batch-grouping`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN）
* **说明**: 将 2-20 个回路归为一个复杂控制回路（如串级/超驰），系统自动生成分组 UUID，并按 `mainLoopId` 指定主回路（MAIN），其余回路设为副回路（SUB）。每个分组仅允许一个 MAIN。
* **Request Body**:
  ```json
  {
    "loopIds": ["uuid-001", "uuid-002", "uuid-003"],
    "mainLoopId": "uuid-001"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "code": "0",
    "message": "批量分组成功",
    "data": {
      "groupId": "uuid-group",
      "affected": 3,
      "assignments": [
        { "loopId": "uuid-001", "tagName": "TIC-101", "role": "MAIN" },
        { "loopId": "uuid-002", "tagName": "FIC-102", "role": "SUB" },
        { "loopId": "uuid-003", "tagName": "LIC-103", "role": "SUB" }
      ]
    }
  }
  ```
* **校验规则**:
  * `loopIds` 须包含 2-20 个回路 ID，否则返回 `ERR_VALIDATION`（422）。
  * `mainLoopId` 须在 `loopIds` 列表中，否则返回 `ERR_COMPLEX_GROUP_MAIN_NOT_IN_LIST`（400）。
  * 所有 `loopIds` 须存在且属于同一工艺单元（最佳实践，非强制）。
  * 分组建立后，原回路若已属于其他分组，将被覆盖为新分组成员。
* **关联错误码**: `ERR_COMPLEX_GROUP_MAIN_NOT_IN_LIST`、`ERR_LOOP_NOT_FOUND`、`ERR_PERMISSION_DENIED`

#### 2.2.17 获取复杂回路分组列表 (List Complex Groups) [v6.1 新增]

* **URL**: `GET /api/v1/loops/complex-groups`
* **权限**: 查看层及以上（所有角色可访问）
* **说明**: 返回当前系统中所有复杂回路分组，按 `groupId` 聚合，每组包含主回路和副回路列表。用于回路管理页展示分组概览。
* **Response (200 OK)**:
  ```json
  {
    "code": "0",
    "data": [
      {
        "groupId": "uuid-group-1",
        "mainLoop": {
          "loopId": "uuid-001",
          "tagName": "TIC-101",
          "description": "反应器入口温度"
        },
        "subLoops": [
          {
            "loopId": "uuid-002",
            "tagName": "FIC-102",
            "description": "进料流量"
          }
        ],
        "memberCount": 2
      }
    ]
  }
  ```
* **说明**: 仅返回 `complex_loop_group_id IS NOT NULL` 的回路；未分组的单回路不在此接口返回。`mainLoop` 为 `complex_role = MAIN` 的回路；若某分组 MAIN 缺席（异常状态），`mainLoop` 为 `null`。

---

### 2.3 性能评估 API (Performance)

本组 API 自包含"指标配置 → 引擎规则 → 全量计算 → 全局看板 → 低效排行 → 统计分析"全流程。配置类 API 仅系统管理员可调用，越权返回 `ERR_CONFIG_FORBIDDEN`。

#### 2.3.1 获取全局看板数据 (Get Performance Board)

* **URL**: `GET /api/v1/performance/board`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按工厂/装置/单元过滤
  * `timeWindow` (String, default=`today`): 时间窗，枚举值 `today`, `yesterday`, `last_7_days`, `last_30_days`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "filterScope": {
        "plantNodeId": null,
        "plantNodeName": "全厂",
        "timeWindow": "today"
      },
      "kpiCards": [
        {
          "metricKey": "accuracy_rate",
          "metricName": "准确率",
          "category": "CORE",
          "value": 88.1,
          "unit": "%",
          "status": "GOOD",
          "algorithmVersion": "KPI_CALC_v1.0"
        },
        {
          "metricKey": "fast_rate",
          "metricName": "快速率",
          "category": "CORE",
          "value": 76.5,
          "unit": "%",
          "status": "FAIR",
          "algorithmVersion": "KPI_CALC_v1.0"
        },
        {
          "metricKey": "steady_rate",
          "metricName": "稳定率",
          "category": "CORE",
          "value": 85.3,
          "unit": "%",
          "status": "WARNING",
          "algorithmVersion": "KPI_CALC_v1.0"
        },
        {
          "metricKey": "effective_auto_rate",
          "metricName": "有效自控率",
          "category": "COMMISSIONING",
          "isDiscountFactor": true,
          "value": 92.5,
          "unit": "%",
          "status": "GOOD",
          "algorithmVersion": "KPI_CALC_v1.0"
        },
        {
          "metricKey": "score",
          "metricName": "综合评分",
          "value": 78.6,
          "unit": "",
          "status": "WARNING",
          "algorithmVersion": "SCORE_CALC_v1.0"
        }
      ],
      "kpiSummary": {
        "accuracy_rate": 88.1,
        "fast_rate": 76.5,
        "steady_rate": 85.3,
        "effective_auto_rate": 92.5,
        "discountFactor": 0.925,
        "score": 78.6,
        "status": "WARNING",
        "snapshotStatus": "SUCCESS",
        "algorithm_version": "KPI_CALC_v1.0"
      },
      "steadyRateTrend": {
        "timestamps": ["2026-06-14", "2026-06-15", "2026-06-16", "2026-06-17"],
        "values": [86.1, 85.9, 85.5, 85.3]
      },
      "partialWarning": {
        "active": true,
        "inconclusiveCount": 3,
        "partialCount": 5,
        "message": "部分数据缺失，3 条回路数据不足，5 条回路 tag 关联不完整"
      }
    }
  }
  ```
* **说明**：KPI 卡片采用 3+1+8 体系（对齐 §2.8 与 FDS v6.0 §1.3）：固定返回 3 项核心指标（accuracy_rate/fast_rate/steady_rate）+ 1 项投用指标（effective_auto_rate，作为折扣因子 R）+ 综合评分（score），辅助诊断指标通过 §2.3.3 单独查询；每卡 `status` 枚举值为 5 级性能定级（`EXCELLENT`/`GOOD`/`FAIR`/`WARNING`/`POOR`，对齐 FDS v6.0 §1.3）；`kpiSummary` 汇总 3 核心指标 + 1 投用指标 + `discountFactor`（折扣因子，0-1）+ `score`（综合评分 = 核心指标加权得分 × 折扣因子）+ `status`（5 级性能定级）+ `snapshotStatus`（KPI 快照状态：SUCCESS/PARTIAL/INCONCLUSIVE，对齐实现契约 v2.0 §4.6）+ `algorithm_version`；指标停用后对应卡片 `status` 为 `INCONCLUSIVE`；`partialWarning` 在存在 `INCONCLUSIVE` 或 `PARTIAL` 回路时激活，强制显示黄色警告横幅。

#### 2.3.2 获取低效回路排行 (Get Performance Ranking)

* **URL**: `GET /api/v1/performance/ranking`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按装置/单元过滤
  * `timeWindow` (String, required): 时间窗，枚举值 `today`, `yesterday`, `last_7_days`, `last_30_days`
  * `limit` (Integer, default=10): 返回条数，最大 100
  * `sortBy` (String, default=`score`): 排序字段，枚举值 `score`, `steady_rate`, `accuracy_rate`, `fast_rate`
  * `sortOrder` (String, default=`asc`): 排序方向，枚举值 `asc`, `desc`
* **Response (200 OK)**:
  ```json
  {
    "data": [
      {
        "rank": 1,
        "loopId": "uuid-xxx",
        "tagName": "101-FC-1023",
        "unitName": "常减压装置-单元A",
        "compositeScore": 45.2,
        "accuracyRate": 65.0,
        "fastRate": 50.5,
        "steadyRate": 60.5,
        "effectiveAutoRate": 80.0,
        "discountFactor": 0.80,
        "status": "POOR",
        "snapshotStatus": "SUCCESS",
        "algorithmVersion": "KPI_CALC_v1.0",
        "preDiagnosis": "疑似阀门粘滞",
        "actionStatus": "PENDING"
      }
    ]
  }
  ```
* **说明**：默认按评分升序返回 Top N 低效回路；响应包含 3 项核心指标（accuracy_rate/fast_rate/steady_rate）+ 1 项投用指标（effective_auto_rate）+ `discountFactor`（折扣因子）+ `compositeScore`（综合评分 = 核心指标加权得分 × 折扣因子）+ `status`（5 级性能定级：EXCELLENT/GOOD/FAIR/WARNING/POOR）+ `snapshotStatus`（KPI 快照状态：SUCCESS/PARTIAL/INCONCLUSIVE）+ `algorithmVersion`；`actionStatus` 来自异常跟踪子模块（待处理/处理中/已实施/已忽略，对齐实现契约 v2.0 §4.6）。

#### 2.3.3 获取性能指标配置列表 (List Performance Metrics)

* **URL**: `GET /api/v1/performance/metrics`
* **权限**: 查看层及以上（所有角色可查看配置，仅系统管理员可编辑）
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "coreMetrics": [
        {
          "metricId": "uuid-c01",
          "metricKey": "accuracy_rate",
          "metricName": "准确率",
          "category": "CORE",
          "formula": "duration(abs(pv - sp) <= pv_range * 0.05) / duration(*) * 100",
          "weight": 40,
          "threshold": { "min": 0, "max": 100, "alert": 80 },
          "gradingThresholds": {
            "EXCELLENT": 95,
            "GOOD": 85,
            "FAIR": 75,
            "WARNING": 65,
            "POOR": 0
          },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "PV 偏离 SP 在 5% 量程内的时长占比（核心指标）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-c02",
          "metricKey": "fast_rate",
          "metricName": "快速率",
          "category": "CORE",
          "formula": "duration(rise_time <= rise_time_threshold) / duration(*) * 100",
          "weight": 30,
          "threshold": { "min": 0, "max": 100, "alert": 75 },
          "gradingThresholds": {
            "EXCELLENT": 90, "GOOD": 80, "FAIR": 70, "WARNING": 60, "POOR": 0
          },
          "controlType": "FAST",
          "isEnabled": true,
          "description": "设定值变化后 PV 响应速度达标的时长占比（核心指标）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-c03",
          "metricKey": "steady_rate",
          "metricName": "稳定率",
          "category": "CORE",
          "formula": "duration(abs(pv - sp) <= pv_range * 0.02) / duration(*) * 100",
          "weight": 30,
          "threshold": { "min": 0, "max": 100, "alert": 85 },
          "gradingThresholds": {
            "EXCELLENT": 95, "GOOD": 85, "FAIR": 75, "WARNING": 65, "POOR": 0
          },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "PV 偏离 SP 在 2% 量程内的时长占比（核心指标）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        }
      ],
      "commissioningMetric": {
        "metricId": "uuid-m01",
        "metricKey": "effective_auto_rate",
        "metricName": "有效自控率",
        "category": "COMMISSIONING",
        "isDiscountFactor": true,
        "formula": "sum(mode in [Auto, Cascade] AND pvQuality == Good) / count(*) * 100",
        "weight": null,
        "threshold": { "min": 0, "max": 100, "alert": 90 },
        "gradingThresholds": {
          "EXCELLENT": 95, "GOOD": 85, "FAIR": 70, "WARNING": 50, "POOR": 0
        },
        "controlType": "STABLE",
        "isEnabled": true,
        "description": "控制器处于自动/串级模式且 PV 质量良好的时长占比，作为综合评分折扣因子（投用指标）",
        "algorithmVersion": "KPI_CALC_v1.0",
        "updatedAt": "2026-06-25T10:00:00Z",
        "updatedBy": "admin"
      },
      "auxiliaryDiagnosticMetrics": [
        {
          "metricId": "uuid-a01",
          "metricKey": "good_value_rate",
          "metricName": "好值率",
          "category": "AUXILIARY_DIAGNOSTIC",
          "weight": null,
          "isEnabled": true,
          "algorithmVersion": "KPI_CALC_v1.0"
        },
        {
          "metricId": "uuid-a02",
          "metricKey": "oscillation_rate",
          "metricName": "振荡率",
          "category": "AUXILIARY_DIAGNOSTIC",
          "weight": null,
          "isEnabled": true,
          "algorithmVersion": "KPI_CALC_v1.0"
        },
        {
          "metricId": "uuid-a03",
          "metricKey": "saturation_rate",
          "metricName": "饱和率",
          "category": "AUXILIARY_DIAGNOSTIC",
          "weight": null,
          "isEnabled": true,
          "algorithmVersion": "KPI_CALC_v1.0"
        }
      ],
      "coreTotalWeight": 100,
      "coreWeightValid": true,
      "structureVersion": "3+1+8"
    }
  }
  ```
* **说明**：返回 3+1+8 体系指标配置（对齐 §2.8 与 FDS v6.0 §1.3）：3 项核心指标（CORE）参与权重配置 + 1 项投用指标（COMMISSIONING）作为折扣因子 + 8 项辅助诊断指标（AUXILIARY_DIAGNOSTIC）不参与权重配置；`category` 枚举值 `CORE`/`COMMISSIONING`/`AUXILIARY_DIAGNOSTIC`；`gradingThresholds` 为 5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR，对齐 FDS v6.0 §1.3）；投用指标的 `isDiscountFactor=true`、`weight=null`；`threshold` 字段为 JSONB 对象 `{min: number, max: number, alert: number}`；`controlType` 枚举值为 `STABLE`/`SLOW`/`FAST`/`LOGIC`，用于权重模板选择（对齐§4.7.3 默认权重配置）；`coreTotalWeight` 标识核心指标权重总和，`coreWeightValid` 标识是否为 100%；辅助诊断指标完整列表见 §2.8.1。

#### 2.3.4 更新性能指标配置 (Update Performance Metric)

* **URL**: `PUT /api/v1/performance/metrics/{metricId}`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Path Parameters**:
  * `metricId` (UUID, required): 指标 ID
* **Request Body**:
  ```json
  {
    "formula": "duration(abs(pv - sp) <= pv_range * 0.05) / duration(*) * 100",
    "weight": 45,
    "threshold": {
      "min": 0,
      "max": 100,
      "alert": 85
    },
    "gradingThresholds": {
      "EXCELLENT": 95,
      "GOOD": 85,
      "FAIR": 75,
      "WARNING": 65,
      "POOR": 0
    },
    "controlType": "STABLE",
    "isEnabled": true,
    "description": "更新准确率公式与权重"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "metricId": "uuid-c01",
      "metricKey": "accuracy_rate",
      "metricName": "准确率",
      "category": "CORE",
      "formula": "duration(abs(pv - sp) <= pv_range * 0.05) / duration(*) * 100",
      "weight": 45,
      "threshold": { "min": 0, "max": 100, "alert": 85 },
      "gradingThresholds": {
        "EXCELLENT": 95, "GOOD": 85, "FAIR": 75, "WARNING": 65, "POOR": 0
      },
      "controlType": "STABLE",
      "isEnabled": true,
      "description": "更新准确率公式与权重",
      "algorithmVersion": "KPI_CALC_v1.0",
      "updatedAt": "2026-06-26T10:30:00Z",
      "updatedBy": "admin"
    }
  }
  ```
* **说明**：
  * 配置变更即时生效，无需重启服务。
  * `threshold` 为 JSONB 对象，结构 `{min, max, alert}`（对齐 C3 修正）。
  * `gradingThresholds` 为 5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR，对齐 FDS v6.0 §1.3）；仅核心指标与投用指标可配置 `gradingThresholds`，辅助诊断指标不参与性能定级。
  * `controlType` 枚举值 `STABLE`/`SLOW`/`FAST`/`LOGIC`，用于权重模板选择。
  * `formula` 字段表达式引擎采用 `simpleeval` 安全沙箱（对齐《关键算法设计说明》§4.9 C7），可用变量：pv/sp/op/mode/pv_quality/timestamps/pv_range/n；可用函数：sum/mean/std/count/count_if/abs/sqrt/min/max/duration；禁止 import/exec/eval/属性访问，表达式长度限制 500 字符，执行超时 5 秒。
  * 后端二次校验权重总和：若本次变更导致 3 项核心指标（accuracy_rate/fast_rate/steady_rate）权重总和 ≠ 100%，返回 `ERR_METRIC_WEIGHT_SUM`。投用指标（effective_auto_rate）的 `weight` 字段固定为 `null`，传入非 null 值返回 `ERR_DISCOUNT_FACTOR_READONLY`；辅助诊断指标的 `weight` 字段固定为 `null`，传入非 null 值返回 `ERR_AUXILIARY_METRIC_WEIGHT_FORBIDDEN`。
  * 投用指标的 `isDiscountFactor` 字段不可修改，由系统固定为 `true`。
  * 指标停用后相关 KPI 显示 `INCONCLUSIVE`，不参与综合评分计算；核心指标停用导致权重重新归一化（其他核心指标按比例分配）。
  * 变更记录审计日志（操作人/时间/变更前后值）。

#### 2.3.5 获取引擎规则配置列表 (List Performance Rules)

* **URL**: `GET /api/v1/performance/rules`
* **权限**: 查看层及以上（所有角色可查看配置，仅系统管理员可编辑）
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "ruleId": "uuid-xxx",
          "ruleName": "评估引擎主规则",
          "calcPeriod": "1h",
          "dataFetchWindow": "1h",
          "scheduleConcurrency": 10,
          "isEnabled": true,
          "lastExecutedAt": "2026-06-20T09:00:00Z",
          "lastExecutionStatus": "SUCCESS",
          "lastExecutionDuration": 125,
          "processedLoopCount": 156,
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        }
      ]
    }
  }
  ```
* **说明**：返回评估引擎的计算周期、数据拉取规则、调度参数等配置；同时返回引擎最近一次执行状态。

#### 2.3.6 更新引擎规则配置 (Update Performance Rule)

* **URL**: `PUT /api/v1/performance/rules/{ruleId}`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Path Parameters**:
  * `ruleId` (UUID, required): 规则 ID
* **Request Body**:
  ```json
  {
    "calcPeriod": "0.5h",
    "dataFetchWindow": "0.5h",
    "scheduleConcurrency": 20,
    "isEnabled": true
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "ruleId": "uuid-xxx",
      "ruleName": "评估引擎主规则",
      "calcPeriod": "0.5h",
      "dataFetchWindow": "0.5h",
      "scheduleConcurrency": 20,
      "isEnabled": true,
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "admin"
    }
  }
  ```
* **说明**：规则变更需前端二次确认；变更即时生效，影响下一批次计算；变更记录审计日志。

#### 2.3.7 获取性能统计报表数据 (Get Performance Analytics)

* **URL**: `GET /api/v1/performance/analytics`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `startTime` (ISO8601, required): 开始时间
  * `endTime` (ISO8601, required): 结束时间
  * `plantNodeId` (UUID, optional): 按装置/单元筛选
  * `metricKey` (String, optional): 按指标筛选，枚举值 `accuracy_rate`, `fast_rate`, `steady_rate`, `effective_auto_rate`, `score`
  * `granularity` (String, default=`day`): 统计粒度，枚举值 `hour`, `day`, `week`, `month`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "filterScope": {
        "startTime": "2026-06-01T00:00:00Z",
        "endTime": "2026-06-20T00:00:00Z",
        "plantNodeId": null,
        "metricKey": "score",
        "granularity": "day"
      },
      "kpiTrend": {
        "timestamps": ["2026-06-01", "2026-06-02", "2026-06-03"],
        "series": [
          {
            "metricKey": "score",
            "metricName": "综合评分",
            "values": [76.5, 77.0, 78.0]
          }
        ]
      },
      "unitRanking": [
        { "unitId": "uuid-xxx", "unitName": "常减压装置-单元A", "score": 82.3, "loopCount": 25 },
        { "unitId": "uuid-yyy", "unitName": "催化裂化装置-单元B", "score": 75.1, "loopCount": 18 }
      ],
      "badActorDistribution": [
        { "label": "疑似阀门粘滞", "count": 8 },
        { "label": "参数过激", "count": 5 },
        { "label": "振荡", "count": 3 }
      ]
    }
  }
  ```
* **说明**：返回 KPI 趋势对比折线图、装置评分排名柱状图、差等生分布饼图所需数据；筛选结果为空时对应字段返回空数组。

#### 2.3.8 导出性能统计报表 (Export Performance Analytics)

* **URL**: `POST /api/v1/performance/analytics/export`
* **权限**: 查看层及以上（所有角色可访问）
* **Request Body**:
  ```json
  {
    "startTime": "2026-06-01T00:00:00Z",
    "endTime": "2026-06-20T00:00:00Z",
    "plantNodeId": null,
    "metricKey": "score",
    "granularity": "day",
    "format": "xlsx"
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx"
    }
  }
  ```
* **说明**：`format` 枚举值 `csv`, `xlsx`；后台服务合并图表截图与数据表格生成导出文件，文件名规范 `CLPM-性能统计报表-[装置]-[日期范围].xlsx`；导出失败时任务状态标记为 `FAILED`，前端提示"报表生成异常，请重试"。

---

### 2.4 诊断中心 API (Diagnosis)

本组 API 自包含"指标配置 → 诊断列表 → 诊断详情 → 异常跟踪 → 统计分析"全流程。Action Tracker 在 v3.0 中降级为本模块的子模块。配置类 API 仅系统管理员可调用，越权返回 `ERR_CONFIG_FORBIDDEN`。

#### 2.4.1 获取诊断列表 (Get Diagnosis List)

* **URL**: `GET /api/v1/diagnosis/list`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按装置/单元筛选
  * `diagnosisLabel` (String, optional): 按诊断标签筛选，枚举值 `OSCILLATION`, `VALVE_STICTION`, `OVERAGGRESSIVE`, `OVERCONSERVATIVE`, `EXTERNAL_DISTURBANCE`, `QUALITY_ABNORMAL`, `OUTPUT_SATURATION`, `MANUAL_REVIEW`
  * `actionStatus` (String, optional): 按处理状态筛选，枚举值 `PENDING`, `IN_PROGRESS`, `IMPLEMENTED`, `IGNORED`
  * `timeWindow` (String, default=`last_7_days`): 时间窗，枚举值 `today`, `yesterday`, `last_7_days`, `last_30_days`
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "loopId": "uuid-xxx",
          "tagName": "101-FC-1023",
          "unitName": "常减压装置-单元A",
          "compositeScore": 45.2,
          "diagnosisLabel": "VALVE_STICTION",
          "labelName": "阀门粘滞",
          "confidence": 0.85,
          "fusedConfidence": 0.82,
          "algorithm": "STICTION_CH_v1.0",
          "actionStatus": "PENDING",
          "diagnosedAt": "2026-06-20T08:00:00Z",
          "algorithmVersion": "STICTION_CH_v1.0"
        }
      ],
      "total": 23,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：返回评分跌破阈值的回路诊断列表；`diagnosisLabel` 枚举为 8 类（对齐《关键算法设计说明》§5.0 C6 修正）：`OSCILLATION`（振荡）/`VALVE_STICTION`（阀门粘滞）/`OVERAGGRESSIVE`（参数过激）/`OVERCONSERVATIVE`（参数过保守）/`EXTERNAL_DISTURBANCE`（外扰频繁）/`QUALITY_ABNORMAL`（PV 质量异常）/`OUTPUT_SATURATION`（输出饱和）/`MANUAL_REVIEW`（人工复核）；`confidence` 为单算法置信度（0-1）；`fusedConfidence` 为 Dempster-Shafer 证据理论融合后的置信度（0-1，对齐§5.7）；`algorithm`/`algorithmVersion` 用于追溯诊断所依据的算法版本号。

#### 2.4.2 获取回路诊断详情 (Get Loop Diagnosis Detail)

* **URL**: `GET /api/v1/diagnosis/{loopId}`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT）；SPONSOR 不可访问单回路诊断详情，越权返回 `ERR_AUTH_403`
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Query Parameters**:
  * `timeWindow` (String, default=`last_24_hours`): 诊断数据时间窗，枚举值 `last_1_hour`, `last_24_hours`, `last_7_days`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "tagName": "101-FC-1023",
      "compositeScore": 45.2,
      "diagnosisLabels": [
        {
          "label": "VALVE_STICTION",
          "labelName": "阀门粘滞",
          "confidence": 0.85,
          "evidence": {
            "stiction_index": 0.78,
            "fitting_score": 0.92,
            "scatter_plot": "/api/v1/timeseries/uuid-xxx/scatter?startTime=...&endTime=...",
            "reasoning": "PV-OP 散点图呈现椭圆轨迹，拟合度 0.92，粘滞指数 0.78"
          },
          "algorithm": "STICTION_CH_v1.0"
        },
        {
          "label": "OSCILLATION",
          "labelName": "振荡",
          "confidence": 0.72,
          "evidence": {
            "oscillation_period": 60.5,
            "dominant_frequency": 0.0165,
            "iae_similarity": 0.78,
            "reasoning": "IAE 零交叉相似率 0.78，振荡周期 60.5s"
          },
          "algorithm": "OSC_IAE_v1.0"
        }
      ],
      "fusedConfidence": 0.82,
      "featureValues": {
        "oscillation_amplitude": 2.3,
        "oscillation_frequency": 0.05,
        "stiction_index": 0.78,
        "output_saturation_rate": 5.2
      },
      "evidenceChain": {
        "waveformUrl": "/api/v1/timeseries/uuid-xxx/waveform?startTime=...&endTime=...",
        "scatterPlot": {
          "x": [45.5, 45.8, 45.7],
          "y": [50.1, 50.2, 50.1]
        },
        "reasoning": "PV-OP 散点图呈现椭圆轨迹，结合振荡频率 0.05Hz，判定为阀门粘滞"
      },
      "algorithmVersion": "STICTION_CH_v1.0",
      "diagnosedAt": "2026-06-20T08:00:00Z"
    }
  }
  ```
* **说明**：返回诊断详情，含 8 类诊断标签数组（每项含 `label`/`confidence`(0-1)/`evidence`(对象)/`algorithm`）、融合置信度 `fusedConfidence`（Dempster-Shafer 证据理论融合，对齐《关键算法设计说明》§5.7）、特征值、证据链（波形 URL/PV-OP 散点图/推理过程）；数据不足时返回 `ERR_DATA_INCONCLUSIVE`。诊断标签枚举为 8 类：`OSCILLATION`/`VALVE_STICTION`/`OVERAGGRESSIVE`/`OVERCONSERVATIVE`/`EXTERNAL_DISTURBANCE`/`QUALITY_ABNORMAL`/`OUTPUT_SATURATION`/`MANUAL_REVIEW`。

#### 2.4.3 获取诊断指标配置列表 (List Diagnosis Metrics)

* **URL**: `GET /api/v1/diagnosis/metrics`
* **权限**: 查看层及以上（所有角色可查看配置，仅系统管理员可编辑）
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "diagId": "uuid-xxx",
          "diagKey": "oscillation_fft",
          "diagName": "振荡检测 FFT",
          "label": "OSCILLATION",
          "algorithmType": "FFT",
          "calcMethod": "auto_correlation",
          "params": {
            "windowSize": 1024,
            "overlap": 0.5,
            "minFrequency": 0.01,
            "maxFrequency": 1.0
          },
          "threshold": {
            "amplitude": 1.5,
            "confidence": 0.7
          },
          "isEnabled": true,
          "algorithmVersion": "OSC_FFT_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-yyy",
          "diagKey": "stiction_scatter",
          "diagName": "粘滞检测散点拟合",
          "label": "VALVE_STICTION",
          "algorithmType": "ScatterFitting",
          "calcMethod": "pv_op_scatter",
          "params": {
            "fittingType": "ellipse",
            "minPoints": 100
          },
          "threshold": {
            "stictionIndex": 0.6,
            "fittingScore": 0.8
          },
          "isEnabled": true,
          "algorithmVersion": "STICTION_CH_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        }
      ]
    }
  }
  ```
* **说明**：返回诊断指标列表（8 类诊断标签对应的算法配置）；`label` 枚举为 8 类（OSCILLATION/VALVE_STICTION/OVERAGGRESSIVE/OVERCONSERVATIVE/EXTERNAL_DISTURBANCE/QUALITY_ABNORMAL/OUTPUT_SATURATION/MANUAL_REVIEW，对齐 C6 修正）；`calcMethod` 字段标识计算方法（如 `auto_correlation`/`pv_op_scatter`/`fft`/`iae_zero_crossing`，对齐 C4 修正）；`threshold` 为 JSONB 对象（对齐 C3 修正）；`algorithmVersion` 标识算法版本号。

#### 2.4.4 更新诊断指标配置 (Update Diagnosis Metric)

* **URL**: `PUT /api/v1/diagnosis/metrics/{diagId}`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Path Parameters**:
  * `diagId` (UUID, required): 诊断指标 ID
* **Request Body**:
  ```json
  {
    "label": "OSCILLATION",
    "algorithmType": "FFT",
    "calcMethod": "auto_correlation",
    "params": {
      "windowSize": 2048,
      "overlap": 0.5,
      "minFrequency": 0.01,
      "maxFrequency": 1.0
    },
    "threshold": {
      "amplitude": 1.8,
      "confidence": 0.75
    },
    "isEnabled": true
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "diagId": "uuid-xxx",
      "diagKey": "oscillation_fft",
      "diagName": "振荡检测 FFT",
      "label": "OSCILLATION",
      "algorithmType": "FFT",
      "calcMethod": "auto_correlation",
      "params": {
        "windowSize": 2048,
        "overlap": 0.5,
        "minFrequency": 0.01,
        "maxFrequency": 1.0
      },
      "threshold": { "amplitude": 1.8, "confidence": 0.75 },
      "isEnabled": true,
      "algorithmVersion": "OSC_FFT_v1.0",
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "admin"
    }
  }
  ```
* **说明**：
  * 配置变更即时生效，无需重启服务。
  * `label` 枚举为 8 类诊断标签（对齐 C6 修正）。
  * `calcMethod` 字段标识计算方法（对齐 C4 修正）。
  * `threshold` 为 JSONB 对象（对齐 C3 修正）。
  * 指标停用后相关诊断标签不再生成。
  * 变更记录审计日志（操作人/时间/变更前后值）。

#### 2.4.5 获取高频波形数据 (Get Timeseries Waveform)

* **URL**: `GET /api/v1/timeseries/{loopId}/waveform`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT）；SPONSOR 不可访问波形证据
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Query Parameters**:
  * `startTime` (ISO8601, required): 开始时间
  * `endTime` (ISO8601, required): 结束时间
  * `downsample` (Boolean, default=true): 是否启用 LTTB 降采样
  * `maxPoints` (Integer, default=2000): 前端最大可接受数据点数
  * `tagGroup` (String, default=`BASE`): 数据分组，枚举值 `BASE`（基础位号组：PV/SP/OP/MODE）/`OP_HF`（OP 高频组）/`PVOP_HF`（PV+OP 高频组）/`MODE_HF`（MODE 高频组）/`QUALITY_HF`（质量码高频组）
  * `qualityPolicy` (String, default=`KEEP_ALL_WITH_VALIDITY`): 质量策略，枚举值 `KEEP_ALL_WITH_VALIDITY`（保留全部点并附带 valid 标记）/`KEEP_ALL`（保留全部点不区分有效性）
  * `aggregationPolicy` (String, default=`LAST`): 聚合策略（降采样窗口内），枚举值 `LAST`（取末值）/`MEAN`（取均值）/`MAX`（取最大值）
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "tagName": "101-FC-1023",
      "timeRange": {
        "startTime": "2026-06-20T00:00:00Z",
        "endTime": "2026-06-20T10:00:00Z"
      },
      "appliedPolicy": {
        "tagGroup": "BASE",
        "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
        "aggregationPolicy": "LAST"
      },
      "timestamps": [1623912000000, 1623912001000, 1623912002000],
      "pv": [50.1, 50.2, 50.2],
      "sp": [50.0, 50.0, 50.0],
      "op": [45.5, 45.8, 45.7],
      "mode": [1, 1, 1],
      "pvQuality": ["Good", "Good", "Bad"],
      "pvValid": [true, true, false]
    }
  }
  ```
* **说明**：
  * **v3.0 关键变更**：响应中增加 `pvQuality` 数组，与 `pv` 数组等长，标识每个时间点的 PV 数据质量码（`Good`/`Bad`/`Uncertain`）。
  * **v4.0 关键变更**：
    * 新增 `tagGroup` 参数支持按数据分组拉取不同采样频率的位号组合（BASE 为默认基础组，*_HF 为高频组），便于算法服务与前端按需获取高频数据。
    * 新增 `qualityPolicy` 参数控制质量码处理策略：`KEEP_ALL_WITH_VALIDITY`（默认）保留全部点并附带 `pvValid` 标记；`KEEP_ALL` 保留全部点但不区分有效性。
    * 新增 `aggregationPolicy` 参数控制降采样窗口内的聚合方式（`LAST`/`MEAN`/`MAX`），默认 `LAST` 与历史行为兼容。
    * 响应中每个 PV 数据点增加 `pvValid` 标记（`true`/`false`），与 `pv` 数组等长；`pvQuality=Good` 时 `pvValid=true`，`pvQuality=Bad`/`Uncertain` 时 `pvValid=false`。
    * 响应中增加 `appliedPolicy` 对象，回显实际生效的 `tagGroup`/`qualityPolicy`/`aggregationPolicy`，便于客户端确认服务端处理策略。
  * **质量码仅针对 PV**：SP/OP/MODE 不携带质量码，响应中无 `sp_quality`/`op_quality`/`mode_quality` 字段。
  * **保留所有点**：PV 质量码为 `Bad` 时，对应 `pv` 值保留（不再置为 `null`），`pvValid=false`，前端按灰色虚线渲染（对齐 §4.4 v4.0 约定）。
  * 数据量超过 10 万点且 `downsample=false` 时，返回 `ERR_TS_DOWNSAMPLE_REQ`。
  * 时间窗超过 30 天时，返回 `ERR_TS_001`。

#### 2.4.6 更新回路处理状态 (Update Action Status)

* **URL**: `PATCH /api/v1/diagnosis/tracker/{loopId}/status`
* **权限**: 仅限执行层（IC_ENGINEER），越权返回 `ERR_AUTH_403`
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Request Body**:
  ```json
  {
    "status": "IN_PROGRESS",
    "comment": "已联系设备部拆阀检查"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "actionStatus": "IN_PROGRESS",
      "comment": "已联系设备部拆阀检查",
      "updatedBy": "zhang.san",
      "updatedAt": "2026-06-20T10:00:00Z"
    }
  }
  ```
* **说明**：`status` 枚举值 `PENDING`（待处理）, `IN_PROGRESS`（处理中）, `IMPLEMENTED`（已实施）, `IGNORED`（已忽略）；不走审批流；状态变更记录审计日志；标记为 `IMPLEMENTED` 后系统自动截取实施前后数据窗口生成 A/B 对比视图。

#### 2.4.7 导出诊断建议书 (Export Diagnosis Report)

* **URL**: `POST /api/v1/diagnosis/tracker/{loopId}/export`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Request Body** (optional):
  ```json
  {
    "timeWindow": "last_24_hours",
    "includeWaveform": true,
    "includeScatterPlot": true
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx"
    }
  }
  ```
* **说明**：触发后台无头浏览器截取当前波形图及预诊结论，异步生成 PDF 文件；文件名规范 `CLPM-诊断建议书-[位号]-[日期].pdf`；任务完成后通过 `checkUrl` 查询下载链接。

#### 2.4.8 获取诊断统计报表数据 (Get Diagnosis Analytics)

* **URL**: `GET /api/v1/diagnosis/analytics`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `startTime` (ISO8601, required): 开始时间
  * `endTime` (ISO8601, required): 结束时间
  * `plantNodeId` (UUID, optional): 按装置/单元筛选
  * `diagnosisLabel` (String, optional): 按诊断标签筛选，枚举值 `OSCILLATION`, `VALVE_STICTION`, `OVERAGGRESSIVE`, `OVERCONSERVATIVE`, `EXTERNAL_DISTURBANCE`, `QUALITY_ABNORMAL`, `OUTPUT_SATURATION`, `MANUAL_REVIEW`
  * `actionStatus` (String, optional): 按处理状态筛选，枚举值 `PENDING`, `IN_PROGRESS`, `IMPLEMENTED`, `IGNORED`
  * `granularity` (String, default=`day`): 统计粒度，枚举值 `day`, `week`, `month`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "filterScope": {
        "startTime": "2026-06-01T00:00:00Z",
        "endTime": "2026-06-20T00:00:00Z",
        "plantNodeId": null,
        "diagnosisLabel": null,
        "actionStatus": null,
        "granularity": "day"
      },
      "labelDistribution": [
        { "label": "VALVE_STICTION", "labelName": "阀门粘滞", "count": 8 },
        { "label": "OVERAGGRESSIVE", "labelName": "参数过激", "count": 5 },
        { "label": "OSCILLATION", "labelName": "振荡", "count": 3 },
        { "label": "MANUAL_REVIEW", "labelName": "人工复核", "count": 2 }
      ],
      "efficiencyTrend": {
        "timestamps": ["2026-06-01", "2026-06-02", "2026-06-03"],
        "resolvedCount": [3, 5, 4],
        "avgCloseDurationHours": [24.5, 22.0, 18.3]
      },
      "closeDurationDistribution": [
        { "range": "0-24h", "count": 8 },
        { "range": "24-72h", "count": 5 },
        { "range": "72h+", "count": 2 }
      ]
    }
  }
  ```
* **说明**：返回诊断标签分布饼图（8 类标签）、处理效率趋势折线、闭环时长分布柱状图所需数据；筛选结果为空时对应字段返回空数组。

#### 2.4.9 导出诊断统计报表 (Export Diagnosis Analytics)

* **URL**: `POST /api/v1/diagnosis/analytics/export`
* **权限**: 查看层及以上（所有角色可访问）
* **Request Body**:
  ```json
  {
    "startTime": "2026-06-01T00:00:00Z",
    "endTime": "2026-06-20T00:00:00Z",
    "plantNodeId": null,
    "diagnosisLabel": null,
    "actionStatus": null,
    "granularity": "day",
    "format": "xlsx"
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx"
    }
  }
  ```
* **说明**：`format` 枚举值 `csv`, `xlsx`；文件名规范 `CLPM-诊断统计报表-[装置]-[日期范围].xlsx`；导出失败时任务状态标记为 `FAILED`。

#### 2.4.10 查询诊断标签列表 (List Diagnosis Tags)

* **URL**: `GET /api/v1/diagnosis/tags`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `loopId` (UUID, optional): 按回路 ID 筛选
  * `severity` (String, optional): 按严重程度筛选，枚举值 `HIGH`, `MEDIUM`, `LOW`
  * `status` (String, optional): 按处理状态筛选，枚举值 `PENDING`, `IN_PROGRESS`, `IMPLEMENTED`, `IGNORED`
  * `label` (String, optional): 按诊断标签筛选，枚举值 `OSCILLATION`, `VALVE_STICTION`, `OVERAGGRESSIVE`, `OVERCONSERVATIVE`, `EXTERNAL_DISTURBANCE`, `QUALITY_ABNORMAL`, `OUTPUT_SATURATION`, `MANUAL_REVIEW`
  * `startTime` (ISO8601, optional): 诊断时间下界
  * `endTime` (ISO8601, optional): 诊断时间上界
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "tagId": "uuid-xxx",
          "loopId": "uuid-yyy",
          "tagName": "101-FC-1023",
          "unitName": "常减压装置-单元A",
          "label": "VALVE_STICTION",
          "labelName": "阀门粘滞",
          "severity": "HIGH",
          "confidence": 0.85,
          "fusedConfidence": 0.82,
          "status": "PENDING",
          "algorithm": "STICTION_CH_v1.0",
          "evidence": {
            "stiction_index": 0.78,
            "fitting_score": 0.92,
            "reasoning": "PV-OP 散点图呈现椭圆轨迹，拟合度 0.92，粘滞指数 0.78"
          },
          "diagnosedAt": "2026-06-20T08:00:00Z",
          "resolvedAt": null,
          "resolvedBy": null,
          "resolveComment": null
        }
      ],
      "total": 23,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：
  * **v4.0 新增**：诊断标签列表接口，支持按回路/严重程度/处理状态/标签类型/时间范围多维筛选。
  * `label` 枚举为 8 类诊断标签（对齐 §2.4.1 C6 修正）；`severity` 枚举值 `HIGH`（高，score < 50 或 confidence ≥ 0.8）/`MEDIUM`（中，50 ≤ score < 70 或 0.6 ≤ confidence < 0.8）/`LOW`（低，score ≥ 70 或 confidence < 0.6）。
  * `status` 枚举值 `PENDING`（待处理）/`IN_PROGRESS`（处理中）/`IMPLEMENTED`（已实施）/`IGNORED`（已忽略），对齐 §2.4.6。
  * `confidence` 为单算法置信度（0-1），`fusedConfidence` 为 Dempster-Shafer 融合置信度（0-1，对齐 §5.7）。
  * 已处理的标签返回 `resolvedAt`/`resolvedBy`/`resolveComment`，未处理时为 `null`。

#### 2.4.11 查询回路诊断标签 (Get Loop Diagnosis Tags)

* **URL**: `GET /api/v1/diagnosis/tags/{loopId}`
* **权限**: 查看层及以上（所有角色可访问）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Query Parameters**:
  * `status` (String, optional): 按处理状态筛选，枚举值 `PENDING`, `IN_PROGRESS`, `IMPLEMENTED`, `IGNORED`
  * `timeWindow` (String, default=`last_7_days`): 时间窗，枚举值 `today`, `yesterday`, `last_7_days`, `last_30_days`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "loopId": "uuid-xxx",
      "tagName": "101-FC-1023",
      "unitName": "常减压装置-单元A",
      "compositeScore": 45.2,
      "tags": [
        {
          "tagId": "uuid-xxx",
          "label": "VALVE_STICTION",
          "labelName": "阀门粘滞",
          "severity": "HIGH",
          "confidence": 0.85,
          "fusedConfidence": 0.82,
          "status": "PENDING",
          "algorithm": "STICTION_CH_v1.0",
          "evidence": {
            "stiction_index": 0.78,
            "fitting_score": 0.92,
            "scatter_plot": "/api/v1/timeseries/uuid-xxx/scatter?startTime=...&endTime=...",
            "reasoning": "PV-OP 散点图呈现椭圆轨迹，拟合度 0.92，粘滞指数 0.78"
          },
          "diagnosedAt": "2026-06-20T08:00:00Z",
          "resolvedAt": null,
          "resolvedBy": null,
          "resolveComment": null
        },
        {
          "tagId": "uuid-yyy",
          "label": "OSCILLATION",
          "labelName": "振荡",
          "severity": "MEDIUM",
          "confidence": 0.72,
          "fusedConfidence": 0.68,
          "status": "IMPLEMENTED",
          "algorithm": "OSC_IAE_v1.0",
          "evidence": {
            "oscillation_period": 60.5,
            "dominant_frequency": 0.0165,
            "iae_similarity": 0.78,
            "reasoning": "IAE 零交叉相似率 0.78，振荡周期 60.5s"
          },
          "diagnosedAt": "2026-06-18T08:00:00Z",
          "resolvedAt": "2026-06-19T10:00:00Z",
          "resolvedBy": "zhang.san",
          "resolveComment": "已调整 PID 参数，振荡消除"
        }
      ],
      "fusedConfidence": 0.82,
      "diagnosedAt": "2026-06-20T08:00:00Z"
    }
  }
  ```
* **说明**：
  * **v4.0 新增**：查询指定回路的全部诊断标签，含历史已处理与当前待处理标签。
  * `tags` 数组每项含 `tagId`/`label`/`severity`/`confidence`/`fusedConfidence`/`status`/`algorithm`/`evidence`/处理信息。
  * 与 §2.4.2 诊断详情的区别：§2.4.2 返回诊断详情（含特征值/证据链/算法版本等完整信息），本接口聚焦标签生命周期管理（状态/处理记录），便于前端标签看板展示。
  * 数据不足时对应字段返回 `null` 或空数组。

#### 2.4.12 处理诊断标签 (Resolve Diagnosis Tag)

* **URL**: `PUT /api/v1/diagnosis/tags/{tagId}/resolve`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT），越权返回 `ERR_AUTH_403`；SPONSOR 不可调用
* **Path Parameters**:
  * `tagId` (UUID, required): 诊断标签 ID
* **Request Body**:
  ```json
  {
    "status": "IMPLEMENTED",
    "comment": "已联系设备部拆阀检查，更换阀芯后粘滞消除"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "tagId": "uuid-xxx",
      "loopId": "uuid-yyy",
      "tagName": "101-FC-1023",
      "label": "VALVE_STICTION",
      "labelName": "阀门粘滞",
      "previousStatus": "PENDING",
      "status": "IMPLEMENTED",
      "comment": "已联系设备部拆阀检查，更换阀芯后粘滞消除",
      "resolvedBy": "zhang.san",
      "resolvedAt": "2026-06-20T10:30:00Z"
    }
  }
  ```
* **说明**：
  * **v4.0 新增**：处理诊断标签，更新标签状态并记录处理意见。
  * `status` 枚举值 `IN_PROGRESS`（处理中）/`IMPLEMENTED`（已实施）/`IGNORED`（已忽略）；不允许直接从 `IMPLEMENTED`/`IGNORED` 回退至 `PENDING`，状态流转需符合 §2.4.6 处理状态约定。
  * `comment`（处理意见，必填）：1-500 字符，记录处理过程与结论。
  * 响应中 `previousStatus` 标识变更前状态，便于前端审计与回溯。
  * 标记为 `IMPLEMENTED` 后系统自动截取实施前后数据窗口生成 A/B 对比视图（对齐 §2.4.6 约定）。
  * 状态变更记录审计日志（操作人/时间/变更前后值）；不走审批流。

---

### 2.5 回路整定 API (Tuning)

> **v4.0 路径同步说明**：原 Phase 2 原型占位路径已正式实现，URL 命名从名词式（records/identification/algorithm/simulation）调整为动词式（tasks/identify/tune/simulate），与实际代码对齐。新增 `GET /tuning/methods` 获取可用整定方法列表。

#### 2.5.1 获取整定记录列表 (List Tuning Tasks)

* **URL**: `GET /api/v1/tuning/tasks`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT）
* **Query Parameters**:
  * `loopId` (UUID, optional): 按回路筛选
  * `status` (String, optional): 按整定状态筛选，枚举值 `DRAFT`, `RUNNING`, `COMPLETED`, `ROLLED_BACK`
  * `startTime` (ISO8601, optional): 开始时间
  * `endTime` (ISO8601, optional): 结束时间
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "recordId": "uuid-xxx",
          "loopId": "uuid-yyy",
          "tagName": "101-FC-1023",
          "status": "COMPLETED",
          "modelType": "FOPDT",
          "method": "IMC",
          "modelParams": {
            "K": 1.2,
            "tau": 30.5,
            "theta": 5.0
          },
          "pidParams": {
            "Kp": 1.5,
            "Ti": 33.0,
            "Td": 2.27
          },
          "fittingScore": 0.92,
          "simulationResult": {
            "riseTime": 12.5,
            "overshoot": 8.3,
            "settlingTime": 45.0,
            "itae": 1250.5
          },
          "beforeScore": 45.2,
          "afterScore": 82.5,
          "algorithmVersion": "IMC_TUNE_v1.0",
          "createdAt": "2026-06-15T10:00:00Z",
          "completedAt": "2026-06-15T12:00:00Z"
        }
      ],
      "total": 5,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：Phase 1 原型阶段返回占位数据；Phase 2 返回真实整定记录。响应包含 `modelParams`（FOPDT 模型参数 K/tau/theta 或 SOPDT 模型参数 K/T1/T2/theta）、`pidParams`（推荐 PID 参数 Kp/Ti/Td）、`simulationResult`（仿真性能指标 rise_time/overshoot/settling_time/itae）、`fittingScore`（模型拟合度 R²，0-1，对齐《关键算法设计说明》§6.1.5 C5 修正）、`method`（整定方法枚举：`IMC`/`LAMBDA`/`ZIEGLER_NICHOLS`/`COHEN_COON`/`SIMC`，对齐§6.3-§6.7）、`algorithmVersion`（算法版本号）。

#### 2.5.2 触发模型辨识 (Trigger Model Identification)

* **URL**: `POST /api/v1/tuning/identify`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT）
* **Request Body**:
  ```json
  {
    "loopId": "uuid-xxx",
    "dataSegment": {
      "startTime": "2026-06-15T00:00:00Z",
      "endTime": "2026-06-15T06:00:00Z"
    },
    "samplePeriod": 1,
    "modelType": "FOPDT",
    "method": "TWO_POINT"
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx"
    }
  }
  ```
* **说明**：Phase 2 实现模型辨识算法（FOPDT/SOPDT/IPDT），输出传递函数参数（K/T/τ）、拟合度（fitting_score R²）、阶跃响应对比曲线。`method` 枚举值 `TWO_POINT`（两点法）/`AREA`（面积法）/`COMBINED`（组合法，对齐《关键算法设计说明》§6.1.4）；`modelType` 枚举值 `FOPDT`/`SOPDT`/`IPDT`。任务完成后通过 `/api/v1/algorithms/tasks/{task_id}` 查询结果，结果包含 `modelParams`（K/tau/theta 或 K/T1/T2/theta）+ `fittingScore`（R² 拟合度，0-1）+ `algorithmVersion`（如 `FOPDT_ID_v1.0`）。Phase 1 原型阶段返回 `501 Not Implemented`。

#### 2.5.3 计算整定参数 (Calculate Tuning Parameters)

* **URL**: `POST /api/v1/tuning/tune`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT）
* **Request Body**:
  ```json
  {
    "loopId": "uuid-xxx",
    "identificationRecordId": "uuid-xxx",
    "method": "IMC",
    "params": {
      "lambda": 3.0
    }
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx"
    }
  }
  ```
* **说明**：Phase 2 实现整定算法，`method` 枚举值 `IMC`/`LAMBDA`/`ZIEGLER_NICHOLS`/`COHEN_COON`/`SIMC`（对齐《关键算法设计说明》§6.3-§6.7）。任务完成后通过 `/api/v1/algorithms/tasks/{task_id}` 查询结果，结果包含 `modelParams`（FOPDT/SOPDT 模型参数）+ `pidParams`（推荐 PID 参数 Kp/Ti/Td）+ `simulationResult`（仿真性能指标）+ `fittingScore`（拟合度 R²）+ `algorithmVersion`（如 `IMC_TUNE_v1.0`）。Phase 1 原型阶段返回 `501 Not Implemented`。

#### 2.5.4 执行闭环仿真 (Run Closed-Loop Simulation)

* **URL**: `POST /api/v1/tuning/simulate`
* **权限**: 执行层及以上（IC_ENGINEER/ADMIN/EXPERT）
* **Request Body**:
  ```json
  {
    "loopId": "uuid-xxx",
    "currentPid": { "p": 1.2, "i": 0.5, "d": 0.0 },
    "recommendedPid": { "p": 1.5, "i": 0.4, "d": 0.0 },
    "disturbanceType": "step",
    "simulationDuration": 300
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx"
    }
  }
  ```
* **说明**：Phase 2 执行闭环仿真，输出阶跃响应双波形对比、性能指标对比表（上升时间/超调/settling time/ITAE/IAE，对齐《关键算法设计说明》§6.8.2）。任务完成后通过 `/api/v1/algorithms/tasks/{task_id}` 查询结果，结果包含 `simulationResult`（current_response/recommended_response/metrics_comparison）。Phase 1 原型阶段返回 `501 Not Implemented`。

#### 2.5.5 获取整定效果统计 (Get Tuning Analytics) [待实现]

* **URL**: `GET /api/v1/tuning/analytics`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `startTime` (ISO8601, required): 开始时间
  * `endTime` (ISO8601, required): 结束时间
  * `plantNodeId` (UUID, optional): 按装置/单元筛选
  * `loopId` (UUID, optional): 按回路筛选
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "filterScope": {
        "startTime": "2026-06-01T00:00:00Z",
        "endTime": "2026-06-20T00:00:00Z",
        "plantNodeId": null,
        "loopId": null
      },
      "beforeAfterComparison": [
        {
          "loopId": "uuid-xxx",
          "tagName": "101-FC-1023",
          "beforeScore": 45.2,
          "afterScore": 82.5,
          "delta": 37.3
        }
      ],
      "effectTrend": {
        "timestamps": ["2026-06-01", "2026-06-02", "2026-06-03"],
        "avgScore": [60.5, 65.0, 70.2]
      }
    }
  }
  ```
* **说明**：返回整定前后 KPI 对比柱状图、整定效果趋势折线所需数据；Phase 1 原型阶段返回占位数据。

---

### 2.6 系统管理 API (System Management)

本组 API 提供系统运维能力，包括用户角色管理、审计日志查询、自动报表管理。仅系统管理员可调用。

#### 2.6.1 获取用户列表 (List Users)

* **URL**: `GET /api/v1/users`
* **权限**: 管理层（ADMIN），越权返回 `ERR_AUTH_403`
* **Query Parameters**:
  * `keyword` (String, optional): 按用户名/姓名模糊查询
  * `role` (String, optional): 按角色筛选，枚举值 `IC_ENGINEER`（仪控工程师）, `PE_ENGINEER`（工艺/设备工程师）, `SPONSOR`（Sponsor）, `ADMIN`（系统管理员）, `EXPERT`（外部专家）
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "userId": "uuid-xxx",
          "username": "zhang.san",
          "fullName": "张三",
          "role": "IC_ENGINEER",
          "roleName": "仪控工程师",
          "isActive": true,
          "lastLoginAt": "2026-06-20T09:00:00Z",
          "createdAt": "2026-06-01T10:00:00Z"
        }
      ],
      "total": 25,
      "page": 1,
      "pageSize": 20
    }
  }
  ```

#### 2.6.2 更新用户角色 (Update User Role)

* **URL**: `PUT /api/v1/users/{userId}/role`
* **权限**: 管理层（ADMIN），越权返回 `ERR_AUTH_403`
* **Path Parameters**:
  * `userId` (UUID, required): 用户 ID
* **Request Body**:
  ```json
  {
    "role": "ADMIN"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "userId": "uuid-xxx",
      "username": "zhang.san",
      "fullName": "张三",
      "role": "ADMIN",
      "roleName": "系统管理员",
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "admin"
    }
  }
  ```
* **说明**：角色变更记录审计日志；`role` 枚举值同 2.6.1。

#### 2.6.3 获取审计日志列表 (List Audit Logs)

* **URL**: `GET /api/v1/audit-logs`
* **权限**: 管理层（ADMIN）
* **Query Parameters**:
  * `startTime` (ISO8601, optional): 开始时间
  * `endTime` (ISO8601, optional): 结束时间
  * `operator` (String, optional): 按操作人筛选（用户名）
  * `operationType` (String, optional): 按操作类型筛选，枚举值 `CONFIG_UPDATE`, `LOOP_CREATE`, `LOOP_UPDATE`, `LOOP_DELETE`, `TAG_MAPPING_UPDATE`, `ACTION_STATUS_UPDATE`, `ROLE_UPDATE`, `REPORT_RETRY`
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "logId": "uuid-xxx",
          "operator": "admin",
          "operatorName": "系统管理员",
          "operationType": "CONFIG_UPDATE",
          "operationDesc": "更新性能指标配置：好值率权重 20 -> 25",
          "targetType": "metric_config",
          "targetId": "uuid-yyy",
          "beforeValue": { "weight": 20 },
          "afterValue": { "weight": 25 },
          "operatedAt": "2026-06-20T10:30:00Z",
          "clientIp": "192.168.1.100"
        }
      ],
      "total": 1024,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：审计日志不可物理删除，仅支持归档；日志量过大时分页加载。

#### 2.6.4 获取自动报表记录列表 (List Report Records)

* **URL**: `GET /api/v1/reports`
* **权限**: 管理层（ADMIN）
* **Query Parameters**:
  * `period` (String, optional): 按报表周期筛选，枚举值 `shift`（班）, `day`（日）, `week`（周）, `month`（月）
  * `status` (String, optional): 按生成状态筛选，枚举值 `SUCCESS`, `PROCESSING`, `FAILED`
  * `startTime` (ISO8601, optional): 开始时间
  * `endTime` (ISO8601, optional): 结束时间
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "reportId": "uuid-xxx",
          "reportName": "CLPM-性能评估报告-day-2026-06-20",
          "period": "day",
          "generatedAt": "2026-06-20T01:00:00Z",
          "status": "SUCCESS",
          "fileUrl": "/api/v1/reports/uuid-xxx/download",
          "fileSize": 2048576,
          "retryCount": 0
        }
      ],
      "total": 30,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：返回自动报表（班/日/周/月）生成记录列表；`fileUrl` 为报表下载链接；`status` 为 `FAILED` 时可通过 2.6.5 接口重试。

#### 2.6.5 重试报表生成 (Retry Report Generation)

* **URL**: `POST /api/v1/reports/{reportId}/retry`
* **权限**: 管理层（ADMIN）
* **Path Parameters**:
  * `reportId` (UUID, required): 报表 ID
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "reportId": "uuid-xxx",
      "status": "PROCESSING",
      "retryCount": 1,
      "retriedAt": "2026-06-20T10:30:00Z",
      "retriedBy": "admin"
    }
  }
  ```
* **说明**：仅状态为 `FAILED` 的报表可重试；重试仍失败时记录错误日志并通知管理员；重试操作记录审计日志。

---

### 2.7 算法服务接口 (Algorithm Services)

本组 API 提供算法引擎的异步任务能力，包括 KPI 计算、诊断分析、整定计算三类算法任务的触发与查询。所有算法接口采用异步模式（202 Accepted + task_id 轮询），对齐《关键算法设计说明》v1.0 §7 输入输出参数定义。任务查询统一通过 §2.7.4 算法任务查询接口获取结果。

#### 2.7.1 触发 KPI 计算 (Trigger KPI Calculation)

* **URL**: `POST /api/v1/algorithms/kpi/calculate`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Request Body**:
  ```json
  {
    "loopIds": ["uuid-xxx", "uuid-yyy"],
    "startTime": "2026-06-20T00:00:00Z",
    "endTime": "2026-06-20T08:00:00Z",
    "metrics": ["accuracy_rate", "fast_rate", "steady_rate", "effective_auto_rate", "good_value_rate", "oscillation_rate", "saturation_rate"],
    "forceRecalculate": false
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "KPI_CALCULATION",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/algorithms/tasks/task-uuid-xxx",
      "estimatedSeconds": 60
    }
  }
  ```
* **说明**：
  * 触发指定回路的 KPI 计算，采用 3+1+8 体系（3 核心 + 1 投用 + 8 辅助诊断，对齐 §2.8 与 FDS v6.0 §1.3）。
  * `loopIds` 为空数组时表示对所有启用回路进行计算；`metrics` 为空数组时表示计算全部 12 项指标（3 核心 + 1 投用 + 8 辅助诊断）。
  * `forceRecalculate=true` 时强制重算（忽略缓存），默认 `false` 复用已有快照。
  * 任务完成后通过 §2.7.4 查询结果，结果结构包含 `kpiResults`（每回路的指标结果 + `score` + `status` + `algorithm_version`）。
  * **v4.0 数据血缘与置信度增强**（对齐《关键算法设计说明》v2.0）：
    * 每个指标结果采用 `metrics` 嵌套对象结构，每项含 `value`（指标值）+ `confidence_level`（置信度等级 A/B/C/D/E）+ `valid_rate`（有效数据率，0-1）+ `data_lineage`（数据血缘对象，5 独立字段 + JSONB 子字段，对齐 DDS v6.0 §3.5）。
    * `data_lineage` 包含 5 个独立字段：`sampling_freq`（采样频率，如 `5s`）/`quality_policy`（质量策略，对齐 §2.4.5）/`tag_group`（数据分组，对齐 §2.4.5）/`valid_rate`（有效数据率）/`confidence_level`（置信度等级）；其中 `data_lineage` JSONB 内部还可记录 `source_metrics`（来源指标列表）等子字段。
    * 回路级结果新增 `confidence_level`（综合置信度等级 A/B/C/D/E）与 `data_lineage`（回路级数据血缘汇总对象）。
    * `score` 结果新增 `data_lineage` JSONB 对象，记录综合评分所依据的数据血缘信息。
    * `confidence_level` 等级规则（对齐 FDS v6.0，valid_rate 阈值）：A（valid_rate ≥ 0.95）/B（0.80-0.95）/C（0.60-0.80）/D（0.20-0.60）/E（< 0.20）。
  * `algorithmVersion` 固定为 `KPI_CALC_v1.0`（对齐§4.10 算法版本号规范）。
  * 计算失败时任务状态标记为 `FAILED`，`error` 字段包含错误码与详情。

#### 2.7.2 触发诊断分析 (Trigger Diagnosis Analysis)

* **URL**: `POST /api/v1/algorithms/diagnosis/analyze`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Request Body**:
  ```json
  {
    "loopIds": ["uuid-xxx", "uuid-yyy"],
    "startTime": "2026-06-20T00:00:00Z",
    "endTime": "2026-06-20T08:00:00Z",
    "labels": ["OSCILLATION", "VALVE_STICTION", "OVERAGGRESSIVE", "OVERCONSERVATIVE", "EXTERNAL_DISTURBANCE", "QUALITY_ABNORMAL", "OUTPUT_SATURATION"],
    "enableFusion": true
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "DIAGNOSIS_ANALYSIS",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/algorithms/tasks/task-uuid-xxx",
      "estimatedSeconds": 120
    }
  }
  ```
* **说明**：
  * 触发指定回路的诊断分析，输出 8 类诊断标签结果（对齐《关键算法设计说明》§5 诊断算法，C6 修正）。
  * `loopIds` 为空数组时表示对所有评分跌破阈值的回路进行分析。
  * `labels` 指定启用的诊断标签子集（8 类枚举：`OSCILLATION`/`VALVE_STICTION`/`OVERAGGRESSIVE`/`OVERCONSERVATIVE`/`EXTERNAL_DISTURBANCE`/`QUALITY_ABNORMAL`/`OUTPUT_SATURATION`/`MANUAL_REVIEW`）；为空数组时启用全部（`MANUAL_REVIEW` 除外，由系统自动标记）。
  * `enableFusion=true` 时启用 Dempster-Shafer 证据理论融合（对齐§5.7），输出 `fused_confidence`；默认 `true`。
  * 任务完成后通过 §2.7.4 查询结果，结果结构包含 `diagnosisResults`（每回路的 `diagnosisLabels` 数组，每项含 `label`/`confidence`(0-1)/`evidence`(对象)/`algorithm` + `fusedConfidence`）。
  * 各诊断标签对应的 `algorithmVersion` 见《关键算法设计说明》§5（如 `OSC_FFT_v1.0`/`STICTION_CH_v1.0`/`OVERAGGRESSIVE_PID_v1.0` 等）。

#### 2.7.3 触发整定计算 (Trigger Tuning Calculation)

* **URL**: `POST /api/v1/algorithms/tuning/calculate`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
* **Request Body**:
  ```json
  {
    "loopId": "uuid-xxx",
    "identificationParams": {
      "dataSegment": {
        "startTime": "2026-06-15T00:00:00Z",
        "endTime": "2026-06-15T06:00:00Z"
      },
      "samplePeriod": 1,
      "modelType": "FOPDT",
      "method": "TWO_POINT"
    },
    "tuningParams": {
      "method": "IMC",
      "params": {
        "lambda": 3.0
      }
    },
    "enableSimulation": true,
    "simulationConfig": {
      "disturbanceType": "step",
      "simulationDuration": 300
    }
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "TUNING_CALCULATION",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/algorithms/tasks/task-uuid-xxx",
      "estimatedSeconds": 90
    }
  }
  ```
* **说明**：
  * 触发整定计算流程，包含模型辨识 → PID 参数计算 → 闭环仿真三阶段（对齐《关键算法设计说明》§6 整定算法）。
  * `identificationParams.method` 枚举值 `TWO_POINT`（两点法）/`AREA`（面积法）/`COMBINED`（组合法，对齐§6.1.4）；`modelType` 枚举值 `FOPDT`/`SOPDT`/`IPDT`。
  * `tuningParams.method` 枚举值 `IMC`/`LAMBDA`/`ZIEGLER_NICHOLS`/`COHEN_COON`/`SIMC`（对齐§6.3-§6.7）。
  * `enableSimulation=true` 时执行闭环仿真（对齐§6.8.2），输出 `simulationResult`。
  * 任务完成后通过 §2.7.4 查询结果，结果结构包含：
    * `modelParams`：FOPDT（K/tau/theta）或 SOPDT（K/T1/T2/theta）模型参数
    * `fittingScore`：模型拟合度 R²（0-1，对齐§6.1.5 C5 修正）
    * `pidParams`：推荐 PID 参数（Kp/Ti/Td）
    * `simulationResult`：仿真性能指标（riseTime/overshoot/settlingTime/itae）
    * `algorithmVersion`：如 `IMC_TUNE_v1.0`/`FOPDT_ID_v1.0`
  * Phase 1 原型阶段返回 `501 Not Implemented`；Phase 2 实现完整算法链路。

#### 2.7.4 查询算法任务状态 (Get Algorithm Task Status)

* **URL**: `GET /api/v1/algorithms/tasks/{task_id}`
* **权限**: 触发任务的角色层级及以上（按 `taskType` 校验：KPI/诊断任务需管理层，整定任务需执行层及以上）
* **Path Parameters**:
  * `task_id` (String, required): 任务 ID（由 §2.7.1/§2.7.2/§2.7.3 返回的 `taskId`）
* **Response (200 OK - 处理中)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "KPI_CALCULATION",
      "status": "PROCESSING",
      "progress": 45,
      "startedAt": "2026-06-20T10:00:00Z",
      "estimatedRemainingSeconds": 30
    }
  }
  ```
* **Response (200 OK - KPI 计算完成)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "KPI_CALCULATION",
      "status": "SUCCESS",
      "progress": 100,
      "startedAt": "2026-06-20T10:00:00Z",
      "completedAt": "2026-06-20T10:01:00Z",
      "result": {
        "algorithmVersion": "KPI_CALC_v1.0",
        "kpiResults": [
          {
            "loopId": "uuid-xxx",
            "tagName": "101-FC-1023",
            "metrics": {
              "good_value_rate": {
                "value": 95.2,
                "confidence_level": "A",
                "valid_rate": 0.985,
                "data_lineage": {
                  "sampling_freq": "5s",
                  "quality_policy": "KEEP_ALL_WITH_VALIDITY",
                  "tag_group": "BASE"
                }
              },
              "auto_mode_rate": {
                "value": 88.5,
                "confidence_level": "A",
                "valid_rate": 0.992,
                "data_lineage": {
                  "sampling_freq": "5s",
                  "quality_policy": "KEEP_ALL_WITH_VALIDITY",
                  "tag_group": "BASE"
                }
              },
              "steady_rate": {
                "value": 76.8,
                "confidence_level": "B",
                "valid_rate": 0.940,
                "data_lineage": {
                  "sampling_freq": "5s",
                  "quality_policy": "KEEP_ALL_WITH_VALIDITY",
                  "tag_group": "BASE"
                }
              },
              "accuracy_rate": {
                "value": 82.1,
                "confidence_level": "A",
                "valid_rate": 0.978,
                "data_lineage": {
                  "sampling_freq": "5s",
                  "quality_policy": "KEEP_ALL_WITH_VALIDITY",
                  "tag_group": "BASE"
                }
              },
              "oscillation_rate": {
                "value": 12.3,
                "confidence_level": "B",
                "valid_rate": 0.935,
                "data_lineage": {
                  "sampling_freq": "1s",
                  "quality_policy": "KEEP_ALL_WITH_VALIDITY",
                  "tag_group": "PVOP_HF"
                }
              },
              "saturation_rate": {
                "value": 5.4,
                "confidence_level": "A",
                "valid_rate": 0.980,
                "data_lineage": {
                  "sampling_freq": "5s",
                  "quality_policy": "KEEP_ALL_WITH_VALIDITY",
                  "tag_group": "BASE"
                }
              }
            },
            "score": 78.5,
            "confidence_level": "A",
            "status": "GOOD",
            "data_lineage": {
              "sampling_freq": "5s",
              "quality_policy": "KEEP_ALL_WITH_VALIDITY",
              "tag_group": "BASE",
              "valid_rate": 0.985,
              "source_metrics": ["good_value_rate", "auto_mode_rate", "steady_rate", "accuracy_rate", "oscillation_rate", "saturation_rate"]
            },
            "calculatedAt": "2026-06-20T10:00:30Z"
          }
        ],
        "failedLoops": []
      }
    }
  }
  ```
* **Response (200 OK - 诊断分析完成)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "DIAGNOSIS_ANALYSIS",
      "status": "SUCCESS",
      "progress": 100,
      "startedAt": "2026-06-20T10:00:00Z",
      "completedAt": "2026-06-20T10:02:00Z",
      "result": {
        "diagnosisResults": [
          {
            "loopId": "uuid-xxx",
            "tagName": "101-FC-1023",
            "diagnosisLabels": [
              {
                "label": "VALVE_STICTION",
                "labelName": "阀门粘滞",
                "confidence": 0.85,
                "evidence": {
                  "stiction_index": 0.78,
                  "fitting_score": 0.92,
                  "scatter_plot": "/api/v1/timeseries/uuid-xxx/scatter?startTime=...&endTime=...",
                  "reasoning": "PV-OP 散点图呈现椭圆轨迹，拟合度 0.92，粘滞指数 0.78"
                },
                "algorithm": "STICTION_CH_v1.0"
              }
            ],
            "fusedConfidence": 0.82,
            "diagnosedAt": "2026-06-20T10:01:30Z"
          }
        ],
        "failedLoops": []
      }
    }
  }
  ```
* **Response (200 OK - 整定计算完成)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "TUNING_CALCULATION",
      "status": "SUCCESS",
      "progress": 100,
      "startedAt": "2026-06-20T10:00:00Z",
      "completedAt": "2026-06-20T10:01:30Z",
      "result": {
        "loopId": "uuid-xxx",
        "tagName": "101-FC-1023",
        "modelType": "FOPDT",
        "method": "IMC",
        "modelParams": { "K": 1.2, "tau": 30.5, "theta": 5.0 },
        "fittingScore": 0.92,
        "pidParams": { "Kp": 1.5, "Ti": 33.0, "Td": 2.27 },
        "simulationResult": {
          "riseTime": 12.5,
          "overshoot": 8.3,
          "settlingTime": 45.0,
          "itae": 1250.5
        },
        "algorithmVersion": "IMC_TUNE_v1.0",
        "completedAt": "2026-06-20T10:01:30Z"
      }
    }
  }
  ```
* **Response (200 OK - 任务失败)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "KPI_CALCULATION",
      "status": "FAILED",
      "progress": 45,
      "startedAt": "2026-06-20T10:00:00Z",
      "failedAt": "2026-06-20T10:00:30Z",
      "error": {
        "errorCode": "ERR_ALGORITHM_DATA_INSUFFICIENT",
        "message": "回路 uuid-xxx 在指定时间窗内有效数据点不足（<100），无法计算 KPI",
        "details": {
          "loopId": "uuid-xxx",
          "validPointCount": 50,
          "requiredPointCount": 100
        }
      }
    }
  }
  ```
* **说明**：
  * 统一查询 §2.7.1/§2.7.2/§2.7.3 提交的算法任务状态与结果。
  * `taskType` 枚举值 `KPI_CALCULATION`/`DIAGNOSIS_ANALYSIS`/`TUNING_CALCULATION`。
  * `status` 枚举值 `PROCESSING`（处理中）/`SUCCESS`（成功）/`FAILED`（失败），对齐§6.6 异步任务响应格式。
  * `progress` 为 0-100 的整数百分比；任务完成后 `result` 字段按 `taskType` 返回不同结构。
  * 任务结果保留 7 天，超期查询返回 `ERR_TASK_NOT_FOUND`。
  * 前端轮询建议间隔 3 秒，任务完成后停止轮询并渲染结果。

#### 2.7.5 DataPlanner 数据规划（内部接口）

本组接口为 **内部接口**，仅供算法服务（KPI 计算/诊断分析/整定计算）内部调用，**不对外暴露**，不在 BFF 路由中注册，不进入权限网关。其作用是根据算法的指标需求，规划数据拉取方案（tag 分组/采样频率/质量策略/聚合策略），并按计划返回算法所需的 MetricDataBundle，统一数据血缘信息（对齐《关键算法设计说明》v2.0 DataPlanner 章节）。

##### 2.7.5.1 提交数据需求 (Submit Data Plan)

* **URL**: `POST /api/v1/algorithms/dataplanner/plan`
* **权限**: 内部接口（仅供算法服务调用，不对外暴露）
* **Request Body**:
  ```json
  {
    "loopIds": ["uuid-xxx"],
    "metrics": ["accuracy_rate", "fast_rate", "steady_rate", "effective_auto_rate", "good_value_rate", "oscillation_rate", "saturation_rate"],
    "startTime": "2026-06-20T00:00:00Z",
    "endTime": "2026-06-20T08:00:00Z",
    "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
    "aggregationPolicy": "LAST"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "planId": "plan-uuid-xxx",
      "loopCount": 1,
      "queryPlans": [
        {
          "loopId": "uuid-xxx",
          "metricGroups": [
            {
              "metrics": ["accuracy_rate", "fast_rate", "steady_rate", "effective_auto_rate", "good_value_rate", "saturation_rate"],
              "tagGroup": "BASE",
              "samplingFreq": "5s",
              "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
              "aggregationPolicy": "LAST",
              "estimatedPoints": 5760
            },
            {
              "metrics": ["oscillation_rate"],
              "tagGroup": "PVOP_HF",
              "samplingFreq": "1s",
              "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
              "aggregationPolicy": "LAST",
              "estimatedPoints": 28800
            }
          ]
        }
      ],
      "createdAt": "2026-06-20T10:00:00Z"
    }
  }
  ```
* **说明**：
  * 内部接口，仅供算法服务调用，不对外暴露。
  * DataPlanner 根据指标需求自动规划每个回路的数据拉取方案，将指标按所需 tag 分组与采样频率聚合为多个 metricGroup，输出 queryPlans。
  * `metrics` 枚举值对齐 §2.8 的 3+1+8 体系（3 核心 + 1 投用 + 8 辅助诊断，共 12 项指标）。
  * `tagGroup` 枚举值对齐 §2.4.5（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF）；`qualityPolicy`/`aggregationPolicy` 枚举值对齐 §2.4.5。
  * `planId` 用于后续 §2.7.5.2 获取 MetricDataBundle。
  * 规划结果记录数据血缘信息（sampling_freq/quality_policy/tag_group/valid_rate/confidence_level），随指标结果回传（对齐 §2.7.1 v4.0 数据血缘字段，5 独立字段 + `data_lineage` JSONB 子字段，对齐 DDS v6.0 §3.5）。

##### 2.7.5.2 获取指标数据包 (Get Metric Data Bundle)

* **URL**: `POST /api/v1/algorithms/dataplanner/bundle`
* **权限**: 内部接口（仅供算法服务调用，不对外暴露）
* **Request Body**:
  ```json
  {
    "planId": "plan-uuid-xxx",
    "loopIds": ["uuid-xxx"]
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "planId": "plan-uuid-xxx",
      "bundles": [
        {
          "loopId": "uuid-xxx",
          "metricGroups": [
            {
              "tagGroup": "BASE",
              "samplingFreq": "5s",
              "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
              "aggregationPolicy": "LAST",
              "data": {
                "timestamps": [1623912000000, 1623912005000],
                "pv": [50.1, 50.2],
                "sp": [50.0, 50.0],
                "op": [45.5, 45.8],
                "mode": [1, 1],
                "pvQuality": ["Good", "Good"],
                "pvValid": [true, true]
              },
              "validRate": 0.985,
              "pointCount": 5760
            },
            {
              "tagGroup": "PVOP_HF",
              "samplingFreq": "1s",
              "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
              "aggregationPolicy": "LAST",
              "data": {
                "timestamps": [1623912000000, 1623912001000],
                "pv": [50.1, 50.2],
                "op": [45.5, 45.8],
                "pvQuality": ["Good", "Good"],
                "pvValid": [true, true]
              },
              "validRate": 0.972,
              "pointCount": 28800
            }
          ],
          "lineage": {
            "sampling_freq": "5s",
            "quality_policy": "KEEP_ALL_WITH_VALIDITY",
            "tag_group": "BASE",
            "valid_rate": 0.985
          }
        }
      ]
    }
  }
  ```
* **说明**：
  * 内部接口，仅供算法服务调用，不对外暴露。
  * 按 `planId` 拉取各回路的 MetricDataBundle，每个 bundle 按 metricGroup 分组返回时序数据 + 数据血缘信息。
  * `validRate` 为该 metricGroup 的有效数据率（0-1），用于推导 §2.7.1 的 `confidence_level`。
  * `lineage` 对象为回路级数据血缘汇总，随算法结果回传至 §2.7.1 的 `data_lineage` 字段。
  * 数据量过大时支持分批拉取（通过 `loopIds` 分批传入），单次最多返回 10 个回路的数据包。

#### 2.7.6 任务管理接口 (Task Management)

本组接口提供评估任务的触发与查询能力，包括标准评估任务（每小时定时触发，由调度器调用）与自定义评估任务（按需触发，由用户通过前端调用）。任务查询统一返回状态、进度与结果。

##### 2.7.6.1 触发标准评估任务 (Trigger Standard Evaluation)

* **URL**: `POST /api/v1/tasks/standard/evaluate`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Request Body**:
  ```json
  {
    "timeWindow": "last_1_hour",
    "plantNodeId": null,
    "forceRecalculate": false
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "STANDARD",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx",
      "estimatedSeconds": 120
    }
  }
  ```
* **说明**：
  * 触发标准评估任务，默认由系统调度器每小时定时调用，对全厂启用回路执行 3+1+8 体系指标计算（3 核心 + 1 投用 + 8 辅助诊断，对齐 §2.8）+ 诊断分析。
  * `timeWindow` 枚举值 `last_1_hour`/`last_24_hours`，默认 `last_1_hour`。
  * `plantNodeId` 为空时表示全厂评估；`forceRecalculate=true` 时强制重算（忽略缓存）。
  * 任务完成后通过 §2.7.6.3 查询结果，结果结构对齐 §2.7.4 KPI/诊断结果（含 v4.0 数据血缘与 confidence_level 字段）。
  * 任务并发度受 §2.3.5 引擎规则 `scheduleConcurrency` 约束。

##### 2.7.6.2 触发自定义评估任务 (Trigger Custom Evaluation)

* **URL**: `POST /api/v1/tasks/custom/evaluate`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
* **Request Body**:
  ```json
  {
    "loopIds": ["uuid-xxx", "uuid-yyy"],
    "metrics": ["accuracy_rate", "fast_rate", "steady_rate", "effective_auto_rate", "good_value_rate", "oscillation_rate", "saturation_rate"],
    "startTime": "2026-06-19T00:00:00Z",
    "endTime": "2026-06-20T00:00:00Z",
    "taskName": "常减压装置专项评估"
  }
  ```
* **Response (202 Accepted)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "CUSTOM",
      "taskName": "常减压装置专项评估",
      "status": "PROCESSING",
      "checkUrl": "/api/v1/tasks/task-uuid-xxx",
      "estimatedSeconds": 90
    }
  }
  ```
* **说明**：
  * 触发自定义评估任务，按需对指定回路集合在指定时间窗内执行指定指标的计算。
  * `loopIds`（回路 ID 数组，必填）：至少 1 个，上限 500 个。
  * `metrics`（指标 key 数组，必填）：枚举值 `accuracy_rate`/`fast_rate`/`steady_rate`/`effective_auto_rate`/`good_value_rate`/`oscillation_rate`/`saturation_rate`/`stiction_index`/`overaggressive_index`/`overconservative_index`/`disturbance_index`/`quality_abnormal_rate`；为空数组时计算全部 12 项（3 核心 + 1 投用 + 8 辅助诊断，对齐 §2.8 3+1+8 体系）。
  * `startTime`/`endTime`（ISO8601，必填）：评估时间窗，结束时间不得早于开始时间，时间窗不得超过 30 天，否则返回 `ERR_TS_001`。
  * `taskName`（任务名称，必填）：用户自定义任务名，1-100 字符，用于 §2.7.6.4 任务列表展示。
  * 任务完成后通过 §2.7.6.3 查询结果，结果结构对齐 §2.7.4（含 v4.0 数据血缘与 confidence_level 字段）。

##### 2.7.6.3 查询任务状态 (Get Task Status)

* **URL**: `GET /api/v1/tasks/{taskId}`
* **权限**: 触发任务的角色层级及以上（按 `taskType` 校验：STANDARD 需管理层，CUSTOM 需执行层及以上）
* **Path Parameters**:
  * `taskId` (UUID, required): 任务 ID（由 §2.7.6.1/§2.7.6.2 返回的 `taskId`）
* **Response (200 OK - 处理中)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "CUSTOM",
      "taskName": "常减压装置专项评估",
      "status": "PROCESSING",
      "progress": 45,
      "startedAt": "2026-06-20T10:00:00Z",
      "estimatedRemainingSeconds": 50
    }
  }
  ```
* **Response (200 OK - 成功)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "CUSTOM",
      "taskName": "常减压装置专项评估",
      "status": "SUCCESS",
      "progress": 100,
      "startedAt": "2026-06-20T10:00:00Z",
      "completedAt": "2026-06-20T10:01:30Z",
      "results": {
        "algorithmVersion": "KPI_CALC_v1.0",
        "kpiResults": [
          {
            "loopId": "uuid-xxx",
            "tagName": "101-FC-1023",
            "metrics": {
              "good_value_rate": {
                "value": 95.2,
                "confidence_level": "A",
                "valid_rate": 0.985,
                "data_lineage": {
                  "sampling_freq": "5s",
                  "quality_policy": "KEEP_ALL_WITH_VALIDITY",
                  "tag_group": "BASE"
                }
              }
            },
            "score": 78.5,
            "confidence_level": "A",
            "status": "GOOD",
            "data_lineage": {
              "sampling_freq": "5s",
              "quality_policy": "KEEP_ALL_WITH_VALIDITY",
              "tag_group": "BASE",
              "valid_rate": 0.985
            }
          }
        ],
        "failedLoops": []
      }
    }
  }
  ```
* **Response (200 OK - 失败)**:
  ```json
  {
    "data": {
      "taskId": "task-uuid-xxx",
      "taskType": "CUSTOM",
      "taskName": "常减压装置专项评估",
      "status": "FAILED",
      "progress": 45,
      "startedAt": "2026-06-20T10:00:00Z",
      "failedAt": "2026-06-20T10:00:30Z",
      "error": {
        "errorCode": "ERR_ALGORITHM_DATA_INSUFFICIENT",
        "message": "回路 uuid-xxx 在指定时间窗内有效数据点不足（<100），无法计算 KPI"
      }
    }
  }
  ```
* **说明**：
  * 查询 §2.7.6.1/§2.7.6.2 提交的评估任务状态与结果。
  * `taskType` 枚举值 `STANDARD`（标准评估）/`CUSTOM`（自定义评估）。
  * `status` 枚举值 `PROCESSING`/`SUCCESS`/`FAILED`，对齐 §6.6 异步任务响应格式。
  * `progress` 为 0-100 的整数百分比；任务完成后 `results` 字段返回评估结果，结构对齐 §2.7.4 KPI 结果（含 v4.0 数据血缘与 confidence_level 字段）。
  * 任务结果保留 7 天，超期查询返回 `ERR_TASK_NOT_FOUND`。

##### 2.7.6.4 查询任务列表 (List Tasks)

* **URL**: `GET /api/v1/tasks`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `taskType` (String, optional): 按任务类型筛选，枚举值 `STANDARD`（标准评估）/`CUSTOM`（自定义评估）
  * `status` (String, optional): 按任务状态筛选，枚举值 `PROCESSING`, `SUCCESS`, `FAILED`
  * `startTime` (ISO8601, optional): 任务开始时间下界
  * `endTime` (ISO8601, optional): 任务开始时间上界
  * `keyword` (String, optional): 按 taskName 模糊查询（仅对 CUSTOM 任务有效）
  * `page` (Integer, default=1): 页码
  * `pageSize` (Integer, default=20): 每页条数，最大 100
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "taskId": "task-uuid-xxx",
          "taskType": "CUSTOM",
          "taskName": "常减压装置专项评估",
          "status": "SUCCESS",
          "progress": 100,
          "loopCount": 25,
          "startedAt": "2026-06-20T10:00:00Z",
          "completedAt": "2026-06-20T10:01:30Z",
          "triggeredBy": "zhang.san"
        },
        {
          "taskId": "task-uuid-yyy",
          "taskType": "STANDARD",
          "taskName": "标准评估-2026-06-20T09:00:00",
          "status": "SUCCESS",
          "progress": 100,
          "loopCount": 156,
          "startedAt": "2026-06-20T09:00:00Z",
          "completedAt": "2026-06-20T09:02:00Z",
          "triggeredBy": "system"
        }
      ],
      "total": 48,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：
  * 返回评估任务列表，支持按 `taskType`/`status`/时间范围/`keyword` 筛选。
  * `taskType=STANDARD` 为系统定时任务，`triggeredBy` 为 `system`；`taskType=CUSTOM` 为用户自定义任务，`triggeredBy` 为触发用户。
  * 列表项不含完整结果数据，需通过 §2.7.6.3 查询任务详情获取结果。
  * 标准任务默认保留最近 7 天记录，自定义任务默认保留最近 30 天记录。

---

### 2.8 指标配置接口 (Metric Config Batch Operations)

本组 API 提供指标配置的批量读写能力，便于前端配置界面一次性加载/保存全部指标配置。与 §2.3.3/§2.3.4 单条操作接口互补，批量保存时后端事务化处理，任一项校验失败则全部回滚。

**v4.0 指标体系升级（3+1+8 结构，对齐《关键算法设计说明》v2.0）**：
* **3 核心指标（CORE，参与综合评分权重配置）**：准确率（accuracy_rate）/ 快速率（fast_rate）/ 稳定率（steady_rate）。3 项核心指标权重总和须为 100%，综合评分 = Σ(核心指标值 × 权重)。
* **1 投用指标（COMMISSIONING，作为折扣因子，不参与权重配置）**：有效自控率（effective_auto_rate）。综合评分 = 核心指标加权得分 × 投用指标折扣因子（discount_factor），未投用（自控率为 0）时综合评分折半，全投用时折扣因子为 1.0。
* **8 辅助诊断指标（AUXILIARY_DIAGNOSTIC，不参与权重配置与综合评分）**：好值率（good_value_rate）/ 振荡率（oscillation_rate）/ 饱和率（saturation_rate）/ 粘滞指数（stiction_index）/ 过激指数（overaggressive_index）/ 过保守指数（overconservative_index）/ 外扰指数（disturbance_index）/ 质量异常率（quality_abnormal_rate）。辅助诊断指标仅用于诊断标签生成与看板展示，权重字段固定为 `null`。
* 每项指标配置增加 `category` 字段（`CORE`/`COMMISSIONING`/`AUXILIARY_DIAGNOSTIC`）标识所属类别；投用指标增加 `isDiscountFactor=true` 标记。
* 权重校验仅针对 3 项核心指标（总和须为 100%），投用指标与辅助诊断指标不参与权重总和校验。

#### 2.8.1 批量获取指标配置 (Batch Get Metric Config)

* **URL**: `GET /api/v1/configs/metrics`
* **权限**: 查看层及以上（所有角色可查看配置，仅系统管理员可编辑）
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "coreMetrics": [
        {
          "metricId": "uuid-c01",
          "metricKey": "accuracy_rate",
          "metricName": "准确率",
          "category": "CORE",
          "formula": "duration(abs(pv - sp) <= pv_range * 0.05) / duration(*) * 100",
          "weight": 40,
          "threshold": { "min": 0, "max": 100, "alert": 80 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "PV 偏离 SP 在 5% 量程内的时长占比（核心指标）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-c02",
          "metricKey": "fast_rate",
          "metricName": "快速率",
          "category": "CORE",
          "formula": "duration(rise_time <= rise_time_threshold) / duration(*) * 100",
          "weight": 30,
          "threshold": { "min": 0, "max": 100, "alert": 75 },
          "controlType": "FAST",
          "isEnabled": true,
          "description": "设定值变化后 PV 响应速度达标（上升时间 ≤ 阈值）的时长占比（核心指标，对齐 v2.0）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-c03",
          "metricKey": "steady_rate",
          "metricName": "稳定率",
          "category": "CORE",
          "formula": "duration(abs(pv - sp) <= pv_range * 0.02) / duration(*) * 100",
          "weight": 30,
          "threshold": { "min": 0, "max": 100, "alert": 85 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "PV 偏离 SP 在 2% 量程内的时长占比（核心指标）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        }
      ],
      "commissioningMetric": {
        "metricId": "uuid-m01",
        "metricKey": "effective_auto_rate",
        "metricName": "有效自控率",
        "category": "COMMISSIONING",
        "isDiscountFactor": true,
        "formula": "sum(mode in [Auto, Cascade] AND pvQuality == Good) / count(*) * 100",
        "weight": null,
        "threshold": { "min": 0, "max": 100, "alert": 90 },
        "controlType": "STABLE",
        "isEnabled": true,
        "description": "控制器处于自动/串级模式且 PV 质量良好的时长占比，作为综合评分折扣因子（投用指标）",
        "algorithmVersion": "KPI_CALC_v1.0",
        "updatedAt": "2026-06-25T10:00:00Z",
        "updatedBy": "admin"
      },
      "auxiliaryDiagnosticMetrics": [
        {
          "metricId": "uuid-a01",
          "metricKey": "good_value_rate",
          "metricName": "好值率",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "sum(quality==Good) / count(*) * 100",
          "weight": null,
          "threshold": { "min": 0, "max": 100, "alert": 80 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "剔除通讯中断、超量程、冻结等无效数据后的时长占比，基于 PV tag 质量码统计（辅助诊断指标）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a02",
          "metricKey": "oscillation_rate",
          "metricName": "振荡率",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "duration(oscillation_detected == true) / duration(*) * 100",
          "weight": null,
          "threshold": { "min": 0, "max": 100, "alert": 20 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "检测到振荡的时长占比（辅助诊断指标，对应 OSCILLATION 标签）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a03",
          "metricKey": "saturation_rate",
          "metricName": "饱和率",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "duration(op >= 95 OR op <= 5) / duration(*) * 100",
          "weight": null,
          "threshold": { "min": 0, "max": 100, "alert": 15 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "OP 输出饱和（≥95% 或 ≤5%）的时长占比（辅助诊断指标，对应 OUTPUT_SATURATION 标签）",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a04",
          "metricKey": "stiction_index",
          "metricName": "粘滞指数",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "pv_op_scatter_fitting_index",
          "weight": null,
          "threshold": { "min": 0, "max": 1, "alert": 0.6 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "阀门粘滞指数（0-1，辅助诊断指标，对应 VALVE_STICTION 标签）",
          "algorithmVersion": "STICTION_CH_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a05",
          "metricKey": "overaggressive_index",
          "metricName": "过激指数",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "pid_gain_deviation_index",
          "weight": null,
          "threshold": { "min": 0, "max": 1, "alert": 0.4 },
          "controlType": "FAST",
          "isEnabled": true,
          "description": "PID 参数过激指数（0-1，辅助诊断指标，对应 OVERAGGRESSIVE 标签）",
          "algorithmVersion": "OVERAGGRESSIVE_PID_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a06",
          "metricKey": "overconservative_index",
          "metricName": "过保守指数",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "response_time_deviation_index",
          "weight": null,
          "threshold": { "min": 0, "max": 1, "alert": 0.5 },
          "controlType": "SLOW",
          "isEnabled": true,
          "description": "PID 参数过保守指数（0-1，辅助诊断指标，对应 OVERCONSERVATIVE 标签）",
          "algorithmVersion": "OVERCONSERVATIVE_PID_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a07",
          "metricKey": "disturbance_index",
          "metricName": "外扰指数",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "spectral_disturbance_concentration_index",
          "weight": null,
          "threshold": { "min": 0, "max": 1, "alert": 0.5 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "外部扰动频繁指数（0-1，辅助诊断指标，对应 EXTERNAL_DISTURBANCE 标签）",
          "algorithmVersion": "DISTURBANCE_SPEC_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a08",
          "metricKey": "quality_abnormal_rate",
          "metricName": "质量异常率",
          "category": "AUXILIARY_DIAGNOSTIC",
          "formula": "sum(pvQuality in [Bad, Uncertain]) / count(*) * 100",
          "weight": null,
          "threshold": { "min": 0, "max": 100, "alert": 10 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "PV 质量码为 Bad/Uncertain 的时长占比（辅助诊断指标，对应 QUALITY_ABNORMAL 标签）",
          "algorithmVersion": "QUALITY_CHECK_v1.0",
          "updatedAt": "2026-06-25T10:00:00Z",
          "updatedBy": "admin"
        }
      ],
      "coreTotalWeight": 100,
      "coreWeightValid": true,
      "structureVersion": "3+1+8"
    }
  }
  ```
* **说明**：
  * **v4.0 结构升级**：响应从单一 `items` 数组改为按类别分组的 `coreMetrics`（3 项）/`commissioningMetric`（1 项）/`auxiliaryDiagnosticMetrics`（8 项）三段式结构，`structureVersion` 标识结构版本（`3+1+8`）。
  * `category` 枚举值 `CORE`（核心指标）/`COMMISSIONING`（投用指标）/`AUXILIARY_DIAGNOSTIC`（辅助诊断指标）。
  * **核心指标权重配置**：仅 3 项核心指标（accuracy_rate/fast_rate/steady_rate）参与权重配置，`coreTotalWeight` 标识核心指标权重总和，`coreWeightValid` 标识是否为 100%。
  * **投用指标作为折扣因子**：`commissioningMetric` 的 `isDiscountFactor=true`，`weight=null`，不参与权重总和校验；综合评分 = 核心指标加权得分 × 投用折扣因子。
  * **辅助诊断指标不参与权重配置**：`auxiliaryDiagnosticMetrics` 每项 `weight=null`，不参与权重总和校验，仅用于诊断标签生成与看板展示。
  * `threshold` 为 JSONB 对象 `{min, max, alert}`（对齐 C3 修正）；`controlType` 枚举值 `STABLE`/`SLOW`/`FAST`/`LOGIC`（对齐§4.7.3 默认权重配置）。
  * `formula` 字段表达式引擎采用 `simpleeval` 安全沙箱（对齐 C7 修正），可用变量：pv/sp/op/mode/pv_quality/pvValid/timestamps/pv_range/n；可用函数：sum/mean/std/count/count_if/abs/sqrt/min/max/duration。

#### 2.8.2 批量更新指标配置 (Batch Update Metric Config)

* **URL**: `PUT /api/v1/configs/metrics`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Request Body**:
  ```json
  {
    "coreMetrics": [
      {
        "metricId": "uuid-c01",
        "formula": "duration(abs(pv - sp) <= pv_range * 0.05) / duration(*) * 100",
        "weight": 45,
        "threshold": { "min": 0, "max": 100, "alert": 80 },
        "controlType": "STABLE",
        "isEnabled": true,
        "description": "更新准确率公式与权重"
      },
      {
        "metricId": "uuid-c02",
        "weight": 25,
        "threshold": { "min": 0, "max": 100, "alert": 75 },
        "controlType": "FAST",
        "isEnabled": true
      },
      {
        "metricId": "uuid-c03",
        "weight": 30,
        "isEnabled": true
      }
    ],
    "commissioningMetric": {
      "metricId": "uuid-m01",
      "formula": "sum(mode in [Auto, Cascade] AND pvQuality == Good) / count(*) * 100",
      "threshold": { "min": 0, "max": 100, "alert": 90 },
      "isEnabled": true,
      "description": "更新有效自控率公式"
    },
    "auxiliaryDiagnosticMetrics": [
      {
        "metricId": "uuid-a01",
        "threshold": { "min": 0, "max": 100, "alert": 85 },
        "isEnabled": true
      },
      {
        "metricId": "uuid-a04",
        "threshold": { "min": 0, "max": 1, "alert": 0.65 },
        "isEnabled": true
      }
    ]
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "updatedCount": 6,
      "coreMetrics": [
        {
          "metricId": "uuid-c01",
          "metricKey": "accuracy_rate",
          "metricName": "准确率",
          "category": "CORE",
          "formula": "duration(abs(pv - sp) <= pv_range * 0.05) / duration(*) * 100",
          "weight": 45,
          "threshold": { "min": 0, "max": 100, "alert": 80 },
          "controlType": "STABLE",
          "isEnabled": true,
          "description": "更新准确率公式与权重",
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-26T10:30:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-c02",
          "metricKey": "fast_rate",
          "metricName": "快速率",
          "category": "CORE",
          "weight": 25,
          "threshold": { "min": 0, "max": 100, "alert": 75 },
          "controlType": "FAST",
          "isEnabled": true,
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-26T10:30:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-c03",
          "metricKey": "steady_rate",
          "metricName": "稳定率",
          "category": "CORE",
          "weight": 30,
          "threshold": { "min": 0, "max": 100, "alert": 85 },
          "controlType": "STABLE",
          "isEnabled": true,
          "algorithmVersion": "KPI_CALC_v1.0",
          "updatedAt": "2026-06-26T10:30:00Z",
          "updatedBy": "admin"
        }
      ],
      "commissioningMetric": {
        "metricId": "uuid-m01",
        "metricKey": "effective_auto_rate",
        "metricName": "有效自控率",
        "category": "COMMISSIONING",
        "isDiscountFactor": true,
        "formula": "sum(mode in [Auto, Cascade] AND pvQuality == Good) / count(*) * 100",
        "weight": null,
        "threshold": { "min": 0, "max": 100, "alert": 90 },
        "isEnabled": true,
        "algorithmVersion": "KPI_CALC_v1.0",
        "updatedAt": "2026-06-26T10:30:00Z",
        "updatedBy": "admin"
      },
      "auxiliaryDiagnosticMetrics": [
        {
          "metricId": "uuid-a01",
          "metricKey": "good_value_rate",
          "category": "AUXILIARY_DIAGNOSTIC",
          "weight": null,
          "threshold": { "min": 0, "max": 100, "alert": 85 },
          "isEnabled": true,
          "updatedAt": "2026-06-26T10:30:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-a04",
          "metricKey": "stiction_index",
          "category": "AUXILIARY_DIAGNOSTIC",
          "weight": null,
          "threshold": { "min": 0, "max": 1, "alert": 0.65 },
          "isEnabled": true,
          "updatedAt": "2026-06-26T10:30:00Z",
          "updatedBy": "admin"
        }
      ],
      "coreTotalWeight": 100,
      "coreWeightValid": true,
      "structureVersion": "3+1+8"
    }
  }
  ```
* **说明**：
  * **v4.0 结构升级**：请求/响应体从单一 `items` 数组改为 `coreMetrics`/`commissioningMetric`/`auxiliaryDiagnosticMetrics` 三段式结构，与 §2.8.1 一致。
  * 批量更新指标配置，事务化处理：任一项校验失败则全部回滚，返回 `ERR_METRIC_WEIGHT_SUM` 或对应字段错误码。
  * **权重校验仅针对核心指标**：若本次变更导致 3 项核心指标（accuracy_rate/fast_rate/steady_rate）权重总和 ≠ 100%，返回 `ERR_METRIC_WEIGHT_SUM`。投用指标与辅助诊断指标的 `weight` 字段固定为 `null`，传入非 null 值将被忽略并告警。
  * 投用指标（commissioningMetric）的 `isDiscountFactor` 字段不可修改，由系统固定为 `true`；可更新 formula/threshold/isEnabled/description。
  * 辅助诊断指标（auxiliaryDiagnosticMetrics）不参与权重配置，可更新 formula/threshold/controlType/isEnabled/description。
  * `threshold` 为 JSONB 对象（对齐 C3 修正）；`controlType` 枚举值 `STABLE`/`SLOW`/`FAST`/`LOGIC`。
  * `formula` 字段表达式引擎采用 `simpleeval` 安全沙箱（对齐 C7 修正），可用变量：pv/sp/op/mode/pv_quality/pvValid/timestamps/pv_range/n；可用函数：sum/mean/std/count/count_if/abs/sqrt/min/max/duration；禁止 import/exec/eval/属性访问，表达式长度限制 500 字符，执行超时 5 秒。
  * 配置变更即时生效，无需重启服务；变更记录审计日志（操作人/时间/变更前后值）。
  * 指标停用后：核心指标停用导致权重重新归一化（其他核心指标按比例分配）；辅助诊断指标停用后对应诊断标签不再生成；投用指标停用后综合评分折扣因子按 1.0 处理（即不折扣）。

---

### 2.9 诊断配置接口 (Diagnosis Config Batch Operations)

本组 API 提供诊断配置的批量读写能力，便于前端配置界面一次性加载/保存全部 8 类诊断标签配置。与 §2.4.3/§2.4.4 单条操作接口互补，批量保存时后端事务化处理，任一项校验失败则全部回滚。

#### 2.9.1 批量获取诊断配置 (Batch Get Diagnosis Config)

* **URL**: `GET /api/v1/configs/diagnosis`
* **权限**: 查看层及以上（所有角色可查看配置，仅系统管理员可编辑）
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "diagId": "uuid-xxx",
          "diagKey": "oscillation_fft",
          "diagName": "振荡检测 FFT",
          "label": "OSCILLATION",
          "algorithmType": "FFT",
          "calcMethod": "auto_correlation",
          "params": {
            "windowSize": 1024,
            "overlap": 0.5,
            "minFrequency": 0.01,
            "maxFrequency": 1.0
          },
          "threshold": { "amplitude": 1.5, "confidence": 0.7 },
          "isEnabled": true,
          "algorithmVersion": "OSC_FFT_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-yyy",
          "diagKey": "stiction_scatter",
          "diagName": "粘滞检测散点拟合",
          "label": "VALVE_STICTION",
          "algorithmType": "ScatterFitting",
          "calcMethod": "pv_op_scatter",
          "params": { "fittingType": "ellipse", "minPoints": 100 },
          "threshold": { "stictionIndex": 0.6, "fittingScore": 0.8 },
          "isEnabled": true,
          "algorithmVersion": "STICTION_CH_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-zzz",
          "diagKey": "overaggressive_pid",
          "diagName": "参数过激检测",
          "label": "OVERAGGRESSIVE",
          "algorithmType": "PIDAnalysis",
          "calcMethod": "pid_gain_analysis",
          "params": { "gainMarginThreshold": 0.5, "phaseMarginThreshold": 30 },
          "threshold": { "gainDeviation": 0.3, "oscillationIndex": 0.4 },
          "isEnabled": true,
          "algorithmVersion": "OVERAGGRESSIVE_PID_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-www",
          "diagKey": "overconservative_pid",
          "diagName": "参数过保守检测",
          "label": "OVERCONSERVATIVE",
          "algorithmType": "PIDAnalysis",
          "calcMethod": "pid_gain_analysis",
          "params": { "responseTimeThreshold": 60, "settlingTimeThreshold": 120 },
          "threshold": { "riseTimeDeviation": 0.5, "settlingTimeDeviation": 0.5 },
          "isEnabled": true,
          "algorithmVersion": "OVERCONSERVATIVE_PID_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-vvv",
          "diagKey": "external_disturbance",
          "diagName": "外扰频繁检测",
          "label": "EXTERNAL_DISTURBANCE",
          "algorithmType": "DisturbanceAnalysis",
          "calcMethod": "spectral_analysis",
          "params": { "windowSize": 2048, "frequencyBand": [0.01, 0.5] },
          "threshold": { "disturbanceIndex": 0.5, "frequencyConcentration": 0.6 },
          "isEnabled": true,
          "algorithmVersion": "DISTURBANCE_SPEC_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-uuu",
          "diagKey": "quality_abnormal",
          "diagName": "PV 质量异常检测",
          "label": "QUALITY_ABNORMAL",
          "algorithmType": "QualityCheck",
          "calcMethod": "quality_code_stats",
          "params": { "windowSize": 3600 },
          "threshold": { "badRate": 0.1, "uncertainRate": 0.2 },
          "isEnabled": true,
          "algorithmVersion": "QUALITY_CHECK_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-ttt",
          "diagKey": "output_saturation",
          "diagName": "输出饱和检测",
          "label": "OUTPUT_SATURATION",
          "algorithmType": "SaturationCheck",
          "calcMethod": "op_range_stats",
          "params": { "highThreshold": 95, "lowThreshold": 5, "minDuration": 60 },
          "threshold": { "saturationRate": 0.15 },
          "isEnabled": true,
          "algorithmVersion": "SATURATION_CHECK_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-sss",
          "diagKey": "manual_review",
          "diagName": "人工复核",
          "label": "MANUAL_REVIEW",
          "algorithmType": "Manual",
          "calcMethod": "manual_trigger",
          "params": {},
          "threshold": { "compositeScore": 40 },
          "isEnabled": true,
          "algorithmVersion": "MANUAL_REVIEW_v1.0",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        }
      ]
    }
  }
  ```
* **说明**：返回全部 8 类诊断标签配置（对齐 C6 修正）；`label` 枚举为 8 类（OSCILLATION/VALVE_STICTION/OVERAGGRESSIVE/OVERCONSERVATIVE/EXTERNAL_DISTURBANCE/QUALITY_ABNORMAL/OUTPUT_SATURATION/MANUAL_REVIEW）；`calcMethod` 字段标识计算方法（对齐 C4 修正）；`threshold` 为 JSONB 对象（对齐 C3 修正）；`algorithmVersion` 标识算法版本号。

#### 2.9.2 批量更新诊断配置 (Batch Update Diagnosis Config)

* **URL**: `PUT /api/v1/configs/diagnosis`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Request Body**:
  ```json
  {
    "items": [
      {
        "diagId": "uuid-xxx",
        "label": "OSCILLATION",
        "algorithmType": "FFT",
        "calcMethod": "auto_correlation",
        "params": {
          "windowSize": 2048,
          "overlap": 0.5,
          "minFrequency": 0.01,
          "maxFrequency": 1.0
        },
        "threshold": { "amplitude": 1.8, "confidence": 0.75 },
        "isEnabled": true
      },
      {
        "diagId": "uuid-yyy",
        "label": "VALVE_STICTION",
        "algorithmType": "ScatterFitting",
        "calcMethod": "pv_op_scatter",
        "params": { "fittingType": "ellipse", "minPoints": 200 },
        "threshold": { "stictionIndex": 0.65, "fittingScore": 0.85 },
        "isEnabled": true
      }
    ]
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "updatedCount": 2,
      "items": [
        {
          "diagId": "uuid-xxx",
          "diagKey": "oscillation_fft",
          "diagName": "振荡检测 FFT",
          "label": "OSCILLATION",
          "algorithmType": "FFT",
          "calcMethod": "auto_correlation",
          "params": {
            "windowSize": 2048,
            "overlap": 0.5,
            "minFrequency": 0.01,
            "maxFrequency": 1.0
          },
          "threshold": { "amplitude": 1.8, "confidence": 0.75 },
          "isEnabled": true,
          "algorithmVersion": "OSC_FFT_v1.0",
          "updatedAt": "2026-06-20T10:30:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-yyy",
          "diagKey": "stiction_scatter",
          "diagName": "粘滞检测散点拟合",
          "label": "VALVE_STICTION",
          "algorithmType": "ScatterFitting",
          "calcMethod": "pv_op_scatter",
          "params": { "fittingType": "ellipse", "minPoints": 200 },
          "threshold": { "stictionIndex": 0.65, "fittingScore": 0.85 },
          "isEnabled": true,
          "algorithmVersion": "STICTION_CH_v1.0",
          "updatedAt": "2026-06-20T10:30:00Z",
          "updatedBy": "admin"
        }
      ]
    }
  }
  ```
* **说明**：
  * 批量更新诊断配置，事务化处理：任一项校验失败则全部回滚，返回对应字段错误码。
  * `label` 枚举为 8 类诊断标签（对齐 C6 修正）；`calcMethod` 字段标识计算方法（对齐 C4 修正）；`threshold` 为 JSONB 对象（对齐 C3 修正）。
  * 配置变更即时生效，无需重启服务；指标停用后相关诊断标签不再生成。
  * 变更记录审计日志（操作人/时间/变更前后值）。

---

## 3. 标准错误码与异常响应 (Error Handling)

所有的非 2xx 响应必须遵循统一的错误格式：

```json
{
  "errorCode": "ERR_TS_001",
  "errorMessage": "Requested time window exceeds maximum allowed range (30 days)",
  "details": "请缩小查询时间范围或使用聚合接口"
}
```

### 3.1 核心错误码映射

| 错误码 | HTTP 状态 | 错误说明 | 触发场景 |
|---|---|---|---|
| `ERR_AUTH_401` | 401 | Token 缺失或失效 | JWT 未携带、过期或签名无效。 |
| `ERR_AUTH_403` | 403 | 越权操作 | 当前角色无权执行该操作（如 Sponsor 尝试修改状态、非管理员尝试修改配置）。 |
| `ERR_DATA_INCONCLUSIVE` | 422 | 数据不足，无法计算 | 目标回路的指定时间窗内有效数据不足（好值率过低），无法计算结果。 |
| `ERR_TS_001` | 400 | 时间窗超出最大允许范围 | 波形查询时间窗超过 30 天。 |
| `ERR_TS_DOWNSAMPLE_REQ` | 400 | 要求开启降采样 | 数据量超过 10 万点，要求必须开启 `downsample=true`。 |

### 3.2 v3.0 新增错误码

| 错误码 | HTTP 状态 | 错误说明 | 触发场景 |
|---|---|---|---|
| `ERR_TAG_NOT_FOUND` | 404 | Tag 未找到 | 更新回路 tag 关联时，请求体中的 tagId 在 `tag_registry` 中不存在。 |
| `ERR_LOOP_TAG_REQUIRED` | 400 | 回路必填 tag 缺失 | 更新回路 tag 关联时，请求体中 PV/SP/OP/MODE 全部缺失或为 null，无法完成最小关联校验。 |
| `ERR_METRIC_WEIGHT_SUM` | 400 | 性能指标权重总和不为 100% | 更新性能指标配置时，3 项核心指标（accuracy_rate/fast_rate/steady_rate）权重总和 ≠ 100%；或更新回路扩展配置时 `scoreWeights` 总和 ≠ 100%。投用指标与辅助诊断指标不参与权重总和校验。 |
| `ERR_CONFIG_FORBIDDEN` | 403 | 越权修改配置 | 非系统管理员角色尝试调用配置类 API（性能指标配置/诊断指标配置/引擎规则配置的更新接口）。 |
| `ERR_DISCOUNT_FACTOR_READONLY` | 400 | 投用指标折扣因子字段只读 | 更新性能指标配置时，尝试修改投用指标（effective_auto_rate）的 `weight` 或 `isDiscountFactor` 字段，该字段由系统固定为 `null`/`true`。 |
| `ERR_AUXILIARY_METRIC_WEIGHT_FORBIDDEN` | 400 | 辅助诊断指标不允许配置权重 | 更新性能指标配置时，尝试为辅助诊断指标（好值率/振荡率/饱和率等）设置 `weight` 非 null 值，辅助诊断指标 `weight` 固定为 `null`，不参与权重配置。 |

### 3.3 v3.2 新增错误码（算法服务相关）

| 错误码 | HTTP 状态 | 错误说明 | 触发场景 |
|---|---|---|---|
| `ERR_TASK_NOT_FOUND` | 404 | 算法任务未找到 | 查询算法任务状态时，`task_id` 不存在或已超过 7 天保留期。 |
| `ERR_ALGORITHM_DATA_INSUFFICIENT` | 422 | 算法数据不足 | KPI 计算/诊断分析时，回路在指定时间窗内有效数据点不足（<100），无法执行算法。 |
| `ERR_ALGORITHM_TIMEOUT` | 504 | 算法执行超时 | 算法任务执行超过最大允许时长（KPI 5 分钟/诊断 10 分钟/整定 15 分钟），任务标记为 FAILED。 |
| `ERR_ALGORITHM_INVALID_PARAMS` | 400 | 算法参数无效 | 算法任务请求参数校验失败（如 `modelType` 不在 FOPDT/SOPDT/IPDT 枚举内、`method` 不在允许枚举内、`lambda` 为负数等）。 |
| `ERR_FORMULA_INVALID` | 400 | 表达式语法错误 | 性能指标 `formula` 字段无法通过 `simpleeval` 安全沙箱解析（语法错误/禁用函数/超长/超时）。 |
| `ERR_LABEL_INVALID` | 400 | 诊断标签枚举无效 | 诊断配置/分析请求中 `label` 字段不在 8 类枚举（OSCILLATION/VALVE_STICTION/OVERAGGRESSIVE/OVERCONSERVATIVE/EXTERNAL_DISTURBANCE/QUALITY_ABNORMAL/OUTPUT_SATURATION/MANUAL_REVIEW）内。 |
| `ERR_CONTROL_TYPE_INVALID` | 400 | 控制类型枚举无效 | 性能指标配置中 `controlType` 字段不在 STABLE/SLOW/FAST/LOGIC 枚举内。 |

### 3.4 错误响应示例

**越权修改配置**：
```json
{
  "errorCode": "ERR_CONFIG_FORBIDDEN",
  "errorMessage": "Only system administrator can modify configuration",
  "details": "性能指标配置仅系统管理员可修改，请联系管理员"
}
```

**Tag 未找到**：
```json
{
  "errorCode": "ERR_TAG_NOT_FOUND",
  "errorMessage": "Tag not found in registry",
  "details": "tagId 'uuid-xxx' 在 tag_registry 中不存在，请先触发 AAS 同步"
}
```

**回路必填 tag 缺失**：
```json
{
  "errorCode": "ERR_LOOP_TAG_REQUIRED",
  "errorMessage": "Required tags (PV/SP/OP/MODE) are all missing",
  "details": "回路关联至少需要提供 PV/SP/OP/MODE 中的一个必填 tag"
}
```

**性能指标权重总和不为 100%**：
```json
{
  "errorCode": "ERR_METRIC_WEIGHT_SUM",
  "errorMessage": "Total weight of core metrics must be 100%",
  "details": "当前 3 项核心指标（accuracy_rate/fast_rate/steady_rate）权重总和为 95%，请调整至 100%（投用指标与辅助诊断指标不参与权重校验）"
}
```

---

## 4. 通用约定

### 4.1 请求与响应格式

* 所有请求与响应均使用 `application/json` 编码（报表导出等异步任务除外）。
* 所有列表类接口支持分页，统一使用 `page` 与 `pageSize` 参数，响应中返回 `total`/`page`/`pageSize`。
* 时间字段统一使用 ISO8601 格式（如 `2026-06-20T10:00:00Z`）。
* UUID 字段统一使用标准 UUID 格式。

### 4.2 异步任务通用约定

报表导出、AAS 同步、整定计算等耗时操作采用异步任务模式：

* 请求返回 `202 Accepted`，响应体包含 `taskId`/`status`/`checkUrl`。
* 客户端通过 `checkUrl`（如 `/api/v1/tasks/{taskId}`）轮询任务状态。
* 任务状态枚举：`PROCESSING`（处理中）, `SUCCESS`（成功）, `FAILED`（失败）。
* 任务成功后，`checkUrl` 响应中包含结果下载链接或数据 URL。

### 4.3 权限层级约定

| 权限层级 | 角色 | 说明 |
|---|---|---|
| 查看层 | SPONSOR（生产技术 Sponsor） | 仅查看工厂/装置级全局看板与定期报告；不可访问单回路诊断详情、波形证据或异常跟踪编辑。 |
| 协同层 | PE_ENGINEER（工艺/设备工程师） | 查看诊断报告，协助排查，不可修改状态与配置。 |
| 执行层 | IC_ENGINEER（仪控工程师） | 配置回路与 tag 关联，查看性能看板，分析波形，标记异常跟踪状态。 |
| 管理层 | ADMIN（系统管理员） | 管理工厂模型，配置指标与算法阈值，管理用户，查看审计日志。 |
| 服务层 | EXPERT（外部专家） | 诊断复核，出具高级诊断意见与整定模型样例。 |

### 4.4 PV 质量码处理约定

* 数据质量主要针对 PV 值，PV tag 携带质量码（`Good`/`Bad`/`Uncertain`）。
* SP/OP/MODE/PID_P/PID_I/PID_D 不携带质量码。
* 波形 API 响应中 `pvQuality` 数组与 `pv` 数组等长，标识每个时间点的 PV 质量码。
* **v4.0 关键变更（保留所有点）**：所有数据点保留在时间序列中，不再以 `null` 屏蔽 `Bad` 点。
  * 每个数据点增加 `valid` 标记（`true`/`false`），与 `pv` 数组等长（对应字段 `pvValid`）。
  * `pvQuality=Good` 时 `pvValid=true`；`pvQuality=Bad`/`Uncertain` 时 `pvValid=false`。
  * PV 质量码为 `Bad` 时，`pv` 值保留（不再置为 `null`），`valid=false`。
  * PV 质量码为 `Uncertain` 时，`pv` 值保留，`valid=false`。
* **波形渲染约定**：
  * `Good` 点：实线渲染（`valid=true`）。
  * `Bad` 点：灰色虚线渲染（`valid=false`，原 v3.x 为断线，v4.0 改为灰色虚线保留连线以便观察趋势）。
  * `Uncertain` 点：黄色虚线渲染（`valid=false`）。
* **Metric Validity Mask（指标有效点掩码）**：不同指标使用不同的有效点判定规则决定参与计算的数据点，由算法服务根据指标 key 自动选择对应的 Validity Mask。
  * `good_value_rate`：有效点 = `pvQuality=Good` 的点（基于 PV 质量码统计）。
  * `auto_mode_rate`（辅助诊断指标，已并入 `effective_auto_rate` 投用指标）：有效点 = 全部点（不依赖 PV 质量码，基于 MODE tag 判定）；`effective_auto_rate` 有效点 = `mode in [Auto, Cascade]` 且 `pvQuality == Good` 的点。
  * `steady_rate`/`accuracy_rate`：有效点 = `pvQuality=Good` 且 `mode in [Auto, Cascade]` 的点。
  * `oscillation_rate`：有效点 = `pvQuality=Good` 的高频 PV/OP 点（`tagGroup=PVOP_HF`）。
  * `saturation_rate`：有效点 = 全部点（基于 OP 范围判定，不依赖 PV 质量码）。
  * 自定义指标可在指标配置中通过 `validity_mask` 字段指定有效点规则。
* **qualityPolicy 策略**（对齐 §2.4.5）：
  * `KEEP_ALL_WITH_VALIDITY`（默认）：保留全部点并附带 `valid` 标记，算法按 Metric Validity Mask 过滤有效点。
  * `KEEP_ALL`：保留全部点不区分有效性，算法按指标逻辑自行处理。
* KPI 好值率基于 PV 质量码统计，`Bad`/`Uncertain` 时段不计入好值（对应 `valid_rate` 字段，对齐 §2.7.1 v4.0 数据血缘）。
* `valid_rate`（有效数据率，0-1）= 有效点数 / 总点数，用于推导 `confidence_level`（A/B/C/D/E，对齐 §2.7.1）。

### 4.5 数据模型映射约定

本规范涉及的 API 资源对应 DDS v6.0 的 PostgreSQL 表与 TDengine 超级表，前后端字段命名遵循以下映射规则（对齐 DDS v6.0 与代码 ORM 模型）：

| API 资源领域 | PostgreSQL 表（ORM 模型） | 说明 |
|---|---|---|
| 工厂层级 | `plant_node`（`PlantNode`） | 工厂/装置/单元多级层级 |
| AAS Tag | `tag_registry`（`TagRegistry`） | AAS 同步的 OPC tag 位号注册表 |
| 回路台账 | `loop_ledger`（`LoopLedger`） | 回路实体，含 `control_type`/`importance_level`/`include_in_evaluation` |
| 回路-Tag 关联 | `loop_tag_mapping`（`LoopTagMapping`） | 7 个 OPC tag 角色映射 |
| 指标配置 | `metric_config`（`MetricConfig`） | 3+1+8 体系指标配置，含 `category`/`is_discount_factor`/`grading_thresholds` |
| 引擎规则 | `engine_rule`（`EngineRule`） | 评估引擎调度与执行规则 |
| KPI 小时快照 | `kpi_snapshot_hourly`（`KpiSnapshotHourly`） | KPI 计算结果小时级快照，含 5 个数据血缘字段 + `data_lineage` JSONB |
| KPI 自定义快照 | `kpi_snapshot_custom`（`KpiSnapshotCustom`） | 自定义评估任务结果，含 `steady_rate`（修正自 `stability_rate`） |
| 装置级 KPI 汇总 | `unit_kpi_summary`（`UnitKpiSummary`） | 装置级聚合，含 `excluded_loops`/`status` |
| 节点级 KPI 快照 | `kpi_node_snapshot_hourly`/`daily`/`monthly` | 节点级 KPI 3 张快照表 |
| 指标数据需求 | `clpm_metric_data_requirement`（`ClpmMetricDataRequirement`） | 指标计算所需数据需求 |
| 诊断配置 | `diagnosis_config`（`DiagnosisConfig`） | 8 类诊断标签算法配置 |
| 诊断结果 | `diagnosis_result`（`DiagnosisResult`） | 诊断分析结果 |
| 诊断标签 | `diagnosis_tag`（`DiagnosisTag`） | 诊断标签生命周期管理 |
| 异常跟踪 | `action_tracker`（`ActionTracker`） | 异常跟踪记录，状态机 PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED |
| 整定记录 | `tuning_record`（`TuningRecord`） | 整定任务记录，状态机 DRAFT/RUNNING/COMPLETED/ROLLED_BACK |
| 回路模式映射 | `loop_mode_mapping`（`LoopModeMapping`） | MODE tag 值与控制模式映射 |
| 回路类型权重 | `loop_type_weight`（`LoopTypeWeight`） | 按回路类型（STABLE/SLOW/FAST/LOGIC）的权重模板 |
| 回路级别权重 | `loop_level_weight`（`LoopLevelWeight`） | 按回路重要性级别（HIGH/MEDIUM/LOW）的权重 |
| 系统用户 | `sys_user`（`SysUser`） | 用户账号，角色用枚举字段（非独立角色表） |
| 审计日志 | `sys_audit_log`（`SysAuditLog`） | 操作审计日志，不可物理删除 |
| 系统配置 | `sys_config`（`SysConfig`） | 系统级配置键值对 |
| 报表记录 | `report_record`（`ReportRecord`） | 报表生成记录 |
| 报表配置 | `report_config`（`ReportConfig`） | 报表周期与模板配置 |

**字段命名约定**：
* API 响应字段统一使用 `camelCase`（如 `loopId`/`tagName`/`scoreWeights`），数据库字段统一使用 `snake_case`（如 `loop_id`/`tag_name`/`score_weights`），由后端 ORM 自动转换。
* 时间字段在 API 中使用 ISO8601 字符串，在数据库中存储为 `TIMESTAMP WITH TIME ZONE`。
* UUID 字段在 API 与数据库中均使用标准 UUID 格式字符串。
* JSONB 字段（如 `threshold`/`grading_thresholds`/`data_lineage`/`score_weights`）在 API 中以 JSON 对象形式返回。

### 4.6 时序数据存储约定

回路时序数据存储于 TDengine 超级表 `st_loop_data`，与 PostgreSQL 业务表分离（对齐 DDS v6.0 §3）：

* **超级表结构**：`st_loop_data`，子表命名规则 `loop_{loop_id}`。
* **Tag 列**（2 个）：`loop_id`（UUID）、`plant_node_id`（UUID）。
* **Field 列**（9 个）：`ts`（时间戳）、`pv`（过程变量）、`sp`（设定值）、`op`（控制器输出）、`mode`（控制模式）、`quality`（PV 质量码，1=Good/0=Bad/2=Uncertain，对齐 TDengine schema；同时支持 OPC DA 192=Good）、`pid_p`/`pid_i`/`pid_d`（PID 参数）。
* **统一 1 秒采集**：所有 tag 统一以 1 秒采样频率写入 TDengine；指标计算层按需降采样（LTTB，`maxPoints=2000`，30 天时间窗口）。
* **质量码处理**：PV 质量码为 `Bad`/`Uncertain` 时数据点保留（不置 null），`pvValid=false`；KPI 计算按 Metric Validity Mask 过滤有效点（对齐 §4.4）。
* **数据读取接口**：时序数据读取统一通过 §2.4.5 波形接口与 §2.7.5 DataPlanner 内部接口，禁止直接暴露 TDengine 查询接口。

### 4.7 支撑性 API 领域说明

本规范 §2.1-§2.9 定义了 6 大业务 API 领域（dashboard/loops/performance/diagnosis/tuning/users）+ 算法服务（§2.7）+ 配置批量操作（§2.8/§2.9）。除此之外，系统还存在以下支撑性 API 领域（对齐实现契约 v2.0 与代码实际）：

| API 领域 | 路径前缀 | 说明 | 对应章节 |
|---|---|---|---|
| 认证授权 | `/api/v1/auth/*` | 登录/登出/Token 刷新/当前用户/修改密码 | §5 |
| 工厂层级 | `/api/v1/plant-nodes/*` | 工厂节点 CRUD | §2.2.1-§2.2.4 |
| AAS 同步 | `/api/v1/aas/*` | AAS 配置/同步/Tag 查询 | §2.2.5-§2.2.6 |
| Tag 管理 | `/api/v1/tags/*` | Tag 列表/详情/导入导出/批量删除 | 代码实际，对齐 DDS v6.0 §2 |
| 实时数据 | `/api/v1/realtime/*` | 实时数据查询 | 代码实际 |
| WebSocket 推送 | `/api/v1/ws/*` | 实时数据 WebSocket 推送（对齐 UIUX v6.0 实时刷新需求） | 代码实际 |
| 时序数据 | `/api/v1/timeseries/*` | 波形/散点图等高频时序数据查询 | §2.4.5 |
| 任务管理 | `/api/v1/tasks/*` | 评估任务触发/查询/取消/删除/通知 | §2.7.6 |
| 算法服务 | `/api/v1/algorithms/*` | KPI/诊断/整定算法任务 | §2.7.1-§2.7.4 |
| 数据计划 | `/api/v1/algorithms/dataplanner/*` | DataPlanner 内部接口（不对外暴露） | §2.7.5 |
| 节点级性能 | `/api/v1/performance/nodes/*` | 节点级 KPI 快照/趋势/排行/对比 | 代码实际，对齐 DDS v6.0 节点级 KPI 表 |
| 配置批量操作 | `/api/v1/configs/*` | 指标/诊断配置批量读写 + 类型/级别权重 + 权重模板 + 性能定级阈值 | §2.8/§2.9 + 代码实际 |
| 健康检查 | `/api/v1/health` | 服务健康与就绪检查 | 代码实际 |

**说明**：
* 上述支撑性 API 领域中，部分接口（如 `/api/v1/tags/*`、`/api/v1/realtime/*`、`/api/v1/ws/*`、`/api/v1/performance/nodes/*`、`/api/v1/configs/loop-type-weights/*`、`/api/v1/configs/loop-level-weights/*`、`/api/v1/configs/weight-templates/*`、`/api/v1/configs/grading-thresholds/*`、`/api/v1/health`）在代码中已实现但未在本规范中展开详细 Schema 定义，将在后续版本补全。
* 实现契约 v2.0 §4.4 声明"不新增 `/api/v1/configs/metrics` 与 `/api/v1/configs/diagnosis` 聚合接口"，但代码实际已实现这两类接口（§2.8/§2.9）；实现契约 v2.0 追认其存在，本规范保留 §2.8/§2.9 的接口定义。

---

## 5. 认证与授权 API

本章节定义系统认证与授权相关接口，为所有业务 API 的前置依赖。认证采用 JWT Bearer Token 方案，Access Token 有效期 30 分钟，Refresh Token 有效期 7 天。

### 5.1 用户登录 (Login)

* **URL**: `POST /api/v1/auth/login`
* **权限**: 公开（无需 Token）
* **描述**: 用户通过用户名/密码登录，获取 Access Token 和 Refresh Token。

**请求头**:

| Header | 值 | 说明 |
|---|---|---|
| Content-Type | application/json | 请求体编码格式 |

**请求参数 (Request Body)**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| username | String | 是 | 用户名（3-50 字符） |
| password | String | 是 | 密码（明文传输，HTTPS 加密通道保护，6-64 字符） |
| rememberMe | Boolean | 否 | 是否记住登录（true 时 Refresh Token 有效期延长至 30 天），默认 false |

**请求示例**:
```json
{
  "username": "admin",
  "password": "admin123",
  "rememberMe": false
}
```

**成功响应 (200 OK)**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "tokenType": "Bearer",
    "expiresIn": 1800,
    "user": {
      "id": "00000000-0000-0000-0000-000000000001",
      "username": "admin",
      "displayName": "系统管理员",
      "email": "admin@clpm.local",
      "role": "ADMIN",
      "permissions": [
        "dashboard:view",
        "loop:*",
        "performance:*",
        "diagnosis:*",
        "tuning:*",
        "system:*"
      ]
    }
  }
}
```

**响应字段说明**:

| 字段 | 类型 | 说明 |
|---|---|---|
| code | Integer | 业务状态码，0 表示成功（见 §6 统一响应规范） |
| message | String | 状态描述 |
| data.accessToken | String | JWT Access Token，有效期 30 分钟 |
| data.refreshToken | String | JWT Refresh Token，有效期 7 天（rememberMe=true 时 30 天） |
| data.tokenType | String | Token 类型，固定为 "Bearer" |
| data.expiresIn | Integer | Access Token 过期时间（秒），固定 1800 |
| data.user.id | String (UUID) | 用户唯一标识 |
| data.user.username | String | 用户名 |
| data.user.displayName | String | 显示名称 |
| data.user.email | String | 邮箱 |
| data.user.role | String | 角色枚举：ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR / EXPERT |
| data.user.permissions | Array<String> | 权限列表（模块:操作格式，* 表示通配） |

**错误响应**:

| HTTP 状态码 | errorCode | errorMessage | 触发条件 |
|---|---|---|---|
| 400 | ERR_VALIDATION | 请求参数校验失败 | username/password 为空或格式不符 |
| 401 | ERR_INVALID_CREDENTIALS | 用户名或密码错误 | 用户名不存在或密码不匹配 |
| 403 | ERR_ACCOUNT_DISABLED | 账户已禁用 | sys_user.is_active = false |
| 429 | ERR_TOO_MANY_ATTEMPTS | 登录尝试次数过多 | 5 分钟内连续失败 5 次，锁定 15 分钟 |

---

### 5.2 刷新 Token (Refresh Token)

* **URL**: `POST /api/v1/auth/refresh`
* **权限**: 公开（需携带有效的 Refresh Token）
* **描述**: 使用 Refresh Token 获取新的 Access Token。

**请求头**:

| Header | 值 | 说明 |
|---|---|---|
| Content-Type | application/json | 请求体编码格式 |

**请求参数 (Request Body)**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| refreshToken | String | 是 | 登录时获取的 Refresh Token |

**请求示例**:
```json
{
  "refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**成功响应 (200 OK)**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "tokenType": "Bearer",
    "expiresIn": 1800
  }
}
```

**错误响应**:

| HTTP 状态码 | errorCode | errorMessage | 触发条件 |
|---|---|---|---|
| 401 | ERR_TOKEN_EXPIRED | Token 已过期 | Refresh Token 已过期，需重新登录 |
| 401 | ERR_TOKEN_INVALID | Token 无效 | Refresh Token 签名错误或已被吊销 |

---

### 5.3 用户登出 (Logout)

* **URL**: `POST /api/v1/auth/logout`
* **权限**: 已认证（需携带 Access Token）
* **描述**: 登出当前会话，吊销 Access Token 和 Refresh Token。

**请求头**:

| Header | 值 | 说明 |
|---|---|---|
| Authorization | Bearer {accessToken} | JWT Access Token |

**请求参数**: 无

**成功响应 (200 OK)**:
```json
{
  "code": 0,
  "message": "success",
  "data": null
}
```

**说明**: 登出后，当前 Access Token 和 Refresh Token 加入 Redis 黑名单，直至自然过期。

---

### 5.4 获取当前用户信息 (Get Current User)

* **URL**: `GET /api/v1/auth/me`
* **权限**: 已认证（需携带 Access Token）
* **描述**: 获取当前登录用户的完整信息，包括角色和权限列表。前端路由守卫和菜单渲染依赖此接口。

**请求头**:

| Header | 值 | 说明 |
|---|---|---|
| Authorization | Bearer {accessToken} | JWT Access Token |

**请求参数**: 无

**成功响应 (200 OK)**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "00000000-0000-0000-0000-000000000002",
    "username": "ic_engineer",
    "displayName": "张三（仪控工程师）",
    "email": "zhangsan@clpm.local",
    "role": "IC_ENGINEER",
    "permissions": [
      "dashboard:view",
      "loop:view",
      "loop:edit",
      "loop:tag_mapping",
      "performance:view",
      "diagnosis:view",
      "diagnosis:tracker:edit",
      "tuning:view"
    ],
    "lastLoginAt": "2026-06-21T08:30:00Z",
    "defaultHome": "/dashboard"
  }
}
```

**响应字段说明**:

| 字段 | 类型 | 说明 |
|---|---|---|
| data.role | String | 角色枚举（同 §5.1） |
| data.permissions | Array<String> | 权限列表，格式为 `模块:操作`，用于前端路由守卫和按钮级权限控制 |
| data.lastLoginAt | String (ISO8601) | 最后登录时间 |
| data.defaultHome | String | 角色默认首页路径（对齐 UI/UX v4.0 §5.1） |

**权限列表枚举**:

| 权限标识 | 角色 | 说明 |
|---|---|---|
| dashboard:view | 全部角色 | 查看工作台 |
| loop:view | IC_ENGINEER, PE_ENGINEER, ADMIN | 查看回路管理 |
| loop:edit | IC_ENGINEER, ADMIN | 编辑回路/工厂层级 |
| loop:tag_mapping | IC_ENGINEER, ADMIN | Tag 关联管理 |
| performance:view | 全部角色 | 查看性能评估 |
| performance:config | ADMIN | 性能指标/引擎规则配置 |
| diagnosis:view | 全部角色 | 查看诊断中心 |
| diagnosis:tracker:edit | IC_ENGINEER, EXPERT | 异常跟踪状态变更 |
| diagnosis:config | ADMIN | 诊断指标配置 |
| tuning:view | IC_ENGINEER, EXPERT, ADMIN | 查看回路整定（P2） |
| system:user:manage | ADMIN | 用户与角色管理 |
| system:config | ADMIN | 系统配置 |
| system:audit:view | ADMIN | 审计日志查看 |

---

### 5.5 修改密码 (Change Password)

* **URL**: `PUT /api/v1/auth/password`
* **权限**: 已认证（需携带 Access Token）
* **描述**: 当前用户修改自己的密码。

**请求头**:

| Header | 值 | 说明 |
|---|---|---|
| Authorization | Bearer {accessToken} | JWT Access Token |
| Content-Type | application/json | 请求体编码格式 |

**请求参数 (Request Body)**:

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| oldPassword | String | 是 | 当前密码（明文，HTTPS 保护） |
| newPassword | String | 是 | 新密码（6-64 字符，需包含字母+数字） |

**请求示例**:
```json
{
  "oldPassword": "admin123",
  "newPassword": "Admin@2026"
}
```

**成功响应 (200 OK)**:
```json
{
  "code": 0,
  "message": "密码修改成功，请重新登录",
  "data": null
}
```

**错误响应**:

| HTTP 状态码 | errorCode | errorMessage | 触发条件 |
|---|---|---|---|
| 400 | ERR_VALIDATION | 新密码不符合复杂度要求 | 少于 6 字符或缺少字母/数字 |
| 401 | ERR_INVALID_CREDENTIALS | 当前密码错误 | oldPassword 不匹配 |
| 400 | ERR_PASSWORD_SAME | 新密码不能与旧密码相同 | newPassword == oldPassword |

**说明**: 修改成功后，当前 Access Token 和 Refresh Token 立即失效，前端需跳转登录页。

---

### 5.6 JWT Token 结构

**Access Token Payload**:
```json
{
  "sub": "00000000-0000-0000-0000-000000000002",
  "username": "ic_engineer",
  "role": "IC_ENGINEER",
  "type": "access",
  "iat": 1718950800,
  "exp": 1718952600,
  "jti": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Refresh Token Payload**:
```json
{
  "sub": "00000000-0000-0000-0000-000000000002",
  "type": "refresh",
  "iat": 1718950800,
  "exp": 1719528800,
  "jti": "b2c3d4e5-f6a7-8901-bcde-f12345678901"
}
```

**字段说明**:

| 字段 | 说明 |
|---|---|
| sub | 用户 ID (UUID) |
| username | 用户名（仅 Access Token） |
| role | 角色枚举（仅 Access Token） |
| type | Token 类型：access / refresh |
| iat | 签发时间（Unix 时间戳） |
| exp | 过期时间（Unix 时间戳） |
| jti | Token 唯一标识，用于黑名单吊销 |

**Token 安全策略**:

| 策略 | 说明 |
|---|---|
| 签名算法 | HS256 |
| 密钥 | 通过环境变量 `JWT_SECRET_KEY` 配置，至少 32 字符 |
| Access Token 有效期 | 30 分钟（1800 秒） |
| Refresh Token 有效期 | 7 天（604800 秒），rememberMe=true 时 30 天 |
| 黑名单机制 | 登出/改密时将 jti 写入 Redis，TTL 等于 Token 剩余有效期 |
| 载荷最小化 | Refresh Token 不携带 role/username，仅用于换取新 Access Token |

---

## 6. 统一响应规范

本章节定义所有 API 接口的统一响应格式，前后端开发必须严格遵循。

### 6.1 成功响应格式

所有成功响应（HTTP 2xx）使用统一的 envelope 包装：

```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| code | Integer | 是 | 业务状态码，0 表示成功 |
| message | String | 是 | 状态描述，成功时为 "success" |
| data | Any | 否 | 业务数据，可为对象/数组/null |

**单对象响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "loopId": "00000000-0000-0000-0000-000000000001",
    "loopName": "R-101 反应器入口温度",
    "score": 85.5
  }
}
```

**无数据响应（DELETE/PUT 操作）**:
```json
{
  "code": 0,
  "message": "success",
  "data": null
}
```

### 6.2 分页响应格式

所有列表类接口（GET 列表）使用统一的分页包装：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [ ... ],
    "total": 150,
    "page": 1,
    "pageSize": 20,
    "totalPages": 8
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| data.items | Array | 当前页数据列表 |
| data.total | Integer | 总记录数 |
| data.page | Integer | 当前页码（从 1 开始） |
| data.pageSize | Integer | 每页条数（默认 20，最大 100） |
| data.totalPages | Integer | 总页数 |

**分页请求参数（Query String）**:

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| page | Integer | 1 | 页码，从 1 开始 |
| pageSize | Integer | 20 | 每页条数，1-100 |
| sortBy | String | — | 排序字段 |
| sortOrder | String | asc | 排序方向：asc / desc |

### 6.3 错误响应格式

所有错误响应（HTTP 4xx/5xx）使用统一的错误 envelope：

```json
{
  "code": 4001,
  "message": "用户名或密码错误",
  "errorCode": "ERR_INVALID_CREDENTIALS",
  "errorMessage": "Invalid username or password",
  "details": "请检查用户名和密码是否正确",
  "timestamp": "2026-06-21T10:30:00Z",
  "path": "/api/v1/auth/login"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| code | Integer | 是 | 业务错误码（4 位数字，见 §6.5） |
| message | String | 是 | 错误描述（中文，面向用户） |
| errorCode | String | 是 | 错误码标识（大写下划线，面向开发者） |
| errorMessage | String | 否 | 错误码英文描述 |
| details | String | 否 | 错误详情（调试用，生产环境可关闭） |
| timestamp | String | 是 | 错误发生时间（ISO8601） |
| path | String | 是 | 请求路径 |

### 6.4 HTTP 状态码使用规则

| HTTP 状态码 | 使用场景 | 示例 |
|---|---|---|
| 200 OK | GET/PUT/PATCH/DELETE 成功 | 获取回路列表成功 |
| 201 Created | POST 创建资源成功 | 新建回路成功 |
| 202 Accepted | 异步任务已接受 | 报表导出任务已提交 |
| 204 No Content | DELETE 成功且无响应体 | 删除回路成功（可选，也可用 200） |
| 400 Bad Request | 请求参数校验失败 | 必填字段缺失、格式错误 |
| 401 Unauthorized | 未认证或 Token 失效 | 未携带 Token / Token 过期 |
| 403 Forbidden | 已认证但无权限 | 仪控工程师尝试访问用户管理 |
| 404 Not Found | 资源不存在 | 回路 ID 不存在 |
| 409 Conflict | 资源冲突 | 用户名已存在 |
| 422 Unprocessable Entity | 业务规则校验失败 | 权重总和不等于 100% |
| 429 Too Many Requests | 请求频率超限 | 登录失败 5 次锁定 |
| 500 Internal Server Error | 服务器内部错误 | 未捕获异常 |
| 502 Bad Gateway | 上游服务错误 | AAS 服务不可达 |
| 503 Service Unavailable | 服务不可用 | 维护中 |

### 6.5 业务错误码定义

业务错误码为 4 位数字，按模块分段：

| 码段 | 模块 | 示例 |
|---|---|---|
| 1000-1999 | 通用/认证 | 1001=参数校验失败, 1002=未认证, 1003=无权限 |
| 2000-2999 | 回路管理 | 2001=回路不存在, 2002=Tag 关联不完整 |
| 3000-3999 | 性能评估 | 3001=权重总和错误, 3002=指标不存在 |
| 4000-4999 | 诊断中心 | 4001=诊断结果不存在, 4002=跟踪状态非法 |
| 5000-5999 | 回路整定 | 5001=整定任务不存在 |
| 6000-6999 | 系统管理 | 6001=用户不存在, 6002=账户已禁用 |
| 9000-9999 | 系统错误 | 9001=数据库错误, 9002=AAS 连接失败 |

**与 errorCode 的映射关系**:

| code | errorCode | message | HTTP |
|---|---|---|---|
| 0 | — | success | 200 |
| 1001 | ERR_VALIDATION | 请求参数校验失败 | 400 |
| 1002 | ERR_UNAUTHORIZED | 未认证，请先登录 | 401 |
| 1003 | ERR_TOKEN_EXPIRED | Token 已过期 | 401 |
| 1004 | ERR_TOKEN_INVALID | Token 无效 | 401 |
| 1005 | ERR_FORBIDDEN | 无权限访问 | 403 |
| 1006 | ERR_CONFIG_FORBIDDEN | 仅系统管理员可执行配置操作 | 403 |
| 1007 | ERR_TOO_MANY_ATTEMPTS | 操作过于频繁 | 429 |
| 1008 | ERR_INVALID_CREDENTIALS | 用户名或密码错误 | 401 |
| 1009 | ERR_ACCOUNT_DISABLED | 账户已禁用 | 403 |
| 1010 | ERR_PASSWORD_SAME | 新密码不能与旧密码相同 | 400 |
| 2001 | ERR_LOOP_NOT_FOUND | 回路不存在 | 404 |
| 2002 | ERR_LOOP_TAG_REQUIRED | 必填 Tag（PV/SP/OP/MODE）缺失 | 422 |
| 2003 | ERR_TAG_NOT_FOUND | Tag 不存在 | 404 |
| 2004 | ERR_LOOP_DUPLICATE | 回路位号已存在 | 409 |
| 3001 | ERR_METRIC_WEIGHT_SUM | 指标权重总和不等于 100% | 422 |
| 3002 | ERR_METRIC_NOT_FOUND | 性能指标不存在 | 404 |
| 4001 | ERR_DIAGNOSIS_NOT_FOUND | 诊断结果不存在 | 404 |
| 4002 | ERR_TRACKER_STATUS_INVALID | 跟踪状态变更不合法 | 422 |
| 6001 | ERR_USER_NOT_FOUND | 用户不存在 | 404 |
| 6002 | ERR_USER_DUPLICATE | 用户名已存在 | 409 |
| 9001 | ERR_DATABASE | 数据库错误 | 500 |
| 9002 | ERR_AAS_CONNECTION | AAS 连接失败 | 502 |

### 6.6 异步任务响应格式

异步任务（报表导出/AAS 同步/评估计算）统一采用 202 + 轮询模式：

**任务提交响应 (202 Accepted)**:
```json
{
  "code": 0,
  "message": "任务已提交",
  "data": {
    "taskId": "task-2026-06-21-001",
    "taskType": "REPORT_EXPORT",
    "status": "PROCESSING",
    "checkUrl": "/api/v1/tasks/task-2026-06-21-001",
    "estimatedSeconds": 30
  }
}
```

**任务状态查询响应 (200 OK)**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "taskId": "task-2026-06-21-001",
    "taskType": "REPORT_EXPORT",
    "status": "SUCCESS",
    "progress": 100,
    "result": {
      "downloadUrl": "/api/v1/reports/task-2026-06-21-001/download"
    },
    "startedAt": "2026-06-21T10:00:00Z",
    "completedAt": "2026-06-21T10:00:25Z"
  }
}
```

**任务状态枚举**: `PROCESSING`（处理中）, `SUCCESS`（成功）, `FAILED`（失败）

**任务失败响应**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "taskId": "task-2026-06-21-001",
    "status": "FAILED",
    "error": {
      "errorCode": "ERR_AAS_CONNECTION",
      "message": "AAS 服务连接超时"
    },
    "startedAt": "2026-06-21T10:00:00Z",
    "failedAt": "2026-06-21T10:00:05Z"
  }
}
```

### 6.7 前端 Axios 拦截器对接规范

前端 Axios 请求/响应拦截器应按以下规范实现：

**请求拦截器**:
- 自动在 Header 中注入 `Authorization: Bearer {accessToken}`
- Token 过期时自动调用 `/api/v1/auth/refresh` 刷新，并重发原请求
- 刷新失败（Refresh Token 也过期）时跳转登录页

**响应拦截器**:
- `code === 0`：正常返回 `data`
- `code !== 0` 且 HTTP 401：Token 失效，触发刷新流程
- `code !== 0` 且 HTTP 403：权限不足，提示用户
- `code !== 0` 且 HTTP 4xx：业务错误，弹出 Toast 提示 `message`
- `code !== 0` 且 HTTP 5xx：系统错误，弹出错误提示并记录日志
