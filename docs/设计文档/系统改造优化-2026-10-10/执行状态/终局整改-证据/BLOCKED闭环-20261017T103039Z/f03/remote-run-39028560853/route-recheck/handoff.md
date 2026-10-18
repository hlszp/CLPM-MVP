# Critical E2E hash/history 路由整改交接

作者 URL 合约自验通过；四条真实业务 case 尚未通过，不签独立验收。

- 源码冻结 `2026-10-17T11:41:41.550566+00:00`，`source-manifest.json` SHA-256 `3a0ddbdd3a5e0c00ba3555400dff6ccfd820c60a87f9c46faeaafe79dd2f07f4`。
- 业务改动仅 `e2e/tests/critical-business.spec.ts`。另按主协调授权修正前端原 smoke config/spec 的两处陈旧注释，未改原 2 smoke、其 timeout/retry 或业务源码。两注释可单独小提交，`smoke-comment-check.json` 确认去注释后的 TypeScript 输出相同。

## 原始业务失败

run39028560853，PR head `add3ae7c6deaf3c1f136f53a14255d8160a68d7d`，三个 job 实际 checkout 均 `563b0da2667d4fcaa620676f0200f2c2a54b88ac`。critical job117384282721 的完整原日志在 `../job-logs/117384282721.log`。

完整 artifact12099965047 已下载，ZIP/官方元数据/91 个恢复文件及各自 SHA 保留在 `../artifacts/`。实际业务门禁摘要为 `../artifacts/12099965047/home/runner/work/_temp/critical-e2e/gate/execution-summary.json`：4 collected、0 passed、1 failed、3 did not run，rc=1；不要把同 artifact 的 `contract/passed/` 夹具目录当成业务通过。

AUTH 已拿到真实 HTTP200 和匹配 username，实际页面到 `http://localhost:15666/auth/login#/cockpit`；旧 `waitForURL` 只检查 pathname，仍含 `/auth/login`，30s 失败。serial 依赖使之后三条未跑，NPZ 数为 0，没有真实计算输入包可交数值验收。trace/video/screenshot/error-context 全保留。

实际 router 根据 `VITE_ROUTER_HISTORY` 选择 hash/history，生产配置为 hash；本轮构建产物和 URL 证明实际用了 hash。此前 workflow 的 history 环境设置不能替代实际路由观察；本次不修改产品构建器或默认配置。

## 最小修复

- 从实际 URL 的 `#/` 读取逻辑 pathname/query，history 模式仍读 pathname/query。登录从根路由进入，admin/sponsor 的实际种子契约只接受同源 `/cockpit` 及其子路由；停在登录、任意 hash、未知路由或跨源 cockpit 均不能过。
- AUTH/PERM/EVIDENCE 所有目标导航复用同一 URL helper，hash 目标完整写入 fragment，history 目标正常导航；loopId、view、taskId 全量保留。
- AUTH 真实身份、受保护 loops GET200 和表格断言保留；PERM 真实403与页面拒绝断言保留；CALC 实际选中回路任务、原三项 KPI、终态与血缘断言保留。
- EVIDENCE 在原“唯一目标行→详情→算法/血缘”之外，严格等待实际 snapshots GET 中携带精确 taskId，核路由 pathname/view/taskId、HTTP200、code字符串0、唯一结果、sourceTaskId/resultRecordId。无法消费 query 或空结果不能过。
- 原 AUTH/PERM/CALC/EVIDENCE 四 ID、单项目 chromium、repeatEach=1、retries=0 均未改。

## 本机真实命令

- `verify-route.mjs` 使用真实 Chromium + 临时 loopback HTTP 页面，读取当前 spec 的实际 helper AST；无 Playwright route mock、无 CLPM API/共享数据/Worker，不冒充产品验收。`legacy-browser.*` 重现旧 predicate 在真实 hash URL 的超时，rc=1；`fixed-browser.*` 在 hash/history 两模式逐一导航 3 个真实目标路径并逐字段消费 query，拒绝 5 个错误落点，rc=0。浏览器和临时 HTTP server 均在 finally 关闭。
- `type.*`：e2e TypeScript rc=0。
- `gate-contract.*`：真实 Playwright 门禁契约 8/8，0 skip，rc=0；包含失败、不可达、全/部分 skip、错 ID、flaky/retry 拒绝。
- `four-case-collect.*`：原 4 case 可收集，rc=0、1 project/retries0/repeatEach1。`--list` JSON skipped=4 是仅收集语义，4 case 此处未执行，不计业务通过。

需正常提交/推送后以新 CI 实际四 case、原始任务/结果/NPZ/manifest 与独立数值复算收口。当前远端 Frontend job117384282726 已真实通过 unit1021、type/build/lint 和原 2 smoke（日志3214：2 passed），只证明之前缺依赖已闭环；Backend 和 critical 当前仍失败，总 workflow failure。
