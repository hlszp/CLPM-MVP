# 配置冻结第二次快照原件补齐移交

作者局部自验完成，独立复核待 F04 出具。未连接实际 PG/API，未执行最终 M 配置冻结或正式容量批次。四项用户豁免不变。

## 改动及身份

- 新副本：`/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f02/freeze_capacity_configuration_v2.py`，SHA-256 `2688732523d3f9300c9d11e2dbf9194248a7be195c18cbfa313f5bbf020e66e5`，31380 bytes。
- 新回归：`/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f02/test_freeze_configuration_after_snapshot.py`，SHA-256 `4ebc7fe9b943a212a1542331f21f19ada8d0b2801ff32a289e3b4b1b16a70fc5`。
- 原工具 `/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f02/freeze_capacity_configuration.py` 保持 SHA-256 `07ee03ebf0e4c0c5f1f7bdea7bd5a5d7c0b01ee134ab49cd8787c19f0138388e`，29547 bytes，与 `original-freeze-07ee.py` 相同。
- 本移交的 v2 是新下划线文件 `freeze_capacity_configuration_v2.py`，不是历史其他同名/连字符版本。未修改旧 c452 配置产物、既有协议、六正式运维工具或候选产品源码。

唯一增量在 `after = await snapshot(db)` 后、原 API/revision/digest 稳定性检查前：原 before 路径 `pg-raw/`、`pg-canonical/` 不变；新增 after 实取25表 `pg-after-raw/<table>.json` 原 JSON 与 `pg-after-canonical/<table>.json.gz`。新增前后 `pg-before-identity.json`、`pg-after-identity.json`；`config-freeze.json.postgresSnapshots.before/after` 各保全51个 `{file,bytes,sha256}` 引用（identity1+raw25+canonical25），包括原摘要、表数及省略 sys_config 条数。

MVCC token 同或不同、内容摘要同或不同，均先保存已取得的 after 原件。内容摘要不同/API revision 不同仍按原语义 BLOCKED，被测 `run()` 返回2；稳定控制仍返回0。原摘要、白名单、SQL、API、源文件身份、36000参数推导和拒绝门不变。

## 原始证据

| 目的 | 命令与 stdout/stderr 前缀 | 真实结果 |
|---|---|---|
| 旧07ee：同/异MVCC × 稳定/变更digest四组反例 | `legacy-raw-control-final.*` | pytest rc1，4failed/3deselected；均因 after 表0而要求25；被测稳定run返回0、变更run返回2 |
| 新副本同四组 + API revision拒绝 + 最小AST + 旧新摘要/SQL等价 | `new-raw-control-final.*` | pytest rc0，7passed |
| 原有八项冻结门禁实际导入新副本 | `original-eight-against-new-copy.*` | rc0，8passed；进程内module alias，无文件改写 |
| ruff / format | `ruff-final.*`、`format-final.*` | rc0 / rc0 |

`offline-legacy-originals/`、`offline-new-originals/` 保留 fake DB/HTTP/git 控制的实际 run 输出、原 JSON 与清单。每份状态/表数/路径见 `offline-raw-run-index.json`。fixture 包含1000 loop、7000 mapping、20 enabled node，经原推导得到36000参数项。新回归逐表核 raw 字节/canonical 可恢复相等、前后51引用及全局清单 bytes/SHA；变化时仍拒绝但保留原件。

`ast-scope-check.json`、`minimal-change.diff` 证明：移除唯一新增归档块后原 run AST相同，其余模块AST全同。稳定控制旧新配置摘要和SQL transcript相等，before raw/canonical字节相同。历史独立原始重算器已有 `pg-after-raw/` 支持，未改独立工具。

最初 `legacy-missing-after-red.*`、`new-complete-control.*` 为 async 测试缺少marker的无效harness运行，保留但不作缺陷/修复依据；加marker后的 `*-v2.*` 有效。其中合并15/0是新7+当时仍导入原工具的旧8，因此另存 `original-eight-against-new-copy.*`，明确原八项实际针对新副本。一次写本移交的脚本因未转义f-string花括号报NameError，发生于handoff写入前，另见 `handoff-generation-setup-note.json`，不是产品/被测工具失败。没有删失败记录。

## 重放及独立接续

从 f02 evidence 目录运行（无业务.env、无网络）：

```bash
CLPM_TEST_MODE=1 CLPM_SKIP_EXIT_HOOKS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f02' /Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python -m pytest -q '/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f02/test_freeze_configuration_after_snapshot.py'
```

旧版反向同命令另加 `CLPM_FREEZE_TOOL_SOURCE='/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f02/freeze_capacity_configuration.py'` 以及 `-k before_after_originals`。每次精确argv/环境/原rc见相应command.json；不要复用已存basetemp覆盖原件。

独立人核原07ee/新copy SHA与精确diff，以自有断言核同/异MVCC、同/异摘要、API差异时 after25 raw+25canonical+51refs可恢复和重算；同时核不稳定仍BLOCKED/2，原摘要/SQL/白名单/推导未放宽。本移交只给作者局部自验，不签自身独立PASS。

独立通过后，由唯一 Keeper 在新的最终 M 协议登记本新路径/SHA及实际运行身份。CLI flags与原07ee完全相同，仅文件名替换；实际投前仍需最终M、24窗输入/闭端点、完整source/API/配置、7PID连续身份，不得伪造formalReady=true。旧preformal checklist/inventory保留历史身份，不回写冒称早已用新工具。

`evidence-manifest.json` 覆盖本局部全部归档及源文件bytes/SHA；不代表整个终局整改或正式20批已验。
