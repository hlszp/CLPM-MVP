# c452 配置冻结历史原件非作者复算

结论：该次已保存配置内容与源码映射 PASS。c452 已因后续 E2E spec 修正进入历史，正式投前须在最终 M2 重新冻结；此回执不签 Worker 内存、20批容量或F03业务。

- 固定版本 c452ea7fa913a7fffc721de81470ef0874b6bc46，原85文件manifest SHA `536aa75493ae1a9f3932c43af7d43f0f2dde6e1adbbbfcc4bc8bd0178f0d0277`，各原大小/SHA前后重算一致。
- 自编只读 `verify_configuration_freeze_raw.py` 不导入作者freeze工具/产品计算器：逐表重读25组原PG JSON与canonical相等，独立重算postgresDigest；AST只读真实源码_DEFAULTS/版本常量，按默认→algorithm_parameter→metric threshold以及TEMPLATE/NODE/LOOP层重建每回路参数。
- 实际9指标×4CT=36全局参数对，1000回路共36000参数对，逐项等于保存重建件与真实API merged参数；1000唯一参评PERF对象、7000唯一七角色点位、20个全部enabled节点的祖先/后代scope逐个一致；无复杂组。36为实测分母，之前沟通中的40是未核前形状估计，已纠正。
- 5份实际API原始body/hash/响应码、请求观察时点、revision3都核。configDigest独立精确复算为 `f565705262b238fe16f66830360fe489a9bd2ab61780eb13e49c6b050a98d6d8`，未靠报告的true代验。
- 两次PG RR READ ONLY原snapshot均 `1050958:1050958:`，同一可见版本、同库，API在两者之间；原件没有after全表JSON，故明确 `afterFullRawArchived=false`，不声称读取第二份不存在的原文。本例以相同MVCC可见版本限定纯表读取一致；若后续两snapshot不同且缺after原文，工具返回BLOCKED2，不能仅因stable=true判通过。
- 完整tracked源码catalog c0e4971a…共4042文件/166535780B，由非作者通过本地只读Git ls-tree/cat-file实际逐blob重算SHA/size，逐path/mode/blob/模块树归属c452，25份freeze源字节映射相同。它证明已保存catalog与不可变Git源码匹配，不替代运行进程实际加载证明。
- CLI反例：错误原manifest→rc1；新造副本保stable=true但换PGsnapshot而无after原文→rc2；新造副本只改第1000回路有效参数并重算伪清单→rc1。真实原件重新复算rc0；format/lint rc0。原输出与控制副本均保全，未改任何实际源证据。

见 `reconstruction-v2.json` 的完整readSet、原始命令/rc及 `controls-check-v2.json`。本次不连接PG/API/Redis/TD，不投业务或操作产品进程。
