# 配置冻结工具非作者回执

**v3工具局部PASS，真实配置冻结与F02整体仍BLOCKED。** 非作者未连接PG/API/TD/Redis，未改配置、业务源或作者工具。所有执行均是本地文件守卫/重建函数。

输入旧源码SHA `83217b072a84b27baebc575ab4d9bc34eae3be5069c9651b69802c3afe67436a`保全在`sources/`；v3源码SHA `07ee03ebf0e4c0c5f1f7bdea7bd5a5d7c0b01ee134ab49cd8787c19f0138388e`、测试SHA `d500e7e6b306abbbba519de687f118aece889f1168d9bb574f6ff3c42523b92e`在`sources-v3/`，helper为前述已独立审过的输入audit v2。静态审阅源码先于执行。

非作者最初发现20个不同batch标签可指向同1份原audit/manifest/窗口，旧`completed_inputs`依然返回passed20。初版合成反例和进一步采用**真实input-a1512-v2完整1000行/1000不同loopTag原件**的加强反例均保全。加强版只新建`aliased-progress-valid-1000.json`侧车，真实原件SHA前后未变，记录在`aliased-valid-1000-counterexample.json`；不将该伪进度当实际容量证据。

同一个正确拒绝断言`assert_reject_alias.py`：

- 旧v2：`reverse-v2-alias.*`，**1个断言失败/真实rc1**，错误地将1原件接受为20窗。
- 新v3：`forward-v3-alias.*`，**真实rc0**，明确以重复原件目录/manifest hash拒绝；传入真实完整planned-batches路径，不用缺plan异常迎合反例。
- 非作者另独立执行冻结的8个纯文件回归，`author-eight-independent.*` **8 passed/0/rc0**，包括合法完整20正控、原manifest tamper、原plan窗/矩阵及稳定1000/7000绑定、配置三层优先级/五层上下文、敏感值拒绝、AST不执行模块。这些回归的fixture由作者编写，本回执明确它与非作者自己的同原件反例不同。

v3实现逐项核真实原plan20个连续小时、A/B冷热各5、1000/600；原audit目录/manifest不同；报告窗/场景、每对象窗、原argv manifest文件名及字节SHA；全部1000对象和7000角色/点位绑定在20窗保持一致，并在实际PG冻结前再对当前PG身份。不只重算progress标签。全24表PG RR/RO快照和可恢复raw/canonical、白名单sys_config、候选源码git字节、API实际merged globals/revision与独立重建、前后稳定区间均保留原方法；不冒称Worker pin内存、PG/API分布式原子或完整TD逐值hash。

实际运行还须全部正常20窗及额外4窗都准备/审查完成，再由Keeper以最终SHA/0600 raw token文件执行，并对真实原输出独立重算。额外4窗不计正常20通过分母；config-freeze.startedAt须晚于全部24窗准备完成。当前没有实际FROZEN输出，没有真实容量/指标PASS，四豁免和D04边界不变。
