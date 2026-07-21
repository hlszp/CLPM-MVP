# 回路管理模块整改十阶段执行计划（2026-07-21）

> 状态：**已暂停，待与性能评估整改通盘规划后重启**（见文末「执行进度与交接状态」）。
> 依据：`docs/过程文档/loop-management-module-review-plan-2026-07-21.md`（审查报告）+ Phase 4 决策 D1-D6。

## Phase 4 决策（用户拍板，2026-07-21）

- **D1 AAS 同步**：以当前实现为准，删不符现状的 PRD 条款与死代码（前端 `api/aas.ts` 未用函数、相关文档要求）；后端 `/aas/*` 端点与 Celery 同步任务保留
- **D2 测点**：保留编辑/导入能力，PRD §4.2.6 只读条款改为与实际一致
- **D3 数据管理权限**：维持现状（允许导入/删除），契约权限条款按现状标注"待统一完善"
- **D4 断点续传机制**（GAP_BACKFILL_*，2026-07-20 上线）补入 PRD
- **D5 文档版本口径**前后一致（实现契约 v2.0 为准）
- **D6 卡片视图（PRD §4.2.5）与监控导出功能**：文档降级（标注未实现/后续阶段）

每阶段验收：后端 `uv run pytest -q` 全绿 + `ruff check . && ruff format --check .`；涉前端时 `pnpm run check:type`。

## 阶段 1：P0 崩溃修复（批量链路 + 导入默认值）✅ 已完成

目标：批量配置/删除/Excel 导入不再 500。

- `services/loop_batch.py`：`loop.level`→`importance_level`（126/137/144 行）；`_BATCH_UPDATABLE_FIELDS` 补 `importance_level`/`include_in_evaluation`；`batch_delete_loops` 返回 `{"deleted","skipped"}` 与 endpoint（`loops.py:177-183`）对齐；审计 target_id 逐回路单条写入（UUID 列，逗号串会 INSERT 失败）
- `services/loop.py`：新建/导入回路 `importance_level` 默认 2、`include_in_evaluation` 默认 True（`create_loop` 682-684 行附近、`_import_one_row` 1712-1729 行）
- 测试：`tests/test_loop_batch.py` 适配新接口 + 新增 include_in_evaluation/partial-skip 用例；`tests/test_loop.py` 新增缺省值兜底用例

验收：2056 passed / 1 skipped + ruff 全过（2026-07-21 已验收）

## 阶段 2：下线孤儿页 tag-mapping.vue 🔶 进行中（半成品，状态自洽）

目标：删除失效页面，监控页跳转不再死链。

- 删 `views/loop/tag-mapping.vue`（`getAasTagsApi({pageSize:10000})` 撞后端 `le=100` 永远 422，页面无路由注册）
- `monitor.vue:869` 跳转 `/loop/tag-mapping?loopId=...` 改到 `/loop/manage`（Tag 关联在其内嵌抽屉）
- 删前端 `api/aas.ts`（7 个函数均无调用；`quality-tag.vue` 仅用 `AasApi.Quality` 类型——**已内联完成**）
- 验收：`pnpm run check:type` 无残留引用

## 阶段 3：时区口径核实与修复

目标：导入/补数与实时写入时间戳口径一致，消除 8h 偏移风险。

- 实测：调用远端 HistoryData/Get 拿已知时刻数据点比对 timestamps 时区；查 SignalR collectTime 实际格式
- 统一约定后修 `data_import.py:_parse_dt/_parse_ts_str` 与 `realtime_subscriber.py:_build_row` 为显式 `astimezone` 转换
- 抽查 TDengine 同回路实时段与导入段 ts 连续性
- 注：实测若受阻可顺延，不阻塞 4-9

## 阶段 4：断点续传加固（P1 最紧迫，真实环境已暴露）

目标：补数失败不丢缺口、可重试、可观测、不压垮远端。

- `realtime_subscriber.py`：`failed>0` 时不推进 checkpoint（当前无条件推进，2026-07-20 已发生 2 回路失败缺口静默丢失）；补数失败启动延迟重试定时器（5min 起步，指数退避上限 30min，连接在线也生效）
- 补数登记 Redis 任务记录（来源标记 auto-backfill，任务列表可见）+ 失败接 alerting
- `data_import._fetch_remote_history` 接入 `remote_api_provider` 共享熔断器/全局限流（当前补数+导入+provider 三路并发可达 8）
- 新增单测：部分失败不推进/重试定时器/任务记录写入

## 阶段 5：回路监控契约对齐 ⚠️ 与性能评估整改 #7（SPONSOR 门控）同改 `monitor.vue`，需错开

- 后端 `services/monitor.py`：`status` 拆 `loopStatus`+`kpiStatus`（当前 KPI 状态与 LoopStatus 撞名 PARTIAL）；`currentValues` 补 `unit`；去 `"GOOD"` 改 INCONCLUSIVE；列表过滤 `is_active` 与统计卡片口径统一；非法 trendWindow 返回 400；补 `last_7_days`（或前端删该枚举）
- 前端 `monitor.vue`：类型对齐；WS MODE 映射与后端统一（当前写死 0/1/2，MODE=3/4 显示 Unknown）；统计联动
- 接口级测试 + type-check

## 阶段 6：回路配置契约修复

- `schemas/loop.py`：LoopUpdate 补 `unitId`（当前静默丢弃且确认弹窗展示该 diff）+ service 落库校验；评估 `CamelModel extra="forbid"`
- `services/loop.py`：`_mode_value_to_label` 硬编码改 dcs_model→dcs_mode_mapping→默认回退链；删除回路先解绑 7 Tag 再软删（对齐弹窗文案，当前有关联永远删不掉）
- 前端：抽屉/详情补 PID 只读区（runtimeParams.pidP/I/D 后端已返回）；权限按钮与后端对齐（PE_ENGINEER 放开入口）⚠️ 与对方收紧 SPONSOR 的门控口径一起核对

## 阶段 7：测点配置契约修复

- 前端 `api/tag.ts`、`tag/list.vue`：枚举 KP/TI/TD→PID_P/I/D、POSITION→SPEED（当前筛选查空、编辑提交 500）
- 后端：编辑支持参数类型（TagUpdate 补字段）；列表/详情补 unitName（嵌套 loop vs 前端扁平字段不一致）
- `services/tag_mapping.py:216-225`：解关联清 is_linked 前查其他回路引用；`services/tag.py:766`：导入忽略"是否启用"列（is_linked 仅由映射派生，防污染实时订阅集合）；AAS 同步不回冲手工编辑的描述

## 阶段 8：链路配置安全与一致性

- `datasource_config.py`：审计与 GET 响应 Token 打码 + "不填即不变"；`signalrSubscriberRunning` 接订阅器真实状态（当前是 settings 镜像，保存后"需重启"提示立即消失）；`_cast_value` 脏数据容错；配置读写合并 IN 查询；支持清空 URL/Token
- 前端 `aas.vue`：测试连接前提示"将先保存"；网络模式切换加二次确认（瞬断实时链路）；清空字段可用
- Tailscale 切换失败回滚 sys_config networkMode（当前 DB 与路由发散）

## 阶段 9：韧性增强包

- 数据停滞看门狗：N 分钟（默认 5min）无消息主动断开重连（覆盖"WS 连接活着但上游停推"盲区）；落库 checkpoint 与接收 checkpoint 分离（flush 最终失败不推进落库点）
- WS 客户端参数放宽：ping_interval=30/ping_timeout=60/open_timeout=15（默认 20/20/10 对过载边缘服务器太激进）
- `data_link_monitor.py` TDengine 新鲜度检查接 Celery beat + 告警（当前整模块死代码）
- 导入任务生命周期：chunk 级取消检查、Celery finally 兜底终态、RUNNING 超时清扫（worker 被杀任务永久卡"执行中"）、Redis 任务 TTL（30 天）+ 索引修剪
- 断点续传 Redis SETNX 分布式锁（多副本防重复补数）

## 阶段 10：性能 UX 打包 + 文档口径落地 ⚠️ 与对方 #2（权重口径）同触 PRD/FDS 文档，需错开

- 性能：快照查询真 DISTINCT ON（`monitor.py:369-379` 注释自称用了但没用）；子孙节点 CTE（loop.py/monitor.py 两处重复递归）；树计数 GROUP BY 聚合；`/tags/match-loop` 合并 IN 查询；stats 短 TTL 缓存
- UX：监控页错误/空态分离、WS 在线状态栏、pageSize 兜底（`|| 100` vs 默认 20）；数据管理页取消默认全选、取消加确认、透传后端错误、回路列表服务端分页（当前 pageSize=100 硬编码超量静默丢失）；质量码 REST/WS 语义统一（2 一边 GOOD 一边 UNCERTAIN）
- 文档（D1-D6 全部落地）：PRD v6.0→v6.1 增补——删 AAS 同步 UI 条款（D1）、测点可编辑/导入（D2）、数据管理权限按现状标注（D3）、断点续传机制章节（D4）、卡片视图与监控导出标注未实现（D6）；implementation-contract 同步；全库版本口径统一（D5，PRD 引"契约 v2.1" vs 基线自称 v2.0）；AGENTS.md 更新

---

## 执行进度与交接状态（2026-07-21 暂停时快照）

**工作区未提交改动（自洽，type-check/测试不会因半成品失败）：**

| 文件 | 状态 |
|---|---|
| `backend/app/services/loop_batch.py` | 阶段 1 完成 |
| `backend/app/services/loop.py` | 阶段 1 完成（缺省值兜底） |
| `backend/tests/test_loop_batch.py`、`tests/test_loop.py` | 阶段 1 完成（适配+新用例） |
| `frontend/apps/web-antd/src/components/loop/quality-tag.vue` | 阶段 2 半成品（Quality 类型已内联） |
| 待删：`frontend/apps/web-antd/src/views/loop/tag-mapping.vue`、`frontend/apps/web-antd/src/api/aas.ts` | 阶段 2 未做 |
| 待改：`frontend/apps/web-antd/src/views/loop/monitor.vue:869`（死链跳转） | 阶段 2 未做 |

**与性能评估整改（`metric-module-health-check-plan-2026-07-21.md`）的交叠点：**

1. `monitor.vue`：我方阶段 5（契约对齐）vs 对方 #7（SPONSOR 门控诊断 Modal）——同一文件，需错开或合并实施
2. `PRD.md`/实现契约/FDS：我方阶段 10（D1-D6）vs 对方 #2（权重口径裁决改 FDS/算法说明）——文档协调
3. 权限口径：对方 #7 收紧 SPONSOR vs 我方 D3 维持现状 + 阶段 6 放开 PE_ENGINEER 入口——需统一权限矩阵口径
4. `kpi_calc.py` 对方大改，我方不碰（阶段 4 仅作为 `backfill_kpi_range` 调用方）

**重启时注意：**

- 用户意向：另起对话将回路管理 + 性能评估整改通盘规划（可能重排阶段/合并交叠项），本文档阶段划分届时以新通盘计划为准
- 工作流：双机协作规范已取消（commit 4fb2f680），改为"对话中显式提出 PR 要求时由 agent 直接经 gitea API 执行"；当前改动是否先本地 commit 由用户定
- 后端服务（7101）/前端（5666）/worker 运行中；阶段 1 改动已被 uvicorn --reload 热加载
