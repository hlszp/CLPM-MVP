# CLPM v4.0 Phase 5 — API 接口文档

> **版本**: v1.1.0
> **最后更新**: 2026-06-26
> **设计依据**: IDS v3.2 §2.4.5 / §2.7.1 / §2.7.5 / §2.7.6 / §2.4.10-2.4.12, PRD §4.3.7 / §5.6 / §8.1-8.3
> **基础路径**: `/api/v1`
> **认证方式**: Bearer Token (JWT)
> **响应格式**: OpenAPI 3.0 规范，统一响应包络 `{code, message, data}`

---

## 目录

- [1. 通用约定](#1-通用约定)
  - [1.1 统一响应包络](#11-统一响应包络)
  - [1.2 认证与授权](#12-认证与授权)
  - [1.3 错误码总表](#13-错误码总表)
- [2. 波形数据接口 (5.1)](#2-波形数据接口-51)
  - [2.1 GET /timeseries/{loopId}/waveform](#21-get-timeseriesloopidwaveform)
- [3. 批量波形查询接口 (5.2)](#3-批量波形查询接口-52)
  - [3.1 POST /timeseries/batch/waveform](#31-post-timeseriesbatchwaveform)
- [4. KPI 返回 Schema 扩展 (5.3)](#4-kpi-返回-schema-扩展-53)
  - [4.1 数据血缘字段定义](#41-数据血缘字段定义)
  - [4.2 DataLineageSchema 完整结构](#42-datalineageschema-完整结构)
  - [4.3 KpiSnapshotSchema 完整结构](#43-kpisnapshotschema-完整结构)
- [5. DataPlanner 内部接口 (5.4)](#5-dataplanner-内部接口-54)
  - [5.1 POST /algorithms/dataplanner/plan](#51-post-algorithmsdataplannerplan)
  - [5.2 POST /algorithms/dataplanner/bundle](#52-post-algorithmsdataplannerbundle)
  - [5.3 GET /algorithms/dataplanner/cache/stats](#53-get-algorithmsdataplannercachestats)
  - [5.4 DELETE /algorithms/dataplanner/cache/{loopId}](#54-delete-algorithmsdataplannercacheloopid)
- [6. 任务管理 API (5.5)](#6-任务管理-api-55)
  - [6.1 POST /tasks/standard/evaluate](#61-post-tasksstandardevaluate)
  - [6.2 POST /tasks/custom/evaluate](#62-post-taskscustomevaluate)
  - [6.3 GET /tasks/{taskId}](#63-get-taskstaskid)
  - [6.4 GET /tasks](#64-get-tasks)
  - [6.5 POST /tasks/{taskId}/cancel](#65-post-taskstaskidcancel)
  - [6.6 GET /tasks/notifications](#66-get-tasksnotifications)
  - [6.7 POST /tasks/notifications/{taskId}/read](#67-post-tasksnotificationstaskidread)
- [7. 诊断标签接口 (5.6)](#7-诊断标签接口-56)
  - [7.1 GET /diagnosis/tags](#71-get-diagnosistags)
  - [7.2 GET /diagnosis/tags/{loopId}](#72-get-diagnosistagsloopid)
  - [7.3 PUT /diagnosis/tags/{tagId}/resolve](#73-put-diagnosistagstagidresolve)
- [8. 附录](#8-附录)
  - [8.1 枚举值定义](#81-枚举值定义)
  - [8.2 性能与并发约束](#82-性能与并发约束)

---

## 1. 通用约定

### 1.1 统一响应包络

所有接口返回统一 JSON 包络，HTTP 状态码与业务状态码分离：

```json
{
  "code": "0",
  "message": "success",
  "data": { }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 业务状态码，`"0"` 表示成功，其余为错误码 |
| `message` | string | 描述信息，成功为 `"success"`，失败为错误描述 |
| `data` | object \| null | 业务数据，结构由具体接口决定 |

**HTTP 状态码映射**:

| HTTP | 含义 | 触发场景 |
|------|------|----------|
| 200 | 成功 | 业务执行成功 |
| 400 | 请求错误 | 参数校验失败、业务约束违反 |
| 401 | 未认证 | 缺少或无效的 Token |
| 403 | 无权限 | 角色不满足要求 |
| 404 | 资源不存在 | 任务/回路/标签不存在 |
| 422 | 校验失败 | 请求体格式正确但语义无效 |
| 429 | 请求过多 | 并发限制触发 |
| 500 | 服务器错误 | 内部异常 |

### 1.2 认证与授权

所有接口要求在请求头携带 JWT Token：

```
Authorization: Bearer <token>
```

**角色权限矩阵**:

| 角色 | 说明 | 可访问接口 |
|------|------|-----------|
| `ADMIN` | 系统管理员 | 所有接口 |
| `IC_ENGINEER` | 仪表工程师 | 任务触发、诊断标签处理、波形查询 |
| `PE_ENGINEER` | 过程工程师 | 任务触发、诊断标签处理 |
| `EXPERT` | 专家 | 诊断建议书生成 |
| `SPONSOR` | 赞助者 | 只读查询（不可触发任务） |

### 1.3 错误码总表

| 错误码 | HTTP | 说明 |
|--------|------|------|
| `ERR_PERMISSION_DENIED` | 403 | 角色权限不足 |
| `ERR_VALIDATION` | 422 | 请求参数校验失败 |
| `ERR_INVALID_REQUEST` | 400 | 请求体无效 |
| `ERR_INVALID_TIME` | 400 | 时间格式无效或时间窗非法 |
| `ERR_INVALID_TAG_GROUP` | 400 | 无效的 tagGroup |
| `ERR_INVALID_CONTROL_TYPE` | 400 | 无效的控制类型 |
| `ERR_LOOP_NOT_FOUND` | 404 | 回路不存在 |
| `ERR_TASK_NOT_FOUND` | 404 | 任务不存在 |
| `ERR_DIAG_TAG_NOT_FOUND` | 404 | 诊断标签不存在 |
| `ERR_NOTIFICATION_NOT_FOUND` | 404 | 通知不存在或已读 |
| `ERR_METRIC_NOT_FOUND` | 404 | 指标契约未找到 |
| `ERR_TASK_NOT_CANCELLABLE` | 400 | 任务已处于终态，无法取消 |
| `ERR_TASK_CONCURRENCY_LIMIT` | 429 | 并发任务数超限 |
| `ERR_TS_001` | 400 | 时间窗超过 30 天 |
| `ERR_TS_002` | 400 | 结束时间不晚于开始时间 |
| `ERR_WAVEFORM_FETCH` | 500 | 获取波形数据失败 |
| `ERR_DATAPLANNER_FAILED` | 500 | DataPlanner 取数失败 |

---

## 2. 波形数据接口 (5.1)

> **设计依据**: IDS §2.4.5, 算法说明 §3.4 / §3.7.1
> **响应时间阈值**: 2000ms 以内（L1 缓存命中时 < 5ms）

### 2.1 GET /timeseries/{loopId}/waveform

获取单个回路的波形数据，支持 `tagGroup` 筛选和 `valid_mask` 标记。

通过 DataPlanner 获取数据（复用 Phase 2 架构，不直接查 TDengine），返回 `WaveformResponse`（含 points 列表，每个点带 `valid` 和 `outlierReason`）。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|------|
| `loopId` | path | string | 是 | - | 回路 ID |
| `startTime` | query | string | 是 | - | 开始时间（ISO 8601） |
| `endTime` | query | string | 是 | - | 结束时间（ISO 8601） |
| `tagGroup` | query | string | 否 | `BASE` | 标签组筛选: `BASE`/`OP_HF`/`PVOP_HF`/`MODE_HF`/`QUALITY_HF` |
| `includeValidMask` | query | boolean | 否 | `true` | 是否返回 valid_mask |
| `maxPoints` | query | integer | 否 | `5000` | 最大数据点数（100~50000） |

**tagGroup 说明**

| tagGroup | 用途 | 代表性指标 | 采样频率 |
|----------|------|-----------|----------|
| `BASE` | 基础指标计算 | accuracy_rate / fast_response_rate / steady_rate / oscillation_rate | 按控制类型降采样 |
| `OP_HF` | 输出高频分析 | saturation_rate | 1s |
| `PVOP_HF` | 粘滞分析 | stiction_coeff | 1s |
| `MODE_HF` | 有效自控率 | effective_auto_rate | 1s |
| `QUALITY_HF` | 好值率 | good_value_rate | 1s（KEEP_ALL 策略） |

**请求示例**

```bash
curl -X GET "https://api.clpm.example.com/api/v1/timeseries/00000000-0000-0000-0000-000000000201/waveform?startTime=2026-06-26T08:00:00Z&endTime=2026-06-26T09:00:00Z&tagGroup=BASE&includeValidMask=true&maxPoints=5000" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "loopId": "00000000-0000-0000-0000-000000000201",
    "tagName": "80PIC31306_PIDA",
    "timeRange": {
      "startTime": "2026-06-26T08:00:00Z",
      "endTime": "2026-06-26T09:00:00Z"
    },
    "points": [
      {
        "timestamp": "2026-06-26T08:00:00Z",
        "pv": 52.3,
        "sp": 52.0,
        "op": 45.1,
        "mode": 1,
        "pvQuality": 1,
        "valid": true,
        "outlierReason": null
      },
      {
        "timestamp": "2026-06-26T08:00:01Z",
        "pv": null,
        "sp": 52.0,
        "op": 45.2,
        "mode": 1,
        "pvQuality": 0,
        "valid": false,
        "outlierReason": "FROZEN"
      }
    ],
    "samplingFreq": "1s",
    "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
    "validRate": 0.9876,
    "downsampled": false,
    "pointCount": 3600
  }
}
```

**WaveformPoint 字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | string | ISO 8601 时间戳 |
| `pv` | number \| null | 过程值（PV 质量码为 Bad 时为 null） |
| `sp` | number \| null | 设定值 |
| `op` | number \| null | 操作输出 |
| `mode` | integer \| null | 控制模式（0=手动, 1=自动） |
| `pvQuality` | integer \| null | PV 质量码（1=Good, 0=Bad） |
| `valid` | boolean | valid_mask 标记（True=有效, False=无效/异常） |
| `outlierReason` | string \| null | 异常原因码（多个以逗号分隔，如 `"FROZEN,JUMP"`） |

**异常响应**

| 场景 | 错误码 | HTTP |
|------|--------|------|
| 时间窗超过 30 天 | `ERR_TS_001` | 400 |
| 结束时间不晚于开始时间 | `ERR_TS_002` | 400 |
| 无效的 tagGroup | `ERR_INVALID_TAG_GROUP` | 400 |
| 回路不存在 | `ERR_LOOP_NOT_FOUND` | 404 |
| 获取数据失败 | `ERR_WAVEFORM_FETCH` | 500 |

---

## 3. 批量波形查询接口 (5.2)

> **设计依据**: IDS §2.4.5, 数据流程图 §7

### 3.1 POST /timeseries/batch/waveform

批量查询多个回路的波形数据，使用 `asyncio.gather` 并行获取。单个回路失败不影响其他回路。

**限制**: 最多 50 个回路，防止资源滥用。

**请求体 (BatchWaveformRequest)**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `loopIds` | string[] | 是 | - | 回路 ID 列表（1~50 个） |
| `startTime` | string | 是 | - | 开始时间（ISO 8601） |
| `endTime` | string | 是 | - | 结束时间（ISO 8601） |
| `tagGroup` | string \| null | 否 | `BASE` | 标签组筛选 |
| `includeValidMask` | boolean | 否 | `true` | 是否返回 valid_mask |
| `maxPoints` | integer | 否 | `5000` | 每个回路最大数据点数（100~50000） |

**请求示例**

```bash
curl -X POST "https://api.clpm.example.com/api/v1/timeseries/batch/waveform" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "loopIds": [
      "00000000-0000-0000-0000-000000000201",
      "00000000-0000-0000-0000-000000000202"
    ],
    "startTime": "2026-06-26T08:00:00Z",
    "endTime": "2026-06-26T09:00:00Z",
    "tagGroup": "BASE",
    "includeValidMask": true,
    "maxPoints": 5000
  }'
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "items": [
      {
        "loopId": "00000000-0000-0000-0000-000000000201",
        "tagName": "80PIC31306_PIDA",
        "timeRange": { "startTime": "...", "endTime": "..." },
        "points": [ ],
        "samplingFreq": "1s",
        "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
        "validRate": 0.98,
        "downsampled": false,
        "pointCount": 3600
      }
    ],
    "failed": [
      {
        "loopId": "00000000-0000-0000-0000-000000000202",
        "error": "ERR_LOOP_NOT_FOUND: 回路不存在"
      }
    ],
    "total": 1
  }
}
```

**响应字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `items` | WaveformResponse[] | 成功获取的波形数据列表（结构同 2.1） |
| `failed` | BatchWaveformFailure[] | 失败的回路列表 |
| `total` | integer | 成功回路数 |

---

## 4. KPI 返回 Schema 扩展 (5.3)

> **设计依据**: IDS §2.7.1, DDS §2.8, 算法说明 §3.7

### 4.1 数据血缘字段定义

KPI 快照返回结构 (`KpiSnapshotSchema`) 新增 7 个数据血缘字段，用于审计追溯和可信度判定。所有新增字段均有默认值 `null`，保持向后兼容。

| 序号 | 字段 | 类型 | 说明 |
|------|------|------|------|
| 1 | `idealSettlingTime` | number \| null | 理想稳态时间（秒），用于快速率评估基准 |
| 2 | `algorithmVersion` | string \| null | 算法版本（如 `KPI_CALC_v2.0`） |
| 3 | `samplingFreq` | string \| null | 实际采样频率（如 `1s` / `5s`） |
| 4 | `qualityPolicy` | string \| null | 质量策略（`KEEP_ALL_WITH_VALIDITY` / `KEEP_ALL`） |
| 5 | `validRate` | number \| null | 有效数据率（0~1） |
| 6 | `confidenceLevel` | string \| null | 可信度等级（A/B/C/D/E） |
| 7 | `dataLineage` | DataLineageSchema \| null | 完整数据血缘信息（JSONB） |

**可信度等级判定规则**（算法说明 §3.7.2）:

| 等级 | 有效数据率 | 含义 |
|------|-----------|------|
| A | ≥ 0.95 | 高可信，结果可直接采用 |
| B | 0.80 ~ 0.95 | 较可信，结果可参考 |
| C | 0.60 ~ 0.80 | 一般可信，建议结合人工判断 |
| D | 0.20 ~ 0.60 | 低可信，结果仅供参考 |
| E | < 0.20 | 不可信，结果不应采用 |

### 4.2 DataLineageSchema 完整结构

数据血缘信息随指标结果一起存储于 `kpi_snapshot_hourly.data_lineage` (JSONB)，由 `DataLineage.to_dict()` 序列化生成。

```json
{
  "samplingFreq": "1s",
  "aggregationPolicy": "LAST",
  "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
  "tagGroup": "BASE",
  "dataBlockIds": ["block-001", "block-002"],
  "validRate": 0.9876,
  "dataPolicyVersion": "pre_v1",
  "algorithmVersion": "KPI_CALC_v2.0"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `samplingFreq` | string | 实际采样频率 |
| `aggregationPolicy` | string | 聚合策略（LAST / MEAN / MAX） |
| `qualityPolicy` | string | 质量策略 |
| `tagGroup` | string | 数据来源 tagGroup |
| `dataBlockIds` | string[] | 使用的 DataBlock ID 列表 |
| `validRate` | number | 有效数据率（0~1） |
| `dataPolicyVersion` | string | 预处理版本（如 `pre_v1`） |
| `algorithmVersion` | string | 算法版本 |

### 4.3 KpiSnapshotSchema 完整结构

```json
{
  "loopId": "00000000-0000-0000-0000-000000000201",
  "tsStart": "2026-06-26T08:00:00Z",
  "tsEnd": "2026-06-26T09:00:00Z",
  "score": 85.6,
  "goodValueRate": 0.98,
  "autoModeRate": 0.95,
  "effectiveAutoRate": 0.92,
  "steadyRate": 0.88,
  "accuracyRate": 0.90,
  "oscillationRate": 0.05,
  "saturationRate": 0.02,
  "fastResponseRate": 0.85,
  "stictionCoeff": 0.12,
  "steadyStateTime": 45.3,
  "outputTravelIndex": 0.78,
  "status": "SUCCESS",
  "idealSettlingTime": 30.0,
  "algorithmVersion": "KPI_CALC_v2.0",
  "samplingFreq": "1s",
  "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
  "validRate": 0.9876,
  "confidenceLevel": "A",
  "dataLineage": {
    "samplingFreq": "1s",
    "aggregationPolicy": "LAST",
    "qualityPolicy": "KEEP_ALL_WITH_VALIDITY",
    "tagGroup": "BASE",
    "dataBlockIds": ["block-001"],
    "validRate": 0.9876,
    "dataPolicyVersion": "pre_v1",
    "algorithmVersion": "KPI_CALC_v2.0"
  }
}
```

---

## 5. DataPlanner 内部接口 (5.4)

> **设计依据**: IDS §2.7.5, PRD §8.1-8.3, ADS §10.7.1-10.7.3
> **权限**: 仅 `ADMIN` 角色可访问
> **说明**: 所有响应仅返回摘要信息，不包含完整时序数据（数据量过大）

### 5.1 POST /algorithms/dataplanner/plan

提交查询计划，根据指标数据需求契约构建合并后的查询计划。不执行实际数据查询，仅返回计划摘要，用于调试和验证。

**请求体 (PlanRequest)**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `loopId` | string | 是 | 回路 ID |
| `metrics` | string[] | 是 | 指标代码列表，如 `["accuracy_rate", "steady_rate"]` |
| `start` | string | 是 | 起始时间（ISO 8601） |
| `end` | string | 是 | 结束时间（ISO 8601） |
| `controlType` | string | 是 | 控制类型: `FC`/`PC`/`TC`/`LC`/`CC` |

**请求示例**

```bash
curl -X POST "https://api.clpm.example.com/api/v1/algorithms/dataplanner/plan" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "loopId": "00000000-0000-0000-0000-000000000201",
    "metrics": ["accuracy_rate", "steady_rate", "oscillation_rate"],
    "start": "2026-06-26T08:00:00Z",
    "end": "2026-06-26T09:00:00Z",
    "controlType": "PC"
  }'
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "loopId": "00000000-0000-0000-0000-000000000201",
    "queryTasks": [
      {
        "tagGroup": "BASE",
        "metrics": ["accuracy_rate", "steady_rate", "oscillation_rate"],
        "tagRoles": ["pv", "sp", "op"],
        "intervalS": 2,
        "reusedFrom": null
      },
      {
        "tagGroup": "PVOP_HF",
        "metrics": ["stiction_coeff"],
        "tagRoles": ["pv", "op"],
        "intervalS": 1,
        "reusedFrom": null
      }
    ],
    "totalTagGroups": 2
  }
}
```

### 5.2 POST /algorithms/dataplanner/bundle

执行查询计划并返回 Bundle 摘要。调用 `DataPlanner.request_bundles` 执行完整取数流程（查缓存 → 未命中查 TDengine + 预处理 → 写缓存 → 组装 Bundle），但仅返回摘要信息。

**请求体 (BundleRequest)**

字段同 [5.1 PlanRequest](#51-post-algorithmsdataplannerplan)。

**请求示例**

```bash
curl -X POST "https://api.clpm.example.com/api/v1/algorithms/dataplanner/bundle" \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "loopId": "00000000-0000-0000-0000-000000000201",
    "metrics": ["accuracy_rate", "steady_rate"],
    "start": "2026-06-26T08:00:00Z",
    "end": "2026-06-26T09:00:00Z",
    "controlType": "PC"
  }'
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "loopId": "00000000-0000-0000-0000-000000000201",
    "bundles": [
      {
        "metricCode": "accuracy_rate",
        "tagGroup": "BASE",
        "samplingFreq": "2s",
        "pointCount": 1800,
        "validRate": 0.9876,
        "dataBlockId": "pdb-001-abc"
      },
      {
        "metricCode": "steady_rate",
        "tagGroup": "BASE",
        "samplingFreq": "2s",
        "pointCount": 1800,
        "validRate": 0.9876,
        "dataBlockId": "pdb-001-abc"
      }
    ],
    "validRate": 0.9876,
    "confidenceLevel": "A"
  }
}
```

**BundleSummary 字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `metricCode` | string | 指标代码 |
| `tagGroup` | string | 数据来源 tagGroup |
| `samplingFreq` | string | 实际采样频率 |
| `pointCount` | integer | 数据点数 |
| `validRate` | number | 有效数据率（0~1） |
| `dataBlockId` | string | DataBlock 唯一标识 |

### 5.3 GET /algorithms/dataplanner/cache/stats

查看 L1 DataBlock 缓存的命中率、内存占用和按 tagGroup 的键数分布。通过 Redis SCAN 遍历 `pdb:*` Key 统计，不阻塞 Redis 主线程。

**请求参数**: 无

**请求示例**

```bash
curl -X GET "https://api.clpm.example.com/api/v1/algorithms/dataplanner/cache/stats" \
  -H "Authorization: Bearer <admin-token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "totalKeys": 156,
    "hitRate": 0.8234,
    "memoryUsageMb": 128.45,
    "byTagGroup": {
      "BASE": 89,
      "OP_HF": 23,
      "PVOP_HF": 21,
      "MODE_HF": 15,
      "QUALITY_HF": 8
    }
  }
}
```

### 5.4 DELETE /algorithms/dataplanner/cache/{loopId}

失效指定回路的所有 L1 DataBlock 缓存。用于回路配置变更（量程/控制类型）后主动清除脏数据。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `loopId` | string | 回路 ID |

**请求示例**

```bash
curl -X DELETE "https://api.clpm.example.com/api/v1/algorithms/dataplanner/cache/00000000-0000-0000-0000-000000000201" \
  -H "Authorization: Bearer <admin-token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "已失效 3 个缓存键",
  "data": {
    "loopId": "00000000-0000-0000-0000-000000000201",
    "deletedKeys": 3
  }
}
```

---

## 6. 任务管理 API (5.5)

> **设计依据**: IDS §2.7.6, PRD §4.3.7
> **存储**: 任务状态存储在 Redis（key: `task:{task_id}`），索引使用有序集合（key: `task:index`，score 为创建时间戳）

### 6.1 POST /tasks/standard/evaluate

触发标准评估任务（手动触发每小时定时评估）。调用 Celery 任务 `calculate_hourly_kpi`，全量计算所有 ACTIVE 回路。

**权限**: `IC_ENGINEER` / `PE_ENGINEER` / `ADMIN`

**请求体 (StandardTaskCreate)**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tsStart` | string \| null | 否 | 评估时间窗起始（ISO 8601），None=当前小时 |

**请求示例**

```bash
curl -X POST "https://api.clpm.example.com/api/v1/tasks/standard/evaluate" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"tsStart": "2026-06-26T08:00:00Z"}'
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "标准评估任务已触发",
  "data": {
    "taskId": "550e8400-e29b-41d4-a716-446655440000",
    "taskType": "STANDARD",
    "status": "PENDING",
    "progress": null,
    "currentStage": null,
    "loopsTotal": null,
    "loopsDone": null,
    "createdAt": "2026-06-26T08:30:00+00:00",
    "startedAt": null,
    "finishedAt": null,
    "errorMessage": null,
    "createdBy": "ic_engineer"
  }
}
```

### 6.2 POST /tasks/custom/evaluate

触发自定义评估任务（按需触发）。对每个目标回路调用 Celery 任务 `calculate_loop_kpi`。

**权限**: `IC_ENGINEER` / `PE_ENGINEER` / `ADMIN`

**并发限制**: 单用户 ≤ 3 个活跃任务，全系统 ≤ 20 个活跃任务。

**请求体 (CustomTaskCreate)**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `loopIds` | string[] | 是 | 目标回路 ID 列表 |
| `metrics` | string[] | 是 | 目标指标子集 |
| `tsStart` | string | 是 | 评估时间窗起始（ISO 8601） |
| `tsEnd` | string | 是 | 评估时间窗结束（ISO 8601） |

**请求示例**

```bash
curl -X POST "https://api.clpm.example.com/api/v1/tasks/custom/evaluate" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "loopIds": ["00000000-0000-0000-0000-000000000201"],
    "metrics": ["accuracy_rate", "steady_rate"],
    "tsStart": "2026-06-26T08:00:00Z",
    "tsEnd": "2026-06-26T09:00:00Z"
  }'
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "自定义评估任务已触发",
  "data": {
    "taskId": "550e8400-e29b-41d4-a716-446655440001",
    "taskType": "CUSTOM",
    "status": "PENDING",
    "progress": 0,
    "currentStage": "取数",
    "loopsTotal": 1,
    "loopsDone": 0,
    "createdAt": "2026-06-26T08:30:00+00:00",
    "startedAt": null,
    "finishedAt": null,
    "errorMessage": null,
    "createdBy": "ic_engineer"
  }
}
```

**异常响应**

| 场景 | 错误码 | HTTP |
|------|--------|------|
| 回路列表为空 | `ERR_INVALID_REQUEST` | 400 |
| 指标列表为空 | `ERR_INVALID_REQUEST` | 400 |
| 单用户并发超限 | `ERR_TASK_CONCURRENCY_LIMIT` | 429 |
| 系统并发超限 | `ERR_TASK_CONCURRENCY_LIMIT` | 429 |

### 6.3 GET /tasks/{taskId}

查询单个任务状态。从 Redis 读取任务状态，并惰性同步 Celery 任务状态（PENDING/RUNNING 时）。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务 ID |

**请求示例**

```bash
curl -X GET "https://api.clpm.example.com/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "taskId": "550e8400-e29b-41d4-a716-446655440000",
    "taskType": "STANDARD",
    "status": "RUNNING",
    "progress": 0.45,
    "currentStage": "指标计算",
    "loopsTotal": 120,
    "loopsDone": 54,
    "createdAt": "2026-06-26T08:00:00+00:00",
    "startedAt": "2026-06-26T08:00:05+00:00",
    "finishedAt": null,
    "errorMessage": null,
    "createdBy": "system"
  }
}
```

**TaskResponse 字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务 ID |
| `taskType` | string | 任务类型（`STANDARD` / `CUSTOM`） |
| `status` | string | 任务状态（`PENDING` / `RUNNING` / `SUCCESS` / `FAILED` / `CANCELLED`） |
| `progress` | number \| null | 进度 0~1 |
| `currentStage` | string \| null | 当前阶段：取数/预处理/指标计算/可信度判定 |
| `loopsTotal` | integer \| null | 总回路数 |
| `loopsDone` | integer \| null | 已完成回路数 |
| `createdAt` | string | 创建时间（ISO 8601） |
| `startedAt` | string \| null | 开始执行时间 |
| `finishedAt` | string \| null | 完成时间 |
| `errorMessage` | string \| null | 失败原因 |
| `createdBy` | string | 创建人用户名（定时任务为 `system`） |

**任务状态机**

```
PENDING → RUNNING → SUCCESS
                   → FAILED
                   → CANCELLED
```

### 6.4 GET /tasks

查询任务列表（按类型/状态/时间筛选），按创建时间倒序排列，支持分页。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|------|
| `taskType` | query | string | 否 | - | 按任务类型筛选: `STANDARD` / `CUSTOM` |
| `status` | query | string | 否 | - | 按状态筛选: `PENDING` / `RUNNING` / `SUCCESS` / `FAILED` / `CANCELLED` |
| `startTime` | query | string | 否 | - | 创建时间起始（ISO 8601） |
| `endTime` | query | string | 否 | - | 创建时间结束（ISO 8601） |
| `limit` | query | integer | 否 | `50` | 返回条数（1~200） |
| `offset` | query | integer | 否 | `0` | 偏移量 |

**请求示例**

```bash
curl -X GET "https://api.clpm.example.com/api/v1/tasks?taskType=CUSTOM&status=RUNNING&limit=20" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "items": [
      { "taskId": "...", "taskType": "CUSTOM", "status": "RUNNING", "..." : "..." }
    ],
    "total": 3
  }
}
```

### 6.5 POST /tasks/{taskId}/cancel

取消运行中的任务。撤销关联的 Celery 任务，并将任务状态更新为 `CANCELLED`。终态任务不可取消。

**权限**: `IC_ENGINEER` / `PE_ENGINEER` / `ADMIN`

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务 ID |

**请求示例**

```bash
curl -X POST "https://api.clpm.example.com/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000/cancel" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "任务已取消",
  "data": {
    "taskId": "550e8400-e29b-41d4-a716-446655440000",
    "taskType": "STANDARD",
    "status": "CANCELLED",
    "finishedAt": "2026-06-26T08:35:00+00:00",
    "..."
  }
}
```

**异常响应**

| 场景 | 错误码 | HTTP |
|------|--------|------|
| 任务不存在 | `ERR_TASK_NOT_FOUND` | 404 |
| 任务已终态 | `ERR_TASK_NOT_CANCELLABLE` | 400 |

### 6.6 GET /tasks/notifications

查询当前用户的任务完成通知。任务进入终态（`SUCCESS`/`FAILED`/`CANCELLED`）时自动推送通知到用户通知列表。

**说明**:
- 通知按时间倒序排列（最新在前）
- 每用户最多保留 100 条
- 系统定时任务（`created_by_id` 为空）不发送通知

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|------|
| `limit` | query | integer | 否 | `20` | 返回条数（1~100） |

**请求示例**

```bash
curl -X GET "https://api.clpm.example.com/api/v1/tasks/notifications?limit=20" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "items": [
      {
        "taskId": "550e8400-e29b-41d4-a716-446655440001",
        "taskType": "CUSTOM",
        "status": "SUCCESS",
        "progress": 1.0,
        "loopsTotal": 5,
        "loopsDone": 5,
        "createdAt": "2026-06-26T08:30:00+00:00",
        "finishedAt": "2026-06-26T08:32:00+00:00",
        "createdBy": "ic_engineer"
      }
    ],
    "total": 1
  }
}
```

### 6.7 POST /tasks/notifications/{taskId}/read

标记指定任务的通知为已读（从通知列表移除）。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务 ID |

**请求示例**

```bash
curl -X POST "https://api.clpm.example.com/api/v1/tasks/notifications/550e8400-e29b-41d4-a716-446655440001/read" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "通知已标记为已读",
  "data": {
    "taskId": "550e8400-e29b-41d4-a716-446655440001",
    "read": true
  }
}
```

**异常响应**

| 场景 | 错误码 | HTTP |
|------|--------|------|
| 通知不存在或已读 | `ERR_NOTIFICATION_NOT_FOUND` | 404 |

---

## 7. 诊断标签接口 (5.6)

> **设计依据**: IDS §2.4.10-2.4.12, PRD §5.6
> **权限**: 查询接口所有认证用户可访问；处理接口需 `IC_ENGINEER` / `PE_ENGINEER` / `ADMIN`

### 7.1 GET /diagnosis/tags

查询诊断标签列表，支持多条件筛选。

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|------|
| `tagType` | query | string | 否 | - | 标签类型筛选（见枚举） |
| `severity` | query | string | 否 | - | 严重等级筛选: `INFO`/`WARN`/`ERROR`/`CRITICAL` |
| `status` | query | string | 否 | - | 处理状态筛选: `ACTIVE`/`RESOLVED`/`SUPPRESSED` |
| `plantNodeId` | query | string | 否 | - | 装置节点 ID 筛选 |
| `tsStart` | query | string | 否 | - | 时间范围开始（ISO 8601） |
| `tsEnd` | query | string | 否 | - | 时间范围结束（ISO 8601） |
| `page` | query | integer | 否 | `1` | 页码（≥1） |
| `pageSize` | query | integer | 否 | `20` | 每页条数（1~100） |

**请求示例**

```bash
curl -X GET "https://api.clpm.example.com/api/v1/diagnosis/tags?tagType=OSCILLATION&status=ACTIVE&page=1&pageSize=20" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "items": [
      {
        "id": "tag-001",
        "loopId": "00000000-0000-0000-0000-000000000201",
        "tagType": "OSCILLATION",
        "severity": "WARN",
        "status": "ACTIVE",
        "sourceMetric": "oscillation_rate",
        "triggerCondition": {
          "algorithm": "IAE_zero_crossing",
          "threshold": 0.3
        },
        "triggerValue": 0.45,
        "threshold": 0.3,
        "confidenceLevel": null,
        "description": "振荡",
        "detectedAt": "2026-06-26T08:00:00+00:00",
        "resolvedAt": null,
        "resolvedBy": null,
        "resolutionNote": null
      }
    ],
    "total": 1,
    "page": 1,
    "pageSize": 20
  }
}
```

### 7.2 GET /diagnosis/tags/{loopId}

查询指定回路的诊断标签。在 `/diagnosis/tags` 基础上增加 `loop_id` 固定筛选。

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `loopId` | string | 回路 ID |

**请求参数**: 同 [7.1](#71-get-diagnosistags)（不含 `plantNodeId`）

**请求示例**

```bash
curl -X GET "https://api.clpm.example.com/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000201?status=ACTIVE" \
  -H "Authorization: Bearer <token>"
```

**成功响应 (200)**: 同 [7.1](#71-get-diagnosistags)

### 7.3 PUT /diagnosis/tags/{tagId}/resolve

处理诊断标签，更新处理状态为 `RESOLVED`（已处理）或 `SUPPRESSED`（已抑制），记录处理人、处理时间和处理说明，写入审计日志。

**权限**: `IC_ENGINEER` / `PE_ENGINEER` / `ADMIN`

**路径参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `tagId` | string | 诊断标签 ID |

**请求体 (TagResolveRequest)**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | string | 是 | 目标处理状态: `RESOLVED` / `SUPPRESSED` |
| `resolutionNote` | string \| null | 否 | 处理说明（抑制时必填） |

**请求示例**

```bash
curl -X PUT "https://api.clpm.example.com/api/v1/diagnosis/tags/tag-001/resolve" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "RESOLVED",
    "resolutionNote": "已调整 PID 参数，振荡消除"
  }'
```

**成功响应 (200)**

```json
{
  "code": "0",
  "message": "处理成功",
  "data": {
    "id": "tag-001",
    "loopId": "00000000-0000-0000-0000-000000000201",
    "tagType": "OSCILLATION",
    "severity": "WARN",
    "status": "RESOLVED",
    "sourceMetric": "oscillation_rate",
    "triggerCondition": { "algorithm": "IAE_zero_crossing", "threshold": 0.3 },
    "triggerValue": 0.45,
    "threshold": 0.3,
    "confidenceLevel": null,
    "description": "振荡",
    "detectedAt": "2026-06-26T08:00:00+00:00",
    "resolvedAt": "2026-06-26T09:00:00+00:00",
    "resolvedBy": "00000000-0000-0000-0000-000000000100",
    "resolutionNote": "已调整 PID 参数，振荡消除"
  }
}
```

**异常响应**

| 场景 | 错误码 | HTTP |
|------|--------|------|
| 标签不存在 | `ERR_DIAG_TAG_NOT_FOUND` | 404 |
| 无效的处理状态 | `ERR_VALIDATION` | 422 |

---

## 8. 附录

### 8.1 枚举值定义

**TaskType** — 任务类型

| 值 | 说明 |
|----|------|
| `STANDARD` | 标准评估任务（每小时定时，全量回路覆盖） |
| `CUSTOM` | 自定义评估任务（用户按需触发，选定回路/指标/时间范围） |

**TaskStatus** — 任务状态

| 值 | 说明 |
|----|------|
| `PENDING` | 已创建待执行 |
| `RUNNING` | 执行中 |
| `SUCCESS` | 成功完成 |
| `FAILED` | 执行失败 |
| `CANCELLED` | 已取消 |

**DiagnosisTagType** — 诊断标签类型（8 类）

| 值 | 说明 |
|----|------|
| `OSCILLATION` | 振荡 |
| `VALVE_STICTION` | 阀门粘滞 |
| `OVERAGGRESSIVE` | 整定过度 |
| `OVERCONSERVATIVE` | 整定不足 |
| `EXTERNAL_DISTURBANCE` | 外部扰动 |
| `QUALITY_ABNORMAL` | 质量异常 |
| `OUTPUT_SATURATION` | 输出饱和 |
| `MANUAL_REVIEW` | 人工复审 |

**DiagnosisTagSeverity** — 严重等级

| 值 | 说明 |
|----|------|
| `INFO` | 信息 |
| `WARN` | 警告 |
| `ERROR` | 错误 |
| `CRITICAL` | 严重 |

**DiagnosisTagStatus** — 处理状态

| 值 | 说明 |
|----|------|
| `ACTIVE` | 活跃（未处理） |
| `RESOLVED` | 已处理 |
| `SUPPRESSED` | 已抑制 |

**tagGroup** — 标签组

| 值 | 说明 |
|----|------|
| `BASE` | 基础指标数据（PV/SP/OP） |
| `OP_HF` | 输出高频数据（OP 1s） |
| `PVOP_HF` | PV+OP 高频数据（1s） |
| `MODE_HF` | 控制模式高频数据（1s） |
| `QUALITY_HF` | 质量码高频数据（1s，KEEP_ALL 策略） |

**ControlType** — 控制类型

| 值 | 说明 | 默认采样间隔 |
|----|------|-------------|
| `FC` | 流量控制 | 1s |
| `PC` | 压力控制 | 2s |
| `TC` | 温度控制 | 5s |
| `LC` | 液位控制 | 5s |
| `CC` | 成分控制 | 10s |

### 8.2 性能与并发约束

**波形数据接口**

| 约束 | 值 | 说明 |
|------|----|------|
| 单次查询时间窗 | ≤ 30 天 | 超过返回 `ERR_TS_001` |
| 单次查询最大点数 | 50000 | 超过触发 LTTB 降采样 |
| 批量查询回路数 | ≤ 50 | 防止资源滥用 |
| 响应时间阈值 | 2000ms | L1 缓存命中时 < 5ms |

**任务管理接口**

| 约束 | 值 | 说明 |
|------|----|------|
| 单用户活跃自定义任务 | ≤ 3 | 超过返回 `ERR_TASK_CONCURRENCY_LIMIT` (429) |
| 系统活跃自定义任务 | ≤ 20 | 超过返回 `ERR_TASK_CONCURRENCY_LIMIT` (429) |
| 任务列表查询条数 | ≤ 200 | `limit` 参数上限 |
| 用户通知保留条数 | 100 | 每用户最多 100 条，超出自动裁剪 |

**DataPlanner 接口**

| 约束 | 值 | 说明 |
|------|----|------|
| 访问权限 | `ADMIN` only | 非 ADMIN 返回 403 |
| 响应内容 | 仅摘要 | 不返回完整时序数据 |
| 缓存失效操作 | SCAN + DEL | 不阻塞 Redis 主线程 |
