# F07 P6-05 关注入口 — 非作者独立复跑回执

**结论：PASS（本回执范围 = P6-05 入口增量的测试复跑与五项行为映射）**。复跑人为非作者独立验收执行者，**未编写或修改任何 P6-05 产品代码**，未操作生产。固定候选 main `390348d7`（`390348d7c7b0625398169d3546b16b32912bbe30`），P6-05 三提交 `a6c20cb8` / `cf498c4a` / `2c2d36e6` 均为该 HEAD 祖先（`merge-base --is-ancestor` 已核）。

本回执补的是**账户隔离/409 真实 UI 链定向复验**（已 PASS，见 `直接整改复验-20261017T093240Z/f07/`）之外缺口的**P6-05 入口增量非作者复跑签字**。

---

## 1. 测试文件名勘误（如实登记）

任务提示词指定的后端文件 `tests/test_user_loop_follow*.py` / `tests/test_user_work_resume*.py` **在 CLPM-MVP 与 clpm-remediation-20261017 两棵树上均不存在**（glob 零匹配）。关注/续作真实覆盖在：

- `backend/tests/test_p6_05_personal_workspace.py`（`/me/loop-follows` CAS、`/me/work-resume` generation 栅栏、双用户隔离、撤权不泄露）
- `backend/tests/test_p6_05_personal_work.py`（`/me/work` 四类聚合、分区互斥、cursor 分页）

两树该二文件字节一致（`diff -q` 无差异）。**按真实文件名复跑**，未放空、未以不存在的 glob 充当已执行。命令与分母见 [commands.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f07-p605-receipt/commands.json)。

---

## 2. 复跑分母与退出码（全部 rc=0）

| 序 | 切片 | 命令要点 | 分母 | rc |
|---|---|---|---|---|
| 1 | 后端纯单测 | `pytest tests/test_p6_05_personal_workspace.py tests/test_p6_05_personal_work.py -q`（`CLPM_TEST_MODE=1 CLPM_SKIP_EXIT_HOOKS=1`，addopts 默认 `-m 'not integration'`） | **40 passed / 21 deselected** | **0** |
| 2 | 后端 RealPG 集成 | 同上 + `-m integration` + 显式 `TEST_DATABASE_URL` 指向一次性 scratch 库 `clpm_test_p6`（`clpm-mvp-postgres:17102`，测后已 DROP） | **21 passed / 40 deselected** | **0** |
| 3 | 前端关注定向 | `pnpm exec vitest run --dom loop-follow.test.ts loop-fleet-view.test.ts my-work.test.ts` | **3 文件 / 36 tests passed**（loop-follow 10 + fleet 6 + my-work 20） | **0** |
| 4 | 前端全量 | `pnpm run test:unit` | **105 文件 / 996 tests passed** | **0** |

原始输出：`backend-unit.exit.log`、`backend-p605.stdout.log`、`backend-integration.exit.log`、`backend-p605-integration.stdout.log`、`frontend-targeted.exit.log`、`frontend-follow-targeted.stdout.log`、`frontend-full-unit.exit.log`；退出码汇总 `exit-codes.txt`。

说明：

- 后端 21 个 RealPG 用例默认被 `addopts = "-m 'not integration'"` 排除，且需显式 `TEST_DATABASE_URL`（防误写共享 dev 库）。本次按文件 docstring 既定方式建一次性 `clpm_test_p6` 跑通后即 DROP，未触碰 `clpm` 业务库与生产。
- 前端全量收尾有既知 Vite server 10s 退出等待（`Tests closed successfully but something prevents Vite server from exiting`），与作者原日志 `original-logs/p605-full-test.log` 同款，测试本体全绿，非本次引入、非产品缺陷。
- 作者当时 worktree 全量为 105/990；本候选 main 已含其后并入的提交，故 996 为合理增量，非分母漂移。

---

## 3. 五项行为验证映射（关注/取消、刷新接续、跨页面一致性、409 收敛、网络失败回滚）

| 行为 | 判定 | 非作者复跑到的证据 |
|---|---|---|
| **关注 / 取消** | PASS | 前端 `loop-follow.test.ts`：`首次关注：expectedRevision=0 + isFollowed=true + requestId；成功后以服务端 revision 收敛`、`取消关注：携带已知 revision 的设定语义；成功后从关注集移除`（2/2）；`loop-fleet-view.test.ts` 星标初始态回显 + 点击调共享 toggle；`LoopHeader` 未关注 `☆`/已关注 `★` 与 emit。后端 `test_http_end_to_end_me_endpoints` 契约级 PUT/GET 回读。 |
| **刷新接续** | PASS | 前端 `refreshFollows：分页拉全量（跨页合并）；就绪后重复调用零请求`（幂等、并发去重）。后端 `test_resume_generation_fence` / `test_resume_clear_isolated_per_user` / `test_resume_write_needs_existing_object`（generation 栅栏、按用户隔离、越权对象拒写）。入口初始态由 onMounted/`ensureLoaded` 拉关注集回显（fleet + wb360 页头均有用例）。 |
| **跨页面一致性** | PASS（见残留注记） | 语义主体由 **409 幂等收敛** 承担：`服务端已是目标态（他处已操作）→ 按成功口径对齐，零重试`——即"另一页已改，本页不假绿、直接收敛服务端真相"。三入口（fleet 星标 / wb360 页头 ★ / my-work 空态引导）共享同一模块级单例 `use-loop-follow`，定向 36/36 覆盖三入口各自接线。后端 `test_dual_user_isolation` + `test_follow_cas_replay_tombstone` 保证跨会话/跨用户不串号。真实 UI 链跨页与刷新保持（"我的关注→360，跨页与刷新后仍已关注"）已由 `直接整改复验-20261017T093240Z/f07/ui-receipt.md` 第 52/53 步实测 PASS，不重复声明。 |
| **409 收敛** | PASS | 前端 3 例全绿：① 幂等收敛（零重试）② 版本失配以 `currentRevision` 重试一次（两次 requestId 不同）③ 重试仍冲突按服务端真实态回滚 + 如实 warning（不保留乐观假态）。后端 `test_follow_cas_replay_tombstone`（CAS 携当前版本、同 requestId 幂等、取消墓碑拒复活）+ `test_concurrent_first_create_unique_constraint` / `test_unique_constraint_db_level`。 |
| **网络失败回滚** | PASS | `网络/5xx 失败：回滚操作前状态 + 错误提示（不冒充成功）`（API 层 `skipErrorMessage:true`，提示归本层，不静默）。`busy 防重入：在途期间同回路二次点击直接失败且不新增请求`。my-work 侧 `整请求失败（断网/5xx）：显式失败态 + 重试，不显示空清单伪装无工作`。 |

---

## 4. 源码接线核对（只读，未改动）

复跑同时只读核对三入口与跨页刷新确已接线（与提交 `a6c20cb8` stat 9 文件一致）：

- `composables/use-loop-follow.ts`：共享关注集单例（`refreshFollows` / `toggleLoopFollow` / 409 三分支 / 失败回滚 / `busy` 防重入）
- `components/monitor/loop-fleet-view.vue`：操作列星标 + onMounted 批量回显
- `views/loop/workbench360/components/LoopHeader.vue`：`isFollowed`/`followBusy` props + `toggleFollow` emit，`isFollowed===undefined` 不渲染（向后兼容）
- `views/loop/workbench360/components/SingleLoopWorkbench.vue:630`：成功后 `emit('resultChanged', { changeKind: 'loop-follow-changed', loopId })`
- `views/loop/workbench360/index.vue:161-162`：`onResultChanged` 对 `loop-follow-changed` 调 `refreshFollows(true)`（宿主强制对齐服务端真相）
- `views/my-work/index.vue`：FOLLOW 空态引导 + `data-testid="follow-goto-monitor"` 跳 `/monitor/loops`

---

## 5. 残留注记（如实登记，不阻断本回执 PASS）

1. **宿主 `refreshFollows(true)` 无独立单测**：`index.vue` 收 `loop-follow-changed` 后强制刷新关注集这条接线，套件内无专门用例直接断言（定向 36 例覆盖的是 composable / LoopHeader / fleet / my-work 四层）。其语义由「409 幂等收敛」用例（服务端真相优先）+ 上述源码行号核对 + 既有真实 UI 链第 52/53 步（跨页/刷新后仍已关注）共同闭合。若后续要求"每一行接线皆有专属单测"，此处应补一条 host-level 用例；本回执不因该缺口降级。
2. **提示词文件名与真实文件名不符**（见 §1）：按真实文件执行，建议后续任务书直接写 `test_p6_05_personal_workspace.py` / `test_p6_05_personal_work.py`，避免执行者空跑 glob。
3. 本回执**不等于**四岗位走查、完整 IAR、生产部署验收；四项用户豁免保留。未启动业务服务/Worker/Beat，未改代码，未提交/推送。
4. 真实 UI 链（账户隔离、真实 409 body、断网 `net::ERR_INTERNET_DISCONNECTED` 回滚、同 SPA 换号不串号）见 `直接整改复验-20261017T093240Z/f07/ui-receipt.md` 与 `ui-independent-results.json`，本回执不重复其结论、只签"入口增量测试复跑"范围。

---

## 6. 签署范围

| 项 | 内容 |
|---|---|
| 判定 | **PASS** |
| 范围 | P6-05 三入口关注动作修复在固定候选 main `390348d7` 上的后端/前端测试非作者复跑 + 五项行为映射 + 接线只读核对 |
| 不含 | 真实 UI 链复跑（已有 PASS 回执）、四岗位走查、生产部署、代码改动 |
| 分母合计 | 后端 40+21=**61/61**（unit+integration 全选集无失败）；前端定向 **36/36**；前端全量 **105 文件 / 996 tests**；四切片退出码 **全 0** |
| 证据目录 | `…/BLOCKED闭环-20261017T103039Z/f07-p605-receipt/` |
