# F07 实施移交（作者自验，待非作者复验）

本轮角色为 F07 修复作者。候选工作树 `/Users/zhangping/DEV/clpm-remediation-20261017`，起点 `dbb1de31`，未提交。主协调先在真实隔离 UI 复现后放行本项修改；其原始证据在上级 `f07/cli/`，包括 `34-account-isolation-confirm.json`（rc=1，peer 星标错误）和 `pre-fix-account-db.json`（ic 有 1 条关注、peer 为 0）。`33` 的初始断言选择器不精确，已由 `34` 修正；两份原件均不改写。

## 最小实现与边界

只修改四份归属文件，完整 diff 在 `f07-source.diff`，绝对路径、字节数、SHA-256 在 `source-files.json`。

| 文件 | 改动 |
|---|---|
| `frontend/apps/web-antd/src/composables/use-loop-follow.ts` | 单例绑定稳定 userId 与递增 generation；当前 Pinia 的 user/access store 采用 detached、flush=sync 订阅，账号变化、未登录、登出开始立即清 Map/ready/busy/loadRequest。每个异步读写/分页/409 重试均携上下文，await 后检查归属；晚到旧响应不应用、不回滚、不继续分页/重试、不弹旧切换消息。读取去重与 finally 只释放自身 request；旧写 finally 不能清除新账户同回路 busy。getter 不产生清理副作用。 |
| `frontend/apps/web-antd/src/store/auth.ts` | 登录取得新 token 后，在存储/切换 token 前失效旧关注 session；logout 在网络 logout await 前清理。动态 import 延续既有避免 request/store 初始化环的惯例；同一 userId 重新登录也不会接受旧 session 响应。 |
| `frontend/apps/web-antd/src/__tests__/loop-follow.test.ts` | 原 10 例保留，新增 11 例真实 Pinia 用户/会话竞态测试；API 仅在单测中控制时序。 |
| `frontend/apps/web-antd/src/__tests__/store.test.ts` | 原 10 例保留；加强既有登录/退出例的先后顺序断言，确保清理早于新 token 与 logout 网络调用。 |

三个页面入口、后端关注 CAS/墓碑/幂等语义、用户数据均未改。F03 原证据目录保持冻结；未提交、合并、推送、部署，未启动服务/Worker/Beat，未调用真实业务写接口。

## 反向与正向证据

每次命令旁均有 `.command.json`、stdout/stderr 原始文件与真实进程 rc。

| 检查 | 结果 | 原始证据（本目录） |
|---|---|---|
| 新测试对旧实现反向验证 | **rc=1，2 failed / 19 因 -t 筛选未执行**。换 B 后仍读到 A 星标；A 的旧 409 触发第 2 次写请求。 | `reverse-account-tests.*` |
| 原实现保全与反向身份核验 | 测试时实现 SHA 与保全文件一致 | `pre-fix-use-loop-follow.ts`、`pre-fix-auth.ts`、`source-integrity-check.json` |
| 修复后的关注与 auth 单测 | **31/31 passed，rc=0**（21+10） | `forward-account-tests.*` |
| 格式/lint 修正后关联回归 | **57/57 passed，rc=0**（关注21、auth10、我的工作20、监视表6） | `related-forward-tests-final.*` |
| 全目录 ESLint | **rc=0，0 errors、16 warnings**；warnings 在未改文件 | `frontend-eslint-final.*` |
| 前端 TypeScript/Vue 类型检查 | **rc=0**，`pnpm -F @vben/web-antd run typecheck` | `frontend-typecheck.*` |

初次 ESLint 的 9 条新增测试 await 成员访问错误已修复；其失败日志 `frontend-eslint.*` 保留，不用最终通过覆盖。关联回归的旧 LoopFleetView 测试夹具仍输出 Invalid watch source 警告，原始 stderr 保留，进程 rc=0。

新增 11 例覆盖：A 已加载→B 清空并重新读取；旧读成功/失败不能覆盖 B；旧 finally 不解除 B 的读取去重；分页在换账户后停止；旧取消成功不能覆盖 B 的乐观关注或清 B busy；旧写失败不能回滚 B；旧 409 不以 B token 重试；已发出的旧重试成功/失败均失效；logout 立即清理及同 UID 重登屏障；匿名零请求与无旧内容。

## 待非作者确认

1. 主协调在同一真实隔离 UI 重新执行 ic → SPA logout → audit_peer → 我的关注为空 → 软导航监视的账户隔离链，并补 409、断网回滚、跨页/刷新证据。作者不签真实 UI 独立验收。
2. 全量 Vitest 由主协调统一执行，新增分母为本项 +11；最终完整分母以其实测为准，不能将本目录 57 例升格全量通过。
3. 本项验证的是候选工作树增量，不能写成已合入 main 或已部署；四项用户豁免与 G6/G7=CONDITIONAL_PASS 由主协调保持，不在本项更改。

移交状态：**修复与作者自验完成，待非作者真实 UI 及全量回归验收。**
