# Keeper资源停止门非作者纯文件反例

投前工具审查，不是实际业务失败或容量结论。冻结副本`sources/validate_capacity_batch.py` SHA `37b5476009a63747609ab0f7705de0cecd1066269331385539d172c71416c1d4`。

非作者独立构造完整51轮/2秒、3容器原cgroup/7PID、VM meminfo、连续worker日志/实际提交和终态时刻的合成磁盘fixture，没有复用作者fixture或连接环境。`independent-forward.*` **5 tests/0/rc0**：完整对照PASS，缺OOM原文/字段、整个worker日志不可用、重复时间、倒序时间均拒绿。最初`independent-control.*` 1失败是非作者正控fixture漏inode/offsets，保留真实rc1，修fixture后通过，未改作者validator。该5例版本的测试源另保全为`test_resource_gate_independent-v2.py`，哈希与原命令记录相符。

再加原文一致性反例：原cgroup stdout首行6000000000字节已超过4.8GiB，但memoryCurrentBytes摘要仍268435456。该版本只信摘要，仍LOCAL_CHECKS_PASS，`raw-memory-counterexample.*` **1失败/真实rc1**，输入与实际返回在`memory-summary-counterexample-artifacts/`。已退Keeper最小修复：合法非负整数、原首行与摘要严格一致，并以原值求和。本回执对该版本仍REWORK；后续新源另收回执，不能用5例通过掩盖第6反例。

所有资料纯合成，不代表真实内存越线。独立正式上界复算工具在`../independent_resource_accounting.py`，其从原值求和且有该负控；两者是独立实现。真实20批/末300秒/所有身份/日志/冷热及全对象屏障仍待原件。
