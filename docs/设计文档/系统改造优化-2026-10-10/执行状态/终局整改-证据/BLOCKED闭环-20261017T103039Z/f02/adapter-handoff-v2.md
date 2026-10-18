# 容量原件无损适配 v2 移交

作者只做纯磁盘工具修复/自验，不操作测试环境、不投递、不签自身独立PASS。当前尚无真实20批适配输出，实际容量与受控故障仍等Root执行及F04非作者复算。

- 冻结源码：`adapt_capacity_artifacts.py`，SHA-256 `102197849aa80dcb76da80908ff86600f4c57f96a5359f2c99e16184dc210d28`（84981B）。完整六源/依赖/原反例身份见 `adapter-source-sha256-v2.json`。
- 原件保全：`adapter-sources-v1/` 中原3e0源码、原作者两测试、88f NULL反例和v1身份均逐字保留；F04固定正/反原件位于 `../independent/f02-adapter-review-v1/`，未修改。
- 真实缺口闭环：NULL/坏digest/空seriesId不再准入，三个版本需与冻结原源码literal相等；node汇总需显式total/success/skipped/failed/cancelled非负整数、四类守恒且20/20/0/0/0。六态仍属于每NODE不可变roster，交原checker逐项重算，不改算法/expected/分母。
- 原固定反例作者前向：`adapter-fixed-counterexamples-forward-v2.{command.json,stdout.log,stderr.log}`，4 passed/rc0。增强的完整1000/7000/20+三版本原源正控，同测试原件在3e0为2 failed/1 passed/rc1，在v2为3 passed/rc0，证据 `adapter-complete-identity-{reverse-v1-bound-source,forward-v2}.*`。仅证明工具准入修复，不冒称原整个series曾假通过。
- 作者完整纯磁盘回归：`adapter-selftest-frozen-v2.*`，71 passed/rc0；`adapter-{ruff,format}-frozen-v2.*`均rc0。完整A/B各1000对象与20节点fixture明确为合成文件，不是实际业务。
- 外置原生采样：不改原d0b资源列/同事务四数组。v2要求每批`nativeIdentityObservations`指整summary，沿真实SHA/bytes读取plan/整档JSONL/全部首次lsof；逐原ps行核整span的7个固定出生身份/父链，原rc/git/env/taos、真实Popen启动、typed cwd/txt与实际解释器解析原件闭合。任务终态与PG屏障分时保存，native后取时点不回填SLO；原STATE不当etime，executorPid仍NULL。
- 必需全局refs：`runtimeTimezoneObservation`、`nativeIdentitySamplerSource`、正式M解释器的`nativeInterpreterObservation`、现有正式部署字节/完整目录等`runtimeCodeIdentityRefs`。源码目录真实性仍由非作者另核，Git HEAD/tree不单独代替已加载字节。原件缺失/范围不齐即BLOCKED。
- `adapter-plan.template.json`仍是模板，finalM/config/正式解释器/全部正常20原件均不伪填；旧模板保在`adapter-plan.template-v1.json`。CLI模板实际rc2，原输出 `adapter-template-guard-v2.*`，新目录仅保BLOCKED回执，不产生series.json。
- 精确说明和命令：`无损证据适配-接口与边界.md`。native方法05原件由F04维护，外置生产者f9ef已独立局部验；该空进程smoke不计7身份/容量。作者对lsof参数静态猜测已被Root真实反证推翻并撤回，见`adapter-author-corrections-v2.md`。

原历史selectedReports为1..7、9pp、10，6 BARRIER_PASSED+3 TERMINAL_MISMATCH，各1000；不改写为1..9或新全过。正常验收仍需原20×1000、A/B冷热各5、完整九条件、600s、固定资源profile及独立最终回执。四项用户豁免、G6/G7 CONDITIONAL_PASS、其他G5-D04未验边界均不变。
