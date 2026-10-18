# TD INPUT 回读 SQL 整改移交

作者局部门通过，真实四业务与数值验收仍待执行。本增量只给 `SELECT` 的 `value` 标识符添加两个反引号，完整源 SHA `431e8df1763f0b847e8e8d2ef0d29d87f699b5310186091157eb4f7cf1b8becc` / 28015B。未改生成器、时间窗、质量码、7 角色、容差、4 case、retry 或业务算法；e2e 保护测试仍原 SHA55f507…。

原始失败：远端 run39037226096，PRhead f29b，实际 checkout adc74b7a365b480f234dd6e5a2df7ac5592335ef，critical job117409399118 原日志3010/3011 为 TD code0x2600 与真实 exit1；尚未 CALC，不能把 gate contract fixture 当作业务通过。

真实只读负正控由唯一 Keeper Root 执行，原件在 `root-actual-readonly-controls/`。HTTP 均 200，必须看 TD body：旧 SQL LIMIT0/1 均 code9728，新引用标识符后 LIMIT0/1 均 code0；全窗 PV3600 行 code0，原 `validate_td_readback` 逐时间/数值/质量严格匹配，容差0。五份原 body 大小/SHA 已重算一致。此处只验已写入的 PV 输入，不是 7 角色完整 seed 或 KPI 结果验收。

`offline-literal.command.json` rc0：真实 synth_series(seed1 normal/steady span100)→原 `_td_rows` 的 PV3600/SP3/OP3600/MODE与PID各1，总7207输入数值及质量字面量全部严格相等（double hex逐项核），无格式化截断；完整原输入与 SQL 行原文保留。SQL模板与输入点原始证据可恢复，未改变任何数值迎合断言。

`candidate-seventeen.*` 17/0；candidate lint/format/diffcheck 均0。实际 argv/stdout/stderr/rc/hash 在同目录。修复前源码/修后源码/精确diff 已保留；本代理未执行真实 SQL、启停服务、投递计算或提交/推送。F04 独立核源差异与 Root 原 body；原两次 v5b 停机失败由 Root 另册保留，不在本增量改判。后续真实本机四例/NPZ/oracle/三job整体 CI 均不由此局部结论代签。
