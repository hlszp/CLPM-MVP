# F03 synthetic INPUT 资格与两状态增量非作者复验

判定：**PASS（源码范围、纯保护及原反例复验）**。最终源 `prepare-critical-fixture.py` SHA-256 `0e1fc40d5e79a85992c4c99f1c60e09ed38dceb00f57ab366c2b2ab8de00edb7`；测试 `55f507d8dd6bff5b48b45d8770dd5815c8f24a25bf92e80d0b87526356890110`；已登记提交 `8a438372d21395fdb5246e4d8f73a0a5b7bf9c97`。候选和封存原件逐字节一致。

历史范围只允许严格隔离环境中新 API inserted7 tags/inserted1 loop、updated0 的 fresh synthetic 对象。实际 PG database、精确 loop 名称/说明/创建时刻、七个角色及 UUID/名称/说明、无其它 loop 的 current/history 使用均核后，才修改这七条初始 MVP_INIT 行的 valid_from/basis；无历史时只插该七点历史。旧/重绑/闭合/部分/重复历史拒绝，精确 DML rowcount 不符整事务回滚。未改产品 DataPlanner 追溯绑定契约。

独立静态比较：原 isolation/service-config 函数、原输入 synth_series/_td_rows/TD INSERT调用及原全部保护测试方法 AST 不变；两状态增量除 declare_synthetic_bindings 外原字节全部不变。原四业务 spec、4 IDs、Playwright配置、锁文件与CI等六份合同 SHA 与 f29b 完全相同。两个提交之间只有该fixture脚本及测试改变。见 `input-scope-static-check.json`、`v2-delta-check.json`、`v2-full-proof-crosscheck.json`。作者封存目录82项均独立重算 SHA/字节相等。

原实质缺陷已闭环：**同一未改 probe**（SHA `1688289b47eb5ca86593642a98518df9edbbc3eacccde53a88e6ebe401b0d4f7`）对旧 e39f 实际 rc1，写commit1后RO mismatch却committed=false；对新0e1f实际 rc0，同样失败仍抛原异常/关闭连接，而落件诚实 committed=true、persistedReadbackVerified=false。见 `post-commit-{original-red,fixed-green}*`。更新并未把失败变成成功，只修正证据事实分列。

另外自有 RO transaction __aexit__ 抛错负控对新源真实通过：即使查询已返回匹配行，完整RO事务退出前仍不可写 verified=true 或发布合格 readback；新源 committed=true/verified=false、关闭连接、原异常保持。`readback-exit-fixed-green*`。旧源对新增verified字段测试为KeyError，此原件保留，**不当作第二个已证原产品缺陷**。

独立执行原17纯保护：旧/new各17 passed、rc0；新writer方法含10个事务子工况（更新/插入×正常、预提交回滚、提交后不符/断连/RO退出失败），原输出 `author-17-*-independent.*`。未连接实际PG/TD、未投递或启停服务。

限制：实际新H的PG事务、七角色TD全值/QC/时间读回、绑定声明两个状态及SHA、真实四业务与NPZ、独立数学、远端CI均待Keeper实跑后另核。数值原始样本/品质/时窗与四业务断言均未降门；本签不扩为F03整体PASS、20批容量或四项用户豁免的实测通过。原 e39f REWORK 记录不可覆盖。
