# F03 v6 启动工具独立增量复验

结论：PASS（纯工具门）。源 `keeper-local-f03-v6.py` SHA `87c985f311f804c80a3beb84d78e342143e277fe18d8f5041c5fcb54c81e405f`。本复验人使用实际Python3.12执行自写8例/0和Root原10例/0，退出码均0；命令/源码SHA/stdout/stderr在 `own-controls.*`、`root-controls.*`。

自写检查直接取8a旧运行三PID记录并确认拒绝；七PID正控后逐字段反证出生时间、PPID、argv、Beat暂停状态、pidfile、初始化/父监视器、respawn。直接执行实际start函数但将网络、Popen、psutil与信号全部替换为内存假对象：正控只暂停精确Beat并等待队列排空；STOP不生效、暂停期间pool出生改变均在frontend启动前拒绝；队列90s不排空拒绝继续seed。snapshot每次核固定七身份与STOP，不允许缺readiness的旧state绕过。

除start、snapshot_status和3个新增纯helper外，全部AST与v5b相同；stop函数原字节与已独立验收的9fdc版本完全相同。原4业务分母、错误码、隔离前置、HTTP承载、停机门没有变化。无真实服务、Popen、网络、业务DB/队列操作或OS信号；工具通过不代表实际七PID启动和业务验收通过。初始Beat历史退出原因仍未知；实际运行必须保全v6所要求原件，缺失则不能签运行PASS。
