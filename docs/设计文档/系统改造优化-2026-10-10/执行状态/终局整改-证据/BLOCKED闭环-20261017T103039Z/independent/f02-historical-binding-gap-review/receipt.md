# F02 历史绑定输入缺口独立复核

结论：发现成立（PASS）；正式容量准入仍为 BLOCKED。本复核只读取原始文件，没有访问数据库、服务、队列，没有读取真实 KPI 输出。

独立复算 `verify_capacity_historical_binding_gap.py` 实际 rc=0，原命令、起止、stdout/stderr 及哈希见本目录 `command.json`。输入为 `f02/historical-binding-readonly-preflight-root/` 原查询、命令、原 stdout 和其六项清单，六项大小/SHA 全吻合。实际库为 `clpm_capacity_20261017_s01`，RR / READ ONLY，前后 MVCC `1051247:1051247:` 相同。SQL 实际成功执行 `SET LOCAL TIME ZONE 'UTC'`；事务 JSON 未另输出 timezone，不补造该字段。

完整 1000 个 `PERF-T0001..1000` 均有明确合成描述、READY、active、参评。7000 个不同点位、每回路完整七角色，历史均 MVP_INIT / version1 / open。7000 条 valid_from 全落在 `2026-10-17T11:02:09.884681Z` 至 `11:02:22.846409Z`；正常20窗及额外4窗范围 `2026-10-15T12:00Z` 至 `2026-10-16T12:00Z`，逐窗均7000条生效过晚。正常20批原1000/600秒要求与额外4不计20分母未变。

将 aware 时间统一 UTC 后，旧 `series-s01/config-freeze-c452/pg-raw/` 的7000历史完整行、1000回路完整行与本轮原件全部相同，7000当前映射亦逐项对应。本缺口原已存在，不能将先前 TD 覆盖、24窗端点采集或旧配置准备快照等同于产品历史 resolver 可用；不改写此前原件和历史6+3。正式0/20为 Keeper 的执行状态声明，这份只读 SQL 本身不查询任务/结果表，不单独证明零执行。

允许的后续处置范围仍为正式测试前、专属 S01 新合成 INPUT 的历史有效期/basis 准备；不改产品 resolver，不改 IDs、点位、TD值、expected状态、1000分母、既有结果/latest，不新增用户豁免。工具须先独立审查，实际写入仅由 Keeper 执行，保全提交与持久读回两种状态及全列差异，之后重新冻结实际配置和最终运行版本。本回执不签工具写入安全或容量性能通过。
