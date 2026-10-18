# S01 实际 INPUT 准备非作者复验回执

**PASS_INPUT_PREPARATION_ONLY。** 非作者独立重算冻结原件后，未发现超出批准范围的行/列变化。只签本次 INPUT 准备；正式容量仍 **0/20**，不签新 configuration freeze、生产、真实恢复或项目终局完成。

本次实际数据库、Docker、服务调用均为 **0**。以下数据库/队列事实来源于 Keeper 当时实际执行并封存的原件，经非作者逐件哈希和全行比较；不是本代理新做的实时数据库查询，也不扩为当前环境持续为空的声明。

## 1. 原件和准入身份

`verify_originals.py` 只导入标准库、读取磁盘原件，无数据库或 subprocess 客户端。实际运行 **rc0**，原命令/源码 SHA/起止时间在 `verification.command.json`，完整结果在 `result.json`。原 source、plan、reference、四目录均未修改。

| 原件目录（均在 E/f02/） | 原 manifest SHA-256 | 重算文件数 |
|---|---|---|
| `synthetic-capacity-binding-actual-root-v1/` | `36bc5f00cd768a7afb343a549bdb1b9e7db368afdcb6019e90306d4a08a9939d` | 119 |
| `synthetic-capacity-binding-root-invocation-v1/` | `768381dea40013e5c62770aa4ad83080d3325540dd3f740c1b25f9b02061952d` | 6 |
| `synthetic-capacity-dump-structure-root-v1/` | `98b3b1c6f16eaa351ec5df010cf0a94a44ee18f1fea08d8c91d1db421b0f6e2b` | 3 |
| `synthetic-capacity-dump-adapter-root/` | `1e1810c82d6bc577da37ed02f6adfb42da55dfa1018bf4c1d63b333d8728a3fe` | 10 |

138 项原件逐 SHA/size 全部吻合，文件集合与各 manifest 完全一致，没有额外或重复条目。实际 helper 源 SHA 为 `e213f27d9abc631ad3cb11233366e6f631a9fb4d88bcbf3b81cdf0d60464650b`，dump adapter 为 `c27d789a2c8308cfbeebc692d4565d2460b6c65fe2f3bc295fc72f51469fb3c8`，与已独立准入和实际调用 pin 一致。

实际命令使用 `redis-url-guard-v2/offline-plan/plan.json`（SHA `bb0a2d9f2b787d8e21ecd9baabe3410e15e58b4aa5bf499ba08337ecf3334e52`）；reference SHA `7f4f1bd130d8143806e73f3e3d02a8802477bab7ab0fccbb0cd5caa2a63bad2a` 与计划登记一致。未读取业务 `.env` 的秘密内容；其身份只引用当时调用原件中的 pin。

## 2. 四套 25 表全行比较

比较范围是冻结 reference 与 `before/`、`transaction-before/`、`transaction-after/`、`persisted/` 的 100 份原始表 JSON。每行全部字段参与比较，列表按完整行排序以保留重复行及其数量；不是只比行数或作者的 digest。`sys_config` 范围遵循已冻结 key 白名单，不扩称整个系统秘密配置均已核验。

- `before` 与 `transaction-before` 同原 reference 行事实一致；24 张非 history 表在四套快照中的完整行事实全部一致。1000 loop、7000 mapping/tag 的原 ID 和全列在该范围内保持。
- history 前后都是 **7000 行/7000 唯一 ID**，ID 集合与实际 UPDATE 参数完全一致，无新增或删除；实际状态为 `UPDATE 7000`。
- 每个 history 的差异字段集合严格等于 **`{valid_from,basis}`**，不接受“差异不超过两列”。7000 行均从 `MVP_INIT` 改为 `SYNTHETIC_FIXTURE`，`valid_from` 均成为计划冻结的 `2026-10-15T12:00:00+00:00`。
- 所有 `id/tag_id/loop_id/tag_role/mapping_version/valid_to/created_at` 逐行保持，其他所有列也保持；提交后 persisted 与事务 after 的全部历史行事实相同。
- 四个 identity 原件均是专库 `clpm_capacity_20261017_s01`、用户 clpm；before/persisted 为 RR READ ONLY，事务两套为 SERIALIZABLE，事务 before/after 的 snapshot 相同。

每张表的四个原件路径/SHA、行数及 reference 身份在 `result.json:tables`，7000 行约束与变化结果在 `historyChanges`。

## 3. 结果、队列和实际执行

四套快照的 **12 张结果表**键集合完整、共 48 个计数全部为 0。`tasksBefore/tasksAtWrite/tasksAfter` 三次实际观察原件均为：`taskKeys=[]`、`brokerPendingOrUnacked=0`、`celeryResultKeys=[]`。这些是当时的三次观察，不是持续不变保证。

invocation 原件证明实际执行 `rc0`、5.19971583300503s，原 stdout/stderr SHA 与实际文件一致，state SHA 对上实际 state；其 `committed=true/persistedReadbackVerified=true` 与上述独立全行重算相符，不仅依据作者布尔摘要作判定。

## 4. 备份与结构

原 `before.dump` 实际为 **12905207 B / 0600 / PGDMP**，本次重算 SHA-256：

`265c94e0244a392dceb78ae004d8e9fc21fc12a4df07404208b9b2546487edaf`

该 SHA 与 adapter generation/result、state backup、实际调用和后续结构检查原件一致。实际 argv 仍是批准的专库、7 参数和精确目标；内部使用固定 CID `4ec91098…` 的 `pg_dump`，连接该容器 `127.0.0.1:5432`，不把秘密值写入 argv。

当时 identity/version 原 stdout、stderr、命令和 rc 全部重算吻合：固定 CID/name/image `postgres:16`、5432→17102 映射，实际 `pg_dump (PostgreSQL) 16.14`、preflight/dump rc 均 0。

后续原结构命令是该容器 `pg_restore --list`，stdin 原 dump、无 DB 连接，实际 rc0，stdout 65835 B，其原 hash 对上。**仅证明 archive 可列结构，不是恢复实测。** 本代理没有重新调用它。

## 5. 比较器纠偏及结论边界

本代理初始直接比较 reference 的 `+08:00` 时间字符串与实际 UTC 导出，产生表示层误报；一次单边 ISO 格式化还受到小数秒尾零差异影响。两轮原源/命令/rc1/结果均分别以 `initial-*`、`timezone-only-*` 保存，没有覆盖。

例如 reference `2026-10-17T19:02:15.18458+08:00` 与 before `2026-10-17T11:02:15.18458+00:00` 是同一瞬间。最终只在 reference 对照的 history 已知 TIMESTAMPTZ 三列（created_at/valid_from/valid_to）两侧统一 UTC 后比较；四套实际快照之间及其他 24 表仍严格全字段比较。7000 行其余字段不变的结论未依赖泛化的“忽略时间”规则。初始告警因可复现反证判为**不成立，是 reviewer 比较器错误，不是产品 REWORK**。

本回执继续不采纳旧 freeze 作为正式执行配置；需要最终 M 上新的配置冻结另验。容量 **0/20**、四项用户豁免和生产边界保持。完成后即封档，不新增探针，不连接任何数据库、Docker、队列或服务。
