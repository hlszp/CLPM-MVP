# 非作者只读工具交接（真实输入未冻结）

本目录工具只读取磁盘 artifact，不连接数据库、Redis、TD 或 API，不投递业务、不启停服务、不导入产品模块。生成的期望/报告以 `open('x')` 新建，拒绝覆盖历史文件。运行时输入仍未冻结，**当前没有真实容量或真实指标判定**。

- `verify_capacity_series.py`：独立重算 20 批/1000对象、九条件、原始record/latest/node输入版本、父子TaskRecord/Celery、同轮屏障、600s、资源与历史6+3；不调用产品 `_evaluate_run_gate`。
- `independent_kpi_oracle.py`：先 `compute` 只读输入/配置，独立生成三指标期望；再单独 `compare` 读取实际指标。无产品 calculator / replay CLI。NPZ依赖使用已有backend venv的NumPy，`allow_pickle=False`。
- `independent_support.py`：受限证据根、SHA/size、前后字节不变、严格时间和不可覆盖输出。
- `test_independent_tools.py`：工具自己的数学和合成 artifact 自验，不是CLPM业务用例或真实20批。日志 `tool-selftest-v3.*`：**20 tests / 0失败 / rc=0**，其中完整20×1000的纯磁盘合成形状对照，以及漏行/重复/错run/错latest窗/子任务未终结/旧Worker/错节点input record等反例被拒绝；NPZ适配有非100闭式解对照。`tool-ruff-v3.*` rc=0。

## 1. 容量工具输入和适配纪律

先保全 `f02/前置诊断与容量执行方案.md` 规定的逐轮完整原件；不要为了满足工具而修改原PG导出、benchmark冻结件、manifest或原始退出码。可在单独目录做无损字段名适配/拼装 sidecar，附每个原文件SHA和字段映射；**不从report gate布尔值生成“事实”**。正式采集 JSONL 可以转换成 JSON数组；只转封装，不改记录内容。尚未提供的字段必须缺失/NULL并明确BLOCKED，不能造值。

所有引用采用 `{"path":"证据根内路径","sha256":"原字节64hex","size":可选整数}`。`--evidence-root` 建议指定本执行包 `执行状态/`，使本轮与历史九批都在明确的只读根内；越界/symlink逃逸、hash不符拒绝。引用每次实际读取后会重算一次，结束时再次核字节未变。

顶层 `series.json`：

```json
{
  "seriesId": "投前冻结的完整series ID",
  "candidateSha": "完整40hex",
  "configDigest": "投前固定算法/配置digest",
  "coldHotDefinitions": {"path":"...","sha256":"..."},
  "resourceProfile": {"path":"...","sha256":"..."},
  "history": {"path":"...","sha256":"..."},
  "batches": ["下面定义的20个对象；A/B各COLD/HOT至少5个"]
}
```

`history` 内容为 `{"selectedReports":[九个原report的ref]}`，实际读取原 `completionReason/gate.denominators.frozenEligible` 验证6+3/每批1000，不能用新写摘要取代原report。`resourceProfile` 至少 `{"tdVariables":{"queryUseMemoryPool":0,"minReservedMemorySize":1024},"rawRefs":[ref...]}`；实际值由Keeper投前冻结，工具不假设0或1024已经生效。每批实际 dnode 原响应逐项比较。内存池测试profile边界见同目录独立审阅；它不是第五项豁免。

单批对象：

```json
{
  "id":"batch-01", "scenario":"A", "cacheClass":"COLD",
  "manifest":{"path":"...","sha256":"..."},
  "freeze":{"path":"...","sha256":"..."},
  "nodeScopes":{"path":"...","sha256":"..."},
  "witness":{"path":"...","sha256":"..."},
  "resources":{"path":"...","sha256":"..."},
  "command":{"path":"...","sha256":"..."},
  "pollLog":{"path":"...","sha256":"..."},
  "cacheVector":{
    "input":{"state":"真实状态","definition":"投前定义及边界","rawRefs":["ref"]},
    "precompute":{"state":"真实状态","definition":"投前定义及边界","rawRefs":["ref"]},
    "redis":{"state":"真实状态","definition":"投前定义及边界","rawRefs":["ref"]},
    "process":{"state":"真实状态","definition":"投前定义及边界","rawRefs":["ref"]}
  },
  "rawRefs":["原始SQL/观测及归属资料ref"],
  "rawLogRefs":["本批冻结的真实worker/API日志片段ref"]
}
```

`manifest/freeze` 保持产品原形：1000 rows及其 loopTag/composition/expectedStatus，freeze.frozenAt/window/manifest.sha256/algorithmVersion/eligible/excluded/predictedNodes。逐对象需要 loopId、loopTag、expectedStatus、expectedSnapshotStatus；excluded另需投前原因。A normal1000；B normal800+insufficient100+manual50+gap50。新库预定1000全部eligible，manual仍在分母，不能投后改预期。

`nodeScopes` 是**另建的投前真实后代查询sidecar**，不改原freeze：`{"frozenAt":"UTC","complexGroupPolicy":"NONE","scopes":{"真实nodeId":["后代eligible loopId"]},"rawRefs":[原始树/对象/complex group查询ref]}`。本容量集应真实没有复杂回路组；若有组，当前工具不猜代表对象规则，先BLOCKED并按冻结组合同扩适配，不用错误全量期待裁掉真实去重。scope keys严格对应predictedNodes。

`witness` 应代表**一个完整DB只读一致性事务与其关联的任务观测**：

| 顶层字段 | 内容 |
|---|---|
| submittedAt / observedAt | 原始真实UTC投递、完整见证时间，支持显式offset；naive仅按投前UTC约定；不能用各条件firstMet拼时间 |
| mode | `standard-hourly` |
| transaction / readOnlyConfirmed | `REPEATABLE READ READ ONLY` / `on`，有原始SQL和事务证据 |
| parentTaskId | 真Celery标准小时父ID |
| runtime | 见下文，不只checkout HEAD |
| barrier | 原始 ledger_rows/latest_rows/node_rows/record_rows 完整数组 |
| celeryTasks | 父与全部必要子任务实际请求/终态数组 |
| taskRecord | 真实id/status/celeryTaskId，id与所有ledger.sourceTaskId一致 |

`runtime.members` 包含 `role=api/worker_master/beat/worker_child`、唯一identity、实际loadedSha、startedAt、argv；API/master/Beat各唯一。另含schemaRevision、configDigest、postgresDatabase、brokerNamespace；`tdDnodeVariables` 为实际 `SHOW DNODE VARIABLES` 原响应ref（直接response或`{"response":...}`），业务code必须0，逐项比资源profile。PID工作目录/环境/实际加载字节的证据需人工核，脚本不把`loadedSha`声明自身当可信事实。

数据库数组字段对应：

| 数组 | 规范字段（必须从真实列/关联取，不补虚构关联） |
|---|---|
| ledger_rows | loopId、tsStart/tsEnd、status、score（真实数值/NULL）、source、sourceTaskId、resultRecordId、recordLogicalRunId、algorithmVersion、fitnessTags；不能只从WHERE参数填窗口，导实际时间列 |
| latest_rows | loopId、实际data_ts_start/data_ts_end映射tsStart/tsEnd、algorithmVersion、resultRecordId、recordLogicalRunId |
| node_rows | plantNodeId、实际tsStart/tsEnd、algorithmVersion、resultRecordId；assessment_stats/score等原始附加字段全部保全 |
| record_rows | 包括所有LOOP及NODE不可变record：id映射recordId、object_kind→objectKind、object_id→objectId、logical_run_id→logicalRunId、实际tsStart/tsEnd、status、algorithmVersion、完整payload |

NODE真实保存路径 `node_performance.save_node_snapshot` 的payload包含 `loopRecordIds=[{loopId,recordId}]`（实际SUCCESS代表对象），`assessment_stats.roster.members/states`（全部eligible六态）。工具直接核真实输入版本、全scope和六态守恒；**NODE logicalRunId为内容寻址，不能强行等父Celery UUID**。B组INCONCLUSIVE对象保留在roster，不能要求全部进入SUCCESS聚合input。若节点 record丢失，不能靠node行count凑通过。

`celeryTasks` 每条 id/name/state/runtimeSha/finishedAt/rawRefs；节点子任务另含parentId/rootId/nodeIds（来自实际request/原始日志scope）。追parent链到标准小时父并核真实终态；不能从“队列空”推成功。原始任务证据JSONL和日志仍需人工交叉核。`command` 含真实argv/start/end/rc与stdout/stderr的ref；`pollLog` 为原观测数组，接受见证项带原observedAt及该witness原字节 `witnessSha256`，不接受只有firstMet。

`resources` 为独立采样封装：memoryBudgetBytes、scopeIdentities、samples、events、rawRefs。sample含at、processes（identity/rssBytes，不重复计算同进程/容器）、workerMasters=1、beats=1；events每项kind/message/rawRefs，实际采样无事件时可空数组但原始日志不能缺。投递前≥60s、屏障后≥30s、间隔≤5s，逐sample RSS和≤80%冻结预算。原始rawLogRefs另逐行扫描资源耗尽签名，防只删events摘要变绿。CPU/IO/连接/VM可用内存和原生OOM状态等全量轨迹仍在人工回执核，不由RSS脚本替代。

工具输出 `artifactContractVerdict` 而非自动最终验收，保留每批 `manualChecksPending`。四维缓存真正命中/冷态定义、源码/加载进程、节点树scope真实性、异常/取消/最终FAILED持久化、资源全域、至少一组独立业务复跑均要非作者另外核；不能拿工具PASS扩签D04。

## 2. F03 oracle 输入与实际比较分离

`oracle-plan.json` 的字段：

```json
{
  "candidateSha":"完整40hex",
  "identity":{
    "assessmentProfile":"LEGACY_V2或CANDIDATE_V3_RC1",
    "algorithmVersion":"冻结值", "configRevision":"冻结值", "executableBuildDigest":"冻结值"
  },
  "effectiveParamsSha256":"投前参数map规范JSON的完整SHA256",
  "inclusiveEndSeconds":1,
  "heldLimitsSeconds":{"pv":60,"sp":7200,"mode":7200},
  "fixture":{"path":"真实critical-fixture.json","sha256":"..."},
  "rawInput":{"path":"真实critical-fixture.inputs.json","sha256":"..."},
  "datasetManifest":{"path":"真实dataset UUID.manifest.json","sha256":"..."},
  "datasetNpz":{"path":"真实dataset UUID.npz","sha256":"..."},
  "declaredDatasetInputHash":"实际dataset元数据input_hash；可先缺失，但此时不得声称已核DBinputHash"
}
```

上列held默认数仅为字段形状说明，实际以投前冻结的有效策略为准，不凭示例当已验配置。`effectiveParamsSha256` 按 `json.dumps(params,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)` 计算；params形态为 `metric|controlType→展开参数`。任务包source.effectiveParams必须PINNED并严格匹配投前值；缺参数无默认静默回退。

真实输入读取作者现有的完整七角色samples和points SHA、固定fixture.candidateSha，保存包中的window/engineering/replayContext/source/blocks/arrays。NPZ实际dtype/shape与manifest逐一核，再独立按内容哈希格式计算dataset inputHash；不调用任何产品helper。

当前适配器有意只支持本轮FLOW1s/量程0..100、单绑定无gap的隔离合成输入。独立从原始COV样本保持值对比NPZ PV/SP/MODE/QC，按冻结held策略核known，独立求mask表达式（空mask按合同全保留）。steady以包中实际保存的preprocessing validity为输入事实，独立重算SP暂态、无偏σ、IAE零穿越/相似率/半周期/幅度门控与指数或带内率。**这不声称重新实现全部预处理分类器，也不证明其它网格/原始工业数据现场适用性。** 不支持或缺资料时报BLOCKED，不从实际输出倒调。

先运行compute：代码路径没有实际快照/指标参数，输出readActualMetrics=false、全部readSet、具体期望及σ/分子分母/剔除/Osc中间值；该输出sha落盘后，才另运行compare读取spec附件snapshot.json。数值比较不代签任务/权限/血缘/CI。

## 3. 命令和退出码

```sh
CLPM_AUDIT_PY=/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python
CLPM_AUDIT_E=/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z
"$CLPM_AUDIT_PY" "$CLPM_AUDIT_E/independent/verify_capacity_series.py" --series <冻结series.json> --evidence-root <执行状态绝对路径> --out <新的复算输出.json>
"$CLPM_AUDIT_PY" "$CLPM_AUDIT_E/independent/independent_kpi_oracle.py" compute --plan <冻结oracle-plan.json> --evidence-root <artifact绝对目录> --out <新的期望输出.json>
# 独立期望输出与SHA保存后，再运行：
"$CLPM_AUDIT_PY" "$CLPM_AUDIT_E/independent/independent_kpi_oracle.py" compare --expected <前述固定期望.json> --actual <真实snapshot附件.json> --storage rounded2dp --out <新的比较输出.json>
```

真实rc：0=该工具实际可核范围PASS，1=反证REWORK，2=缺资料/不支持BLOCKED。未知参数、文件不符、不可读输入不能转0。输出已存在则拒绝覆盖；换有明确身份的新文件，不删旧失败重跑。

**当前就绪状态仅为方法/工具准备完成。** 等Keeper提供最终SHA/20批原件/真实四例附件/专属环境授权，再执行真实独立复验，逐项保留PASS/REWORK/BLOCKED。没有实际授权前，本工具不提供投递/故障注入/重启命令，也不触碰任何产品环境。
