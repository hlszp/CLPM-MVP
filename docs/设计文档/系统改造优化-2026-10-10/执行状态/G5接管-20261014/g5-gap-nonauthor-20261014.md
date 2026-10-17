# G5 缺口语义修复：非作者复核

> 当前有效裁决及候选身份见文末“最终15文件冻结复验”。前文保留首版及12文件版历史取证，不把后续文件修订冒充同一内容。

## 裁决与范围

**本次冻结文件的缺口语义与 v2r1 输入元数据离线复核 PASS。** 同一固定输入17例及5个反事实全部符合独立预期；13个非缺口小样的状态、评分、有效率、指标值、fitness、source/algorithm/configRevision和掩码与基线一致。缺口4例由旧错误SUCCESS变为INCONCLUSIVE、score=None，不依赖额外打开覆盖开关。

本报告不认证整个G5或600s容量。只认证下列实际文件内容及本次离线边界；没有真实Worker/TDengine/PG/Redis、数据库提交、ledger/latest/nodes屏障、浏览器、H5/API p95或实际部署证据。观测代码在本轮 `CLPM_G5_TRACE=0`；enabled路径与TD连接阶段不在本核查范围。

源树：`/private/tmp/clpm-g5-followup-20261014`。Git HEAD仍为基线`e07dcf8019ea0cb65cfbe1ea0f9c1e108bc6d252`，**dirty工作树未提交**，因此SHA本身不能代表这些修复已进提交。三处缺口源、KPI零值分支、生成器以及当前default-off观测接线随实际文件hash认证。没有编辑修复源、提交、推送、操作共享运行环境。

## 实际候选身份

最终完整小样、249项定向测试、额外零有效率oracle的七文件hash逐一相同；每次运行前后hash保持不变。详见`/private/tmp/g5-gap-nonauthor-final-comparison-20261014.json`。

| 文件（源树内） | SHA-256 |
|---|---|
| backend/app/contracts/series_context.py | 5854de663228bd652bcb3576977d5e21574cd88ad9a96c5e5efb780e5b962fab |
| backend/app/services/data_source/logical_wide_builder.py | a52d86fc2722b4ff7a68837e8ec06fd51806fdc0021157d8672c02b0e3d32da8 |
| backend/app/services/preprocessing/pipeline.py | 233d293df09c08fe55615f5fe461515a4777a7b484ac5ba14378013be21bd55f |
| backend/app/tasks/kpi_calc.py | 6b06db499b46bf7d8935d4758bf75b7c17a8a1519529bdf88ec31bdced7ee6eb |
| backend/scripts/generate_capacity_dataset.py | e179e42529c78b9708e9970e92d1fa75654d507e18ceac72da5e902d86308a34 |
| backend/app/services/data_planner.py | f34187ca049a577d6bdf2b213f3f8cd2b6eefcad53a076e158d915de0136c84f |
| backend/app/core/g5_trace.py | c7045ced8489a8a6f55a6767c263cf0c4469f00f276dd153190931d996ae818d |

当时dirty：上述tracked源（g5_trace为untracked），另有tdengine_native、test_generate_capacity_dataset修改；g5容量日志工具/trace测试、gap测试、零覆盖测试未跟踪。它们有其他Owner/验收，不能从本报告推导整个工作树已全验。

## 代码与独立oracle核查

1. `series_context._count_grid_slots` 将半开游程映射为实际离散格点的整数索引区间，再合并去重；不再用持续秒数冒充槽数。`logical_wide_builder._build_context`携带真实step及尾槽边界。独立oracle没有调用作者测试helper：随机seed64014，步长1/2/5/10/2.5，分数原点、交错/重复/越界游程，枚举格点是否落入任一游程。**1500随机+3异常边界 PASS**；源码hash前后稳定。
2. Pipeline只在可信有效率副本中将核心PV/SP/OP/MODE的UNKNOWN排除，保留原值、原质量和数值validity；合法HELD仍known，PID等非核心UNKNOWN不拉低核心有效率。复用逐槽known判定，**没有用最短连续段资格冒充有效点数**。现有回归明确覆盖短HELD段有效率1而consecutive_segments为空、core UNKNOWN并集而不是各角色比例相乘、legacy无上下文仍有效率1。
3. `PREPROCESS_VERSION`改为pre_v2，使DataPlanner的L1预处理块键区分旧块。本次不操作真实缓存，Keeper仍须证明旧pre_v1不会混入新候选。TIME_QUALITY_POLICY_VERSION未由本报告宣称更新。
4. KPI有效率零分支：当前pre_v2确实计算出的0保持0；pre_v1、None、缺字段手工块仍回退原数值valid内核。独立4分支结果为0/1/1/1；另补FC/TC两例**全窗登记UNKNOWN但值和QC仍Good**的真实Pipeline→完整KPI：base有效率0、PV invalid=0、known=0/unknown=N、落库参数valid_rate=0、INCONCLUSIVE/score=None。没有将保留的Good数值误充可评样本。
5. 生成器修为capacity_v2r1_bounded_normal_noise：仅版本标识与gap的PV/OP source points从3600更正900；synth_series函数、seed、原始数据形态和expectedStatus映射没有修改。四类manifest点数回归通过；22例原始输入SHA与v2逐一相同。旧manifest与失败批保留。

## 同一输入的最终复验

固定参数仍为原审计：point布局、fast held60s/slow7200s、minObservedRatio=None、无前置状态/锚点/改绑、固定1h窗；算子固定默认参数、fitness空覆盖、默认权重。输入时间戳是离线任意固定窗，不认证真实时钟/调度。仅5个反事实使用离线ratio0.7或900s后登记gap，原预期不改。

| 类别 | 固定seed | 最终独立结果 |
|---|---|---|
| 正常 | 1/2/3/4/949 | 5/5 SUCCESS，与基线评分/指标/fitness/valid_rate完全相同；949真实5s无invalid |
| 坏质量 | 801/802/803/804 | 4/4 INCONCLUSIVE/score=None，与基线完全相同 |
| 手动 | 901/902/903/904 | 4/4 SUCCESS/score0，auto与effective_auto0；仍eligible，与基线完全相同 |
| 缺口 | 951/952/953/954 | 4/4 INCONCLUSIVE/score=None/最终confidenceE；有效率约26.63%–26.66%，明确数据不足 |
| 反事实 | FC/PC/TC ratio0.7；FC/TC额外登记gap | 5/5 INCONCLUSIVE/score=None；无后改预期 |

缺口独立离散槽数：无登记时FC960/3601、PC480/1801、TC/LC192/721；登记900s后FC900/3601、TC180/721。各role已知+未知=N、上下文步长与实际周期一致；未知计数不大于N，PC覆盖率不再错误翻倍，TC不再被裁成1。

22例逐一比对：输入series_sha256、base原数值validity hash、全部bundle mask hash、configRevision、source及algorithmVersion相同。13个非缺口persist字段、指标明细、base摘要完全相同。掩码保持数值可用事实，UNKNOWN的可评性由新可信率/gate及已有时间视图承担；本报告不宣称所有辅助动态算子已实现分段科学分析。

## 检查与证据

- **249 passed / 2 warnings / exit0 / 1.33s**，socket attempts=0，源和测试前后hash稳定。包括新增gap回归15项、KPI零值1项、生成器（含全部100个noisy固定seed与新增manifest4项）、R14稀疏准入、held R2/R3与分层配置、P3-08b/c时间质量契约、Pipeline及quality summary。缺口15项已独立阅读，不用文件存在替代验收。
- 网络在所有AF_INET/AF_INET6 connect/connect_ex处阻断；外部IO使用内存替身，实际held配置解析逻辑保留。trace默认关闭。
- 全链原始JSON：`/private/tmp/g5-gap-nonauthor-final-20261014.json`；日志同前缀`.log`。
- 数值/身份/掩码/格点比较：`/private/tmp/g5-gap-nonauthor-final-comparison-20261014.json`，比较脚本`/private/tmp/g5_gap_compare_final_20261014.py`。
- 独立零值：`/private/tmp/g5-gap-zero-nonauthor-20261014.json`及`.log`；脚本`/private/tmp/g5_gap_zero_nonauthor_20261014.py`。
- 格点oracle：`/private/tmp/g5-gap-grid-oracle-20261014.json`及`.log`；脚本`/private/tmp/g5-gap-grid-oracle-20261014.py`。
- 定向检查：`/private/tmp/g5-gap-nonauthor-tests-r2-20261014.json`及`.log`；脚本`/private/tmp/g5_gap_nonauthor_tests_r2_20261014.py`。该证据七文件hash已与最终完整链严格核同，不拼不同候选。

复验命令（输出名需换成新的唯一文件名，不覆盖本报告证据）：

```sh
cd /private/tmp
G5_AUDIT_SOURCE_ROOT=/private/tmp/clpm-g5-followup-20261014 G5_AUDIT_OUTPUT_BASE=/private/tmp/g5-gap-nonauthor-newrun CLPM_G5_TRACE=0 PYTHONDONTWRITEBYTECODE=1 /Users/zhangping/DEV/CLPM-MVP-p1int/backend/.venv/bin/python /private/tmp/g5_v2_candidate_probe_20261014.py > /private/tmp/g5-gap-nonauthor-newrun.log 2>&1
```

## 保留的中间态与测试工具失败

- 最早候选完整链17/17+5/5，但manifest当轮更新，未认证为最终冻结候选；记录`g5-gap-nonauthor-candidate-20261014.*`保留。
- 00:37观测接线中间编辑态曾在builder装饰器处NameError（缺g5_trace import），`g5-gap-nonauthor-candidate-stable-20261014.log`保留；主协调补全接线后最终稳定小样通过。该中间态不算正式冻结候选失败，也不删去。
- 独立适配脚本增hash时曾误把str放入Path列表，报AttributeError；`g5-gap-nonauthor-baseline-full-20261014.log`保留。修正后baseline-full-r2和最终候选均hash稳定；这属于核查工具失误，不算产品失败。
- 首轮扩展测试248过/1失败，是核查runner全局替换了held配置解析函数，覆盖了用例自己的sys_config300s配置。更正runner为仅替换_metadata_execute外部IO，保留实际held函数；同一249项全过。首轮`g5-gap-nonauthor-tests-20261014.*`保留。没有修改测试预期或生产阈值。

## 下一步和未验项

主协调可把**此文件hash对象**标为“已实现/离线自验/非作者离线验收通过”；已合入、已推送、已部署、运行实证仍需各自证据。Keeper需实际部署固定候选、冻结held/coverage/anchors/gap/expiry与布局/缓存状态、生成v2r1新manifest并小样实投，再按完整1000分母与九条件同轮屏障测量。运行态热缓存/PG schema/进程版本不由本报告确认。

本次未检查全部1000输入、并发容量、实际持久化、重启/取消/迟到、连续两真实时窗、enabled观测路径、浏览器/读API或其余G5项。P6/P7保持NOT_STARTED，G5仍未获得正式容量通过证据。

## 正式12文件冻结复验（当前有效裁决）

**正式freeze的缺口语义与v2r1输入小样非作者离线复验 PASS。** 对象为 `/private/tmp/g5-candidate-source-freeze-20261014.json`，该文件SHA-256为 `727a7fea6a8840fa6d1f34807e6c7bf733bb8389648c33e58c0c5b33ff4e0db4`，内含12个源码/测试文件。开始import前即逐一匹配12个hash；完整小样、零分支小样、回归测试后hash再次逐一匹配，运行中稳定。

正式冻结与首版相比builder/trace已更新，因此重新运行，没有将首版结果拼入此版通过计数。当前builder hash=`7b593be73763c2d294a61fc8ed3128e9741227eaafd6078ddf820896d6c256be`，trace hash=`e00245edce71ca12d9f38252f7576589f8c7471bf1a297f97b58819f2624a684`；其余完整清单以formal freeze为准。Git HEAD仍`e07dcf8019ea0cb65cfbe1ea0f9c1e108bc6d252`+dirty，未提交/合入/推送/部署状态不从离线PASS推导。

- 冻结后完整KPI链：**17/17主小样+5/5反事实**，全部符合原独立预期。13非缺口persist/指标/base完全等价；22输入hash、原数值validity/bundle mask、configRevision、source和algorithmVersion等价；gap score=None/INCONCLUSIVE、coverage/有效率按独立格点oracle正确。
- 真实零有效率：**4版本/缺字段分支+FC/TC两例全UNKNOWN完整KPI PASS**，保留Good数值但有效率与落库参数均0；旧pre_v1/None/无字段仍回退旧内核。
- 必要回归：**255 passed / 2 warnings / exit0 / 1.34s**，包括原249项和当前6项trace单测。新增gap15项及零值/manifest/held实际配置分支均独立执行；socket attempts=0；源码及测试hash稳定。并未认证真实运行环境的enabled trace/TD资源及耗时。
- 独立1500格点+3边界oracle的series_context源码hash与本freeze完全一致，保留同内容的既有证据，无需重复相同oracle。

当前原始证据独立保存：

- `/private/tmp/g5-gap-nonauthor-frozen-20261014.json`及`.log`：22完整KPI例、正式freeze关联、12文件before/after hash。
- `/private/tmp/g5-gap-nonauthor-frozen-comparison-20261014.json`及`.log`：与基线逐项比较，严格核同本轮12文件测试/zero证据；脚本`/private/tmp/g5_gap_compare_frozen_20261014.py`。
- `/private/tmp/g5-gap-zero-nonauthor-frozen-20261014.json`及`.log`：零有效率分支与2完整KPI例；脚本`/private/tmp/g5_gap_zero_nonauthor_frozen_20261014.py`。
- `/private/tmp/g5-gap-nonauthor-tests-frozen-20261014.json`及`.log`：255项回归与前后hash；脚本`/private/tmp/g5_gap_nonauthor_tests_frozen_20261014.py`。

正式复验命令必须携带freeze路径，输出换成新的唯一名称：

```sh
cd /private/tmp
G5_AUDIT_SOURCE_ROOT=/private/tmp/clpm-g5-followup-20261014 G5_AUDIT_FREEZE_PATH=/private/tmp/g5-candidate-source-freeze-20261014.json G5_AUDIT_OUTPUT_BASE=/private/tmp/g5-gap-nonauthor-unique CLPM_G5_TRACE=0 PYTHONDONTWRITEBYTECODE=1 /Users/zhangping/DEV/CLPM-MVP-p1int/backend/.venv/bin/python /private/tmp/g5_v2_candidate_probe_20261014.py > /private/tmp/g5-gap-nonauthor-unique.log 2>&1
```

仍无Keeper回执或实际部署/生成/投递/连续两时窗/完整1000分母九条件屏障/600s容量证据；G5未获得正式运行通过证据，P6/P7仍NOT_STARTED。日志采集工具的另外2文件不在此12文件冻结内，由其Owner另行复核；本报告不扩大认证范围。

## 最终15文件冻结复验（当前有效裁决）

**最终15文件候选的缺口/KPI/v2r1离线复验及保护清单核查 PASS。** 对象为 `/private/tmp/g5-final-candidate-freeze-v2-20261014.json`，文件SHA-256=`683090161f4e5a52626f539640dc0f470567120c766223b201d8cde8f99c66f0`。本轮全部15文件在import前逐一匹配，在完整链、零分支、定向回归、保护清单独立核查后仍匹配；运行中hash稳定。旧12文件证据原样保留，未拼接当作此版通过计数。

- 同一输入：**17/17主小样+5/5反事实**；正常/坏质量/手动13例persist/指标/base完全等价，22例输入/mask/configRevision/source/algorithmVersion等价；缺口按同一预期INCONCLUSIVE+空评分，known/unknown格点比例正确。
- 零有效率：**4版本/缺字段分支+2全UNKNOWN真实Pipeline→完整KPI PASS**。pre_v2的真实0不变；pre_v1/None/无字段旧块仍回退；保留Good值、原数值validity不充有效样本。
- 必要回归：**277 passed / 2 warnings / exit0 / 1.45s**；覆盖gap、KPI零值、生成器/noisy/manifest、R14、held/配置、时间质量/预处理/摘要、trace取消竞态及collector、普通protected guard。socket attempts=0；源码和测试前后hash稳定。没有真实环境写入、服务启动、任务投递或commit/push。
- 15文件版本相对12版只在新增工具/保护golden、trace取消竞态计时和测试发生变化；核心缺口/KPI/生成器身份按manifest核同。完整原始内容以最终freeze为准。

### 受保护算法清单独立核查

独立读取e07dcf80的Git对象并逐文件算SHA：当前保护集合**41文件**，与e07的golden集合相同，无增删；实际文件hash与当前golden逐项相同。**唯一与e07源码不同的是已授权 `app/services/preprocessing/pipeline.py`**，差异仅为UNKNOWN核心覆盖参与可信有效率、合法HELD/legacy保留、pre_v2缓存身份和角色筛选helper。其余40个受保护文件（指标计算器、辨识/整定主体、质量映射、采样/异常阈值、数据结构等）逐字节与e07一致。

golden自身diff只改变baseline_head为e07全SHA及pipeline该项hash；保护集合、registry和默认guard测试源码均与e07逐字节一致。以普通模式（`REFACTOR_UPDATE_GOLDEN`明确unset）调用guard：当前清单PASS；临时换成e07旧golden即确定失败，错误只含pipeline `32d468da242f→233d293df09c`。旧临时golden未写改，当前golden未由本核查更新。因此这是现有授权UPDATE流程的登记，守卫逻辑及范围没有放松。

`.github`、lefthook配置、backend/pyproject、guard/registry无diff；`core.hooksPath`未设置，本核查环境的LEFTHOOK/LEFTHOOK_EXCLUDE/HUSKY/SKIP/GIT_CONFIG_COUNT均unset。本代理没有执行git提交、推送或hook变更；未发现本候选的hook旁路。该核查不替代日后正常commit/pre-push门禁。

原全量日志 `/private/tmp/g5-full-offline-final-20261014.log` 已独立抽查：唯一失败是该protected guard，仅pipeline hash不同；原记录为**1 failed / 6699 passed / 340 skipped / 129 deselected / 12 xfailed / 4 xpassed / 368 warnings，PYTEST_EXIT=1**，305网络尝试被阻断、实际IP连接0。该原始全量仍是FAIL记录，未改为全绿；本轮277定向和普通guardPASS作为追加证据，未宣称已对最终15文件再跑完整全量。SKIP/xfail/xpass等未抹除或算进PASS。

### 最终独立证据

- `/private/tmp/g5-gap-nonauthor-final15-20261014.json`及`.log`：22完整链与15文件before/after hash。
- `/private/tmp/g5-gap-nonauthor-final15-comparison-20261014.json`及`.log`：与v2基线逐项数值/身份/掩码/格点对照，核同本轮zero/tests源码hash；脚本`/private/tmp/g5_gap_compare_final15_20261014.py`。
- `/private/tmp/g5-gap-zero-nonauthor-final15-20261014.json`及`.log`：独立零分支/2完整KPI；脚本`/private/tmp/g5_gap_zero_nonauthor_final15_20261014.py`。
- `/private/tmp/g5-gap-nonauthor-tests-final15-20261014.json`及`.log`：277项；脚本`/private/tmp/g5_gap_nonauthor_tests_final15_20261014.py`。
- `/private/tmp/g5-guard-nonauthor-final15-20261014.json`及`.log`：41保护文件逐项base/current hash、集合、普通guard红/绿反证、无UPDATE/旁路环境快照；脚本`/private/tmp/g5_guard_nonauthor_final15_20261014.py`。

复验使用同一候选脚本，必须将 `G5_AUDIT_FREEZE_PATH` 指向最终15文件freeze，并另取唯一输出名。候选仍为e07基线上的未提交dirty文件内容；本报告不认证已合入、推送、部署或600s。实际配置/机器/负载/缓存、真实任务分母与九条件屏障仍由Keeper出回执，G5未正式通过，P6/P7保持NOT_STARTED。
