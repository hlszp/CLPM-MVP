# F03 本轮独立数值验收的输入冻结清单

本清单先于新 H 的实际四例与数值输出冻结。只要求独立数学复算所用输入，不修改停机 driver 或业务算法。主协调是唯一实际执行者；本复验人不连接数据库、不投递任务。

Gate 前，在本轮 fresh synthetic seed 与绑定/TD 全量读回完成后、任何 CALC 发布前，主协调执行 `f03-input-only-pre-gate.sql`。用 psql `-X -A -t -v ON_ERROR_STOP=1 -v loop_id=<本轮 fixture 的 UUID> -f <该 SQL 原件>`；实际 DSN 只指向本轮专属 F03 PG，不在命令或日志打印密码。保全完整 argv（密码不入参）、实际 UTC 起止、rc、SQL SHA、stdout/stderr SHA/字节及未改写输出。SQL 必须原样执行，禁止挑行或以摘要代替。

同一事务必须 `READ ONLY` / `REPEATABLE READ` / UTC，实际 database 必须本轮专库，前后 transactionTimestamp/snapshot 相同，clock_timestamp 有先后，缺任何元数据或查询失败不得用默认值补成通过。两个 held 键的 LEFT JOIN **始终各输出一行**，包含 rowExists、rawValue（NULL 原样）、updatedAt/By；缺键与有键但 NULL/非法分列。按锁定 builder 原语义独立解析：快默认60s、慢默认7200s，字符串转正 float，失败或非正取默认，再各取 `max(3×实际 grid step, 值)`。pv 属快、sp/mode 属慢。不能从实际 mask 或指标结果反推阈值。

SQL 还保留精确该 loop 的完整工程输入、当前持久 revision、实际 ACTIVE 发布行（空数组也必须明确）、全部算法默认覆盖/metric配置/启用的五层覆盖/类型权重。均为输入配置，不查询 KPI、result_record 或 task 结果表。实际任务消费的完整五层展开值与来源仍以原子保存的输入包 `source.effectiveParams` 为直接数学输入；这些 PG 原行用于对照参数来源与 revision，不重新调用产品计算器生成期望。

与 SQL 一同提供的运行身份原件：本轮 actual git HEAD H、tracked backend source catalog / tree、实际 API/master/pool 的启动身份与同 H 证据、仅允许字段的 `CLPM_BUILD_CODE_SHA`/构建摘要来源、实际 native timezone。caller 自报 H 不当作实测运行 H。fresh lifespan 前无该进程 held 内存缓存；seed 后到 CALC 完成期间两 held 键与参数无写入须由 Keeper 见证，并在数值材料交付时重复同原 SQL 对照。若期间变化或进程先前已计算且缓存来源不明，暂不签此次数学复验。

输入包产生后，先只给以下文件/字段，**不发 gate.snapshot、metricsDetail、任何 actual KPI 值或结果 payload**：

1. `fixture.json`、`fixture.inputs.json`、`fixture.imports.json`，本轮 candidateSha/runId、exact loop/7 role point UUID、windowStart/windowEnd、逐 role 全部 `[offsetSeconds,value,qualityClass,qualityRaw]` 与声明 SHA/数量；真实绑定声明 `.bindings.json` 与完整 TD `.td-readback.json` 及其 SHA。绑定必须已提交并独立读回验证，不能把声明值当 TD 实存值。
2. 实际 CALC 创建的原 `.manifest.json` 与 `.npz`，原路径/完整 SHA/字节：arrays dtype/shape、绝对 epoch-ns 时间轴、numeric/null/validity、maskExpression/maskedIndices/consecutiveSegments、seriesContext grid/quality/coverage，工程 op/pv range、replayContext、schema/kind/window/loop/binding/source。
3. `source` 的实际 profile、algorithmVersion、configRevision、executableBuildDigest、releaseGeneration/dataPolicyVersion/paramsSchemaVersion，以及 `effectiveParams.status/scope/params/sources/typeWeights/fitness/configRevision`。本工具仅支持其原冻结范围：1s 网格、PV量程[0,100]、LEGACY_V2/CANDIDATE_V3_RC1、good/auto/stability及oscillation依赖；遇其它输入先记 BLOCKED，不改公式迎合 actual。
4. `f03-input-only-dataset-row.sql` 是包产生后的可选字段采证入口，但本轮最终数值验收要求恢复实际 `calculation_dataset_snapshot` 原行：loop_id、id、dataset_ref、input_hash、ts_start/end、schema_version、kind、创建引用/时刻、size等。以实际 NPZ UUID/loop UUID执行同样RR/RO SQL。该表不含指标值；其 input_hash 与本工具独立重算值比较。没有此原行可先算独立 expected，最终血缘判据仍未闭合。

完整输入 refs/hash 与确定实际 profile/参数/held/window 后，复验人冻结 numeric plan，用原 `independent_kpi_oracle.py` SHA `3e7f74a511db0abac0b1c49dc8e1f9c9ee7b04011774f6dfc283fcba4ff4b42a` 计算 expected；不导入产品计算器。先封 expected 及 readSet SHA，再接收实际结果。容差不变：原值 1e-8 个百分点、仅两位存储值 0.005 个百分点。请求闭端仍按原契约 end+1秒，分母不能先验写3600或把 GOOD/AUTO 预填100。预处理 validity 是封存的实际输入事实，并独立核 raw→held/QC 与 mask 布尔表达式；不假称重新实现全部预处理分类。

此为本轮所需全部数学输入清单。任务状态、权限、4例分母、0skip/retry/flaky、远端CI checkout与artifact和完整容量20批仍按原协议另验；不以数学局部通过扩签。
