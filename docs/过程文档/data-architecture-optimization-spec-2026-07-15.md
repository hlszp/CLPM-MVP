# CLPM 数据架构优化实施 Spec

> 版本：v1.0 ｜ 日期：2026-07-15 ｜ 状态：待评审
> 作者：mb 机器（Trae）｜ 协作：zp 机器（Claude Code）

## 目录

1. [背景与目标](#1-背景与目标)
2. [架构设计](#2-架构设计)
3. [Phase 1：启用 TDengine 宽表写回 + 批量写入](#3-phase-1启用-tdengine-宽表写回--批量写入)
4. [Phase 2：改造读取路径 + 切换 taospy 原生接口](#4-phase-2改造读取路径--切换-taospy-原生接口)
5. [Phase 3：历史数据导入功能](#5-phase-3历史数据导入功能)
6. [技术决策清单](#6-技术决策清单)
7. [测试计划](#7-测试计划)
8. [风险与对策](#8-风险与对策)
9. [文档对齐清单](#9-文档对齐清单)

---

## 1. 背景与目标

### 1.1 问题现状

CLPM v6.1 的 KPI 计算存在冷启动性能瓶颈：

| 场景 | 耗时 | 目标 | 状态 |
|---|---|---|---|
| 热启动（L2 缓存命中） | 1.63-2.24s | ≤16s | ✅ 达标 |
| 冷启动（无缓存，CONCURRENCY=5） | 41.66s | ≤16s | ❌ 未达标 |

**根因**：
1. 历史数据查询依赖远端 HTTP API（窄表，7 tag 需 7 次请求），延迟高
2. 预处理管道纯 Python 实现，GIL 竞争导致高并发下单任务从 0.6s 暴增到 12s
3. Beat 提前预热方案因缓存键时间窗口不匹配而无法实施

### 1.2 优化目标

| 目标 | 指标 |
|---|---|
| 冷启动性能 | 27 回路冷启动 ≤16s（1000 回路 ≤600s） |
| 数据独立性 | 日常运行不依赖远端 HTTP API（仅首次上线/补传时使用） |
| 查询性能 | 单回路查询延迟从 10-15ms（REST）降到 0.5-3ms（原生） |
| 回算能力 | 支持任意历史时段 KPI 回算（本地 TDengine 持久化） |
| 存储规划 | 1000 回路 × 1 年 ≤150GB |

### 1.3 核心思路

**远端数据本地化**：实时数据（SignalR）持续写入本地 TDengine 宽表，历史数据查询从本地读取，远端 HTTP API 仅用于首次上线导入和断连补传。

```
远端 SignalR Hub (1Hz)
    ↓ 实时推送
RealtimeSubscriber
    ↓ 按回路聚合 7 tag
TDengine 宽表 st_loop_data (本地持久化)
    ↓ taospy 原生查询
DataPlanner → KPI 计算
```

---

## 2. 架构设计

### 2.1 整体架构

```
┌──────────────────────────────────────────────────────────┐
│                    数据来源层                             │
│  ┌─────────────────┐    ┌──────────────────────────────┐│
│  │ SignalR Hub     │    │ 远端 HTTP API (只读)         ││
│  │ (实时推送 1Hz)   │    │ (历史数据查询, 窄表)         ││
│  └────────┬────────┘    └───────────┬──────────────────┘│
│           │                         │ (仅首次上线/补传)   │
└───────────┼─────────────────────────┼──────────────────┘
            │                         │
┌───────────▼─────────────────────────▼──────────────────┐
│                    写入层                                │
│  ┌──────────────────┐    ┌────────────────────────────┐│
│  │ RealtimeSubscriber│    │ HistoryImportService(新增) ││
│  │ (实时写入, 已存在) │    │ (批量导入, 手动触发)       ││
│  │ stmt 批量写入     │    │ DELETE + stmt 批量写入     ││
│  └────────┬─────────┘    └────────────┬───────────────┘│
└───────────┼───────────────────────────┼────────────────┘
            │                           │
┌───────────▼───────────────────────────▼────────────────┐
│              TDengine 本地宽表 st_loop_data              │
│              (持久化, KEEP 365 天)                       │
│              列: ts/pv/sp/op/mode/pid_p/pid_i/pid_d/    │
│                  pv_quality                              │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│                    读取层                                │
│  ┌──────────────────┐    ┌────────────────────────────┐│
│  │ TDengineProvider  │    │ RealtimeCacheProvider      ││
│  │ (taospy 原生查询) │    │ (Redis ZSET, Phase 4)      ││
│  │ 宽表一次查 7 列   │    │ (毫秒级, 后续阶段)         ││
│  └────────┬─────────┘    └────────────────────────────┘│
└───────────┼────────────────────────────────────────────┘
            │
┌───────────▼────────────────────────────────────────────┐
│              DataPlanner (数据编排器)                    │
│  L1 DataBlock 缓存 → 8 步预处理 → L2 Bundle 缓存        │
└───────────────────────┬────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────┐
│              KPI 计算层 (12 个指标)                      │
│  Layer1 → Layer2 → Layer3 (综合评分)                    │
└────────────────────────────────────────────────────────┘
```

### 2.2 模块归属

**数据管理**功能放在**回路管理模块**下：

```
回路管理 (/loop)
├── 链路配置 (/loop/aas-sync)     — 数据源 + DCS + MODE 矩阵
├── 测点配置 (/tag/list)          — Tag 管理
├── 回路配置 (/loop/manage)       — 回路台账
├── 回路监控 (/loop/monitor)      — 实时监控
└── 数据管理 (/loop/data)         — 历史数据导入 + 数据完整性检查 (新增)
```

### 2.3 数据流时序

**正常实时写入**：
```
SignalR 推送 tag 值
    → RealtimeSubscriber._cache_value()
    → 内存缓冲 _buffer (按回路聚合 7 tag)
    → 每 1s flush → stmt 批量 INSERT → TDengine 宽表
```

**历史数据导入**：
```
用户在 /loop/data 页面选择回路 + 时间范围
    → POST /api/v1/loops/data-import/start
    → Celery 任务: HistoryImportTask
    → 1. DELETE 目标时段旧数据 (冲突策略=覆盖时)
    → 2. 从远端 HTTP API 拉取历史数据
    → 3. 转换为宽表格式 + stmt 批量 INSERT
    → 4. 导入完成 → 可选触发 KPI 回算
```

**KPI 计算**：
```
Celery Beat 每小时触发 calculate_hourly_kpi
    → DataPlanner.request_bundles()
    → TDengineProvider.make_query_fn() → taospy 原生查询
    → 一次查 7 列 (宽表) → 8 步预处理 → 指标计算
```

---

## 3. Phase 1：启用 TDengine 宽表写回 + 批量写入

### 3.1 目标

将 RealtimeSubscriber 的实时数据写入从"单行 INSERT via REST"升级为"stmt 批量写入 via taospy"。

### 3.2 改动清单

#### 3.2.1 配置变更

**文件**：`backend/app/core/config.py`

```python
# 新增配置项
REALTIME_WRITEBACK_ENABLED: bool = True  # 默认启用（原为 False）
TDENGINE_BATCH_SIZE: int = 1000          # stmt 批量写入批次大小
TDENGINE_FLUSH_INTERVAL: float = 1.0     # flush 间隔（秒）
```

**文件**：`backend/.env.example`

```env
REALTIME_WRITEBACK_ENABLED=True
TDENGINE_BATCH_SIZE=1000
TDENGINE_FLUSH_INTERVAL=1.0
```

#### 3.2.2 新增 taospy 连接管理器

**新文件**：`backend/app/core/tdengine_native.py`

```python
"""TDengine 原生连接器（taospy），替代 REST API。

性能优势：
- 查询延迟：10-15ms (REST) → 0.5-3ms (原生)
- 写入吞吐：~1K 行/秒 (REST 单行) → ~100K+ 行/秒 (stmt 批量)

Celery 兼容：
- 原生连接器是同步阻塞的，通过 asyncio.to_thread 包装
- 连接生命周期管理：检测 event loop 变化自动重建

设计依据：TDengine 3.x 官方文档
"""

import asyncio
import threading
from contextlib import contextmanager
from typing import Any

import taos

from app.core.config import settings


class TDengineConnectionPool:
    """TDengine 原生连接池（线程安全）。"""

    _pool: list[taos.TaosConnection] = []
    _lock = threading.Lock()
    _max_size: int = 10

    @classmethod
    def _create_connection(cls) -> taos.TaosConnection:
        """创建新连接。"""
        return taos.connect(
            host=settings.TDENGINE_HOST,
            port=settings.TDENGINE_PORT,
            user=settings.TDENGINE_USER,
            password=settings.TDENGINE_PASSWORD,
            database=settings.TDENGINE_DB,
        )

    @classmethod
    @contextmanager
    def get_connection(cls):
        """获取连接（从池中取，用完归还）。"""
        with cls._lock:
            if cls._pool:
                conn = cls._pool.pop()
            else:
                conn = cls._create_connection()
        try:
            yield conn
        finally:
            with cls._lock:
                if len(cls._pool) < cls._max_size:
                    cls._pool.append(conn)
                else:
                    conn.close()

    @classmethod
    def close_all(cls):
        """关闭所有连接。"""
        with cls._lock:
            for conn in cls._pool:
                conn.close()
            cls._pool.clear()


async def execute_native(sql: str) -> list[dict[str, Any]]:
    """异步执行 SQL（通过 asyncio.to_thread 包装同步调用）。"""
    def _execute():
        with TDengineConnectionPool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            fields = [desc[0] for desc in cursor.description] if cursor.description else []
            cursor.close()
            return [dict(zip(fields, row, strict=False)) for row in rows]
    return await asyncio.to_thread(_execute)


async def batch_insert(subtable: str, rows: list[tuple]) -> int:
    """批量写入（stmt 参数绑定）。

    Args:
        subtable: 子表名（如 d_loop_lic_101）
        rows: 数据行列表，每行格式为 (ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)

    Returns:
        写入行数
    """
    def _batch_insert():
        with TDengineConnectionPool.get_connection() as conn:
            stmt = conn.stmt_init()
            try:
                # 准备 INSERT 语句（使用超级表 + 子表自动创建）
                sql = f"INSERT INTO {subtable} USING st_loop_data TAGS(?, ?) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                stmt.prepare(sql)
                # 绑定参数并批量执行
                for batch in _chunks(rows, settings.TDENGINE_BATCH_SIZE):
                    stmt.bind_param(batch)
                    stmt.add_batch()
                    stmt.execute()
                return len(rows)
            finally:
                stmt.close()
    return await asyncio.to_thread(_batch_insert)


def _chunks(lst: list, size: int):
    """将列表分块。"""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]
```

#### 3.2.3 改造 RealtimeSubscriber 写入逻辑

**文件**：`backend/app/services/data_source/realtime_subscriber.py`

改动点：
1. `_flush_buffer()` 改为批量写入（积累多回路后一次 stmt 写入）
2. `_write_loop_data()` 改为调用 `tdengine_native.batch_insert()`
3. 增加写入失败重试（3 次，指数退避）

```python
# 改造前（单行 INSERT via REST）：
async def _write_loop_data(self, loop_part, roles_data):
    sql = f"INSERT INTO {subtable} VALUES ('{ts}', {pv}, {sp}, ...)"
    await execute_sql(sql)

# 改造后（stmt 批量写入 via taospy）：
async def _flush_buffer(self):
    """将缓冲区数据批量写入 TDengine。"""
    async with self._buffer_lock:
        if not self._buffer:
            return
        buffer_copy = dict(self._buffer)
        self._buffer.clear()

    # 按回路组装行数据
    all_rows: dict[str, list[tuple]] = {}
    for loop_part, roles_data in buffer_copy.items():
        subtable = make_subtable_name(loop_part)
        row = self._build_row(roles_data)
        all_rows.setdefault(subtable, []).append(row)

    # 批量写入
    for subtable, rows in all_rows.items():
        try:
            await batch_insert(subtable, rows)
        except Exception as exc:
            logger.warning("批量写入失败: subtable=%s, rows=%d, error=%s", subtable, len(rows), exc)
            # 重试逻辑
```

#### 3.2.4 子表自动创建

**文件**：`backend/app/core/tdengine_native.py`

在首次写入时自动创建子表（如果不存在）：

```python
async def ensure_subtable(subtable: str, loop_id: str, unit_id: str):
    """确保子表存在（不存在则创建）。"""
    sql = (
        f"CREATE TABLE IF NOT EXISTS {subtable} "
        f"USING st_loop_data TAGS ('{loop_id}', '{unit_id}')"
    )
    await execute_native(sql)
```

### 3.3 验收标准

| 验收项 | 标准 |
|---|---|
| 实时数据写入 | 27 回路 × 1Hz 持续写入 1 小时无错误 |
| 写入性能 | 1000 回路 × 1Hz 写入吞吐 ≥1000 行/秒 |
| 数据完整性 | TDengine 中每回路每小时数据点数 ≥3500（容错 ≤5% 丢失） |
| 子表自动创建 | 新回路首次推送时自动创建子表 |
| 写入失败重试 | 单批次失败自动重试 3 次，指数退避 |

---

## 4. Phase 2：改造读取路径 + 切换 taospy 原生接口

### 4.1 目标

将 DataPlanner 的查询从"远端 HTTP API（窄表，7 次请求）"切换为"本地 TDengine（宽表，1 次原生查询）"。

### 4.2 改动清单

#### 4.2.1 新增宽表查询函数

**文件**：`backend/app/core/tdengine.py`（新增函数）

```python
async def query_wide_table(
    loop_id: str,
    tag_roles: list[str],
    start_time: datetime,
    end_time: datetime,
) -> RawTimeSeries:
    """从宽表 st_loop_data 查询回路数据（一次查 7 列）。

    替代原 make_dataplanner_query_fn 中的 7 次窄表查询。

    Args:
        loop_id: 回路 ID
        tag_roles: tag 角色列表（如 ["pv", "sp", "op", "mode", "pid_p", "pid_i", "pid_d"]）
        start_time: 开始时间
        end_time: 结束时间

    Returns:
        RawTimeSeries（含 timestamps + signals + quality_codes）
    """
    from app.core.tdengine_native import execute_native

    # 查询子表名（从 LoopTagMapping 获取 loop_part → make_subtable_name）
    subtable = await _get_subtable_by_loop_id(loop_id)

    # 构造宽表查询 SQL（一次查所有列）
    sql = (
        f"SELECT ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality "
        f"FROM {settings.TDENGINE_DB}.{subtable} "
        f"WHERE ts >= '{start_time.strftime('%Y-%m-%d %H:%M:%S.%f')}' "
        f"AND ts <= '{end_time.strftime('%Y-%m-%d %H:%M:%S.%f')}' "
        f"ORDER BY ts ASC"
    )

    rows = await execute_native(sql)

    # 组装 RawTimeSeries
    timestamps = []
    signals = {"pv": [], "sp": [], "op": [], "mode": [], "pid_p": [], "pid_i": [], "pid_d": []}
    quality_codes = {"pv_quality": []}

    for row in rows:
        timestamps.append(row["ts"])
        for role in tag_roles:
            signals[role].append(row.get(role))
        quality_codes["pv_quality"].append(row.get("pv_quality", 1))

    return RawTimeSeries(
        timestamps=timestamps,
        signals=signals,
        quality_codes=quality_codes,
    )
```

#### 4.2.2 改造 TDengineProvider

**文件**：`backend/app/services/data_source/tdengine_provider.py`

```python
class TDengineProvider(HistoryDataProvider):
    """TDengine 数据源 Provider（宽表 + taospy 原生查询）。"""

    async def make_query_fn(self, db: AsyncSession):
        """返回 DataPlanner 兼容的查询闭包。"""
        async def query_fn(loop_id, tag_roles, start, end, interval_s):
            # 直接查宽表（一次查 7 列），不再 7 次窄表查询
            return await query_wide_table(loop_id, tag_roles, start, end)

        return query_fn

    async def query_trend_data(self, tag_name, start_time, end_time, sample_interval=1):
        """查询单 tag 趋势（兼容旧接口，波形展示路径使用）。"""
        # 保留窄表查询作为兼容（波形展示可能仍需要）
        return await _query_trend_data_legacy(tag_name, start_time, end_time)

    async def close(self):
        from app.core.tdengine_native import TDengineConnectionPool
        TDengineConnectionPool.close_all()
```

#### 4.2.3 数据源切换

**文件**：`backend/.env.example`

```env
# 数据源类型：tdengine（本地宽表）或 remote_api（远端 HTTP API）
DATA_SOURCE_TYPE=tdengine

# 实时数据写回本地 TDengine 宽表
REALTIME_WRITEBACK_ENABLED=True
```

#### 4.2.4 保留窄表查询（兼容）

`query_trend_data`（窄表查询）保留，用于：
- 波形展示路径（如果前端仍查窄表）
- 远端 API 兼容模式（`DATA_SOURCE_TYPE=remote_api`）

### 4.3 验收标准

| 验收项 | 标准 |
|---|---|
| KPI 计算冷启动 | 27 回路 ≤16s |
| 单回路查询延迟 | ≤3ms（taospy 原生） |
| 查询次数 | 每回路 1 次（宽表一次查 7 列），不再是 7 次 |
| 数据源切换 | `DATA_SOURCE_TYPE=tdengine` 时全部走本地宽表 |
| remote_api 兼容 | `DATA_SOURCE_TYPE=remote_api` 时仍走远端 HTTP API |
| 单元测试 | 全量通过，零回归 |

---

## 5. Phase 3：历史数据导入功能

### 5.1 目标

提供前端页面，支持用户批量选择回路 + 时间范围，从远端 HTTP API 拉取历史数据写入本地 TDengine 宽表。

### 5.2 前端设计

#### 5.2.1 路由注册

**文件**：`frontend/apps/web-antd/src/router/routes/modules/loop.ts`

```typescript
// 新增路由
{
  path: 'data',
  name: 'LoopData',
  component: () => import('#/views/loop/data.vue'),
  meta: {
    title: '数据管理',
    icon: 'lucide:database',
    order: 5,
    authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
  },
},
```

#### 5.2.2 页面布局

**新文件**：`frontend/apps/web-antd/src/views/loop/data.vue`

```
┌─────────────────────────────────────────────────────┐
│  数据管理                                            │
├─────────────────────────────────────────────────────┤
│                                                     │
│  [历史数据导入]  [数据完整性检查]  [导入历史]       │
│                                                     │
│  === 历史数据导入 ===                               │
│                                                     │
│  回路选择: [全选] [反选]  已选: 27/27               │
│  ┌─────────────────────────────────────────────┐   │
│  │ ☑ LIC-101 (液位控制)     上次导入: 07-14    │   │
│  │ ☑ FIC-201 (流量控制)     上次导入: 07-14    │   │
│  │ ☑ TIC-301 (温度控制)     未导入             │   │
│  │ ...                                         │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  时间范围: [2026-06-15 00:00] ~ [2026-07-15 00:00] │
│  采样间隔: [1 秒 ▼]                                 │
│  冲突策略: [● 覆盖(手工优先)] [○ 跳过(保留已有)]   │
│  导入后: [☑ 自动触发 KPI 回算]                     │
│                                                     │
│  [开始导入]                          [查看历史任务]  │
│                                                     │
├─────────────────────────────────────────────────────┤
│  导入任务列表                                        │
│  ┌────────┬──────┬─────────────┬───────┬──────────┐ │
│  │ 任务ID │ 回路数│ 时间范围    │ 状态  │ 操作     │ │
│  │ #001   │ 27   │ 06-15~07-15 │ ✅完成│ 回算/详情│ │
│  │ #002   │ 5    │ 07-14~07-15 │ 🔄53%│ 取消     │ │
│  └────────┴──────┴─────────────┴───────┴──────────┘ │
└─────────────────────────────────────────────────────┘
```

#### 5.2.3 前端 API 客户端

**新文件**：`frontend/apps/web-antd/src/api/loop-data.ts`

```typescript
import { requestClient } from '#/api/request';

export namespace LoopDataApi {
  /** 开始历史数据导入 */
  export interface ImportRequest {
    loopIds: string[];
    tsStart: string;
    tsEnd: string;
    interval?: number;
    conflictStrategy?: 'overwrite' | 'skip';
    triggerBackfill?: boolean;
  }

  /** 导入任务状态 */
  export interface ImportTask {
    taskId: string;
    status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled';
    progress: number;
    loopCount: number;
    importedCount: number;
    errorCount: number;
    tsStart: string;
    tsEnd: string;
    createdAt: string;
    finishedAt?: string;
    errorMessage?: string;
  }
}

/** 开始历史数据导入 */
export function startImportApi(data: LoopDataApi.ImportRequest) {
  return requestClient.post('/loops/data-import/start', data);
}

/** 查询导入任务状态 */
export function getImportStatusApi(taskId: string) {
  return requestClient.get(`/loops/data-import/${taskId}/status`);
}

/** 取消导入任务 */
export function cancelImportApi(taskId: string) {
  return requestClient.post(`/loops/data-import/${taskId}/cancel`);
}

/** 查询导入任务列表 */
export function getImportTasksApi(params: { page: number; pageSize: number }) {
  return requestClient.get('/loops/data-import/tasks', { params });
}

/** 触发 KPI 回算 */
export function triggerBackfillApi(taskId: string) {
  return requestClient.post(`/loops/data-import/${taskId}/backfill-kpi`);
}
```

### 5.3 后端设计

#### 5.3.1 API 端点

**新文件**：`backend/app/api/v1/endpoints/loop_data.py`

```python
"""回路数据管理 API（历史数据导入 + 数据完整性检查）。

路由前缀: /api/v1/loops/data-import
权限: ADMIN / IC_ENGINEER / PE_ENGINEER
"""

from fastapi import APIRouter, Depends
from app.api.deps import require_roles

router = APIRouter(prefix="/loops/data-import", tags=["loop-data"])


@router.post("/start")
@require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")
async def start_import(request: ImportRequest):
    """开始历史数据导入。

    创建 Celery 任务，从远端 HTTP API 拉取历史数据写入本地 TDengine 宽表。
    """
    task = import_history_data.delay(
        loop_ids=request.loopIds,
        ts_start=request.tsStart,
        ts_end=request.tsEnd,
        interval=request.interval or 1,
        conflict_strategy=request.conflictStrategy or "overwrite",
        trigger_backfill=request.triggerBackfill or False,
    )
    return {"task_id": task.id}


@router.get("/{task_id}/status")
@require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")
async def get_import_status(task_id: str):
    """查询导入任务状态。"""
    # 从 TaskTracker 或 Redis 获取状态
    ...


@router.post("/{task_id}/cancel")
@require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")
async def cancel_import(task_id: str):
    """取消导入任务。"""
    ...


@router.get("/tasks")
@require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")
async def list_import_tasks(page: int = 1, page_size: int = 20):
    """查询导入任务列表。"""
    ...


@router.post("/{task_id}/backfill-kpi")
@require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")
async def trigger_backfill(task_id: str):
    """导入完成后触发 KPI 回算。"""
    ...
```

#### 5.3.2 导入服务

**新文件**：`backend/app/services/data_import.py`

```python
"""历史数据导入服务。

从远端 HTTP API 拉取历史数据，写入本地 TDengine 宽表。
支持冲突策略：overwrite（覆盖）或 skip（跳过）。
"""

async def import_history_data(
    loop_ids: list[str],
    ts_start: str,
    ts_end: str,
    interval: int = 1,
    conflict_strategy: str = "overwrite",
    trigger_backfill: bool = False,
) -> dict:
    """执行历史数据导入。

    流程：
    1. 查询回路信息 + tag 映射
    2. 对每个回路：
       a. (overwrite 策略) DELETE 目标时段旧数据
       b. 从远端 HTTP API 拉取历史数据
       c. 转换为宽表格式
       d. stmt 批量写入 TDengine
    3. 更新导入进度
    4. (可选) 触发 KPI 回算

    Args:
        loop_ids: 回路 ID 列表
        ts_start: 开始时间 (ISO 8601)
        ts_end: 结束时间 (ISO 8601)
        interval: 采样间隔（秒），默认 1
        conflict_strategy: 冲突策略，overwrite 或 skip
        trigger_backfill: 是否在导入完成后触发 KPI 回算

    Returns:
        导入结果 {total, succeeded, failed, errors}
    """
    ...


async def _import_single_loop(
    loop_id: str,
    tag_mapping: dict[str, str],
    ts_start: datetime,
    ts_end: datetime,
    interval: int,
    conflict_strategy: str,
) -> int:
    """导入单个回路的历史数据。

    Returns:
        导入的数据点数
    """
    subtable = make_subtable_name(loop_part)

    # 1. 冲突处理：删除旧数据
    if conflict_strategy == "overwrite":
        await execute_native(
            f"DELETE FROM {subtable} WHERE ts >= '{ts_start}' AND ts <= '{ts_end}'"
        )

    # 2. 从远端 HTTP API 拉取数据
    remote_provider = RemoteApiProvider()
    raw_data = await remote_provider.query_trend_data_batch(
        tag_codes=list(tag_mapping.values()),
        start_time=ts_start,
        end_time=ts_end,
        sample_interval=interval,
    )

    # 3. 转换为宽表行格式
    rows = _convert_to_wide_rows(raw_data, tag_mapping)

    # 4. stmt 批量写入
    count = await batch_insert(subtable, rows)

    return count
```

#### 5.3.3 Celery 任务

**文件**：`backend/app/tasks/kpi_calc.py`（新增任务）

```python
@celery_app.task(
    name="app.tasks.kpi_calc.import_history_data",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 1, "countdown": 30},
    retry_backoff=True,
    time_limit=3600,  # 1 小时超时
)
def import_history_data(
    self: AsyncTask,
    loop_ids: list[str],
    ts_start: str,
    ts_end: str,
    interval: int = 1,
    conflict_strategy: str = "overwrite",
    trigger_backfill: bool = False,
) -> dict:
    """历史数据导入 Celery 任务。"""
    return self.run_async(_do_import_with_tracking(
        loop_ids, ts_start, ts_end, interval, conflict_strategy, trigger_backfill
    ))
```

#### 5.3.4 路由注册

**文件**：`backend/app/main.py`

```python
from app.api.v1.endpoints import loop_data
v1_router.include_router(loop_data.router, prefix="/api/v1")
```

#### 5.3.5 冲突处理策略

| 策略 | 实现 | 适用场景 |
|---|---|---|
| **overwrite（覆盖）** | 先 `DELETE` 目标时段，再 `INSERT` | 断连补传、数据修正 |
| **skip（跳过）** | 直接 `INSERT`，依赖 TDengine UPSERT | 通用场景 |

**overwrite 策略的时序安全**：

```
手工导入 (历史时段: 10:00-11:00)
    │
    ├── DELETE FROM d_loop_xxx WHERE ts >= '10:00' AND ts <= '11:00'
    │   (仅删除 10:00-11:00 的数据，不影响当前秒)
    │
    ├── INSERT INTO d_loop_xxx VALUES (...)
    │   (写入 10:00-11:00 的完整数据)
    │
    └── 完成

同时进行的实时写入 (当前秒: 11:30:15)
    │
    └── INSERT INTO d_loop_xxx VALUES ('11:30:15', ...)
        (不受 DELETE 影响，因为不在 10:00-11:00 范围内)
```

### 5.4 验收标准

| 验收项 | 标准 |
|---|---|
| 页面访问 | `/loop/data` 可正常访问，权限正确 |
| 回路选择 | 支持全选/反选/搜索，显示上次导入时间 |
| 导入功能 | 选择回路 + 时间范围后可启动导入 |
| 冲突策略 | overwrite 先 DELETE 再 INSERT，skip 直接 INSERT |
| 进度跟踪 | 实时显示导入进度（已完成回路数/总回路数） |
| 取消功能 | 可取消正在进行的导入任务 |
| KPI 回算 | 导入完成后可触发 KPI 回算 |
| 1000 回路导入 | 1000 回路 × 7 天数据导入 ≤30 分钟 |

---

## 6. 技术决策清单

| # | 决策项 | 选择 | 理由 |
|---|---|---|---|
| 1 | TDengine 接口 | taospy 原生连接器 | 比 REST 快 3-10 倍，已安装 |
| 2 | 表结构 | 宽表 `st_loop_data` | 一次查 7 列，减少查询次数 |
| 3 | 写入方式 | stmt 参数绑定批量写入 | 吞吐量 ~100K 行/秒 |
| 4 | 冲突策略 | 默认 overwrite（先 DELETE 再 INSERT） | 手工导入优先，保证数据一致性 |
| 5 | 数据管理归属 | 回路管理模块 `/loop/data` | 数据是回路的核心资产 |
| 6 | 远端 API 保留 | 保留作为数据补传通道 | 首次上线 + 断连补传 |
| 7 | 断点续传 | 不实现 | 同一台服务器，ConfidenceEvaluator 兜底 |
| 8 | Celery 任务 | `import_history_data` 独立任务 | 复用 AsyncTask + TaskTracker 模式 |
| 9 | 子表创建 | 首次写入时自动创建 | `CREATE TABLE IF NOT EXISTS` |
| 10 | 窄表查询保留 | 保留 `query_trend_data` | 波形展示路径兼容 |

---

## 7. 测试计划

### 7.1 单元测试

| 测试项 | 范围 |
|---|---|
| `tdengine_native.py` | 连接池、batch_insert、execute_native |
| `data_import.py` | import_history_data、_import_single_loop、冲突策略 |
| `tdengine_provider.py` | make_query_fn（宽表查询）、query_trend_data（兼容） |
| `realtime_subscriber.py` | _flush_buffer（批量写入）、写入重试 |

### 7.2 集成测试

| 测试项 | 步骤 |
|---|---|
| 实时写入 → KPI 计算 | SignalR 推送 1 小时 → 触发 KPI 计算 → 验证结果 |
| 历史导入 → KPI 回算 | 从远端 API 导入 1 天数据 → 触发回算 → 验证结果 |
| 冲突处理 | 实时写入 1 小时 → 手工导入同一时段 → 验证数据被覆盖 |
| 数据源切换 | `DATA_SOURCE_TYPE=tdengine` vs `remote_api` 切换 |

### 7.3 性能测试

| 测试项 | 目标 |
|---|---|
| 27 回路冷启动 | ≤16s |
| 1000 回路冷启动 | ≤600s |
| 27 回路 × 7 天导入 | ≤5 分钟 |
| 1000 回路 × 7 天导入 | ≤30 分钟 |
| 实时写入吞吐 | ≥1000 行/秒（1000 回路） |
| 单回路查询延迟 | ≤3ms |

### 7.4 E2E 测试

| 场景 | 步骤 |
|---|---|
| 系统上线 | 部署 → 配置回路 → 导入 30 天历史 → 回算 KPI → 启动实时 |
| 断连恢复 | SignalR 断连 1 小时 → 恢复 → 补传缺失数据 → 重算 KPI |
| 日常运行 | 实时写入 1 小时 → 自动 KPI 计算 → 验证结果 |

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| taospy 在 Celery async 环境不兼容 | 写入/查询失败 | 用 `asyncio.to_thread` 包装同步调用 |
| TDengine 连接泄漏 | 连接耗尽 | 连接池管理 + finally 块确保归还 |
| 远端 API 限流 | 导入缓慢 | 限流 + 重试 + 进度可恢复 |
| 子表创建失败 | 写入失败 | `CREATE TABLE IF NOT EXISTS` + 重试 |
| 数据丢失（SignalR 断连） | KPI 计算数据不足 | ConfidenceEvaluator 降级为 INCONCLUSIVE |
| 磁盘空间不足 | 写入失败 | 监控 + 告警 + KEEP 自动过期 |
| schema 不一致 | 读取失败 | 统一到宽表，保留窄表查询兼容 |

---

## 9. 文档对齐清单

实施完成后需同步更新以下文档：

| 文档 | 更新内容 |
|---|---|
| `implementation-contract.md` | 新增 `/loop/data` 路由 + `/loops/data-import/*` API |
| `DDS.md` | TDengine 宽表写入策略 + 存储规划 |
| `IDS.md` | 新增 API 端点定义 |
| `ui-ux-design-guidelines.md` | 数据管理页面设计 |
| `.env.example` | 新增配置项 |
| `AGENTS.md` | 更新基线版本 |

---

## 附录 A：文件变更清单

| 文件 | 变更类型 | Phase |
|---|---|---|
| `backend/app/core/config.py` | 修改（新增配置项） | 1 |
| `backend/.env.example` | 修改（新增配置项） | 1 |
| `backend/app/core/tdengine_native.py` | **新增** | 1 |
| `backend/app/services/data_source/realtime_subscriber.py` | 修改（批量写入） | 1 |
| `backend/app/core/tdengine.py` | 修改（新增 query_wide_table） | 2 |
| `backend/app/services/data_source/tdengine_provider.py` | 修改（宽表查询） | 2 |
| `backend/app/services/data_import.py` | **新增** | 3 |
| `backend/app/api/v1/endpoints/loop_data.py` | **新增** | 3 |
| `backend/app/tasks/kpi_calc.py` | 修改（新增 import_history_data 任务） | 3 |
| `backend/app/main.py` | 修改（注册新路由） | 3 |
| `frontend/.../router/routes/modules/loop.ts` | 修改（新增 /loop/data 路由） | 3 |
| `frontend/.../views/loop/data.vue` | **新增** | 3 |
| `frontend/.../api/loop-data.ts` | **新增** | 3 |

## 附录 B：实施顺序

```
Phase 1 (1-2 天)
  ├── 新增 tdengine_native.py
  ├── 改造 realtime_subscriber.py
  ├── 配置变更
  └── 测试：实时写入 27 回路 × 1 小时

Phase 2 (1-2 天)
  ├── 新增 query_wide_table
  ├── 改造 tdengine_provider.py
  ├── 切换 DATA_SOURCE_TYPE=tdengine
  └── 测试：27 回路冷启动 ≤16s

Phase 3 (2-3 天)
  ├── 新增 data_import.py
  ├── 新增 loop_data.py API
  ├── 新增 Celery 任务
  ├── 新增前端页面
  └── 测试：导入 + 回算 + 冲突处理
```

---

**文档状态**：待用户评审确认后开始实施。
