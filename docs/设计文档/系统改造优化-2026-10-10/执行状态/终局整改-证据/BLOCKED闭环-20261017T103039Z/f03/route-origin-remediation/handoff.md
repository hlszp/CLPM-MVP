# Critical 路由 authority 反例整改增量

本机 URL 合约红绿验证完成；真实四业务 case 仍待新 CI，不签业务验收。

非作者真实临时浏览器反例表明：原 `1db60cd…` helper 将 `#//foreign.invalid/cockpit` 规范化为外域逻辑 URL，只核 outer origin 后误认已进入 cockpit。此为测试 helper 的误绿边界，不是已证明的产品权限漏洞。原完整 spec 保存在 `spec.before.ts`，精确 SHA `1db60cd208c385b43ff3c71ff5a8f0012c00abeaf3eea1892ca22739c173c98a`；独立真实反证为复制的 `independent-counterexample-v2.*`，不是之前命令语法失败的那次。

本次仅修改同一 spec 的 helper：hash 必须为单斜杠站内路由，拒绝 authority、反斜杠、未知 hash 和会被 URL 规范化改变的逻辑 pathname；landing 同时严格匹配 outer/逻辑 origin 及原 cockpit 路径。导航目标限制为原 `/config/loop`、`/loop/workbench360`、`/metric/loop-evaluation` 三项本地路径，拒绝外域、authority、非法路径及额外 fragment。正常 hash/history 和全部 query 保留；没有修改产品 router/build、原真实身份/403/任务/血缘断言、四 ID、单次执行或 retry0。

源码于 `2026-10-17T11:55:58.697163+00:00` 冻结：

- `source-manifest.json` SHA-256：`d9d63f33ed2d363cf183c9cb4a6518366c6614e18c2186ec4d84b6353e5c1c91`。
- 当前完整 spec / `spec.fixed.ts` SHA：`c492347c387fcba3d954450f99bba41fe25a7c73f9988b03dcdded5ffb7c553f`。
- 主协调正常新提交 `2f09d5a026af2e2343ec06b9c5fd2dc352114c49`，未 amend `e418ac92` 或 `930ae487` 历史；作者未提交/推送。

`verify-route.mjs` 读取旧或当前 spec 的实际 helper AST，以真实 Chromium 和自有临时 HTTP 页面执行；不 mock CLPM、不访问产品 API、不使用共享数据。

- `before-browser.*`：原 authority 负控真实 rc=1，accepted=true 的原件在 `before-browser.json`。
- `fixed-browser.*`：修后真实 rc=0；hash/history 各三个目标完整 path/query，外域和同源 authority hash、反斜杠 authority、明文/编码 dot 规范化、任意 hash，以及六个非法导航目标全部拒绝。浏览器与临时 server 均 finally 关闭。
- `type.*`：e2e 类型检查 rc=0。
- `gate-contract.*`：8 passed、0 skip、rc=0。
- `four-case-collect.*`：原四 ID、chromium/retry0/repeatEach1，rc=0。JSON skipped=4 是 `--list` 语义，四例未执行，不能当业务通过。

当前 root 的正常 pre-push 正在执行，是否已到达 remote 尚未在本增量中声明。新 run 要另存原始 run/jobs/actual PR merge SHA、三 job 日志与完整 artifact；四业务完成后取真实 NPZ/manifest 交非作者数值复算。旧 242 文件目录未修改。
