# Slice A 任务书：P6-05 个人关系分片（O05-BE）

Owner：子代理A ｜ 基线 6c92cf69 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（**只在此树工作**）

## 必读（按节，禁全量通读）
1. `docs/设计文档/系统改造优化-2026-10-10/执行状态/P6-01a-契约与文件Owner-20261014.md` **§3**（数据/API 契约，唯一事实源）
2. `docs/设计文档/工程师任务导向IA改造-2026-10-11/03-个人收藏与工作接续数据设计.md`（设计背景）
3. 复用点源码（工作树内）：`backend/app/api/deps.py`（get_current_user）、`backend/app/services/authz_scope.py`（ScopeContext/ensure_loop_in_scope）、`backend/app/models/` 现有模型范式、`backend/app/api/v1/endpoints/` 现有端点范式（CamelModel/ApiResponse/分页）、`backend/alembic/versions/` 最新 revision 范式（当前单头 `p504srvflt01`）
4. 根 AGENTS.md 行为红线

## 范围（独占文件，越界即停）
- 拟建：`backend/app/models/personal_workspace.py`、`backend/app/schemas/personal_workspace.py`、`backend/app/services/personal_workspace.py`、`backend/app/api/v1/endpoints/personal_workspace.py`、`backend/tests/test_p6_05_personal_workspace.py`、`backend/alembic/versions/` 单个新 revision（down_revision=`p504srvflt01`）
- 整合点编辑（做完在报告中单列，交 O-INTEGRATE 复核）：`backend/app/main.py` 注册 router、`backend/app/models/__init__.py` 导出模型
- **禁止**：跑迁移到共享 dev `clpm` 库；连共享 Redis/TDengine 写；commit/push；改共享 deps/scope；实现 loop_investigation 或 `/me/work`（后续分片）；repo 全局 format

## 契约要点（01a §3 全文为准）
- 三表：`user_loop_follow`(user_id,loop_id 唯一; is_followed, revision≥1, last_request_id, 时间戳)、`user_workspace_preference`(user_id,preference_key 唯一; payload JSONB, schema_version, revision)、`user_work_resume`(user_id,resource_kind,resource_id 唯一; context JSONB, revision, generation, last_visited_at, deleted_at)
- API（/api/v1 下）：`GET/PUT /me/loop-follows[/loopId]`、`GET/PUT /me/workspace-preferences[/key]`、`GET/PUT/DELETE /me/work-resume[/kind/id]`、`DELETE /me/work-resume`
- 红线：登录主体只取 get_current_user（无自由 userId）；设定非 toggle；首次创建 expectedRevision=0；过期版本 409 携当前版本；同 requestId 重试幂等返回已有结果；取消关注留版本墓碑不物理删；取消后迟到旧关注不得复活；偏好按 key 独立版本、白名单校验、resume_generation 不对客户端开放写；最近继续清空=先原子增本人 generation 再清旧代，旧代迟到请求拒绝；写入前服务端校验对象存在/scope/权限；撤权保留关系但不得返回名称/状态/证据（只可提示存在不可访问对象）；账户停用拒绝访问
- 迁移与 ORM 同批；外键对齐既有 UUID 类型

## 验证（隔离资源，先认证再跑）
- 隔离库 `clpm_test_p6`（localhost:7102，同 clpm-mvp-postgres 实例，已建空库）：用 `POSTGRES_DB=clpm_test_p6 .venv/bin/alembic upgrade head` 验证新装路径；`alembic check`（同 env）无漂移；单测 `CLPM_TEST_MODE=1 .venv/bin/pytest tests/test_p6_05_personal_workspace.py -q`；集成式写库测试用 `TEST_DATABASE_URL=postgresql+asyncpg://clpm:<pwd>@localhost:7102/clpm_test_p6`（pwd 从 .env 读，不要打印）
- 测试必须覆盖：新装/升级、双用户隔离、CAS 409、同 requestId 幂等、取消墓碑+迟到旧关注拒绝、清空 generation 栅栏、撤权不泄露、未登录/停用 401/403、非法 key/kind 400
- `uv run ruff check .` + `uv run ruff format --check .` 通过（format 如需写只许 scoped 到自己文件）

## 完成判据
上述测试全绿+lint 干净+作者日志；移交状态写回本文件末尾「## 移交记录」（完成项/证据命令与退出码/未完成/风险），标"待验收"。**上下文水位>50% 或额度告警即 WIP 停手并写明断点。**

## 移交记录

**状态：待验收** ｜ 实施者：子代理A（kcode）｜ 日期：2026-10-14 ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（分支 codex/p6-kcode-20261014，基线 6c92cf69）

### 完成项清单

- [x] `backend/app/models/personal_workspace.py`（新建）：UserLoopFollow / UserWorkspacePreference / UserWorkResume 三表 ORM；复合主键即唯一约束；revision/generation CK>=1；user/loop 外键 UUID(as_uuid=False) 对齐既有类型、ondelete CASCADE；follow 取消墓碑（is_followed 行保留）、resume 单删墓碑（deleted_at）。
- [x] `backend/app/schemas/personal_workspace.py`（新建）：CamelModel 请求/响应；写请求均显式 isFollowed/payload/context + expectedRevision + requestId（resume 另携 generation）。
- [x] `backend/app/services/personal_workspace.py`（新建）：设定语义非 toggle；行锁内 CAS，过期版本 409 data 携 currentRevision/currentGeneration；同 requestId 幂等重放 replayed=true 不推进版本；并发首建唯一约束裁决（IntegrityError→重读→409/幂等）；取消墓碑拦截 expectedRevision=0 复活尝试；偏好 key 白名单（default_home/filter_presets/sort_presets）+ 保留键 resume_generation 拒写 400；payload/context 安全校验（凭据字段/任意 URL/深度/条数/8KB 上限）；清空=代际行 FOR UPDATE 锁内原子递增再物理清旧代，写/删与清空共用该锁线性化；写入前对象存在（404)+ensure_loop_in_scope(403 通用拒绝）；撤权读路径只计 inaccessibleCount+readStatus=PARTIAL，不回显名称/描述/context；loopId/resourceId UUID 格式校验+归一化（防非法 UUID 撞 PG 500、大小写漏配）。
- [x] `backend/app/api/v1/endpoints/personal_workspace.py`（新建）：/api/v1/me 下 8 路由（6 端点组）：GET/PUT loop-follows[/loopId]、GET/PUT workspace-preferences[/key]、GET/PUT/DELETE work-resume[/kind/id]、DELETE work-resume；登录主体只取 get_current_user，无任何自由 userId 入参（HTTP 端到端验证携带 ?userId= 无效）。
- [x] `backend/alembic/versions/p605person01_personal_workspace.py`（新建）：单 revision，down_revision=`p504srvflt01`，单头保持（`alembic heads` 仅 p605person01）。
- [x] `backend/tests/test_p6_05_personal_workspace.py`（新建）：24 纯单测 + 11 真 PG 集成（含 HTTP 端到端）。
- [x] 整合点编辑（交 O-INTEGRATE 复核）：`backend/app/main.py`（import 块 + monitor 后 include_router，共 2 处 4 行注释式追加）；`backend/app/models/__init__.py`（import + __all__ 追加三模型）。两处 diff 已核只含本分片追加，未动既有行。

### 验证命令与真实退出码

| 验证 | 命令（工作树 backend/ 下） | 退出码/结果 |
|---|---|---|
| 单头 | `.venv/bin/alembic heads` | 0；仅 `p605person01 (head)` |
| 新装基线 | `psql -d clpm_test_p6 -f db/postgresql/01_schema.sql` + `02_seed_data.sql`（先 DROP/CREATE 空库） | 0 / 0 |
| 升级路径 | `POSTGRES_DB=clpm_test_p6 .venv/bin/alembic stamp p504srvflt01` → `alembic upgrade head` | 0；仅执行 `p504srvflt01 -> p605person01` |
| 漂移检查 | `POSTGRES_DB=clpm_test_p6 .venv/bin/alembic check` | **0**（No new upgrade operations detected；仅 comment 噪音，env.py 钩子按既有口径过滤） |
| 迁移往返 | `alembic downgrade p605person01-1` → `alembic upgrade head` → `alembic check` | 0 / 0 / 0 |
| 纯单测 | `CLPM_TEST_MODE=1 .venv/bin/pytest tests/test_p6_05_personal_workspace.py -q` | **0；24 passed, 11 deselected** |
| 真 PG 集成 | `TEST_DATABASE_URL=postgresql+asyncpg://clpm:<pwd>@localhost:17102/clpm_test_p6 .venv/bin/pytest tests/test_p6_05_personal_workspace.py -q -m integration` | **0；11 passed** |
| 全量单测回归 | `CLPM_TEST_MODE=1 .venv/bin/pytest tests/ -q -x --ignore=tests/integration` | **0**（全绿；DLASCL 警告为数值库既有噪音） |
| ruff | `.venv/bin/ruff check .` / `.venv/bin/ruff format --check .` | 0 / 0（仓库级 896 文件全过；新增文件 format 写入仅 scoped 自己文件） |
| OpenAPI 接线 | `app.openapi()` 路径枚举 | /api/v1/me 8 路由全部在列 |
| 共享库红线 | 全程只操作 `clpm_test_p6`（localhost:17102，clpm-mvp-postgres 实例内独立空库）；未连共享 dev `clpm`、未写共享 Redis/TDengine（测试 HTTP 层 redis_client 全部 patch FakeRedis）；未启动 celery；未 commit/push | — |

### 测试覆盖点 vs 判据对照

| 判据 | 覆盖测试 |
|---|---|
| 新装/升级 | alembic CLI 证据（上表）；`test_unique_constraint_db_level` 唯一约束 DB 级硬保证 |
| 双用户隔离 | `test_dual_user_isolation`、`test_resume_clear_isolated_per_user`（清空不越账号） |
| CAS 409 携当前版本 | `test_follow_cas_replay_tombstone`、`test_preference_independent_keys_and_cas`、HTTP 段第 3 步 |
| 同 requestId 幂等 | follow/preference/resume 三处 replay 断言（replayed=true、版本不推进） |
| 取消墓碑+迟到旧关注拒绝 | `test_follow_cas_replay_tombstone`：墓碑行在库、旧版本号 409、expectedRevision=0 重建 409、显式新版本可复活 |
| 清空 generation 栅栏 | `test_resume_generation_fence`：清空后代际 1→2、旧代写/删 409 ERR_GENERATION_STALE、单删幂等、墓碑防复活 |
| 撤权不泄露 | `test_revocation_no_leak`：列表空+inaccessibleCount=1+PARTIAL、序列化无 tagName/evidenceRefs、写入 403、恢复后关系仍在 |
| 未登录/停用 401/403 | `test_*_unauthenticated_401`（4 例）、`test_disabled_account_403`（ERR_ACCOUNT_DISABLED） |
| 非法 key/kind 400 | 保留键/未知键/坏 payload/未知 kind/非法 resourceId（单测 7 例 + 校验器 12 例） |
| 并发唯一约束裁决 | `test_concurrent_first_create_unique_constraint`（双会话并发首建恰一成一 409） |

### 未完成与风险

1. **端口偏差澄清**：任务书写 clpm_test_p6 在 localhost:7102，实际该库建在 **17102**（clpm-mvp-postgres 实例；7102 是原项目 clpm-postgres 容器，其内无 clpm_test_p6）。已按"同 clpm-mvp-postgres 实例"的真实位置（17102）验证，与既有 integration 测试 `_pg_reachable()` 口径一致。
2. **新装 stamp-head 路径缺口（交 O-INTEGRATE/Keeper 裁决）**：项目新装=01_schema.sql+stamp head（lib-migrate.sh 口径），01_schema.sql 不在我的独占范围故未改——**新装库不会自动获得三表**，需 Owner 将三表 DDL 补入 `db/postgresql/01_schema.sql`（可直接取迁移 p605person01 的 create_table 口径）。存量升级路径（upgrade head）已验证无缺口。
3. **全量 alembic 从空库重放不可行属既有事实**（772edf67d12d 基线为空迁移、a1b2c3d4e5f6 等早期 ALTER 依赖 01_schema.sql 建表），非本分片引入；验证采用项目文档化的"schema.sql+旧头 stamp+upgrade head"路径。
4. resource_kind 白名单首版仅 `loop`（loop_investigation 等属 05 后续分片）；preference 白名单三键（default_home/filter_presets/sort_presets）如需扩展由后续分片登记。
5. /me 端点未挂 require_perms（契约只要求 get_current_user 主体；个人记录为登录主体自助数据，SPONSOR 零写红线管业务数据不涉本人偏好——如需角色门禁由 O-INTEGRATE 在目录登记后追加）。
6. 前端 06 分片对接时注意：写请求必须携 expectedRevision/requestId（resume 另 generation）；409 data 携 currentRevision/currentGeneration 供刷新重试；清空后必须重新 GET 取 currentGeneration。
