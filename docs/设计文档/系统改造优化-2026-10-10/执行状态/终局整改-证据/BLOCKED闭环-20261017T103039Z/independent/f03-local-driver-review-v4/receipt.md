# F03 本地静态承载/停机屏障增量：非作者复核

**v4工具控制局部PASS；允许Root使用，真实四业务/停机仍另验。** 冻结driver SHA-256 `83462d20d8fb8cbd0224722ae269eb3f87df5eb6a41b89c653edaff53c39f622`。仅stop.read_barrier变更，函数外所有字节与原de1a v3严格相等，证据delta-check.json。

原v3只凭信号后 app.main stop Worker/Beat JSON，会把退出兜底误当watchdog关闭。非作者从旧真实API日志核实：Finished server process后出现两条精确JSON stop，整档没有Waiting for application shutdown；原始行号/offset/行SHA保全real-atexit-source-crosscheck.json。纯提取reader原反例1failed+1passed/rc1保留在../f03-local-driver-review-v3，不改写旧结论。

v4先要求同信号后offset内精确Uvicorn Waiting for application shutdown原行，随后实际app.main stop JSON；两段原文/offset/SHA均保全。同signal跨poll可接续，旧signal/旧offset/错logger/未进入lifespan的atexit不可接续。自编6项独立日志边界全部0；另正确CLI实际重跑作者9项（含真实旧atexit原件）及完整stop/finally8项均0。首次误用pytest导入作者带--driver参数的脚本rc3/无测试，原日志保留；纠正为CLI后9/0+8/0，不能把调用失误当产品失败。

v3之外的承载/退出范围亦核：HTTP/1.1+backlog256+daemon threads；API沿用formal的15s graceful与no-access-log；Beat保持STOP，API先TERM，确认本次屏障后重新核Beat PID/create-time/PPID/STOP才TERM→CONT；75+30超时、-2或队列pending3保留失败；finally只收本次owner frontend并核原进程树/实际broker。没有改edfa产品或四条spec。

独立只读重算Root真实纯静态old-v2/new-v3对照：同一站点所有文件字节相同，server副本只改监听端口17266→17267，probe stdout/stderr SHA匹配。旧256 HTTP中90次失败、成功166次原字节全部匹配，4browser均有reset；新256 HTTP全部原字节匹配，4browser无失败/外域请求，成功response逐件重哈希。两静态server由controller TERM退出-15，不是业务API退出码。证据root-static-original-crosscheck-v2.json；此仅transport，不能当AUTH/PERM/CALC/EVIDENCE通过。

旧本机首轮AUTH已过、SPONSOR403/404已见但完整PERM因CSS reset失败，CALC/EVIDENCE未执行/NPZ0，stop超时后API-2且frontend未自动清理、旧broker pending3，都保留失败事实。Root后来独立owner清理并保全/轮换专属资源，不将旧队列说成空或旧TD表说成仍在线。本回执未启动服务、发信号、接DB/Redis或读取任何actual指标；后续先input-only独立math封SHA，再读outputs。
