# CLPM 接口设计规范说明书 (IDS)

**文档状态**: 正式版
**当前版本**: v2.0
**发布日期**: 2026-06-19
**设计依据**: PRD (v2.2), FDS (v2.0), ADS (v2.0)

---

## 1. 接口设计原则

本规范定义了前后端 (BFF) 之间及系统与外部系统交互的核心 API 契约，严格遵循以下原则：
* **RESTful 风格**：资源导向设计，标准 HTTP 方法 (GET, POST, PUT, PATCH, DELETE)。
* **防超载设计**：对于时序波形数据的查询，必须强制提供时间窗参数，并默认执行降采样。
* **安全与权限**：所有接口需在 Header 中携带 JWT Bearer Token，网关层执行 RBAC 校验。

---

## 2. 核心业务接口契约

### 2.1 性能看板与评估 (Performance & Dashboard)

**2.1.1 获取低效回路排行榜 (Top Bad Actors)**
* **URL**: `GET /api/v1/performance/ranking`
* **权限**: 查看层及以上
* **Query Parameters**:
  * `plantNodeId` (UUID, optional): 按指定装置/单元过滤
  * `timeWindow` (String, required): 时间窗，枚举值 `today`, `yesterday`, `last_7_days`
  * `limit` (Integer, default=10): 返回条数
* **Response (200 OK)**:
  ```json
  {
    "data": [
      {
        "loopId": "uuid-xxx",
        "tagName": "101-FC-1023",
        "score": 45.2,
        "steadyRate": 60.5,
        "status": "SUCCESS",
        "preDiagnosis": "疑似阀门粘滞"
      }
    ]
  }
  ```

### 2.2 诊断与时序波形 (Diagnostics & Timeseries)

**2.2.1 获取高频波形数据 (Get Timeseries Waveform)**
* **URL**: `GET /api/v1/timeseries/{loopId}/waveform`
* **权限**: 执行层及以上
* **Query Parameters**:
  * `startTime` (ISO8601, required): 开始时间
  * `endTime` (ISO8601, required): 结束时间
  * `downsample` (Boolean, default=true): 是否启用 LTTB 降采样
  * `maxPoints` (Integer, default=2000): 前端最大可接受数据点数
* **Response (200 OK)**:
  ```json
  {
    "data": {
      "timestamps": [1623912000000, 1623912001000],
      "pv": [50.1, 50.2],
      "sp": [50.0, 50.0],
      "op": [45.5, 45.8],
      "mode": [1, 1]
    }
  }
  ```

### 2.3 轻量级异常跟踪 (Action Tracker)

**2.3.1 更新回路处理状态 (Update Action Status)**
* **URL**: `PATCH /api/v1/tracker/{loopId}/status`
* **权限**: 仅限执行层 (仪控工程师)
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
    "success": true,
    "updatedAt": "2026-06-19T10:00:00Z"
  }
  ```

**2.3.2 导出诊断建议书 (Export Diagnosis Report)**
* **URL**: `POST /api/v1/tracker/{loopId}/export`
* **说明**: 触发后台无头浏览器截取当前波形图及预诊结论，异步生成 PDF 文件。
* **Response (202 Accepted)**:
  ```json
  {
    "taskId": "task-uuid-xxx",
    "status": "PROCESSING",
    "checkUrl": "/api/v1/tasks/task-uuid-xxx"
  }
  ```

---

## 3. 标准错误码与异常响应 (Error Handling)

所有的非 200 响应必须遵循统一的错误格式：
```json
{
  "errorCode": "ERR_TS_001",
  "errorMessage": "Requested time window exceeds maximum allowed range (30 days)",
  "details": "请缩小查询时间范围或使用聚合接口"
}
```

**核心错误码映射**：
* `ERR_AUTH_401`: Token 缺失或失效。
* `ERR_AUTH_403`: 越权操作（如 Sponsor 尝试修改状态）。
* `ERR_DATA_INCONCLUSIVE`: 目标回路的指定时间窗内有效数据不足（好值率过低），无法计算结果。
* `ERR_TS_DOWNSAMPLE_REQ`: 数据量超过 10万点，要求必须开启 `downsample=true`。
