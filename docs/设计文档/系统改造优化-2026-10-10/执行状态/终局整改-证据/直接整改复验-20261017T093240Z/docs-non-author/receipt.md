# F05 / F06 / F08 非作者独立核验回执

**本次限定范围结论：F05 PASS、F06 PASS、F08 PASS。** 结论只覆盖下述文档一致性、当前运行事实和本机归档可恢复性，不表示 F01–F08 全部收官。核验时最终《整改后复验报告》仍为进行中占位，待主协调冻结后另行快审；F02 新候选真实容量屏障、F03 真实业务四例与远端 CI 仍未验。

角色：本智能体不是 F05/F06/F08 文档或归档作者。虽参与 F03/F07 代码实施，但不在本回执签这两项的独立技术验收。未修改被核验文档、原审计、上一轮非作者回执或源证据；仅在本目录写验证脚本、原始输出和本回执。没有服务启停、Worker/Beat、共享 DB/TD 写入或 Git 提交/合入/推送/部署。

证据目录（本回执中的文件名均相对此目录）：
`/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/docs-non-author/`

## F05 — PASS（当前事实与历史标记）

观察时间：2026-10-17T10:07:46Z。逐文阅读 P7 汇总、任务看板、终局整改台账、遗留索引、运行来源台账、旧作者报告、STATE、纠偏矩阵、整改执行记录和当前最终报告占位，共 10 份。

- P7/任务看板/终局台账/遗留索引四份现行汇总均将 P6-07 跨仓、IAR-A01–A28 全量、真实岗位走查、P7-01 现场校准各自成行，明示“用户豁免”及未实施/未验/不等于岗位验收的技术边界；Owner、最小输入、恢复步骤与验收保留。G6/G7 仍为 CONDITIONAL_PASS。
- G5 的 900s 有界单批、600s 原目标缺口、DEC-05 α、D03 撤销、D04 抽验另列，没有替换成新的四项豁免，也没有用豁免关闭 F02 正确性失败。
- STATE 的 CP61/62/71/72 接续指针全部保留（当前第139/140/149/150行）；顶部把 CP1–79、旧基线和阶段表列为历史快照，序号80检查点记录本轮整改未提交/未部署、最终结论待接续。纠偏矩阵顶部 PRE-RULING SNAPSHOT / SUPERSEDED 明示旧 b31/G6-G7未过口径失效，正文作为历史保留。
- 旧《终局整改-独立复验报告》新增明确历史输入标记，原正文与开工前备份逐字一致，不能用其原 PASS 作为当前销项。原错误中的“全部完成/全过”只在已失效历史或原发现上下文出现；现行文档没有将本轮局部测试、实现、合入或部署混为全部完成。
- 已知债务和发布限制保留：发布 SQL 快照滞后、生产新装/有业务数据的生产回退未验、旧诊断读链/自动报表/service stub/G-N3/导航遗留、hourly 幂等重算、资源错误、关键业务 E2E、F04 恢复依赖审计保留期、F07 账户隔离与 409、三大文件仅本机归档。新候选 F02/F03 真实运行未验的口径一致；F04/F07 仍按独立复跑进展记载，没有预填通过。

机械复核 `verify_documents.command.json` 的真实 rc=0；`documents-independent-result.json` 留存全部 10 份内容 SHA、豁免行/行号、CP 接续原文、历史命中上下文、旧作者正文比对及 7/7 开工前备份 hash。最初验证脚本误把序号“80. CP-...”当成字面“CP80”，rc=1；修正本核验器匹配后 rc=0，未改 STATE，初次脚本/输出另存 `verify_documents.initial.*`。

原文保护：以下**开工前 SHA 来源为主协调本轮交接记录**；本智能体独立重算当前文件并匹配，不能凭单份当前文件自证过去。

| 原件 | 交接初始 SHA-256 = 当前独立重算 |
|---|---|
| `执行状态/终局对标检查报告.md` | `517e91b57f2e2ff3c182be30d32ffb329d9c7b9397e2d3395188f190fe167016` |
| `执行状态/终局整改-非作者复验回执.md` | `54cc82ca97e64fb7fd5314b14b41d5444ccaaf719b8478651d919e7a50f853e6` |

## F06 — PASS（当前运行观测范围）

观察时间：2026-10-17T10:00:28.997068Z（18:00:28+08）。独立执行 git、ps 父子链、lsof cwd/监听端口、只读健康查询和显式只读事务 SQL，与主协调 `f06/runtime-facts.json` 逐项对照，未覆盖其 capture。

| 核验 | 当前独立观测 |
|---|---|
| 主工作树 | `/Users/zhangping/DEV/CLPM-MVP`，main=`b676ea59158aa4d1e106579017a85860add60293`，文档有本轮未提交修改 |
| 候选树 | `/Users/zhangping/DEV/clpm-remediation-20261017`，fix/final-audit-remediation=`dbb1de314ae50e7373c6559ab746b690909bf1b0`，含本轮未提交补丁，不能仅用 HEAD 代表补丁内容 |
| 后端运行树 | `/Users/zhangping/DEV/clpm-g5-runtime-20261015`，HEAD=`c9e01f5d85294f5ad403fac7ae2e571ee472e9ad`；当前 uvicorn/Worker/Beat cwd 在运行树 |
| 前端 | 当前监听15666的 Vite cwd 在主工作树；不将隔离17261/17266私有复验进程算共享部署 |
| Celery 父子结构 | **1 Worker 主进程 + 4 pool 子进程 + 1 Beat**；Worker主PID57326、Beat57324均由当前uvicorn57294派生；完整命令/PID/PPID在原始 JSON。池进程单列，不说成5个独立Worker |
| DB / 健康 | `alembic_version=p702perm01`；`/health` status=ok、version=7.1；健康版本不是 Git SHA |
| 配置身份 | 27条 `sys_config` 的 key/valueSHA/updatedAt/updatedBy 内容快照；规范化SHA=`f49ad24a8cb3d479e6f154b86bf33f1a3dc5c015be47a799c52c1f28a96dd130`，与主协调27条快照相同；不是单一全局revision |

`policy_publication_guard.revision` 另列；SQL 在 `BEGIN TRANSACTION READ ONLY` 中执行，配置 value 在数据库内计算 SHA，未输出明文。`git diff c9e01f5d b676ea59 -- backend` 为空，rc=0，只表示所比磁盘源码相同。没有测量进程内存 SHA，也没有以旧PID消失反推它历史正常退出，远端状态未新验。

原始命令/实际 rc：`verify_runtime.command.json`（rc=0）、`verify_runtime.stdout.log`、`verify_runtime.stderr.log`、`runtime-independent-result.json`；后者逐条保留嵌套命令 argv/stdout/stderr/rc、配置行、父子结构、主协调快照 SHA 和受保护原件重算值。当前台账与这些观测一致。

## F08 — PASS（3/3 同机持久保全与完整恢复）

观察时间：2026-10-17T10:00:29Z。独立读取源 `终局整改-证据/evidence-manifest.json` 的24项，按各自真实路径全部重算字节数与 SHA，**24/24 一致**；与本轮保存的旧 manifest 比对，原 file/bytes/sha 身份全保留。旧 manifest SHA=`4030065cb8e06474f3a970c866c9d2609cf8e926fff227844c2777a98bf64e3d`。

持久压缩包：`/Users/zhangping/DEV/CLPM-evidence-archive/2026-10-17-final-audit/large-original-evidence.tar.gz`；27,474,475 bytes，SHA=`241e671130b9aad1d3f0b1bb3b19befcac185411620f4c711ecf8e315fe2bbf1`；文件系统owner=zhangping。核对 README/归档 manifest、证据区 manifest 副本与源 manifest 的归档字段一致。

由本智能体新建独占临时目录，从 tar **完整恢复全部3份文件**，逐个流式重算，并非仅抽压缩包目录或复用作者已解压文件。临时恢复目录验证后已清理；源文件和持久压缩包没有修改。

| tar 内文件 | 恢复字节数 | 独立 SHA-256 |
|---|---:|---|
| `G5-D01-04-实证-20261015/log-audit-a1pp.json` | 130871639 | `57ca05fb88a40a951136405c5616d1f200731d7b3d18d621a65768fa76c5a8e3` |
| `G5-D01-04-实证-20261015/trace-a1-raw.log` | 87273096 | `4eb2d0e4f02303d756a69131cc501ff3f364fd040e313665ce0b45d02d776c88` |
| `G5接管-20261014/b1000-real/observed.json` | 44268996 | `ba97ba29da5c97b7fcfb8df93d68b980ed6835280724816e59f060f9244b23e5` |

**3/3 与源和归档 manifest 一致。** 原始命令/退出码：`verify_archive.command.json`（rc=0）、同名 stdout/stderr、`archive-independent-result.json`。结果记录24项源文件与3项恢复文件的 expected/actual 大小和hash、旧manifest不变、归档身份和临时目录已清理。

本项通过的是本机持久目录可移交、可完整恢复的范围；没有异机、云副本、生产灾备或永久保留保证。现行文档已保留该边界，未把本机压缩包称为异机备份。

## 待终版快审

当前三项未发现需退回的不一致；最终整改报告与完整新增证据清单尚未冻结，留待主协调通知后检查其结论、实际分母、未验事项和源码身份是否与上述范围相符。本回执不为仍在变化的 F04/F07 技术结论提前背书。
