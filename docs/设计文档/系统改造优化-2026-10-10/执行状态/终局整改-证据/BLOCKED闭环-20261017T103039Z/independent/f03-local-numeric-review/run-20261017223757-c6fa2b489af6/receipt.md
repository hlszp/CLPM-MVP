# F03 本机真实四例、盲算与停机非作者回执

结论：本机业务四例 PASS、三项独立数值 PASS、停机与容量隔离对照 PASS。适用 H `c6fa2b489af66da392ea72fa387629f1607ba613`、runId `20261017223757`；不扩大为远端 CI、完整容量20批或 F03 整体收官通过。复验人未参与这些产品修复，仅执行文件复算及两条原生只读进程/端口观察，没有数据库、队列、投递或信号操作。

| 核验 | 结论 | 原始依据 |
|---|---|---|
| 运行与原始输入 | PASS | `input-audit.json`、原21项清单 SHA `90e8ca6890421ea0c340bceff1f6e00452e43e0892eef9ba098b0c66185c500c`；4046 tracked / 166618736B 逐 Git blob/运行文件重算；catalog SHA `0416a2f33b96ad953509e75cc722746bb2f23f1e6d9ba9310524e295160f9b86` |
| 输入与参数血缘 | PASS | 七角色7207原始样本的时间、float.hex值、QC、point UUID全部一致；七历史绑定实际提交/持久读回；`input-preconditions.json` 核全部36固定参数与冻结源默认字典、四类型权重与原PG行、真实dataset原行/原9ade SQL；前后原d202 SQL均RR/RO/UTC、11节内容完全相同 |
| 真实四用例 | PASS | 原 `OUT/gate/executed.json` 与 `execution-summary.json` 逐项相等，四固定ID各恰一次 passed，0 skip/retry/flaky；原 bash gate 命令 rc0，真实 Playwright 1.61.1；见 `actual-business-audit.json` 和 `actual/` 原HTTP附件 |
| 三项数值 | PASS | `expected.json` 盲算后封 SHA，再从原 Playwright base64 HTTP body 解出 `actual/snapshot.json`，原冻结比较器实际 rc0；见 `numeric-comparison.json` |
| 结果闭环 | PASS | CUSTOM 1回路/1窗口，loopsDone=loopsTotal=progress=1；snapshot→task UUID→不可变LOOP record→dataset UUID/inputHash→原manifest/NPZ精确闭合，窗口/算法/profile/config/build一致 |
| 停机与隔离 | PASS | 原三段Uvicorn日志、实际API退出-15、watchdog屏障后BeatTERM/CONT、全原7身份消失；独立只读ps/lsof八原PID/两端口全空；原broker空；容量9表三次54份raw/canonical逐值/字节/SHA均同；见 `cleanup-audit.json` |
| 远端与容量整体 | BLOCKED | 此回执仅本机；远端真实PG政策/行证据及最终CI/checkout另验，S01历史INPUT准备、重新freeze、20批与额外工况未由本回执验收；四项用户豁免保持不变 |

盲验顺序：`expectation-seal.json` 于 `2026-10-17T14:52:53.255965Z` 封存，SHA `97f7bf8f1f7fa827a6c872a5b2ae0afebdcc3d5eb9ce185d180e4799dfe32a3b`；expected SHA `7fa6eff663881430a739b5188415c1d6111609421ce9f86a4eb727aeadb9fd5d`，readSet SHA `d2304048d36eb55bb817849854036aaeac7bd69c86e93e1f70f96c97b2f951a8`。首次 actual 阶段在 `14:53:15.854698Z` 后开始，原 `actual-read-start.json` 保留。封存后未修改公式、参数、阈值、容差或 expected。

| 指标 | 独立期望 | 实际两位存储 | 差值（百分点） | 原容差 |
|---|---:|---:|---:|---:|
| goodValueRate | 100 | 100 | 0 | 0.005 |
| autoModeRate | 100 | 100 | 0 | 0.005 |
| steadyRate | 59.86430194720249 | 59.86 | 0.004301947202492329 | 0.005 |

数值由实际1秒网格、闭端 `[12:00:00Z,13:00:01Z)`、3601秒分母、实际raw/QC/held/mask及固定参数计算，未预填100。实际输出的两位精度由冻结候选 `_make_result` 默认precision2与CUSTOM模型 `Numeric(5,2)` 证明，保存在 `actual-storage-source/`。未保存的未舍入实际值不冒称通过1e-8原值比较。独立inputHash `d7d7c4262fc4cf069eb861afee4a94ad895f3b7d42b4fc2d98df9b8f09d1dcfd` 与真实dataset原行、record.inputSnapshot三方相同；真实包manifest SHA `41284dd7d029e36ad9e718f610433cf7bea9993d042bbe76b082eceeff7bbe68`、NPZ SHA `f214a2790f71162bf65610e7a9d71691e9936a4883161245d4548e26feb0e2fe`。

运行构建摘要由实际H和uv.lock独立重算为 `a7dbf45a406ae709`；配置内容摘要独立重算为 `rev:0:4cc7116649a473df`。实际输入包 `LEGACY_V2/KPI_CALC_v2.0`，pinned状态PINNED。两个held键在前后原PG查询均明确不存在，按原规则固定pv60秒、sp/mode7200秒。发布元数据的 `pre_v1` 与实际DataBlock lineage的 `pre_v2` 分列保留，不改写成同一字段。

独立工具格式适配如实留档：原四核心工具未改，旧oracle对本轮原包 rc2（只找stability_rate而原block叫steady_rate）；冻结候选已有该精确别名。主协调接受方法补充06后，新oracle SHA `8fe8340986b9511390455931abd0f157ffdef03eaf1fbed75b732a4ba2dbbd4e` 仅增加输入选择别名，原包/参数/inputHash不变；两名同时出现、重复或未知仍拒绝歧义，5项选择控制保留。所有数学函数其余字节与原oracle相同，先冻新源/方法再算expected；不是用actual拟合。

清理边界：API50699真实返回-15，仅在已请求TERM、原本轮Waiting→shutdown complete→Finished ownPID完整且无INT回退、整树与broker闭合时接受。前端waitReturnCode原NULL不补0；本复验人在 `14:58:52Z` 另做只读ps/lsof，八原PID和17261/17266监听均无输出（rc1），证明离场。Redis DB1仍有1个非业务绑定键，报告的是队列/unacked为空，未称全库空。容量九表的raw/canonical来自前后不同RR只读事务，MVCC各自保留，内容及hash相同，不伪称同一事务。

复验脚本自身第一次把原Finished行误写成带句点，rc1原件和脚本保存在 `own-control-corrections/`；修正为真实原字节，并按PG to_jsonb规范行与原行值逐项对应后复跑rc0。没有修改产品、driver或原日志。此前各次失败、旧9f缺manifest/旧环境字节失效、原historical 6+3和本机失败尝试全部维持原判，不因本轮局部通过重写。
