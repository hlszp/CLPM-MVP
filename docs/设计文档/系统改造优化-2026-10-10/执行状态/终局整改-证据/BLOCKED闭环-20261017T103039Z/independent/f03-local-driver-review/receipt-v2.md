# F03 本地 driver RR/RO 增量复核

**局部工具控制 PASS；放行 Root 执行专属环境准备/真实四例，不签业务通过。**

冻结源 `keeper-local-f03-v2.py` SHA-256 `225372ed218fdb770ebeab0c3b6d18c0c99f4433fc4a337d37331b9f652c511a` 已原样保全 `driver.v2.py`。独立按 AST 行边界分离 `capacity_observation`，与 v1 比较后确认函数外所有字节完全相同；证据 `v2-delta-check.json`。v1 已通过的环境/队列/信号/退出码10项控制不因该增量改变。

自编6项假PG验证全部 passed、rc=0（`rr-increment-independent-final.*`）：所有九表查询均在显式 isolation=repeatable_read/readonly=True 事务内；保全原始 current_database/read_only/isolation/txid/snapshot/clock 行与 SQL；正控完整7000映射；错实际DB、read_only=off、隔离级别错误、缺必需第九表、目标表非零均拒绝，并正常退出事务/关闭连接。未连接实际 PG 或启动任何服务。

新事务 metadata 单独保全，before/after 各为真实独立事务；只将九表内容原 bytes/hash/count 做前后相等比较，不把两次事务的时刻或snapshot混成同一个。旧3597源、v1 manifest与真实 plan 原件均保留。本回执于实际四例前发给 Root 放行；实际AUTH/PERM/CALC/EVIDENCE与指标数值仍须各自单次通过及独立复算。
