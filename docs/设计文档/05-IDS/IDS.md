# CLPM 接口设计规范说明书 (IDS)

**文档状态**: 正式版
**当前版本**: v3.0 (产品化架构重构版)
**发布日期**: 2026-06-20
**设计依据**: PRD (v3.0), FDS (v3.0), ADS (v3.0), DDS (v3.0)

---

## 0. 文档变更记录

| 版本 | 日期 | 变更说明 | 作者 |
|---|---|---|---|
| v1.0 | 2026-06-16 | 初始版本（基于旧版 PRS 拆解） | 产品团队 |
| v2.0 | 2026-06-19 | 全面重构：基于 PRD v2.2 重新设计，移除繁重工单审批流，重塑为"自动评估+轻量跟踪"架构，定义性能看板、波形查询、异常跟踪三类核心 API。 | 系统设计团队 |
| v3.0 | 2026-06-20 | 产品化架构重构：①对齐 6 模块 + 1 门户结构（工作台/回路管理/性能评估/诊断中心/回路整定/系统管理）；②引入 AAS Tag 模型（7 个 OPC tag：PV/SP/OP/MODE/PID_P/PID_I/PID_D），PID 参数从 tag 只读，数据质量主要针对 PV；③新增工作台、回路管理（含 AAS 同步/回路 CRUD/tag 关联/回路监控）、回路整定（Phase 2 占位）、系统管理 API 组；④扩展性能评估与诊断中心 API（指标配置/引擎规则/统计报表）；⑤波形 API 响应增加 `pv_quality` 数组，明确仅 PV 携带质量码；⑥补充新错误码（ERR_TAG_NOT_FOUND/ERR_LOOP_TAG_REQUIRED/ERR_METRIC_WEIGHT_SUM/ERR_CONFIG_FORBIDDEN）。 | 系统设计团队 |

---

## 1. 接口设计原则

本规范定义了前后端 (BFF) 之间及系统与外部系统交互的核心 API 契约，严格遵循以下原则：

* **RESTful 风格**：资源导向设计，标准 HTTP 方法 (GET, POST, PUT, PATCH, DELETE)。资源路径统一以 `/api/v1/` 为前缀。
* **防超载设计**：对于时序波形数据的查询，必须强制提供时间窗参数，并默认执行降采样（LTTB）。单次返回数据点数不得超过 `maxPoints` 上限。
* **安全与权限**：所有接口需在 Header 中携带 `Authorization: Bearer <JWT>`，网关层执行 RBAC 校验。权限层级分为：查看层（Sponsor）、协同层（工艺/设备工程师）、执行层（仪控工程师）、管理层（系统管理员）、服务层（外部专家）。
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
          "metricKey": "auto_mode_rate",
          "metricName": "自控投用率",
          "value": 92.5,
          "unit": "%",
          "delta": 1.2,
          "trend": "UP",
          "miniTrend": [91.0, 91.5, 92.0, 92.5]
        },
        {
          "metricKey": "steady_rate",
          "metricName": "平稳率",
          "value": 85.3,
          "unit": "%",
          "delta": -0.8,
          "trend": "DOWN",
          "miniTrend": [86.1, 85.9, 85.5, 85.3]
        },
        {
          "metricKey": "score",
          "metricName": "综合评分",
          "value": 78.6,
          "unit": "",
          "delta": 2.1,
          "trend": "UP",
          "miniTrend": [76.5, 77.0, 78.0, 78.6]
        },
        {
          "metricKey": "alarm_count",
          "metricName": "报警次数",
          "value": 12,
          "unit": "次",
          "delta": -3,
          "trend": "DOWN",
          "miniTrend": [15, 14, 13, 12]
        },
        {
          "metricKey": "operation_freq",
          "metricName": "操作频次",
          "value": 45,
          "unit": "次",
          "delta": 5,
          "trend": "UP",
          "miniTrend": [40, 42, 43, 45]
        },
        {
          "metricKey": "good_value_rate",
          "metricName": "好值率",
          "value": 96.8,
          "unit": "%",
          "delta": 0.5,
          "trend": "UP",
          "miniTrend": [96.0, 96.3, 96.5, 96.8]
        }
      ],
      "badActors": [
        {
          "loopId": "uuid-xxx",
          "tagName": "101-FC-1023",
          "score": 45.2,
          "steadyRate": 60.5,
          "status": "SUCCESS",
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
        "autoModeRate": [91.0, 91.5, 92.0, 92.5],
        "steadyRate": [86.1, 85.9, 85.5, 85.3]
      }
    }
  }
  ```
* **说明**：KPI 卡片固定返回 6 项核心指标（自控投用率/平稳率/综合评分/报警次数/操作频次/好值率）；`badActors` 默认返回 Top 10 低效回路；`loopTrendSummary` 返回选中回路近 24h 趋势摘要；数据为空时对应字段返回 `null` 或空数组，前端展示"--"。

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
  * `status` (String, optional): 按回路状态筛选，枚举值 `Ready`, `Partial`, `INCONCLUSIVE`
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
          "status": "Ready",
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
* **说明**：`controlMode` 从 MODE tag 只读读取；`tagMappingStatus` 标识 7 个 OPC tag 的关联完整性，PV/SP/OP/MODE 任一缺失时 `status` 为 `Partial`。

#### 2.2.8 创建回路 (Create Loop)

* **URL**: `POST /api/v1/loops`
* **权限**: 执行层及以上（仪控工程师/系统管理员）
* **Request Body**:
  ```json
  {
    "tagName": "101-FC-1024",
    "description": "常减压装置塔顶温度控制",
    "unitId": "uuid-yyy",
    "scoreWeights": {
      "good_value_rate": 20,
      "auto_mode_rate": 20,
      "steady_rate": 20,
      "accuracy_rate": 15,
      "oscillation_rate": 15,
      "saturation_rate": 10
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
      "status": "Partial",
      "isActive": true,
      "scoreWeights": {
        "good_value_rate": 20,
        "auto_mode_rate": 20,
        "steady_rate": 20,
        "accuracy_rate": 15,
        "oscillation_rate": 15,
        "saturation_rate": 10
      },
      "remark": "新增回路，待关联 tag",
      "createdAt": "2026-06-20T10:00:00Z",
      "createdBy": "zhang.san"
    }
  }
  ```
* **说明**：回路位号在所属单元内唯一；新建回路默认状态为 `Partial`（待关联 tag）；`scoreWeights` 可不传，缺省继承自性能指标配置默认值，权重总和须为 100%，否则返回 `ERR_METRIC_WEIGHT_SUM`。

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
        "isActive": true,
        "status": "Ready",
        "scoreWeights": {
          "good_value_rate": 20,
          "auto_mode_rate": 20,
          "steady_rate": 20,
          "accuracy_rate": 15,
          "oscillation_rate": 15,
          "saturation_rate": 10
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
* **权限**: 执行层及以上（仪控工程师/系统管理员）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Request Body**:
  ```json
  {
    "description": "常减压装置进料流量控制（更新描述）",
    "scoreWeights": {
      "good_value_rate": 25,
      "auto_mode_rate": 20,
      "steady_rate": 20,
      "accuracy_rate": 15,
      "oscillation_rate": 10,
      "saturation_rate": 10
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
        "good_value_rate": 25,
        "auto_mode_rate": 20,
        "steady_rate": 20,
        "accuracy_rate": 15,
        "oscillation_rate": 10,
        "saturation_rate": 10
      },
      "isActive": true,
      "remark": "更新备注：权重调整",
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "zhang.san"
    }
  }
  ```
* **说明**：仅支持更新扩展配置（描述/评分权重/启用状态/备注），回路位号与所属单元不可变更；权重总和须为 100%，否则返回 `ERR_METRIC_WEIGHT_SUM`；变更记录审计日志。

#### 2.2.11 删除回路 (Delete Loop)

* **URL**: `DELETE /api/v1/loops/{loopId}`
* **权限**: 管理层（系统管理员）
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
* **权限**: 执行层及以上（仪控工程师/系统管理员）
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
      "status": "Ready",
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
  * PV/SP/OP/MODE 为必填 tag，缺失时回路状态变更为 `Partial` 并标红提示，但 API 调用本身成功（不报错），由前端根据 `status` 提示。
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
        "score": 78.6,
        "steadyRate": 85.3,
        "goodValueRate": 96.8,
        "autoModeRate": 92.5,
        "calculatedAt": "2026-06-20T09:00:00Z",
        "status": "SUCCESS"
      }
    }
  }
  ```
* **说明**：`currentValues` 为当前最新值快照；`trend` 为趋势数据，`pvQuality` 数组与 `pv` 数组等长，仅 PV 携带质量码；`kpiSummary` 为近期 KPI 摘要。回路处于 `INCONCLUSIVE` 时，`kpiSummary.status` 为 `INCONCLUSIVE`，波形区灰色虚线断线。

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
          "status": "Ready",
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
* **说明**：列表视图与卡片视图返回数据结构一致，仅前端渲染方式不同；回路处于 `Partial` 时卡片置灰，悬浮提示缺失项。

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
          "metricKey": "good_value_rate",
          "metricName": "好值率",
          "value": 96.8,
          "unit": "%",
          "status": "SUCCESS"
        },
        {
          "metricKey": "auto_mode_rate",
          "metricName": "自控率",
          "value": 92.5,
          "unit": "%",
          "status": "SUCCESS"
        },
        {
          "metricKey": "steady_rate",
          "metricName": "平稳率",
          "value": 85.3,
          "unit": "%",
          "status": "SUCCESS"
        },
        {
          "metricKey": "accuracy_rate",
          "metricName": "准确率",
          "value": 88.1,
          "unit": "%",
          "status": "SUCCESS"
        },
        {
          "metricKey": "oscillation_rate",
          "metricName": "振荡率",
          "value": 12.4,
          "unit": "%",
          "status": "SUCCESS"
        },
        {
          "metricKey": "saturation_rate",
          "metricName": "饱和率",
          "value": 5.2,
          "unit": "%",
          "status": "SUCCESS"
        }
      ],
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
* **说明**：KPI 卡片固定返回 6 项核心指标；指标停用后对应卡片 `status` 为 `INCONCLUSIVE`；`partialWarning` 在存在 `INCONCLUSIVE` 或 `Partial` 回路时激活，强制显示黄色警告横幅。

#### 2.3.2 获取低效回路排行 (Get Performance Ranking)

* **URL**: `GET /api/v1/performance/ranking`
* **权限**: 查看层及以上（所有角色可访问）
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按装置/单元过滤
  * `timeWindow` (String, required): 时间窗，枚举值 `today`, `yesterday`, `last_7_days`, `last_30_days`
  * `limit` (Integer, default=10): 返回条数，最大 100
  * `sortBy` (String, default=`score`): 排序字段，枚举值 `score`, `steady_rate`, `good_value_rate`
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
        "score": 45.2,
        "steadyRate": 60.5,
        "goodValueRate": 75.0,
        "status": "SUCCESS",
        "preDiagnosis": "疑似阀门粘滞",
        "actionStatus": "PENDING"
      }
    ]
  }
  ```
* **说明**：默认按评分升序返回 Top N 低效回路；`actionStatus` 来自异常跟踪子模块（待处理/处理中/已实施/已忽略）。

#### 2.3.3 获取性能指标配置列表 (List Performance Metrics)

* **URL**: `GET /api/v1/performance/metrics`
* **权限**: 查看层及以上（所有角色可查看配置，仅系统管理员可编辑）
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "items": [
        {
          "metricId": "uuid-xxx",
          "metricKey": "good_value_rate",
          "metricName": "好值率",
          "formula": "sum(quality==Good) / count(*) * 100",
          "weight": 20,
          "threshold": {
            "min": 0,
            "max": 100,
            "alert": 80
          },
          "isEnabled": true,
          "description": "剔除通讯中断、超量程、冻结等无效数据后的时长占比，基于 PV tag 质量码统计",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "metricId": "uuid-yyy",
          "metricKey": "auto_mode_rate",
          "metricName": "自控率",
          "formula": "sum(mode in [Auto, Cascade]) / count(*) * 100",
          "weight": 20,
          "threshold": { "min": 0, "max": 100, "alert": 90 },
          "isEnabled": true,
          "description": "控制器处于自动或串级模式的时长占比",
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        }
      ],
      "totalWeight": 100,
      "weightValid": true
    }
  }
  ```
* **说明**：返回 6 大核心 KPI（好值率/自控率/平稳率/准确率/振荡率/饱和率）的配置；`totalWeight` 标识当前权重总和，`weightValid` 标识是否为 100%。

#### 2.3.4 更新性能指标配置 (Update Performance Metric)

* **URL**: `PUT /api/v1/performance/metrics/{metricId}`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Path Parameters**:
  * `metricId` (UUID, required): 指标 ID
* **Request Body**:
  ```json
  {
    "formula": "sum(quality==Good) / count(*) * 100",
    "weight": 25,
    "threshold": {
      "min": 0,
      "max": 100,
      "alert": 85
    },
    "isEnabled": true,
    "description": "更新好值率公式与权重"
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "metricId": "uuid-xxx",
      "metricKey": "good_value_rate",
      "metricName": "好值率",
      "formula": "sum(quality==Good) / count(*) * 100",
      "weight": 25,
      "threshold": { "min": 0, "max": 100, "alert": 85 },
      "isEnabled": true,
      "description": "更新好值率公式与权重",
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "admin"
    }
  }
  ```
* **说明**：
  * 配置变更即时生效，无需重启服务。
  * 后端二次校验权重总和：若本次变更导致 6 项指标权重总和 ≠ 100%，返回 `ERR_METRIC_WEIGHT_SUM`。
  * 指标停用后相关 KPI 显示 `INCONCLUSIVE`，不参与综合评分计算。
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
  * `metricKey` (String, optional): 按指标筛选，枚举值 `good_value_rate`, `auto_mode_rate`, `steady_rate`, `accuracy_rate`, `oscillation_rate`, `saturation_rate`, `score`
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
  * `preDiagnosis` (String, optional): 按预诊标签筛选（如 `疑似阀门粘滞`）
  * `actionStatus` (String, optional): 按处理状态筛选，枚举值 `PENDING`, `IN_PROGRESS`, `RESOLVED`, `IGNORED`
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
          "score": 45.2,
          "preDiagnosis": "疑似阀门粘滞",
          "diagnosisLabel": "Stiction",
          "confidence": 0.85,
          "actionStatus": "PENDING",
          "diagnosedAt": "2026-06-20T08:00:00Z",
          "algorithmVersion": "v1.2.0"
        }
      ],
      "total": 23,
      "page": 1,
      "pageSize": 20
    }
  }
  ```
* **说明**：返回评分跌破阈值的回路诊断列表；`confidence` 为诊断算法置信度；`algorithmVersion` 用于追溯诊断所依据的算法版本号。

#### 2.4.2 获取回路诊断详情 (Get Loop Diagnosis Detail)

* **URL**: `GET /api/v1/diagnosis/{loopId}`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
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
      "score": 45.2,
      "preDiagnosis": "疑似阀门粘滞",
      "diagnosisLabels": [
        {
          "label": "Stiction",
          "labelName": "阀门粘滞",
          "confidence": 0.85,
          "evidence": "PV-OP 散点图呈现椭圆轨迹，拟合度 0.92"
        }
      ],
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
      "algorithmVersion": "v1.2.0",
      "diagnosedAt": "2026-06-20T08:00:00Z"
    }
  }
  ```
* **说明**：返回诊断详情，含预诊标签、特征值、证据链（波形 URL/PV-OP 散点图/推理过程）；数据不足时返回 `ERR_DATA_INCONCLUSIVE`。

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
          "algorithmType": "FFT",
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
          "calcMethod": "auto_correlation",
          "isEnabled": true,
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        },
        {
          "diagId": "uuid-yyy",
          "diagKey": "stiction_scatter",
          "diagName": "粘滞检测散点拟合",
          "algorithmType": "ScatterFitting",
          "params": {
            "fittingType": "ellipse",
            "minPoints": 100
          },
          "threshold": {
            "stictionIndex": 0.6,
            "fittingScore": 0.8
          },
          "calcMethod": "pv_op_scatter",
          "isEnabled": true,
          "updatedAt": "2026-06-19T10:00:00Z",
          "updatedBy": "admin"
        }
      ]
    }
  }
  ```
* **说明**：返回诊断指标列表（振荡检测 FFT、粘滞检测散点拟合、参数过激检测、质量码规则等）的算法类型、参数阈值、启用/停止、计算方法配置。

#### 2.4.4 更新诊断指标配置 (Update Diagnosis Metric)

* **URL**: `PUT /api/v1/diagnosis/metrics/{diagId}`
* **权限**: 管理层（系统管理员），越权返回 `ERR_CONFIG_FORBIDDEN`
* **Path Parameters**:
  * `diagId` (UUID, required): 诊断指标 ID
* **Request Body**:
  ```json
  {
    "algorithmType": "FFT",
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
    "calcMethod": "auto_correlation",
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
      "algorithmType": "FFT",
      "params": {
        "windowSize": 2048,
        "overlap": 0.5,
        "minFrequency": 0.01,
        "maxFrequency": 1.0
      },
      "threshold": { "amplitude": 1.8, "confidence": 0.75 },
      "calcMethod": "auto_correlation",
      "isEnabled": true,
      "updatedAt": "2026-06-20T10:30:00Z",
      "updatedBy": "admin"
    }
  }
  ```
* **说明**：
  * 配置变更即时生效，无需重启服务。
  * 指标停用后相关预诊标签不再生成。
  * 变更记录审计日志（操作人/时间/变更前后值）。

#### 2.4.5 获取高频波形数据 (Get Timeseries Waveform)

* **URL**: `GET /api/v1/timeseries/{loopId}/waveform`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
* **Path Parameters**:
  * `loopId` (UUID, required): 回路 ID
* **Query Parameters**:
  * `startTime` (ISO8601, required): 开始时间
  * `endTime` (ISO8601, required): 结束时间
  * `downsample` (Boolean, default=true): 是否启用 LTTB 降采样
  * `maxPoints` (Integer, default=2000): 前端最大可接受数据点数
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
      "timestamps": [1623912000000, 1623912001000, 1623912002000],
      "pv": [50.1, 50.2, null],
      "sp": [50.0, 50.0, 50.0],
      "op": [45.5, 45.8, 45.7],
      "mode": [1, 1, 1],
      "pvQuality": ["Good", "Good", "Bad"]
    }
  }
  ```
* **说明**：
  * **v3.0 关键变更**：响应中增加 `pvQuality` 数组，与 `pv` 数组等长，标识每个时间点的 PV 数据质量码（`Good`/`Bad`/`Uncertain`）。
  * **质量码仅针对 PV**：SP/OP/MODE 不携带质量码，响应中无 `sp_quality`/`op_quality`/`mode_quality` 字段。
  * PV 质量码为 `Bad` 时，对应 `pv` 值为 `null`，前端按灰色虚线断线渲染。
  * 数据量超过 10 万点且 `downsample=false` 时，返回 `ERR_TS_DOWNSAMPLE_REQ`。
  * 时间窗超过 30 天时，返回 `ERR_TS_001`。

#### 2.4.6 更新回路处理状态 (Update Action Status)

* **URL**: `PATCH /api/v1/tracker/{loopId}/status`
* **权限**: 仅限执行层（仪控工程师），越权返回 `ERR_AUTH_403`
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
* **说明**：`status` 枚举值 `PENDING`（待处理）, `IN_PROGRESS`（处理中）, `RESOLVED`（已实施）, `IGNORED`（已忽略）；不走审批流；状态变更记录审计日志；标记为 `RESOLVED` 后系统自动截取实施前后数据窗口生成 A/B 对比视图。

#### 2.4.7 导出诊断建议书 (Export Diagnosis Report)

* **URL**: `POST /api/v1/tracker/{loopId}/export`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
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
  * `preDiagnosis` (String, optional): 按预诊标签筛选
  * `actionStatus` (String, optional): 按处理状态筛选，枚举值 `PENDING`, `IN_PROGRESS`, `RESOLVED`, `IGNORED`
  * `granularity` (String, default=`day`): 统计粒度，枚举值 `day`, `week`, `month`
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "filterScope": {
        "startTime": "2026-06-01T00:00:00Z",
        "endTime": "2026-06-20T00:00:00Z",
        "plantNodeId": null,
        "preDiagnosis": null,
        "actionStatus": null,
        "granularity": "day"
      },
      "labelDistribution": [
        { "label": "疑似阀门粘滞", "count": 8 },
        { "label": "参数过激", "count": 5 },
        { "label": "振荡", "count": 3 },
        { "label": "原因不明(需人工介入)", "count": 2 }
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
* **说明**：返回预诊标签分布饼图、处理效率趋势折线、闭环时长分布柱状图所需数据；筛选结果为空时对应字段返回空数组。

#### 2.4.9 导出诊断统计报表 (Export Diagnosis Analytics)

* **URL**: `POST /api/v1/diagnosis/analytics/export`
* **权限**: 查看层及以上（所有角色可访问）
* **Request Body**:
  ```json
  {
    "startTime": "2026-06-01T00:00:00Z",
    "endTime": "2026-06-20T00:00:00Z",
    "plantNodeId": null,
    "preDiagnosis": null,
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

---

### 2.5 回路整定 API (Tuning - Phase 2 原型占位)

本组 API 为 **Phase 2** 功能，Phase 1 仅完成原型页面设计，API 路径与契约预先定义，但实际算法在 Phase 2 实现。Phase 1 调用返回占位数据或 `501 Not Implemented`。

#### 2.5.1 获取整定记录列表 (List Tuning Records)

* **URL**: `GET /api/v1/tuning/records`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
* **Query Parameters**:
  * `loopId` (UUID, optional): 按回路筛选
  * `status` (String, optional): 按整定状态筛选，枚举值 `PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`
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
          "algorithm": "IMC",
          "recommendedPid": { "p": 1.5, "i": 0.4, "d": 0.0 },
          "beforeScore": 45.2,
          "afterScore": 82.5,
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
* **说明**：Phase 1 原型阶段返回占位数据；Phase 2 返回真实整定记录。

#### 2.5.2 触发模型辨识 (Trigger Model Identification) [Phase 2]

* **URL**: `POST /api/v1/tuning/identification`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
* **Request Body**:
  ```json
  {
    "loopId": "uuid-xxx",
    "dataSegment": {
      "startTime": "2026-06-15T00:00:00Z",
      "endTime": "2026-06-15T06:00:00Z"
    },
    "samplePeriod": 1,
    "modelType": "FOPDT"
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
* **说明**：Phase 2 实现模型辨识算法（FOPDT/SOPDT/IPDT），输出传递函数参数（K/T/τ）、拟合度、阶跃响应对比曲线。Phase 1 原型阶段返回 `501 Not Implemented`。

#### 2.5.3 计算整定参数 (Calculate Tuning Parameters) [Phase 2]

* **URL**: `POST /api/v1/tuning/algorithm`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
* **Request Body**:
  ```json
  {
    "loopId": "uuid-xxx",
    "identificationRecordId": "uuid-xxx",
    "algorithm": "IMC",
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
* **说明**：Phase 2 实现整定算法（IMC/Lambda/Ziegler-Nichols/Cohen-Coon），推荐 PID 参数表（P/I/D 三组对比）、性能预测指标。Phase 1 原型阶段返回 `501 Not Implemented`。

#### 2.5.4 执行闭环仿真 (Run Closed-Loop Simulation) [Phase 2]

* **URL**: `POST /api/v1/tuning/simulation`
* **权限**: 执行层及以上（仪控工程师/系统管理员/外部专家）
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
* **说明**：Phase 2 执行闭环仿真，输出阶跃响应双波形对比、性能指标对比表（上升时间/超调/settling time/ITAE）。Phase 1 原型阶段返回 `501 Not Implemented`。

#### 2.5.5 获取整定效果统计 (Get Tuning Analytics)

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

* **URL**: `GET /api/v1/system/users`
* **权限**: 管理层（系统管理员）
* **Query Parameters**:
  * `keyword` (String, optional): 按用户名/姓名模糊查询
  * `role` (String, optional): 按角色筛选，枚举值 `EXECUTOR`（仪控工程师）, `COLLABORATOR`（工艺/设备工程师）, `VIEWER`（Sponsor）, `ADMIN`（系统管理员）, `EXPERT`（外部专家）
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
          "role": "EXECUTOR",
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

* **URL**: `PUT /api/v1/system/users/{userId}/role`
* **权限**: 管理层（系统管理员），越权返回 `ERR_AUTH_403`
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

* **URL**: `GET /api/v1/system/audit`
* **权限**: 管理层（系统管理员）
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

* **URL**: `GET /api/v1/system/reports`
* **权限**: 管理层（系统管理员）
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
          "fileUrl": "/api/v1/system/reports/uuid-xxx/download",
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

* **URL**: `POST /api/v1/system/reports/{reportId}/retry`
* **权限**: 管理层（系统管理员）
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
| `ERR_METRIC_WEIGHT_SUM` | 400 | 性能指标权重总和不为 100% | 更新性能指标配置时，6 项指标权重总和 ≠ 100%；或更新回路扩展配置时 `scoreWeights` 总和 ≠ 100%。 |
| `ERR_CONFIG_FORBIDDEN` | 403 | 越权修改配置 | 非系统管理员角色尝试调用配置类 API（性能指标配置/诊断指标配置/引擎规则配置的更新接口）。 |

### 3.3 错误响应示例

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
  "errorMessage": "Total weight of metrics must be 100%",
  "details": "当前 6 项指标权重总和为 95%，请调整至 100%"
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
| 查看层 | Sponsor（生产技术） | 仅查看工厂/装置级全局看板与定期报告。 |
| 协同层 | 工艺/设备工程师 | 查看诊断报告，协助排查，不可修改状态与配置。 |
| 执行层 | 仪控工程师 | 配置回路与 tag 关联，查看性能看板，分析波形，标记异常跟踪状态。 |
| 管理层 | 系统管理员 | 管理工厂模型，配置指标与算法阈值，管理用户，查看审计日志。 |
| 服务层 | 外部专家 | 诊断复核，出具高级诊断意见与整定模型样例。 |

### 4.4 PV 质量码处理约定

* 数据质量主要针对 PV 值，PV tag 携带质量码（`Good`/`Bad`/`Uncertain`）。
* SP/OP/MODE/PID_P/PID_I/PID_D 不携带质量码。
* 波形 API 响应中 `pvQuality` 数组与 `pv` 数组等长，标识每个时间点的 PV 质量码。
* PV 质量码为 `Bad` 时，对应 `pv` 值为 `null`，前端按灰色虚线断线渲染。
* PV 质量码为 `Uncertain` 时，前端按黄色虚线渲染。
* KPI 好值率基于 PV 质量码统计，`Bad`/`Uncertain` 时段不计入好值。
