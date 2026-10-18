# 协议 P3 methodBindings 修正 — 非作者独立快速复审回执

- **审核人**：非作者独立审核人（未参与本协议、methodBindings 修订、G3 修复的编写）
- **审核时间**：2026-10-18（CST）
- **被审物**：`f02/series-s01/protocol-12e821a8.json`
  - schemaVersion=2，seriesId=`clpm-final-remediation-s01-12e821a8`
  - `revisedAt`=`2026-10-17T17:59:31.325770+00:00`，`revisedReason`=`P3 methodBindings fix: correct titles match originals, full SHA pinning, scopeLimits added`
- **证据根**：`/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z`
- **审核性质**：只读文档与原件核对。SHA256 由本审核人独立重算。未执行容量测试、未改代码/协议、未操作环境。
- **复审范围**：仅 P3 methodBindings 修正（8 项 title/path/sha256）+ scopeLimits（6 条）。P1 tools / P2 工具 SHA / P4 g3FixNote / P5 configFreeze / P6 readinessPending 上一轮已 PASS，本轮未重审（未见改动）。
- **总判定**：**REWORK**（窄幅，单点残留）

---

## 总判定理由

上轮 methodBindings 三项缺陷中，**两项已完全关闭**：

- **P3b 缺路径 + SHA-256 钉扎 — 已关闭**：8 项全部改为 `{title, path, sha256}` 对象结构；路径全部指向磁盘实际文件；SHA256 全部为 64 位完整摘要，且经本审核人独立重算**逐条匹配**。
- **P3c scopeLimits 未补 — 已关闭**：`scopeLimits` 已补，6 条齐备，覆盖要求的全部六个语义位。

**P3a "描述与原件不符" 未完全关闭**：8 项 title 中 7 项与原件一致（6 项 markdown 与首行标题逐字一致；JSON 项无标题行、文件名标识准确），但 **`fourOracles` 的 title 与原件首行标题仍不一致**：

| 协议声称 | 原件首行实际标题 |
|---|---|
| `P3-06-07 oracle 冻结草案（四业务断言）` | `P3-06/07 oracle 冻结草案（可裁决契约草案 v1）` |

差异两处：`P3-06-07` vs `P3-06/07`（连字符 vs 斜杠；原件正文自称亦用 `P3-06/07`），括注 `（四业务断言）` vs `（可裁决契约草案 v1）`——后者是原件正式定名，前者是内容特征的改写。

按上轮退回标准「**正文、ref 与实际原件不一致即退回**」及本轮复审要点 1「title 是否与原件首行标题一致（逐条比对）」，此项构成 REWORK。缺陷与上轮 P3a 同类（描述性改写代替原件标题），且是 8 项中唯一残留项。

**缺陷幅度**：单点、纯文档、一字级修正即可关闭（见下"修正指引"），不涉及任何验收口径（矩阵/预算/分母/expectedStatus/九条件/四业务断言）改动，亦不影响 candidateSha / CI / configFreeze 绑定。

---

## 逐项核对

### 要点 1：title 与原件首行标题逐条比对

| # | 键 | 协议 title | 原件首行标题（去 `#` 前缀） | 结果 |
|---|---|---|---|---|
| 1 | methodErratum01 | 冻结协议的方法勘误 01（投前，未读真实结果） | 冻结协议的方法勘误 01（投前，未读真实结果） | **一致** |
| 2 | supplement02 | 投前方法补充02：冻结资源上界与版本映射 | 投前方法补充02：冻结资源上界与版本映射 | **一致** |
| 3 | supplement03 | 协议方法补充 03：节点作用域与进程时间来源 | 协议方法补充 03：节点作用域与进程时间来源 | **一致** |
| 4 | supplement04 | 协议方法补充 04：事件 PID 与闭合进程集合 | 协议方法补充 04：事件 PID 与闭合进程集合 | **一致** |
| 5 | supplement05 | 协议方法补充 05：原生进程连续取证 | 协议方法补充 05：原生进程连续取证 | **一致** |
| 6 | supplement06 | 协议方法补充06：平稳率输入块既有别名 | 协议方法补充06：平稳率输入块既有别名 | **一致** |
| 7 | historyInputRefs | history-input-refs.json（D02 6+3 矩阵保留） | （JSON 无标题行，首行 `{`） | **N/A + 标识准确**（见注） |
| 8 | fourOracles | P3-06-07 oracle 冻结草案（四业务断言） | **P3-06/07 oracle 冻结草案（可裁决契约草案 v1）** | **不一致（否决点）** |

**注（第 7 项）**：`history-input-refs.json` 为 JSON 数据文件，无 markdown 首行标题，无法做"标题一致"字面比对。协议 title 以文件名 `history-input-refs.json` 精确标识原件，括注 `（D02 6+3 矩阵保留）` 与文件内容吻合（`expectedHistory: BARRIER_PASSED 6 / TERMINAL_MISMATCH 3`，`scope: "Original selected nine reports retained unchanged"`）。该标签不产生误导，**不构成否决**。

**补充观察（不改判）**：上一轮 P3a 表中 methodErratum01 曾被记为"表述过窄，勉强可接受"——本轮 title 已改为原件首行全文，该观察项一并消除。supplement02/05/06 三项上轮"描述与原件不符"（资源/版本身份被写成 algorithm contract、原生取证被写成 nine-condition audit basis、别名被写成 history input refs）本轮均已改为原件真实标题，**三项缺陷确认关闭**。

### 要点 2：path 是否指向实际文件

| 键 | 协议 path | 存在 | 位置符合原件约定 |
|---|---|---|---|
| methodErratum01 | `independent/协议方法勘误-01.md` | ✓ | ✓ 证据根 `independent/` |
| supplement02 | `independent/协议方法补充-02-资源与版本身份.md` | ✓ | ✓ |
| supplement03 | `independent/协议方法补充-03-节点作用域与进程时间来源.md` | ✓ | ✓ |
| supplement04 | `independent/协议方法补充-04-事件PID与闭合进程集合.md` | ✓ | ✓ |
| supplement05 | `independent/协议方法补充-05-原生进程连续取证.md` | ✓ | ✓ |
| supplement06 | `independent/协议方法补充-06-平稳率输入块既有别名.md` | ✓ | ✓ |
| historyInputRefs | `independent/history-input-refs.json` | ✓ | ✓ |
| fourOracles | `f02/g3-contract-ci-implementation/contract-originals/01-P3-06-07-oracle冻结草案-20261011.md` | ✓ | ✓ 四 oracle 原件目录 |

**8/8 路径正确**。与任务给定原件位置（`independent/` 下勘误-01、补充-02至06、history-input-refs.json；`f02/g3-contract-ci-implementation/contract-originals/` 下四 oracle）完全吻合。

**判定：PASS**

### 要点 3：sha256 完整性与匹配

本审核人以 `hashlib.sha256` 对 8 个实际文件独立重算，与协议声明值逐条比对：

| 键 | 声明 sha256 | 长度 | 独立重算匹配 |
|---|---|---|---|
| methodErratum01 | `df43afc449c93a1701ada5f6da8df001ba8c55cfea542d8615b96e44643ff1b8` | 64 | ✓ |
| supplement02 | `d34b6277eeda543949b3d1ec8517085585ddb07d1dd646d36bfb790bb8684b02` | 64 | ✓ |
| supplement03 | `dce0d31ffe3b4726e300eb7cab25ee8fe4b207351f34720134a1202cb8cdeff8` | 64 | ✓ |
| supplement04 | `105a2c6ff83d664d2d4e2675f52ebe2a8c6d42b54249980762f78739a04592d4` | 64 | ✓ |
| supplement05 | `78633c800b20171586919948e508dffbdd91f3441d5427b92975ea54356c7e0a` | 64 | ✓ |
| supplement06 | `dd5867597e64b34ea37481d2e9a8f175177d0b0bae881dc35d30a76ce0115a7d` | 64 | ✓ |
| historyInputRefs | `9e9f60287f56f27f19e49064b2db6ade7a64907ac7489b06e962bc6b151f1d0a` | 64 | ✓ |
| fourOracles | `8841b12ec938d8955f826a9750d522c0bd9cabdf02e308a71e189c5a1b657f8f` | 64 | ✓ |

**8/8 均为完整 64 字符 SHA-256，且与实际文件字节完全匹配**。上轮 P3b 提供的实测摘要前缀（`df43afc4…`/`d34b6277…`/`dce0d31f…`/`105a2c6f…`/`78633c80…`/`dd586759…`）与本轮声明值一致，钉扎来源可追溯。

**判定：PASS**

### 要点 4：scopeLimits 6 条语义位

| 语义位 | 协议键与内容 | 结果 |
|---|---|---|
| 四用户豁免 | `fourUserWaivers`: "P6-07/IAR-A01-A28/真实岗位走查/P7-01现场校准——各自保留，豁免≠已验" | ✓ 四项齐列，且显式声明"豁免≠已验" |
| G6G7 | `g6g7Status`: "CONDITIONAL_PASS（用户豁免清单四项）" | ✓ |
| 不合 main | `noMergeToMain`: "全部独立总PASS前不合入main" | ✓ |
| 不碰生产 | `noProduction`: "禁止操作生产环境" | ✓ |
| hooks | `noForceNoBypass`: "禁止绕过hooks/强推/删失败记录/降分母/改预期迎合结果" | ✓（含 hooks + 强推 + 记录/分母/预期红线） |
| 仅 github | `githubOnlyPush`: "唯一可推送远端为github，禁止向origin推送" | ✓ |

**6/6 语义位齐备**。上轮 P3c 缺失项确认关闭。内容与 AGENTS.md 红线（origin 禁推、不碰生产）及执行纪律（不降分母/不改预期/不刷绿）一致。

**判定：PASS**

---

## 判定汇总

| 复审要点 | 判定 | 说明 |
|---|---|---|
| 1 title 与原件首行一致 | **REWORK** | 7/8 通过；`fourOracles` 标题与原件不符（否决点） |
| 2 path 指向实际文件 | **PASS** | 8/8 正确 |
| 3 sha256 完整且匹配 | **PASS** | 8/8 均为 64 字符且独立重算匹配 |
| 4 scopeLimits 6 条 | **PASS** | 6/6 语义位齐备 |
| **P3 总判定** | **REWORK**（窄幅） | 单点残留：fourOracles title |

---

## 修正指引（单点，可即时关闭）

将 `methodBindings.fourOracles.title` 改为原件首行标题全文：

```
"P3-06/07 oracle 冻结草案（可裁决契约草案 v1）"
```

（即 `f02/g3-contract-ci-implementation/contract-originals/01-P3-06-07-oracle冻结草案-20261011.md` 第 1 行去掉 `# ` 后的原文。）

注意保留原件的斜杠写法 `P3-06/07` 与括注 `（可裁决契约草案 v1）`，勿写成 `P3-06-07` / `（四业务断言）`。路径与 SHA 无需变动（原件未改）。

---

## 可否投正式容量（结合 G3 已 PASS）

**当前：否。** 本 P3 单点残留关闭前，协议 methodBindings 层仍属"ref 与实际原件不一致"，按投前清单退回标准不可放行正式 20 批。

**单点关闭后：可以。** 理由：

1. **G3 侧无障碍**：上轮统一复审已判 **G3 = PASS**（论证 v2 对齐实现、跨级缺口显式登记、7 例单测 21/21 通过、R9 契约保持）。
2. **协议其余各层已 PASS**：P1 tools 路径、P2 工具 SHA、P4 g3FixNote 如实化、P5 configFreeze 绑定 12e821a8（digest `24e23e9e…`）、P6 readinessPending 均经上轮独立核验通过；容量矩阵/资源预算/不变量/输入状态/候选身份五块核心口径本就无需改动。
3. **本 P3 的 path/sha256/scopeLimits 三项本轮复审均 PASS**，唯一残留为一行标题字符串，不触及任何验收口径与身份绑定。

**放行前置（修正后）**：

- `fourOracles.title` 按上述修正指引改为原件标题，本复审可即时复核关闭（预期 PASS）。
- `formalReady` 仍应保持 `false` 直至上述关闭；关闭后可置 `true` 并投正式 20 批。
- 投递边界不变：四项用户豁免各自保留（豁免≠已验）、G6/G7=CONDITIONAL_PASS、不合 main、不碰生产、禁绕 hooks/强推/删记录/降分母/改预期、仅可推 github。
- 20 批跑完 ≠ F02 PASS：冻结协议 §3.4 的独立重跑门（需 Keeper 另行授权）仍在，须预留窗口。

---

## 审核纪律声明

- 只读核对协议 JSON、8 份原件与上轮复审回执；SHA256 由本审核人独立重算；未执行任何容量测试、未写数据库、未启停服务、未提交/推送。
- 未修改被审协议或任何产品代码。
- 本回执不构成 F02 容量验收 PASS，也不替代正式 20 批实测与独立重跑门。
