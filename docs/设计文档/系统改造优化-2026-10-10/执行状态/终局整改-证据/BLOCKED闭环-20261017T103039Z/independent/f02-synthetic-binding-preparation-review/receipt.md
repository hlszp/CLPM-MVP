# S01 新合成历史INPUT准备工具非作者门

结论：工具局部门 PASS，唯一适用源码 `f02/synthetic-capacity-binding-preparation/redis-url-guard-v2/prepare_bindings.py` SHA `e213f27d9abc631ad3cb11233366e6f631a9fb4d88bcbf3b81cdf0d60464650b`，原计划 SHA `bb0a2d9f2b787d8e21ecd9baabe3410e15e58b4aa5bf499ba08337ecf3334e52`。本复验没有连接PG/Redis/TD、没有S01写入、没有服务/进程信号或业务任务。实际INPUT准备、真实备份/事务/持久读回和容量验收尚待Keeper原件，不由此签PASS。

独立实际执行：同一冻结URL反例脚本 `probe_capacity_binding_redis_url.py` 对原e70真实rc1（guard未拒），对e213真实rc0（已拒）。真实redis-py客户端仅解析连接配置，无socket连接：`/0?db=1` 会使path替换后的DB0/1/2均实际指向DB1，故旧分支可能漏查任务/结果。当前实际native profile采用分离字段，不声称已经触发该问题。v2只增加拒绝query/fragment的两个条件，反替后源码逐字等于旧e70，其他全部函数AST相同；旧反例和旧源完整保留。

自有副本完整运行39项原控制及3项非作者边界，共 **42 passed / 0 failed / 19.51s / rc0**。原33测试字节SHA `186affc72fa60a0b83188a86c4c3e9f2668e12ff9c7eb393d9bf210f5800a1dd` 不变，附6项URL反控；额外三项独立测试分别证明：完整读回后RO事务退出失败仍保committed=true、verified=false；备份后任务出现会在UPDATE前拒绝且0提交；既有证据目录拒绝覆盖且0连接。原输出有1条 `/dev` pytest缓存不可写警告，是本次`-c /dev/null`控制的缓存路径问题，测试未失败，不重跑消除原件。

| 关键边界 | 独立核验结果 |
|---|---|
| 原计划/输入身份 | Keeper待执行argv中的实际plan-dir可由load_plan纯文件强门读取；SHA、25原表/3输入文件、参考9,492,998B SHA `7f4f1bd130d8143806e73f3e3d02a8802477bab7ab0fccbb0cd5caa2a63bad2a`全部匹配；20+4不混分母，earliest Oct15T12/latest Oct16T12 |
| 数据作用域 | 精确S01 PG loopback17102/clpm + Redis17123/0；1000完整PERF roster双synthetic标记、7000唯一七角色历史/当前映射/点位，fresh MVP_INIT/version1/open；只接受原UUID集合 |
| UPDATE范围 | 唯一SQL只改7000既有history行的valid_from和basis；必须UPDATE7000，事务内逐表/逐列等于原参考仅两字段差异；新增行/别列/其他既有覆盖表变化均回滚非0 |
| 执行屏障 | Keeper明确停机ack；RR/RO完整before、12结果表零计数、Redis task/broker/unacked/result空、真实全库pg_dump成功后才进入SERIALIZABLE表锁；锁后重复完整before与任务门 |
| 提交与读回 | 成功退出写事务才记committed=true；提交ack丢失记null，不伪称rollback；新连接RR/RO读回完整且事务成功退出/任务再空后才verified=true；提交后异常仍非0且提交事实保留 |
| 配置覆盖边界 | 25表按原c452实际范围，sys_config沿原已审白名单、省略19条秘密配置，未扩大秘密读取，不冒称省略项逐行对比；真实pg_dump另承担全库恢复备份 |
| 历史与后续门 | 原c452/f565原件不动，新增失效指针；只修正式前新合成INPUT，不retcon历史6+3、不改产品resolver/TD值/身份/latest/expected/分母，不新增豁免；实际修正后必须新配置freeze/digest |

源审与原件强门见 `static-and-original-plan-gates.json`；反例见 `redis-url-original.*`/`redis-url-fixed.*`；独立完整测试命令/原输出见 `independent-pytest.*`；三项新测试在自有副本 `copied-tool-v2/test_independent_boundaries.py`。`pytest-actual-originals/` 全是明确的假PG/假备份纯控制原件，其中PGDMP桩不是真实数据库备份，不得拿它替代Keeper尚未执行的真实dump。

工具准入的前序本机c6真实四例、独立盲算、停机/容量隔离对照已另签PASS，见 `independent/f03-local-numeric-review/run-20261017223757-c6fa2b489af6/receipt.md`，与本工具门分列。实际S01执行只能由唯一Keeper按已授权专属范围完成；复验人随后只读核真实变更两列、原行完整身份、before.dump、提交/读回、旧指针及新freeze，不将42纯控升格实际数据修正或20批通过。

复验元数据导入曾在作者目录产生本复验人自己的单个cpython314缓存；已按精确创建时间仅删除该临时缓存，作者源码/三项旧cpython312缓存未动，处置原件在 `reviewer-cache-cleanup.json`。本回执不含任何产品源码修订。
