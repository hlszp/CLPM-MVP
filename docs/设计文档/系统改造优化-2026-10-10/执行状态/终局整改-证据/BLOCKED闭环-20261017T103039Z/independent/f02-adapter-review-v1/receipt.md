# F02 无损适配器 v1：非作者退件

结论：**REWORK（工具准入）；实际容量仍 BLOCKED**。审查对象为 `adapt_capacity_artifacts.py` 原 SHA-256 `3e0d800713218d2b5c80ec9bf631ca2ea514964ea776de3dcbb8e67f74b4e4d4`，已逐字保全为 `adapter.before.py`。本回执没有宣称整个 series 已可假通过，原独立九条件验收器仍为后续必经门。

| 项 | 原始输入与独立执行 | 结论 |
|---|---|---|
| NODE 取消计数 | 同一冻结事件/后台结果/20scope/闭池夹具，正控 result={total:20,success:20,skipped:0,failed:0,cancelled:0} 可正确派生20ID；只将 cancelled 改为1，v1仍派生20ID，拒绝断言真实1 failed +1 passed、rc=1。见 `positive-zero.json`、`negative-one.json`、`test_independent_node_cancelled.py`、`cancelled-original-red-v2.*`。 | REWORK：显式核实际 summary 字段的整数/非负/四分类守恒、20/20/0/0/0，不得忽略 raw 的自矛盾取消状态。六态属于 NODE roster，不能凭空添加到 summary。 |
| 缺版本/配置身份 | 非作者独立执行作者同一纯磁盘原反例：plan 和 FROZEN.header 同为 NULL configDigest，或 algorithmVersion=NULL；要求 config 准入拒绝的断言实际2 failed、rc=1。见 `null-identity-original-red.*`。 | REWORK：明确有效64hex digest、非空版本并与冻结原源码 literal 对照；不是已证明完整series能PASS。 |
| 原生启动身份 | 实际 d0b sampler 命令为 `pid,ppid,pcpu,rss,state,command`；连续 PID/PPID/argv 无 lstart/create-time/cwd/executable 原列。仅靠同 PID/argv 不能满足方法04固定原生启动身份。 | BLOCKED 前置：保留原采样，补真实、独立、连续的 native 身份原件并拒绝缺失。不得伪造 etime 或把 STATE 解释成启动时间。 |

明确勘误：审查人最初把第5列 STATE误看为etime，已在核实际 source:760 后撤回“etime回退漏拒”这一具体说法；没有把伪造字段作为反证。工具导入失误的rc=2及修正后真正触发断言的rc=1均保留，详见 `reviewer-corrections.md`。

已将固定 JSON/测试与原始退出码退回 F02 作者一次性修 v2。重验应使用同一 negative-one.json，不因修复改变反例；真正合法 native 原件需新冻结身份。本轮未接数据库/Redis/TD、未启动服务、未投任务、未改候选业务源；原历史6+3和20×1000/600s合同未改变。
