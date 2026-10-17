# Slice M 任务书：P2-04 工程模板维护 UI（03/04-a）

Owner：子代理M ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ `B/`=`backend/app/`、`F/`=`frontend/apps/web-antd/src/`

## 必读（按节）
1. `03-阶段任务计划.md` §5 P2-04 段 + §9 P6-04 段（维护 UI 移交范围：模板版本/覆盖预览/真实发布/dry-run/部分失败）
2. `执行状态/P2-03-辨识证据契约冻结.md` 同目录内 P2-04 相关交接/验收文档（**先查执行状态目录的 P2-04 验收报告**，弄清"已验收什么、维护 UI 子项清单"）
3. 现行后端：`B/api/v1/endpoints/engineering_templates.py`、`B/services/engineering_template.py`（CRUD/版本/发布/回滚/回路清单已有；**dry-run/批量应用/覆盖预览需映射是否存在于别处**）
4. 前端配置页结构：`F/views/config/`、`F/router/routes/modules/config.ts`、`F/api/` 范式

## 工作方式（先映射、按设计闭缺口）
1. 映射 P2-04 已验收范围与维护 UI 缺口清单（报告逐项对照设计条目）
2. 后端缺口（如设计确实要求且现无）：dry-run（批量预检，不落库）/覆盖预览（影响回路清单+逐项差异）/批量应用（部分失败项+回退引用）——复用既有任务框架与发布栅栏，**不新建并行体系**；需迁移则同批（守卫/01_schema.sql 同步）
3. 前端：config 模块下模板维护页——模板/版本列表、版本详情与差异、覆盖预览、发布（dry-run→确认→真实发布）、批量应用结果（成功/失败项逐条）与回退入口；权限按 `config:*`/模板动作码（先核权限目录既有码，缺则报主协调整合，不自造）

## 范围（独占）
- 前端：`F/api/engineering-template.ts`（新建）、`F/views/config/engineering-templates.vue`（或同目录拆分）、路由注册 config.ts、`F/__tests__/p6-eng-templates.test.ts`
- 后端（仅当映射证实缺口）：`B/api/v1/endpoints/engineering_templates.py`、`B/services/engineering_template.py`、必要迁移、`backend/tests/test_p6_eng_template_maintenance.py`
- **禁止**：改 handling/tuning/workbench360/RT 冻结/导航其他文件；commit/push；自造权限码

## 验证
- 后端（如改动）：dry-run 不落库断言、覆盖预览计数与清单一致、批量应用部分失败逐项+回退、ruff 双 0、相关 pytest
- 前端：页面渲染/流程状态机（dry-run→确认→发布→结果）/失败项呈现/权限降级 vitest；check:type、eslint scoped exit 0

## 完成判据
P2-04 移交子项逐项（实现位置/证据/未验原因）；千回路批量路径设计内性能口径说明；移交记录写本文件末尾标"待验收"。水位>50% 即 WIP 停手写断点。

## 移交记录

**状态：待验收**（子代理M，2026-10-14；工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`；未 commit/push）

### 映射结论（先映射后闭环）

P2-04 已验收范围（据 `执行状态/P2-04-交接.md` + `P2-04-验收报告.md`（REWORK 后 PASS）逐行核读现行代码证实）：模板 CRUD/版本/发布/回拨/回路清单（`endpoints/engineering_templates.py`+`services/engineering_template.py`）、覆盖预览 dry-run（`POST /configs/publish/overrides/preview`→`preview_publish_override`，零写入/wouldConflict/mergePreview）、批量应用（`POST /loops/batch-config` dryRun+results[] 逐回路四态）、发布账本与回退（`publications`+`publications/{rev}/rollback`）**全部已存在且经非作者验收**。动作权限码 `config:view`/`config:publish`（1600）/`loop:batch-config`（1004）均在 `authz_catalog.py` 既有，未自造。

**证实缺口 1 项（后端，已修）**：`schemas/config.py::PublicationItem.operation` Literal 四值 vs 模型 CHECK（ck_config_publication_operation）五值——任一模板版本发布/回拨后 `GET /configs/publish/publications` 在 `_publication_to_item` pydantic 校验必 500（修复前实测 `ValidationError: Input should be 'PUBLISH','RESET','ROLLBACK' or 'LEGACY_SYNC'`）。维护 UI 发布账本/回退入口因此被真实阻断。修复=Literal 补 `TEMPLATE_VERSION`（1 行）+ 6 回归例。**无迁移/01_schema.sql 变更**（纯 schema Literal，ORM 零改动）。

**页面码缺口 1 项（报主协调整合，未自造）**：`page:config.eng-templates` 不在权限目录/种子；前端路由已按 cockpit.ts `page:my-work.main` 先例注册 permCode+注释，目录/种子下发与守卫计数同步归 O-INTEGRATE（未下发前页面码过滤整棵移除=降级安全）。

### 交付范围（diff 恰好命中任务书范围）

| 文件 | 改动 |
|---|---|
| `B/schemas/config.py`（+6/-1） | PublicationItem.operation 补 TEMPLATE_VERSION + docstring 登记缺口根因 |
| `B/tests/test_p6_eng_template_maintenance.py`（新建，6 例） | 缺口回归 3（Literal 五值与 CHECK 同集/schema 接受/端点序列化路径）；消费契约锚点 3（TEMPLATE 层预览零写入+affectedLoops 计数与样本清单一致；批量结果逐条 FAIL error schema 往返；四态全集可构造） |
| `F/api/engineering-template.ts`（新建） | 模板/版本/发布/回拨/回路 + 发布链 revision/overrides(preview+publish)/publications/rollback + 批量 dryRun 全类型绑定（首个前端消费方；与 loop.ts 旧批量绑定类型面不同已注释说明） |
| `F/views/config/engineering-templates/`（新建 8 文件） | `index.vue`（列表+抽屉+新建+三向导编排，权限灰而不藏）/ `template-utils.ts`（纯函数：reducePublishFlow 状态机/diffProfileEntries/chunkLoopIds≤200/summarizeBatchResults/truncationNote/parseProfileJson）/ `publish-version-modal.vue`（Flow A：编辑→差异与影响预览→确认发布（expectedRevision 乐观锁/409 显式）→结果 revisionRef）/ `batch-apply-modal.vue`（Flow B：选回路+pin 方式→dryRun 零写入→逐条四态+FAIL error→确认执行分批合并）/ `override-publish-modal.vue`（Flow C：注册表单源参数→真实 dry-run 校验（跨层颠倒进 errors）/合并视图/影响回路→确认发布，校验不过/wouldConflict 禁发布）/ `version-history-tab.vue`（版本账本+任意两版差异+回拨乐观锁）/ `publications-tab.vue`（本模板账本过滤+回退+截断提示）/ `profile-diff-view.vue`（键级差异视图，三处复用） |
| `F/router/routes/modules/config.ts`（+19） | `/config/eng-templates` 子路由（order 11；authority=ADMIN/IC/PE；permCode 待整合注释） |
| `F/__tests__/p6-eng-templates.test.ts`（新建，19 例） | 状态机全链路+非法迁移（未预览不得发布）；纯函数矩阵；页面渲染+ADMIN/IC 权限降级（灰而不藏 disabled 占位）；Flow A 预览→发布→revisionRef/409→failed；Flow B dryRun 调用契约+FAIL 逐条呈现+执行合并+edit 态无确认按钮；Flow C 校验闸门/wouldConflict/preview+publish 调用契约 |

### P2-04 移交子项逐项（完成判据对照）

| 子项 | 实现位置 | 证据 | 未验原因 |
|---|---|---|---|
| 模板/版本列表 | index.vue + version-history-tab.vue | 渲染断言（列表 2 模板/版本账本） | — |
| 版本详情与差异 | version-history-tab（详情 pre+两版 diff）/profile-diff-view | diffProfileEntries 四态键并集单测 | — |
| 覆盖预览 | override-publish-modal（真实 dry-run）/publish-version-modal（差异+影响预览，客户端合成，UI 明示非服务端预检） | preview 调用契约+validation/wouldConflict 渲染 | — |
| 发布流程（dry-run→确认→真实发布） | 三向导共用 reducePublishFlow（非法迁移保持原态） | 状态机单测+组件逐步推进+publish expectedRevision=7 断言 | — |
| 批量应用结果（成功/失败项逐条） | batch-apply-modal（四态徽标+error 逐条+汇总） | FAIL error 文本/汇总计数/edit 态无确认按钮断言 | — |
| 回退入口 | version-history-tab（指针回拨）/publications-tab（账本回退），发布结果均展示 rollbackRevision | 后端回拨/账本序列化回归例；前端按钮权限降级断言 | — |
| 真实 PG 端到端（发布→账本→回退） | — | — | 沿用 P2-04 验收既有真实链证据（RV1~RV7）；本 Slice 无新写路径，mock 层+契约层覆盖，真实 HTTP 链归 G6 走查 |

### 千回路批量路径性能口径（设计内说明）

后端单批 loopIds 上限 200（`schemas/loop_batch.py` max_length 防线）；模板关联回路枚举端点 limit 亦封顶 200 且无 offset——**本页批量选择面=模板关联回路前 200 条**（total 更大时 UI 显式提示「仅显示前 200 条（共 M 条）」，指引按装置/单元在回路配置页分批）。执行路径按 `chunkLoopIds` 顺序分批（每批≤200、逐批合并逐条结果、中途失败如实报「已完成 X/Y 批」），单批服务端为 O(N) 逐回路校验+审计（N≤200）。**千回路端到端批维护容量实证归 P5-05/G5-D02 与 G6 走查，本 Slice 不虚称已验证**。

### 验证（真实退出码）

- 后端 `uv run pytest tests/test_p6_eng_template_maintenance.py tests/test_p2_04_engineering_templates.py tests/test_p2_02_config_publish.py -q` → **exit 0**（80 passed：新 6+P2-04 33+P2-02 41）
- 后端 `uv run pytest tests/test_openapi_contract_drift.py tests/test_api_contract_script.py -q` → **exit 0**（24 passed；Literal 加宽=响应侧 non-breaking 实证）
- 后端 `uv run ruff check .` → **exit 0**（All checks passed）；`uv run ruff format --check .` → **exit 0**（908 files）
- dry-run 不落库：`test_preview_template_zero_write_and_count_matches_sample`（db.add 未调/db.commit 未 await/db.rollback 未 await）；批量 dry-run/执行部分失败行为=P2-04 既有 33 例复跑全绿
- 前端 `pnpm exec vitest run …/p6-eng-templates.test.ts …/routes-perm-code.test.ts …/routes-authority.test.ts` → **exit 0**（3 文件 31/31）
- 前端 `pnpm run check:type` → **exit 0**
- 前端 `pnpm exec eslint <本 Slice 全部 11 文件>` → **exit 0**（初跑 22 error→`--fix` 19 处风格+手修 3 处（toSorted/两规则冲突事件名改单词 `changed`/Select null→undefined），复跑 0 error）

### 偏差登记（移交主协调）

1. **整合项（我引起，红线禁改导航文件）**：Slice H 存量 `F/__tests__/p6-01ba-nav-groups.test.ts`「config 模块 path 集合未变」硬编码路径集，本 Slice 新增 `/config/eng-templates` 使其 1 例红（diff 恰为 +该路径）。按 my-work 先例归 O-INTEGRATE 同步断言（连同页面码目录/种子/守卫计数）。
2. **全量 vitest 另 9 例红与本 Slice 无关**：`p6-03-info-hierarchy.test.ts`（8 例，Slice L 在制文件，eng-templates 零引用）与 `p6-03-dbg2.test.ts`（1 例，复跑时已不存在=Slice L 临时调试文件）；全量进程退出 exit 1 为 STATE.md 已登记的本机 vitest 4.1.9 vite server close 竞态。
3. **`uv run alembic check` 报 "Target database is not up to date"**：隔离树 dev 库 current=p504srvflt01 落后本树 head=p605invest01——前序 Slice 迁移按 STATE.md「未上共享 dev」口径未应用，**预存漂移非本 Slice 引入**（本 Slice 零 ORM/迁移改动，git diff 无 models/alembic 文件）。
4. 事件名取舍：`vue/custom-event-name-casing` 与 `vue/v-on-event-hyphenation` 对多词事件互相冲突（仓库无先例），两 Tab 组件事件定名单词 `changed` 同时满足两规则。
