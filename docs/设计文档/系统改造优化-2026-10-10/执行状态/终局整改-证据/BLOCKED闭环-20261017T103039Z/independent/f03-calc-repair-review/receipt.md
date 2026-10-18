# F03 CALC 后端三源非作者局部复验

判定：**PASS（固定六文件的源码合同与离线回归）**。作者冻结清单 `patched-source-v2.json` SHA-256 `b58b3100b6c85dc37a8498c4ecc6d4fa603e57772c7de489e137adcfa61e5e01`，六份快照/候选及测试前后实际 SHA、字节数均相同。产品改动仅 `tasks.trigger_custom_evaluation`、`kpi_calc._do_calculate_custom_batch`、`calc_snapshot.serialize_data_blocks/deserialize_data_blocks`，其余三文件原字节与 edfa 完全相同；DataPlanner 和 task_tracker 也与 edfa 原件全同。见 `source-scope-and-failure-raw-check.json`、`source-after-regression.json`、`unchanged-dependencies.json`。

原始失败链已独立恢复：远端与本机各 49 行，共 98 行，逐行重算实际日志 byte offset、长度、行号及 SHA；原观察前缀整档 SHA 一致，ValueError LOOP_FIELD 原行真实存在。只核选定错误链，未读取业务指标输出或据实际值调整数学期望。见 `frozen-{remote,local}-target-worker-originals.json` 和上述 raw-check。

独立新写 12 个合同，全数通过（`own-contracts-forward.*`，12 passed / rc0）。在同一测试进程仅替换为 git edfa 封存的四个原函数，原例 11 failed / 1 passed / rc1（`own-contracts-original-reverse.*`），磁盘源码未改。失败原因包括真实 producer 的文本来源不能序列化、旧 CUSTOM 不写完成数、原 publish 时记录尚不存在，以及投递失败无可追溯任务记录；不是靠变更 apply_async 签名制造失败。

通过合同覆盖：

- AST 加载实际嵌套 producer 的 LOOP_FIELD/TAG_RANGE/DEFAULT_0_100 三来源，NPZ `allow_pickle=False` 往返无损，真实 NULL 与数值 NULL 分别保留，来源改变参与 inputHash；旧数字包仍读，数值坏值及未知/冲突文本元数据仍拒绝。
- 运行真实批 orchestration 与 tracker，IO 使用自有 fake session / in-memory Redis：两回路的 SUCCESS+INCONCLUSIVE 保持 total2/success1/inconclusive1/failed0，在逐回路 commit 边界后写完成数；lazy terminal sync 保持 loops_done2。重试清掉旧9再写本次1，不把 INCONCLUSIVE 改成评估成功。
- 假 broker 在发布瞬间核完整任务 Hash 与索引已经存在、实际预分配 Celery ID 对上，再模拟快 consumer 写终态，API 不覆盖回初值。受控发布失败保留 FAILED/原错误/结束时刻，实际槽释放恰好一次，二次释放不减。

另独立重跑六相关测试文件：**351 passed、1 skipped、0 failed、5 warnings，5.42s / rc0**；原件 `six-file-regression.*`，包含三来源的实际按包 replay 回归及资源失败父链。既有 skip 不算通过，依赖与旧 mock 警告保留。原状态 lazy-sync 反例本身没有被改成倒填历史任务；修复来自上游真实完成 writer。

复验工具纠错：初次自写测试误将 DataPlanner 的嵌套 `_fill_limit_metadata` 当作模块属性，3 failed/9 passed。`own-contracts-forward-initial.*` 与初版源保留；已改为对实际嵌套函数的 AST 调用，此失败不是产品缺陷。候选六文件未因此修改。

限制：没有真实 PG/TD/Redis 写入、Celery 投递、服务启停或提交部署。本签不覆盖合成历史绑定修复、真实四业务、KPI 独立数值、远端 CI 或 F02 完整20批；这些均保持原门槛独立复验。四项用户豁免不变。
