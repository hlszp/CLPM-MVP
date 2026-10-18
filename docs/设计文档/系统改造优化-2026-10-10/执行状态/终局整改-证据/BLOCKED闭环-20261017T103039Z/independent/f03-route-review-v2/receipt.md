# 路由修正版非作者复验

结论：本地路由 helper 边界与静态/门禁契约 PASS；没有运行实际四个 CLPM 业务例，F03 整体仍待真实远端四例及数值/证据血缘闭合。

冻结 spec SHA `c492347c387fcba3d954450f99bba41fe25a7c73f9988b03dcdded5ffb7c553f`。首修版完整原件 SHA `1db60cd…` 已保全为 `spec.before.ts`。三个 helper 从冻结实际 AST 原文提取，在独立127.0.0.1临时页面通过真实 Chromium 测试，没有执行产品或向产品 API 发请求。

- 原 `#//foreign.invalid/cockpit` 假绿反例在首修版真实 rc=1（前目录 `f03-route-review/authority-hash-counterexample-v2.*`），相同反例在修正版真实 rc=0。见本目录 `original-counter-fixed.*`。
- history/hash 两种模式各对三个实际目标做真实 page.goto，共6次；loopId/section/view/taskId/编码值/空值完整保留。9个错误 landing 与6个错误 target 拒绝，合法 cockpit/child 双模式接受。authority、同源authority、反斜杠、dot路径归一化、任意fragment、外层foreign origin均覆盖。见 `dual-mode-query-guards.*`、snapshot、截图。
- 独立 AST 对比确认除三 helper 外全部原语句未改：身份校验、403、实际计算/任务/不可变record血缘断言保留。typecheck 实际 rc=0；collect-only 实际收集4例、rc=0，不冒称执行。`static-check.json` 保全源前后SHA一致。
- 实际执行仓库 `npm run test:gate-contract`，8/8通过、rc=0，含环境不可达rc2/失败测试rc1/全skip、部分skip、缺例、替换ID拒绿，以及重试后Playwright rc0仍拒绿。见 `gate-contract-independent.*`。此前命令误写历史名称 `test:critical-gate` 得missing-script rc1，仅为复验人命令名称错误，原日志保留，不算产品失败。

专属浏览器 session 和自有临时 server 均已关闭，见 close/server-exit 原件；未操作产品进程。
