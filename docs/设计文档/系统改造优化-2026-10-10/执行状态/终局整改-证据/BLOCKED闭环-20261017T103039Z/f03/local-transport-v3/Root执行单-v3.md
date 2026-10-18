# F03 本地 transport / 生命周期 v3：Root 执行单

源码和四业务用例保持 `edfa831afd6069963e7f6f73c2721637d6a199b5`。v1/v2、首轮真实 AUTH 通过/PERM 失败、API -2、遗留 frontend、pending 3 队列及 Root 资源归档原件均保留。v3 工具 SHA `de1a8981f8beb491265cbda24c3619a83b63c4cb3a80c5795b0c9f592df26801`；四份工具完整身份见 `source-manifest.json`。作者只执行纯 fake 控制 8/8、lint/format/Node 语法，全部 rc=0；没有启动服务、浏览器、初始化 DB 或执行业务。

## 只读诊断与变化

首轮 trace 的 ADMIN auth/login 与 /auth/me 均 200；精确 CSS `node-radar-panel-gZgSqI37.css` 在 ADMIN 冷 context 上 `net::ERR_CONNECTION_RESET`，路由 preload 中断并留在登录页。同一 CSS 原字节 708 B / SHA `8c30731fcf2d78091b6770ef364d34839cae3418074cbfc7bd77a8d104152dab`，SPONSOR context 实际 200；原 AUTH/PERM 业务断言不降级。诊断索引见相邻 `local-transport-diagnosis/`；TCP backlog/新连接并发是有原件支持的 transport 假设，不能仅凭静态分析宣布唯一成因。

仅三个函数改变，函数外字节与 v2 严格相同（纯控制测试已核）：

- `serve_site`：HTTP/1.1、接收 backlog 256、daemon handler threads；监听/白名单/业务文件字节/无 API 响应保持。真正业务监听仍 17266。
- `supervisor_api`：复用原 formal 已有参数 `--timeout-graceful-shutdown 15 --no-access-log`，避免长期排空和 WS access token 记录。
- `stop`：先 API TERM，原 Beat 保持 SIGSTOP 防 watchdog 重建；只读取 signal 前记录 EOF 之后新追加的精确 `app.main` JSON 停 Worker/Beat 日志且时间不早于 signal（真实源码位于 watchdog stop/await 后），保存 offset、完整原行、SHA、观察时间，再重核 Beat pid/ctime/ppid/status 后 TERM→CONT。最多等 75s，必要 SIGINT 再等 30s；真实 -2 仍失败。滚动保全完整进程树，失败也 finally 清理自有 frontend，保存真实 unknown exit 为 null；broker pending/unacked 或任何残留都非0，绝不 purge/伪 stopped=true。

## Root 先执行纯静态并发 / Chromium 冷加载

F04 同时复核工具控制，不改产品。下面命令只启动本次静态 fixture 的 17267，必须 API 17261 与 17267 空闲。fixture 是无产品 JS / API 的 transport-only HTML 与 96 份原 CSS 字节。old v2 / new v3 各建全新目录；执行副本精确只改一个监听端口字面量 17266→17267，原件/执行件/单点身份均落盘，不动冻结源。

```bash
F03_E='/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z'
F03_PY=/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python
"$F03_PY" -B "$F03_E/f03/local-transport-v3/run-static-transport-probe.py" \
  --checkout /Users/zhangping/DEV/clpm-remediation-20261017 \
  --expected-sha edfa831afd6069963e7f6f73c2721637d6a199b5 \
  --python "$F03_PY" \
  --native-env-file /Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env \
  --original-css "$F03_E/f03/local-business-20261017203500-edfa831afd60/site/css/node-radar-panel-gZgSqI37.css" \
  --first-run-id 20261017215771 \
  --out "$F03_E/f03/local-transport-v3/root-static-probe-20261017T125750Z"
```

实际生成两个目录为 `f03/local-business-20261017215771-edfa831afd60`、`f03/local-business-20261017215772-edfa831afd60`。标注 `TRANSPORT_ONLY_NO_CLPM_API`，业务 case 执行数 0。每版真实 256 个新 TCP 请求（4×64，无 retry）+ 4 个 Chromium 独立冷 context，每 context 96 CSS 唯一 URL，逐份 status/bytes/SHA、requestfailed、console/pageerror、截图、trace；每次监听的 PID/pgid/真实退出码与命令原件保留。旧版无论红绿均保留，不制造旧必红；新版须全部字节一致且无缺失/失败。脚本 finally 只 TERM 自有静态 pgid，失败不强杀。

## 新一轮真实四例

仅在 Root 资源轮换完成、v3 非作者控制与真实纯静态验证通过后进行。Root 已保全首轮数据库/Redis RDB/pending 3 原 envelope，不复用旧 gate；新 DB/Redis 为空，TD 仅原七个 owned 表轮换，容量 24 窗与九表未动。新 run-id/out 由 Root 唯一指定（以下建议值若已占用，应整体换一个全新数字 ID），一条命令真实 rc=0 才进入下一条。

```bash
F03_CHECKOUT=/Users/zhangping/DEV/clpm-remediation-20261017
F03_NATIVE=/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env
F03_SHA=edfa831afd6069963e7f6f73c2721637d6a199b5
F03_RUN_ID=20261017220500
F03_OUT="$F03_E/f03/local-business-$F03_RUN_ID-edfa831afd60"
f03_run() {
  "$F03_PY" -B "$F03_E/f03/local-transport-v3/keeper-local-f03-v3.py" "$1" \
    --checkout "$F03_CHECKOUT" --expected-sha "$F03_SHA" \
    --run-id "$F03_RUN_ID" --out "$F03_OUT" \
    --native-env-file "$F03_NATIVE" --python "$F03_PY"
}
f03_run plan
f03_run bootstrap
f03_run build
f03_run start
f03_run seed
f03_run gate
f03_run status
f03_run stop
```

`psutil` 是 Root 明示记录的临时锁外工具依赖，不称产品锁内；F03 停机后由 Root 正常 `uv sync --frozen` 恢复。不得改 native env 或 candidate 源码。前端外置配置仍唯一 API transport 键切 17261，业务 bundle 字节同 H，API cwd/源码/venv 分开登记。

四原 IDs 单次通过且 0 skip/retry/flaky 才是真业务门。后续输入-only 交接入口与 v2 完全相同：本次 `fixture.inputs.json`、真实 `calc-snapshots/202610/<UUID>.manifest.json` + `.npz`、有效 profile/config/完整参数/栅格/掩码，先固定给 F04 预计算并封 expected SHA，再读本次业务结果。没有真实产物时不得签数值通过；本机通过仍不扩签远端三 job、正式容量、合 main 或生产。
