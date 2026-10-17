# Slice E1 任务书：P6-05 后续·核查最小实体（O05-BE 后续分片一）

Owner：子代理E1 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `B/`=`backend/app/`

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§2.1（我的工作与核查）+ §3 末尾 05 后续段 + §6 O05-BE 后续行**
2. Slice A 已交付：`B/models/personal_workspace.py`、`B/services/personal_workspace.py`、`B/api/v1/endpoints/personal_workspace.py`（范式复用，勿改）
3. 既有：`B/models/loop_action_item.py`、`B/models/handling_order.py`（handler_id）、`B/models/sys_user.py`、`B/models/loop_ledger.py`

## 范围（独占）
- 拟建：`B/models/loop_investigation.py`、`B/services/loop_investigation.py`、`B/schemas/loop_investigation.py`、`B/api/v1/endpoints/loop_investigation.py`、`backend/tests/test_p6_05_investigation.py`、`backend/alembic/versions/` 单个新 revision（down_revision=`p605person01`）
- 整合点（报告单列）：main.py 注册、models/__init__ 导出、01_schema.sql DDL 追加（链式注释惯例）、表计数守卫 docstring+断言 81→按实际新表数更新（`backend/tests/test_process_model_version.py`，Slice A 曾漏此项被门禁抓到，务必同步）
- **禁止**：改 personal_workspace 三文件、handling 状态机、deps/scope；`/me/work` 统一查询与稳定审核/验证责任属 E2 不做；跑迁移到共享库；commit/push

## 契约要点（01a §2.1 为准）
- 核查最小阶段 `TODO/IN_PROGRESS/WAITING/CLOSED`；结束结果 `NO_FURTHER_ACTION/ANSWERED/HANDED_OFF/CANCELLED`；WAITING 必有 wait_reason+复查条件+责任；取消≠回路正常
- 一名负责人（稳定 `sys_user.id`）；按来源身份+问题身份去重（不仅 loopId）；并发认领服务端版本裁决（revision CAS，409 携当前版本）；移交仅在目标记录+接续责任真实建立后成立；结束保存目标回链，不复制工单执行/验证状态
- 旧姓名只作显示，不作稳定分派身份；同名/改名记录标"待确认责任"不猜测关联
- API（/api/v1 下，登录主体 get_current_user）：建议路径 `POST /loops/{loopId}/investigations`（创建+来源身份/问题身份去重）、`GET /loops/{loopId}/investigations`、`POST /investigations/{id}/claim`（expectedRevision）、`POST /investigations/{id}/handoff`、`POST /investigations/{id}/close`（结果+目标回链）、`GET /investigations/{id}`；权限/scope 校验复用 ScopeContext/ensure_loop_in_scope
- 迁移与 ORM 同批；外键 UUID 对齐

## 验证
隔离库 clpm_test_p6（POSTGRES_DB 或 TEST_DATABASE_URL，localhost:17102）；单测+真 PG 集成：阶段机非法迁移 422、去重、并发认领 409、移交双记录原子、取消不标正常、撤权不泄露；`alembic upgrade head`+`alembic check`（clpm_test_p6）0；ruff 双 0；全量单测回归 exit 0

## 完成判据
上全绿+作者日志；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。

## 移交记录

**状态：待验收** ｜ 实施者：子代理E1（kcode）｜ 日期：2026-10-14 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（未 commit/push）

### 完成项清单

- [x] `backend/app/models/loop_investigation.py`（新建）：LoopInvestigation 单表 ORM。阶段机 4 态 `TODO/IN_PROGRESS/WAITING/CLOSED`（CK 枚举）；结束结果 4 类 `NO_FURTHER_ACTION/ANSWERED/HANDED_OFF/CANCELLED` 仅 CLOSED 持有且 CLOSED 必有结果+结束时刻（CK，**显式 `outcome IS NOT NULL` 堵 PG CHECK NULL 三值逻辑漏洞**）；WAITING 三要素（wait_reason/wait_review_condition/wait_responsible）进入必填、离开必空（CK）；HANDED_OFF 必有 `close_target_kind='loop_investigation'`+目标回链（CK）；`owner_id` FK sys_user.id SET NULL（唯一稳定分派身份）+ `owner_name` 分派时刻快照（只作显示）；`handoff_source_id` 自引用 SET NULL；`revision>=1` CK + `last_request_id`；created_by/closed_by FK SET NULL；**去重硬保证=部分唯一索引 `uk_loop_investigation_active_identity (loop_id, source_kind, source_id, problem_key) WHERE stage<>'CLOSED'`**（已结束含 CANCELLED 不阻塞同问题再现）。
- [x] `backend/app/schemas/loop_investigation.py`（新建）：CamelModel 请求/响应；全部写请求显式 `expectedRevision`+`requestId`；**请求体无"按姓名分派"通道**（分派只收稳定 sys_user.id：newOwnerId UUID 校验）；outcome 列/字段宽 24（`NO_FURTHER_ACTION`=17 字符，初版 VARCHAR(16) 被集成测试当场抓获后修正）。
- [x] `backend/app/services/loop_investigation.py`（新建）：创建按来源身份+问题身份去重（命中未结束→deduped=true 返回既有；幂等按 last_request_id 精确重放；并发首建部分唯一索引裁决败方重读去重）；claim 行锁 FOR UPDATE + revision CAS（409 携 currentRevision/stage/ownerId），TODO 未认领任意范围内用户可认、移交预留仅接手人本人（或 ADMIN）可认；wait/resume IN_PROGRESS⇄WAITING 三要素写入/清空，负责人或 ADMIN 可操作；close 三结果（HANDED_OFF 直传 422 须走 /handoff；CANCELLED 必填理由；目标回链白名单 handling_order/diagnosis_run/loop_action_item+UUID 形态，只存引用不复制状态）；**handoff 双记录原子**（单事务：源 CLOSED/HANDED_OFF+目标回链，目标 TODO 复制来源/问题身份、owner=接手人落位、handoff_source_id 回指；接手人不存在/停用/为自己→400 整体回滚无半成品）；非法阶段迁移一律 422（ERR_INVESTIGATION_STAGE_INVALID）；每次读/写 ensure_loop_in_scope 重新校验（撤权 403 通用拒绝不回显）；读方姓名以 sys_user 当前显示名为准、用户删除后回退快照并派生 `responsibilityPending=true`（不猜测同名关联）。
- [x] `backend/app/api/v1/endpoints/loop_investigation.py`（新建）：7 路由——`POST/GET /loops/{loopId}/investigations`、`GET /investigations/{id}`、`POST /investigations/{id}/claim|wait|resume|close|handoff`；登录主体只取 get_current_user，无自由 userId；scope 经 get_scope_ctx 每请求校验。
- [x] `backend/alembic/versions/p605invest01_loop_investigation.py`（新建）：单 revision，`down_revision='p605person01'`，单头保持（`alembic heads` 仅 p605invest01）；与 ORM/01_schema.sql 三方一致（双库 alembic check 0 实证）。
- [x] `backend/tests/test_p6_05_investigation.py`（新建）：20 纯单测 + 16 真 PG 集成（含 HTTP 端到端 7 路由全走查）。
- [x] `backend/tests/test_process_model_version.py`（守卫同步，Slice A 漏项教训）：docstring 链式追加"个人关系三表…=81 + 回路核查最小实体（p605invest01，2026-10-14）= 82"，断言 81→82。

### 整合点（任务书要求单列；diff 已核只含本分片追加，未动既有行）

1. `backend/app/main.py`：`from app.api.v1.endpoints import` 块加 `loop_investigation`；注册区 personal_workspace 之后 `include_router(loop_investigation.loops_router)` + `include_router(loop_investigation.investigations_router)`（注释式追加共 8 行）。
2. `backend/app/models/__init__.py`：`from app.models.loop_investigation import LoopInvestigation` + `__all__` 追加（loop_health_flag 之后）。
3. `db/postgresql/01_schema.sql`：脚本结束标记前链式注释段追加 loop_investigation DDL（5 CHECK + 部分唯一索引 + 2 普通索引 + COMMENT，与迁移同批同内容）。

### 验证命令与真实退出码（全部在隔离库，工作树 backend/ 下）

| 验证 | 命令 | 退出码/结果 |
|---|---|---|
| 单头 | `uv run alembic heads` | 0；仅 `p605invest01 (head)` |
| 升级路径 | `POSTGRES_DB=clpm_test_p6 uv run alembic upgrade head` | 0；仅执行 `p605person01 -> p605invest01` |
| 迁移往返 | `POSTGRES_DB=clpm_test_p6 uv run alembic downgrade p605person01` → `uv run alembic upgrade head` | 0 / 0 |
| 漂移检查（升级路径） | `POSTGRES_DB=clpm_test_p6 uv run alembic check` | **0**（No new upgrade operations detected） |
| 新装路径 | DROP/CREATE `clpm_test_p6_e1fresh` → `psql -v ON_ERROR_STOP=1 -f db/postgresql/01_schema.sql` → `POSTGRES_DB=clpm_test_p6_e1fresh uv run alembic stamp head` → `alembic check` | 0 / stamp `p605invest01` / **0** |
| 结构一致性 | pg_indexes/pg_constraint 双库比对 | 两库一致：5 CHECK + uk 部分唯一索引（WHERE 子句在库）+ 2 普通索引 + pkey |
| 纯单测 | `uv run pytest tests/test_p6_05_investigation.py -q` | **0；20 passed, 16 deselected** |
| 真 PG 集成 | `TEST_DATABASE_URL=postgresql+asyncpg://clpm:<pwd>@localhost:17102/clpm_test_p6 uv run pytest tests/test_p6_05_investigation.py -q -m integration` | **0；16 passed** |
| 表计数守卫 | `uv run pytest tests/test_process_model_version.py -q` | 0；8 passed（含 82 表断言） |
| 全量单测回归 | `uv run pytest -q --timeout=300` | **0；6750 passed, 335 skipped, 156 deselected, 12 xfailed, 4 xpassed**（基线 6730 + 本文件 20；DLASCL 警告为数值库既有噪音） |
| ruff | `uv run ruff check .` / `uv run ruff format --check .` | 0 / 0（仓库级 902 文件全过） |
| OpenAPI 接线 | `app.openapi()` 路径枚举 | 7 条 investigation 路由全部在列；camelCase 契约核验（problemKey/requestId/newOwnerId/ownerDisplayName/responsibilityPending 等 25 字段） |
| 共享库红线 | 全程仅 `clpm_test_p6`/`clpm_test_p6_e1fresh`（localhost:17102）；共享 dev `clpm` 库核后 `alembic_version='p504srvflt01'` 未动、`loop_investigation` 表 0 张；未写共享 Redis/TDengine（HTTP 层全 patch FakeRedis）；未启停 celery；未 commit/push | — |

### 测试覆盖点 vs 任务书验证清单对照

| 验证清单 | 覆盖测试 |
|---|---|
| 阶段机非法迁移 422 | `test_stage_machine_illegal_transitions_422`（TODO→wait/resume/handoff、重复 claim、CLOSED 终态再操作）、端点级 HANDED_OFF 直传/非法 outcome/CANCELLED 无理由三个 422、e2e 第 5 步 |
| 去重（来源+问题身份，不仅 loopId） | `test_create_dedup_by_source_and_problem_identity`（同身份 deduped、同 loop 不同 problemKey 独立、同问题不同来源独立）、`test_active_identity_unique_index_db_level`、`test_concurrent_create_dedup`（并发恰一建一去重） |
| 并发认领 409 | `test_concurrent_claim_cas_409`（asyncio.gather 双认领：一胜 rev=2、一 409 携 currentRevision/stage/ownerId）、`test_claim_stale_revision_409_and_replay` |
| 移交双记录原子 | `test_handoff_dual_record_atomic`（源 CLOSED/HANDED_OFF+回链、目标 TODO 责任落位+回指+身份延续、预留防截胡、接手人认领）、`test_handoff_replay_returns_same_target`、`test_handoff_invalid_target_rolls_back_atomically`（不存在/停用/为自己→400 且源零改动） |
| 取消不标正常 | `test_cancel_not_mark_loop_normal`（CANCELLED+理由留痕、loop_ledger 行零改动、取消不阻塞同问题再现——取消≠解决）、端点级 CANCELLED 无理由 422、e2e 第 8 步 |
| 撤权不泄露 | `test_revocation_no_leak`（详情/列表/认领均 403 通用拒绝、报文无标题泄露、恢复 scope 数据仍在） |
| 旧姓名只显示不作分派身份 | `test_handoff_request_has_no_name_assignment_channel`（schema 结构红线）、`test_owner_name_display_only_and_responsibility_pending`（改名即刷新、删除后快照回退+responsibilityPending=true） |
| WAITING 三要素 | schema 必填 422 + `test_wait_resume_cycle_and_owner_enforcement` + `test_waiting_three_elements_db_check`（DB CHECK 兜底三反例：WAITING 缺要素/非 WAITING 残留/CLOSED 无结果均 IntegrityError） |

### 裁决与边界说明（交主协调/验收裁决）

1. **变更记录（事件流水表）未建**：任务书契约要点未列入，E1 按"核查最小实体"单表交付；最小审计链由 created_by/closed_by/closed_at/handoff_source_id/close_target 覆盖。若后续需要阶段变迁流水，建议另立分片（表计数守卫与迁移链式追加口径已跑通）。
2. **接手人 scope 未校验**：handoff 校验接手人存在+启用+非本人；无法以接手人身份解析其数据范围（其 scope 在其本人首次操作时照常强制）——如需"不可移交给范围外人员"约束，建议交 O-INTEGRATE 裁决。
3. **MANUAL 来源 source_id 缺省** = `manual:{user_id}`（同人同问题手工登记去重）；HANDOFF 不对客户端开放为 source_kind（移交目标复制原始来源身份）。
4. **plannedAt/dueAt 未建模**：系 E2 统一查询候选字段，E1 最小实体不含；E2 如需期限分区可另起迁移补列。
5. **修复的两个实现期缺陷**（均被本文件测试当场抓获后修正，非遗留）：outcome 列 VARCHAR(16)→24（`NO_FURTHER_ACTION` 17 字符截断）；outcome CHECK 补显式 `IS NOT NULL`（PG CHECK 对 NULL 求值放行的三值逻辑漏洞）。
6. **E2 范围未做**（红线遵守）：/me/work 统一查询、稳定审核/验证责任（handling 侧）均未触碰；personal_workspace 三文件、handling 状态机、deps/scope 零改动（git diff 可核）。

### 未完成/风险

- 无未完成任务项。残留风险：e2e 中 requestId 跨操作复用属客户端误用场景，服务按 Slice A 同口径幂等处理（首查 last_request_id），不额外防御；`test_http_end_to_end` 第 8 步依赖"负责人语义"（切换登录主体为接手人），与生产口径一致。

### G-N2 后端守卫段

**状态：待复验** ｜ 实施者：子代理E1（后端 Owner）｜ 日期：2026-10-14 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（未 commit/push）

**缺口**（SliceN 报告 G-N2）：`GET /performance/ranking?plantNodeId=1279311030`（int source_node_id）→ asyncpg invalid UUID **500**；应为 400 参数拒绝。

**改动**（仅 2 文件，零业务逻辑变化）：

- `backend/app/api/v1/endpoints/performance.py`
  - 新增三助手（:79-110，与 `parse_iso_datetime` 同口径 `BizError(ERR_PARAM, 400)`）：`_require_uuid`（非空校验+归一）、`_parse_uuid_arg`（可选单值：None/空串→None=未指定，空值语义同 `_parse_dt` 不变）、`_parse_uuid_list`（None/空列表原样透传，元素逐一校验）
  - G-N2 原发点：ranking `plantNodeId` 解析 :261
  - 同类隐患同口径补齐 13 处：PUT metrics/{metricId} :163、PUT rules/{ruleId} :204、analytics :301、analytics/export :326、realtime-auto-rate :370、grade-distribution loopId+plantNodeId 逗号列 :498-499、valve-alerts :542、loops/snapshots 逗号列 :642-643、loops/metric-series loopIds :858、window-agg loopId+plantNodeId :905-906、batch-delete body.loopIds+body.plantNodeId :950-951、result-records/{recordId} :1002（旧投影三表主键已核均 UUID，兼容路径不受影响）、DELETE snapshots/{snapshotId} :1043
- `backend/tests/test_performance.py`：新增 `TestUuidParamGuard` 16 例（非 UUID→400、int→400、合法 UUID→200 且服务层收到归一值、空值→None 透传、13 处隐患点逐点 400）；修正 2 个编码旧行为的用例（`test_update_metric_not_found`/`test_update_rule_not_found` 改用合法未登记 UUID 续测 404 语义，docstring 注明）

**同类隐患清单**（排查结论：本文件 14 处 UUID 输入全部曾可 500，已全部补齐；合法 UUID 路径零行为变化——归一仅小写连字符，PG UUID 按值比较等价）：

| 类别 | 端点 | 参数 |
|---|---|---|
| 单值查询 | ranking / analytics / realtime-auto-rate / valve-alerts / window-agg | plantNodeId |
| 请求体单值 | analytics/export / batch-delete | plantNodeId |
| 逗号列 | grade-distribution / loops/snapshots / window-agg / metric-series | loopId(s) / plantNodeId |
| 请求体列表 | batch-delete | loopIds |
| 路径参数 | metrics PUT / rules PUT / result-records GET / snapshots DELETE | metricId / ruleId / recordId / snapshotId |

**验证命令与真实退出码**（工作树 backend/ 下）：

| 验证 | 命令 | 退出码/结果 |
|---|---|---|
| 目标文件 | `uv run pytest tests/test_performance.py -q` | **0；58 passed**（42 原有 + 16 新增） |
| scoped 回归 | `uv run pytest tests/test_performance.py tests/test_g3_r01_r02.py tests/test_loop_snapshots.py tests/test_s8_supplement.py tests/test_result_ledger.py tests/test_node_performance.py tests/test_p5_03_cache_freshness.py -q` | **0；217 passed, 25 skipped** |
| 周边套件 | `uv run pytest tests/test_assess_overview_revamp.py tests/test_eval_participation_scope.py tests/test_dashboard_governance.py tests/test_cockpit.py tests/test_ai_insight.py tests/test_api_tasks.py -q` | **0；132 passed, 9 skipped** |
| ruff | `uv run ruff check .` / `uv run ruff format --check .` | 0 / 0（909 文件全过） |

**边界说明**：空串 `plantNodeId=` 按文件既有 `_parse_dt` 空值口径归为"未指定"（None 透传）——该形态此前在 ALL scope 下同样 500，无可依赖工作流；未动前端（02b int→UUID 解析修复属前端 Owner，与本守卫互为补充）；其他端点文件未排查（本任务限定 performance.py 同文件）。
