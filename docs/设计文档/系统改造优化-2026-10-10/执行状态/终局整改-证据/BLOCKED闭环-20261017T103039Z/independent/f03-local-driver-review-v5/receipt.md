# F03 v5b 停机工具非作者增量复验

判定：**PASS（仅停机工具）**。冻结源 `keeper-local-f03-v5b.py` SHA-256 `9fdc2b0314e4c54a483c59131383945677e86920171b4e2b7cd5ef88f10af1bd`。独立按 AST 定位后比较原字节，除 `stop()` 外与 v4 `83462d20…` 完全一致；业务四例及其退出码门禁未改。

独立真实控制使用 runtime 的 Uvicorn 0.49.0、仅绑定 `127.0.0.1:0` 的最小 ASGI，无 CLPM 导入、HTTP 请求、数据库、队列、Worker 或 Beat。只发一次 SIGTERM 后，实际依次出现 Shutting down / Waiting for application shutdown / Application shutdown complete / Finished 自身 PID，真实 `wait=-15`，未使用 INT 或强制清理。原始命令与退出码在 `independent-native-control.command.json`；原生退出事实在 `independent-native-control.json`，完整输出在 `independent-native-server.log`。Uvicorn `capture_signals` 实际源码与主协调原件逐字节相同，证明正常关闭后重新触发原信号的原因。此反证推翻“正常关闭必须 rc=0”的旧工具要求，不改写旧 stop 的实际 rc1。

本次独立实际执行：

- 自写原日志边界 9/9：`own-clean-term-boundaries.{command.json,stderr.log}`；覆盖完整链、旧 offset、缺任一行、错 PID、错顺序、未完整行和仅 atexit 末行。
- 主协调完整 fake-process 控制 13/13：`root-stop-controls-independent.{command.json,stderr.log}`；独立重跑验证 rc0 与对应 TERM -15 可用，但 -2、-9、INT fallback、缺 shutdown complete、错身份、忙队列或停后剩 3 条均拒绝，失败也清理本工具持有的前端。
- 真实原日志回放 7/7：`root-real-log-controls-independent.{command.json,stderr.log}`；同时核原独立 ASGI 与已结束 v4 的真实停机日志。未启动任何 CLPM 服务。

三组测试均 rc0。新验收必须具有本轮 TERM 原件、原 API PID、发信号后日志 offset、三行完整顺序及逐行 SHA，且无 INT fallback；其后原进程树全部离场、专属队列为空、隔离数据及 native dotenv 均不变。rc0 也不能绕过这些证据。原始 API 退出码保留，不把 -15 改写为 0。

限制：本轮是工具控制与原件回放，未实际执行新的 v5b 业务轮。当前真实四例仍 AUTH/PERM 通过、CALC loopsDone=0 失败、EVIDENCE 未执行、NPZ=0，不能签 F03 整体 PASS、数值验收或容量通过。
