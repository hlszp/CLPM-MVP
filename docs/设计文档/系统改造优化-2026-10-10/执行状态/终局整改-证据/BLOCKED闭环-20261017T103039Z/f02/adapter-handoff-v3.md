# 容量原件无损适配 v3 移交

当前源码 `adapt_capacity_artifacts.py` SHA-256 `fb6e7f2add4bad514830f675944e7b29f508cf273e4ce1a4a25355751ded4674`（85362B），身份正本 `adapter-source-sha256-v3.json` SHA `8443c6dc2a10030d12f3e3d584d30458614140f12fe57302f6f28448b0a84e81`。当前等待F04非作者复跑原件，不签作者独立PASS。

本版只修F04有效反证：ps查询20.5s开始/22s返回，业务21s结束，不能据“已返回”确认业务之后的进程快照。`native_span`现按before.finishedAt≤start、after.startedAt≥end选原查询，有更后原件则取下一轮，无则BLOCKED，并保留两边原命令时标。`adapter-v2-to-v3.diff`及manifest AST检查证明其余模块未改；原五份测试/probe字节相等，仅新增两项实际边界回归。

原1021全部六源保在`adapter-sources-v2/`，原3e0仍保在`adapter-sources-v1/`。F04固定九原件正本为`../independent/f02-adapter-review-v2/fixed-negative-originals/`，未修改。对同一`replay_fixed_native_boundary.py`：

- `adapter-fixed-native-boundary-reverse-v2.*`：真实rc1，refused:false，source=1021，sameOriginalFilesVerified:true。
- `adapter-fixed-native-boundary-forward-v3.*`：真实rc0，refused:true，source=fb6e，sameOriginalFilesVerified:true。
- `adapter-selftest-frozen-v3.*`：73 passed/rc0；`adapter-fixed-counterexamples-forward-v3.*`：固定cancelled/NULL/query共6 passed/rc0；ruff/format均rc0。

全部运行仅纯磁盘，无host命令、DB/TD/Redis/API连接、服务/队列操作。原d0b collector、f9ef外置生产者、d518端点、07ee配置freeze及原四oracle均未改；SLO/PG时点、20×1000、A/B冷热各5、600s、历史6+3、原expected与四项用户豁免不变。

调用接口和必需原refs与v2相同，见`无损证据适配-接口与边界.md`及仍明确占位的`adapter-plan.template.json`。v2模板CLI旧证据rc2、无series.json原件保留；v3未重复未变的顶层guard。真实正常20/受控故障仍未由本作者执行或验收，缺原件不得填绿。正式全档完成后由Root装入真实M/配置/原ref，F04用原独立工具复算并签回执。
