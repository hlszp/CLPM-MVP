# Slice E2 任务书：P6-05 后续·/me/work 统一工作查询（O05-BE 后续分片二）

Owner：子代理E2 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `B/`=`backend/app/`

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§2.1 末段（统一查询规则）+ §3 末段（统一工作输出字段）+ §6 O06-NAV 行（消费方预期）**
2. 已交付（范式，勿改）：`B/services/personal_workspace.py`、`B/services/loop_investigation.py`、`B/models/loop_investigation.py`、`B/models/personal_workspace.py`
3. 既有来源：`B/models/loop_action_item.py`、`B/models/handling_order.py`（handler_id）、`B/services/diagnosis_system_actions.py`（系统建议）

## 范围（独占）
- 拟建：`B/services/personal_work.py`、`B/schemas/personal_work.py`、`B/api/v1/endpoints/personal_work.py`、`backend/tests/test_p6_05_personal_work.py`
- 整合点（报告单列）：main.py 注册 router（models 无新增则 __init__/守卫/DDL 不动）
- **禁止**：新增/修改表（本分片纯查询，稳定审核/验证责任沿用 handler_id/owner_id，旧姓名只显示标"待确认"不猜测）；改 investigation/personal_workspace/handling 文件；跑迁移到共享库；commit/push

## 契约要点（01a §2.1/§3 为准）
- `GET /api/v1/me/work`：跨来源统一查询——分配给我（investigation owner_id + handling_order handler_id + action_item 责任）/系统建议/我的关注/最近继续四 workKind
- 输出字段 `workKind/workId/loopId/ownerId/stage/partition/plannedAt/dueAt/waitReason/evidenceRefs/nextAction/revision`；集合附 `items/total/nextCursor/readAt/readStatus/sourceStatuses`，readStatus=COMPLETE/PARTIAL/ERROR
- **分区互斥**：先判等待条件→再判今日计划→其余当前可行动进"现在可行动"；未来计划/已结束走筛选；工作日期只用于计划/期限，未完工作不按"今天有更新"筛掉
- 排序=有效优先级+期限+稳定 ID；计数与分页同规则；**来源失败或计数未知不得填 0**（total 可空、sourceStatuses 如实 ERROR）；部分数据必须可见；权限每次查询重新校验（ensure_loop_in_scope 逐条/批校验）
- 撤权对象保留但不回显名称/状态/证据（同 personal_workspace 惯例）；空待办≠健康（响应不输出健康断言）
- 无自由 userId；登录主体唯一；分页 cursor 稳定

## 验证
- 单测+真 PG 集成（clpm_test_p6）：四 workKind 聚合、分区互斥优先级、单来源失败 readStatus=PARTIAL 且 total 语义正确不填 0、撤权不泄露、cursor 稳定分页、同名旧姓名标待确认
- ruff 双 0；全量单测回归 exit 0

## 完成判据
上全绿+作者日志；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。

## 移交记录

状态：**待验收**（作者自验全绿；非作者验收由主协调安排）｜实施：子代理E2（agent-6）｜日期：2026-10-14

### 完成项清单

- [x] `backend/app/schemas/personal_work.py`（新建）：CamelModel 响应契约。四 workKind（`ASSIGNED/SYSTEM_SUGGESTION/FOLLOW/RESUME`）、五分区（`ACTIONABLE/TODAY/WAITING` 默认视图 + `FUTURE/CLOSED` 仅筛选）、readStatus 三态（COMPLETE/PARTIAL/ERROR）；item 契约字段 `workKind/workId/loopId/ownerId/stage/partition/plannedAt/dueAt/waitReason/evidenceRefs/nextAction/revision` 全落地，另补呈现必需同源字段（`title/reason/sourceKind/ownerDisplayName/responsibilityPending/waitReviewCondition/priorityReasons/loopTagName/unitId/unitName/context/updatedAt`，09§4.3 工作目标/来源/进入清单理由/负责人/证据时间）；集合 `items/total/nextCursor/readAt/readStatus/sourceStatuses`，`total: int|None`（未知不填 0）；**无任何健康断言字段**（结构性测试守护）。
- [x] `backend/app/services/personal_work.py`（新建）：五来源聚合（investigation/handling_order/action_item/follow/resume 对 `_WORK_KIND_SOURCES` 映射）。**纯查询**：零新表零改表零写库。分区互斥：先判等待（核查 WAITING）→再判今日计划（planned 日期=服务端 UTC 工作日期）→其余现在可行动（VERIFYING 是可行动非等待——验证是人工动作）；未来/已结束仅 `partition=` 筛选可见；工作日期只用于计划/期限，未完工作不按"今天有更新"筛掉。排序=有效优先级（rank：逾期/SLA breach > SLA 预警/重开/高优先级建议 > 常规 > 关注 > 最近继续）+期限升序 NULL 最后+workKind+稳定 ID；keyset cursor（base64url JSON 含全排序键，非法一律 400 ERR_WORK_CURSOR_INVALID 不静默回首页）；计数与分页同规则。**来源失败隔离**：单来源异常 → `sourceStatuses[src]=ERROR + total=None + readStatus=PARTIAL`，健康来源数据仍可见；全部失败 → ERROR。**撤权不泄露**：SQL 级 scope 过滤 + `loops_in_scope` 批校验（每次查询重新校验），撤权对象仅计 `inaccessibleCount`（readStatus=PARTIAL，同 personal_workspace 惯例），不回显名称/状态/证据；无自由 userId，登录主体唯一。
- [x] `backend/app/api/v1/endpoints/personal_work.py`（新建）：`GET /api/v1/me/work`，参数 `workKind/partition/cursor/pageSize(1..50)`；筛选白名单 400（ERR_WORK_FILTER_INVALID）先于 DB 访问。
- [x] `backend/tests/test_p6_05_personal_work.py`（新建）：16 纯单测 + 10 真 PG 集成（含 HTTP 端到端走查）。

### 整合点（任务书要求单列；diff 已核只含本分片追加，未动既有行）

1. `backend/app/main.py`：`from app.api.v1.endpoints import` 块加 `personal_work`（字母序 personal_workspace 前）；注册区 loop_investigation 之后 `include_router(personal_work.router)`（注释式追加共 4 行；E1 已加的 8 行未动）。
2. **`models/__init__.py`/守卫/`db/postgresql/01_schema.sql`/alembic 均未动**——本分片纯查询无模型新增（任务书"models 无新增则 __init__/守卫/DDL 不动"）；表计数守卫 82 不变（`test_process_model_version.py` 8 passed 实证）。

### 验证命令与真实退出码（工作树 backend/ 下；写库仅隔离库 localhost:17102）

| 验证 | 命令 | 退出码/结果 |
|---|---|---|
| 纯单测 | `uv run pytest tests/test_p6_05_personal_work.py -q` | **0；16 passed, 10 deselected** |
| 真 PG 集成 | `TEST_DATABASE_URL=postgresql+asyncpg://clpm:<pwd>@localhost:17102/clpm_test_p6 uv run pytest tests/test_p6_05_personal_work.py -q -m integration` | **0；10 passed** |
| 相邻分片回归（单测） | `uv run pytest tests/test_p6_05_personal_work.py tests/test_p6_05_investigation.py tests/test_p6_05_personal_workspace.py -q` | 0；60 passed |
| 相邻分片回归（集成） | 同上 TEST_DATABASE_URL `uv run pytest tests/test_p6_05_investigation.py tests/test_p6_05_personal_workspace.py -q -m integration` | 0；27 passed（E1 16 + A 11 全过，本分片零破坏） |
| ruff | `uv run ruff check .` / `uv run ruff format --check .` | **0 / 0**（仓库级 906 文件全过） |
| 表计数守卫 | `uv run pytest tests/test_process_model_version.py -q` | 0；8 passed（82 表不变） |
| 迁移单头/漂移 | `POSTGRES_DB=clpm_test_p6 uv run alembic heads` / `alembic check` | 仅 `p605invest01 (head)` 未变 / **0**（No new upgrade operations，本分片无迁移） |
| 全量单测回归 | `uv run pytest -q --timeout=300` | **0；6766 passed, 335 skipped, 166 deselected, 12 xfailed, 4 xpassed**（基线 6750 + 本文件 16；DLASCL 警告为数值库既有噪音） |
| OpenAPI 接线 | `app.openapi()` 路径枚举 | `/api/v1/me/work` GET 在列；参数 workKind/partition/cursor/pageSize camelCase；`PersonalWorkData`/`WorkSourceStatus` 契约字段核验 |
| 共享库红线 | 全程仅 `clpm_test_p6`（localhost:17102）；共享 dev `clpm` 库核后 `alembic_version='p504srvflt01'` 未动、P6 四表 0 张；未写共享 Redis/TDengine（HTTP 层全 patch FakeRedis）；未启停 celery（lifespan 测试模式守卫实证）；未 commit/push | — |

### 测试覆盖点 vs 任务书验证清单对照

| 验证清单 | 覆盖测试 |
|---|---|
| 四 workKind 聚合 | `test_four_work_kinds_aggregate`（四视图+ASSIGNED 双来源+total/readStatus/sourceStatuses 全绿）+ e2e 聚合段 |
| 分区互斥优先级 | `test_order_partition_mutual_exclusive`（纯函数）+ `test_partition_mutual_exclusive_and_filters`（等待>今日>可行动；逾期排最前；今日计划仍带 nextAction；未来/已结束默认排除仅筛选可见且计数同规则；每 item 恰一分区） |
| 单来源失败 readStatus=PARTIAL 且 total 不填 0 | `test_source_failure_partial_total_not_faked_zero`（total=None、ERROR 如实、健康来源可见）+ `test_all_sources_failure_read_status_error` |
| 撤权不泄露 | `test_revocation_no_leak`（标题/位号/ID 全文不出现在响应、inaccessibleCount=1、PARTIAL、恢复 scope 数据仍在） |
| cursor 稳定分页 | `test_cursor_stable_pagination`（7 条 3/3/1 无重复无遗漏、total 恒 7）+ `test_cursor_across_rank_boundary` + 单测非法游标 5 例 400 + e2e 翻页段 |
| 同名旧姓名标待确认 | `test_same_name_legacy_not_assigned`：handler 字符串=我的 username 但 handler_id NULL 的旧工单**不归入**（不猜测）；owner_id NULL 同名快照核查不归入；我的核查改名后 ownerDisplayName 以 sys_user 当前值刷新；建议池 owner=None 不标待确认 |

### 裁决与边界说明（交主协调/验收裁决）

1. **"action_item 责任"落法（重要裁决）**：`分配给我 = loop_investigation.owner_id + handling_order.handler_id`（稳定 sys_user.id 列）。loop_action_item 当前**无稳定责任列**——`reviewed_by/verified_by/suggested_by` 均为旧姓名字段，契约 01a §2.1 明示"审核/验证等当前姓名字段不能冒充稳定分派身份"、09§4.2"不猜测归给某个同名用户"，故本分片**不按姓名匹配归属任何建议**；已转工单的建议经 `handling_order.suggestion_ids` 稳定链并入我的工单工作项 evidenceRefs（同一事项不重复累加，09§4.2"统计不把同一事项重复累加"）。独立的"建议审核分派给我"需后续"稳定审核/验证责任"分片落稳定列后接入（01a §3 05 后续分片清单在列）。
2. **核查无计划/期限列**：E1 裁决 #4 已登记 plannedAt/dueAt 未建模；本分片红线纯查询不补列，故核查项 plannedAt/dueAt 恒 None，仅参与 WAITING/ACTIONABLE/CLOSED 分区（不能进 TODAY/FUTURE）。如需核查计划分区，须另起迁移补列后接入。
3. **系统建议视图**：`source=SYSTEM + status=PENDING` 待审核池（责任范围内未认领）；"别人已认领事项显示负责人和关联工作"（09§4.2 后半句）未实现——核查来源身份白名单尚无 loop_action_item 类型，认领联动待后续扩展。
4. **工作日期=服务端 UTC 日期**（计划/期限判定口径；页面时区呈现归 06 前端）。
5. **RESUME/FOLLOW 不参与工作分区**（partition=None，关注不产生责任、最近不自动变待办）；`partition=ACTIONABLE` 筛选仅含分配工作与系统建议。RESUME 排序同样遵循统一规则（不按最近时刻优先），`updatedAt` 透出供呈现；若产品要求最近优先需主协调裁决调整排序契约。
6. **inaccessibleCount 口径**：与 personal_workspace 先例一致——按来源候选池（含 stage/status 条件、**不含** partition 视图筛选）计撤权保留对象；total 始终与分页同规则只计可返回条目。
7. **建议 reason 取 basis 原文**（诊断依据=进入清单理由，09§4.3）；无 basis 时回退静态说明，不编造。

### 未完成/风险

- 无未完成任务项。残留风险：排序/分页为内存归并（个人工作量天然有界：我的进行中核查/工单/关注/最近≤20+建议池范围过滤后）；若建议池在超大工厂下增长，需主协调裁决是否加 SQL 级 keyset（契约不变）。
