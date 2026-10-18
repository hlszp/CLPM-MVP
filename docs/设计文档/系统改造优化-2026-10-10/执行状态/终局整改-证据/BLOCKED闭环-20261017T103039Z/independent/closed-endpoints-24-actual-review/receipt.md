# 24窗真实闭端点原始证据非作者复算

结论：闭端点实际原件完整性与映射 PASS；不代表20批容量已跑，也不代表四个业务例/实际KPI数值验收。

输入：`f02/closed-endpoints-24-actual-v3`，采集工具 d518fccb…；原采集 manifest SHA `9c964902e9f2239a88df28dd3a1bce5e331db17c1c2f7a85d1a4eb0d7ce726cd`。独立只读工具 `independent/verify_closed_endpoints_raw.py` 不导入作者工具/业务代码，直接恢复每份原始HTTP字节并重算。

- 全部2739个原文件的大小和SHA-256与完整清单一致，前后原件未变；24窗逐项等于原20+额外4投前计划，20/4分母保留，连续窗不重用。
- 实际PG RR READ ONLY中1000唯一PERF对象/7000唯一七角色绑定与最早真实输入身份 f844f1a5… 一致，无来源身份换绑。
- 所有672份真实 SQL 响应（24×14分片×COUNT/SELECT）逐份核SQL精确7000-ID分片/同谓词/精确端点/无LIMIT、HTTP/TD业务rc、原始gzip与解压SHA、九列metadata/行宽、COUNT与原SELECT分母、canonical与原始每个值/QC完全相等。无引用采集摘要PASS来充当这些事实。
- 前23窗每窗真实7000个端点、总161000行；最后B11的12:00端点实际0行/7000缺失，原查询COUNT均为0，7000角色投影均rawRows=[]，没有保持值、插值或生成补值。全部24×7000角色映射逐项复核。
- `raw-reconciliation-v2.json` 保存完整readSet/每窗结果及外部计划/最早身份SHA；实际rc=0。独立读器9个损坏反例/NULL0控制通过，format/lint均rc0。首版工具lint E731仅lambda风格问题，旧源与初次实际成功日志保留，v2仅改该风格并补外部readSet前后核验，未改原始证据。

未接任何实际端口或数据库，本次是磁盘原件复算，不重复向TD发查询。
