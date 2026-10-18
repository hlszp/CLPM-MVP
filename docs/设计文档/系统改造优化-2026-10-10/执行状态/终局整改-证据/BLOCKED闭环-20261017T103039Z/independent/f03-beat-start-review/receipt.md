# Runtime 测试隔离独立复验

结论：PASS（仅测试隔离修复）。候选测试 SHA `3d6c5e15d71f04a1d08812a1e0cfb3cdf5f89f28bdbb76420ee00a4c01c51eef`；产品 main SHA `5f59552d61e4e1229d4b784471b17f3c00a42af797d810dc5194f73c6624252b` 与原件全文相同，目标测试函数外逐字节不变。

同一未改纯 AST 反例 `probe_pure_test_boundary.py` SHA `fb942a9e653c61051ee4f468d41e32e86ed9952c9bd64a1d7d3e8fb0d3810dd9`，本复验人独立重跑旧源码 rc1、新源码 rc0，完整命令与原输出分别为 `pure-original-replay-v2.*`、`pure-fixed-independent.*`。旧测试自身通过，但其未模拟的 pgrep/终止边界可匹配真实记录70527；新测试只在自有临时 logs/pidfile 上工作，显式模拟发现、fallback终止与killpg，退出还原全局状态与cwd。新反例外部发现/终止 calls0、实际信号0、网络0、产品模块导入0。

反例证明旧测试隔离路径危险可达，不证明历史70527的实际信号发送者。初始取证器曾误触发一次失败PG认证，原件与纠错说明完整保留在 `startup-analysis.md`；该工具不再使用。此回执不签真实服务启动、四业务例或数值验收。
