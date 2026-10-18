# 受控TD代理非作者工具回执

2026-10-17。**工具范围PASS，真实资源恢复/失败持久化及F02整体仍BLOCKED。** 输入`f02/td-proxy-source-sha256-v1.json`，代理SHA `b36666a90663499339625f01d0179d32340d5dfcf03a82eb6b80b834cec042d0`，作者测试SHA `159fdb71c815c585f4a4da7ff85831bbe0831820053eb40c632af36e122e70a5`；两者先重算再复制，运行后原文件再次同SHA。

非作者没有使用作者33例作为自身通过依据。独立编写`test_proxy_independent.py`，自己临时127.0.0.1:0上游stub/代理：`independent-forward.*` **10 passed / 0 / rc0**。每个harness真实端口、关闭线程和无真实服务事实见各`forward-artifacts/*/cleanup.json`；测试只用合成PV/plan/响应，未连接17125/TD/API/Redis/PG，未触发业务或改变runtime。

- DISARMED原响应逐字透传，响应raw/gzip SHA可恢复；Authorization仅真正透传，不落证据。
- 单点events严格库/点/闭窗/SQL/endpoint；其他查询真实透传且不消费故障计数。前2次匹配1850、之后真实上游；18并发请求严格2次注入且序号唯一1–18。
- persistent连续17次仍1850，零真实上游；明确新epoch人工DISARM后才透传。同epoch改字节HTTP503/transportRc78，不静默绿。
- 上游自然1850保留原字节且injected=false；真实drop为502/transportRc70，不伪装数据不足。
- credential SQL回显响应不归档；逐一解压`.gz`后确认合成secret和Authorization token都不在证据，未只扫描压缩字节。
- 错库/loop/UUID、正式20窗重叠、恢复改3次、persistent改9次等策略拒绝；启动ARMED、非loopback上游和0.0.0.0监听均拒绝。实际CLI默认--serve分支与SIGTERM收束另由静态源码和作者原件支持，本独立10例没有冒称重跑该CLI场景。

独立反向在**自己的副本**把persistent条件改成累计9后放行：同一原反例在第10请求发现code0，`independent-reverse-nine.*` **1 failed / rc1**。换回未改冻结源，同反例`independent-restored.*` **1 passed / rc0**；`sources/persistent-count-nine-mutant.py`明示为验证工具的负控，不是产品实现。没有改作者工具、候选业务源或实际policy。

此PASS仅允许Keeper继续指定测试代理准备。真实故障额外两窗不能计正式20批；实际PV归属/闭窗命中、每HTTP与local attempt/Celery retry关联、兄弟IO收束、1/1000不可变FAILED/TaskRecord/父任务、人工disarm恢复与环境回滚仍须真实原件与独立验收。SQL匹配计数不等于产品重试层数，代理transportRc不等于shell退出码。四豁免不变。
