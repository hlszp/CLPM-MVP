# F07 真实 409 错误形状补修复（作者移交）

本目录是第一轮账户隔离修复后的追加记录。父目录原始自验和反向证据保留，第一轮并未覆盖真实 RequestClient 的 HTTP 错误解包形状。本轮由主协调在真实隔离 API/UI 发现并提供反证，作者据此修复，不签真实浏览器独立验收。

候选工作树：`/Users/zhangping/DEV/clpm-remediation-20261017`；HEAD `dbb1de31` 加未提交补丁。六份 F07 文件的最终身份在 `source-files.json`，冻结时间 **2026-10-17T10:06:36.014453Z**。冻结后不再写源文件。

## 反证与根因

- 主协调原始证据：上级 `f07/real-409-precondition.json`、`f07/cli/43-phase2-real-409.json`、`f07/cli/47-phase2-409-wire.json`。服务端关注状态已由 rev0→1→2，而 UI 仍为未关注 rev0；点击后只发送一次真实 PUT 409，没有刷新版本重试。47 的响应 body 为 `ERR_REVISION_CONFLICT`，`data.currentRevision=2`、`isFollowed=false`，后端契约正确。作者未操作该浏览器或其私有 DB。
- `frontend/packages/effects/request/src/request-client/request-client.ts` 的 `RequestClient.request` catch 实际 `throw error.response.data`，调用方收到 `{code,message,data}`。`extractRevisionConflict` 原来只认 `error.response.status===409`，返回 null；之前单测均直接拒绝 Axios 包装，掩盖了这个差异。
- 未改公共请求层。`frontend/apps/web-antd/src/api/personal-work.ts` 现兼容已解包业务 body，仅接受两个精确冲突码；仍保留 Axios 包装兼容，包装存在时必须是 HTTP 409。普通网络、未知业务和 5xx 错误不成为冲突重试。返回字段/关注 CAS、墓碑与幂等语义不变。

## 验证与分母

新增 `personal-work-conflict.test.ts` **14 例**。仅 Axios 传输 adapter 提供确定性 HTTP 响应；真实 `RequestClient`、default/auth/error 响应拦截器、个人工作 API、冲突提取器与关注状态机均执行真实实现。这是请求边界单测，不是运行环境 E2E。

| 验证 | 实测 | 原始文件 |
|---|---|---|
| 新测试对旧提取器反向 | rc=1，5 failed / 9 passed，14 collected | `reverse-wire-contract-v2.command.json`、同名 stdout/stderr、results.json；`pre-fix-personal-work.ts` |
| 修复后请求边界＋关注21＋auth10＋preferences13＋resume5 | rc=0，63/63 passed，0 skipped | `forward-wire-contract-final.*` |
| Web app 全目录 ESLint | rc=0，0 errors / 16 既有 warnings | `frontend-eslint-final.*` |
| Web app Vue/TS 类型检查 | rc=0 | `frontend-typecheck.*` |

反向的五个失败分别覆盖两个精确冲突码在真实解包后的提取、rev0→2 重试成功、已是目标的幂等收敛、重试仍冲突止于两次。正向另核普通服务异常不重试/不成功/回滚，旧 Axios 包装兼容及错误状态拒绝。两次写请求的 expectedRevision 是 `[0,2]`，requestId 不同；UI 关注态和成功消息与服务端响应收敛。

失败过程不覆盖：最初 `reverse-wire-contract.*` 使用了相对 root 不匹配的 app include，rc=1 但未收集测试，不作为反向证据；改用仓库既有根 Vitest 调用后得到 v2 的 14 例实测。初次 `format.*` 的 prettier 不存在，rc=254，随后使用仓库已有 oxfmt（rc=0）。初次 ESLint 有新增测试 catch 命名 1 error（rc=1），只改测试参数名后最终 rc=0。所有原始失败均保留。

## 待独立复验

主协调应在同一真实隔离 UI/API 以旧 rev0 / 服务端 rev2 前置复跑，观察真实 PUT 409→PUT 200、第二次 expectedRevision=2、星标及消息收敛；另核断网回滚和账户隔离。全量 frontend 测试必须在此冻结源码上重新实测，不能继承修改前 v2 全量结果。本目录的 63 例不代表全量或真实 UI 已通过。

作者没有启动服务/Worker/Beat，没有向共享业务 DB/TD 写入，没有合入/推送/部署；四项用户豁免与阶段 Gate 口径保持不变。
