# F03 本地 Keeper driver v1 控制复核

结论：**局部控制 PASS；等待已授权 RR/RO 增量复核后执行；真实四业务仍未验**。源 `keeper-local-f03.py` SHA-256 `3597c3bc3826185dfeaf1b391239c70d7c043f27fb3518ce4d5feb19b27aa084` 已逐字保全 `driver.before.py`，本轮未改源。

独立 10 个纯 mock 控制全部通过、rc=0。自编夹具不连接服务、不调用真实 ps/信号/DB/Redis/HTTP：环境去除共享 endpoint 并固定专属 DB/端口与五项数值线程；从冻结源提取真实嵌入队列程序，错 broker/backend 必须在 inspect 或 Redis 连接之前拒绝；stop 必须按 Beat TERM→CONT→立即 API TERM 执行；未排空或 Beat 原 PID/create-time/parent 不符不发信号；实际 API supervisor rc=1 不得签停机成功。见 `test_independent_driver_controls.py`、`controls-independent.*`。

另实际执行仅 `plan`，使用独立新输出目录 `f03/local-business-20261017204901-edfa831afd60/`，真实 clean HEAD 为 `edfa831afd6069963e7f6f73c2721637d6a199b5`，rc=0；源前后 SHA 相同，readOnly=true/serviceStarts=false。未执行 bootstrap/build/start/seed/gate/stop，未启动服务或写业务数据。原始命令、stdout/stderr 与安全环境见 `actual-plan-independent.*` 和 `actual-plan-environment-safe.json`。

静态全源确认：真实 celery_app.conf 校验专属 broker DB1/backend DB2 后才 inspect；初始 Redis DB0/1/2 全空、master runId/容器 owner/精确绑定校验；TD 只创建专属库；native.env 仅读；生产构建全站清单仅 `_app.config.js` 的 API 单键可差异；静态服务只监听 loopback/白名单且不代理 API；停机校验原整树 PID/create-time、原 master 与 broker 空和 supervisor 真实退出码。

发现一个证据边界：九表 capacity 参照虽全为 SELECT、含完整 raw/canonical 行字节与 SHA，并逐表前后相等，但 v1 未显式开启 RR READ ONLY，也未保全实际 PG 事务/DB 身份。已在投前建议补真实事务原件；root 已明确接受此增量并要求新冻结源复核。不能把 v1 的 `writes:false` 摘要称为真实同事务原件。没有扩大四项业务范围或调整任何测试分母/门槛。
