# 审查过程勘误

- 误把 `ownedProcesses` 第 5 列看成 etime。实核冻结 d0b collector:760 的 argv 为 `pid=,ppid=,pcpu=,rss=,state=,command=`。第 5 列是 STATE；撤回“etime 回退未拒”这一具体反例，没有制作虚假 etime 原件。固定原生启动身份仍是方法04的既定要求，需要真实外置采样原件；不能由相同 PID/PPID/argv 自身证明未重启。
- 生成夹具初次误用系统 python3，因无 pytest 停在导入；未执行产品代码。随后用项目 venv，独立测试第一次因保全源移位后缺 independent_support 导入而 rc=2，原日志保留，增加独立工具路径后原断言实际触发 rc=1。这些工具调用失误不计产品缺陷。
- 节点 task summary 实际为 total 加 success/skipped/failed/cancelled 四分类。六态属于 NODE 不可变 roster，不能为 summary 发明 pending/missing 字段。
