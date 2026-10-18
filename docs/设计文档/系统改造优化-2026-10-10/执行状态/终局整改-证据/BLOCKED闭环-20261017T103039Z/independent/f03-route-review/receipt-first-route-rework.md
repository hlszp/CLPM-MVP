# 路由首修版非作者反例回执

结论：首修版 REWORK；此结论仅针对 E2E 认证落地 URL 判定的假绿边界，不是产品认证漏洞结论。后续新 SHA 须原反例重验。

审阅源为候选 `e2e/tests/critical-business.spec.ts`，执行前全文件 SHA-256 `1db60cd208c385b43ff3c71ff5a8f0012c00abeaf3eea1892ca22739c173c98a`。`prepare-page.cjs` 从实际 TypeScript AST 原样提取三个纯路由 helper；`helpers.source.ts`、转译 JS 与 `source-before.json` 保全。临时页面不运行产品或业务测试、不调用 API、认证、数据库或队列。

真实 Chromium 原反例：本地地址 `/auth/login#//foreign.invalid/cockpit` 外层 origin 为自有 127.0.0.1，hash 被 `applicationRoute` 解释为 authority，使 logical origin 为 `http://foreign.invalid`、pathname 为 `/cockpit`。首修版 `isAuthenticatedLanding` 只检查外层 origin，实际返回 true。独立 CLI 断言失败，真实 rc=1；见 `authority-hash-counterexample-v2.command.json` 与同名 stdout，截图及页面 snapshot 同目录在案。fragment 未产生远端网络请求。

建议修复：拒绝 authority/backslash 形式的 hash 业务路由，或严格复核解析后的 logical origin；保留合法 history/hash 两种模式、完整 query 和四例原有身份/403/任务/不可变记录血缘断言。由作者实施后再由本非作者按同反例独立重验，不能只看作者 fixture。

证据区分：无 `-v2` 的首条 run-code 命令因 CLI 需函数形式而 SyntaxError，属于复验人的命令写法错误，原件保留；真正产品测试 helper 反证为带 `-v2` 命令。两者均未隐藏或覆盖。

清理：仅本复验专属 Chromium session 与自有 127.0.0.1 临时 HTTP server 已关闭，见 `browser-close.*`、`server-closed.json`。未启停任何产品服务。
