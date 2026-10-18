# 闭端点采证工具 v3 非作者复验回执

结论：局部工具 PASS。真实 PG/TD 的 24 窗 × 7000 点闭端点采集未执行，本回执不替代业务容量、实际输入或数值验收。

- 冻结实现：`f02/capture_closed_window_endpoints.py` SHA-256 `d518fccb36b4e0f841fc920632f173c81b5d9422dcf81aa9ce319d6dbec3bcfe`；依赖 audit `8bd8cb60…`、configuration freeze `07ee03eb…`。`source-before-v3.json` 与 `independent-reverify-v3.json` 记录执行前后四源及原反例哈希完全不变；完整源副本在 `sources-v3/`。
- 原始反例：`missing-raw-values-counterexample.json` 的同一缺少 value/quality 原始行，独立脚本 `independent_original_counter.py` 在旧 bbaa 版本实际断言失败 rc=1，在 v3 实际拒绝该行 rc=0。原件未按修后行为改写。原始命令与日志：`reverse-original-v2.*`、`forward-original-v3.*`。
- 独立边界：`test_independent_endpoint_boundaries.py` 11 passed、0 failed，rc=0。覆盖九字段任一缺失、NULL/0 原值保留、data 原列缺失/重复/顺序错误、短长行和 rows 分母错误、COUNT 单列与 data 九列区别、HTTP/资源错误拒绝、500 UUID 精确端点查询与无 LIMIT；每种响应直接对 gzip 解压原始字节复核。见 `independent-boundaries-v3.*`。
- 作者测试由非作者另行运行：35 passed、0 failed，rc=0；不是引用作者口述。见 `author-fixtures-independent-v3.*`。独立脚本 format / ruff check 均 rc=0。
- 静态交叉核验：入口先完成 normal20 与 extra4 原计划/原件/哈希门，再连接私有 PG/TD；PG RR READ ONLY 保全实际 1000/7000 绑定；同谓词 COUNT 与无 LIMIT SELECT 均保全完整 HTTP/原始响应/SQL/SHA；前23闭端点应有下一窗源点，最后一窗真实缺值照录不补。实际输出、时间轴与点位值/QC 仍待 Keeper 执行后独立重算。

非作者未改作者采证器或候选业务源码，未接实际端口、投递任务、启停产品服务或写共享数据。
