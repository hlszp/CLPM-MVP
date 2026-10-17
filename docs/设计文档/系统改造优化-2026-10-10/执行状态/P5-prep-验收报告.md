# P5 批 α 非作者验收报告（聚合收窄 + 容量准备）

- **验收对象**：worktree `/Users/zhangping/DEV/CLPM-MVP-p5prep`，分支 `feat/p5-prep-capacity`，基线 `5ec4e284`（feat/p1-integration）
- **diff hash 复核**：`git diff HEAD | shasum` = `495c662f94dddf78c9b7e585f5ec9ab0292835f8`（与交接一致）；8 已跟踪文件 +771/−140 + 4 未跟踪新文件，改动集与交接 §4 文件清单一字不差
- **验收者**：非作者验收智能体（05 号文 §5 协议；未参与实施）
- **验收日期**：2026-10-13
- **总体结论**：**PASS**（附 2 条登记项：交接自报测试数高报、空集 hash 理论边界；均不构成 REWORK）

---

## A. 聚合收窄两处 — 通过

### A1. 服务参数与 NULL-unit 边界语义（重点项）

`backend/app/services/cockpit_overview.py`：

- `build_overview(db, window, unit_ids=None)`（L410）与 `_build_overview`（L438）：`unit_ids=None`=ALL，非 None=范围节点集
- `_active_loop_ids(unit_ids)`（L251-259）：`unit_ids is not None` 时加 `LoopLedger.unit_id.in_(unit_ids)` 谓词。SQL `NULL IN (...)` 恒不匹配 → **NULL-unit 回路在任何范围口径下被排除，仅 ALL（无谓词）可见**——与 `ScopeContext.node_in_scope`（authz_scope.py L136-138：`node_id None → 仅 allowed_node_ids is None 时 True`）语义一致 ✓
- 收窄透传全量核对：loopTotal（L455-461）、环比加权均值（L469-470，`_query_window_weighted_avg` 加 `node_id.in_`）、GLOBAL 预计算行仅 ALL 消费（L477 `if unit_ids is None`）、收窄 score/autoRate=范围内 unit_kpi_summary 加权（L491-495，与 delta 同源同标度）、等级分布 `plant_node_ids=unit_ids` 逐窗透传（L501/L508）、待办（L521）、预警（L529）、漏斗（L537）、积压（L542）——交接声称的七块全部落实
- `node_performance.py get_nodes_overview(db, start, end, unit_ids=None)`（L1379-1397）：`PlantNode.id.in_(unit_ids)` 节点级收窄 ✓

**NULL-unit 边界真实库实证**（见 A4）：ALL loopTotal=1030 含 1 个 NULL-unit 回路（REF-XIC-901），三个范围口径（29/50/9）均不含它。

### A2. 3 消费端点传参

| 端点 | 传参代码 | 判定 |
|---|---|---|
| `cockpit.py /overview` L48 | `None if scope.is_all else sorted(scope.allowed_node_ids)` | ✓ |
| `node_performance.py /nodes/overview` L300 | 同款 | ✓ |
| `workbench.py /workbench/overview` L129-140（P4-03 先例） | `sorted(...) if allowed else None`（空集当 ALL） | 先例比对完成 |

与先例的语义分叉点：本次两端点把**空集当严格空集**（查询返回 0），先例把空集当 ALL。可达性分析：`_expand_subtree`（authz_scope.py L285-287）返回 `frozenset({node_id, *descendants})` **恒含绑定节点自身 → NODE_SUBTREE 用户 allowed_node_ids 恒非空**，两语义在可达域内等价（详见登记项 2）。

### A3. 缓存 key 范围隔离与 TTL 语义

- 新 key：`cockpit-overview:{window}:{_unit_ids_hash(unit_ids)}:{policyRevision}`（L430）；旧 key 仅 `cockpit-overview:{window}`（diff 确认）——key 空间完全不相交，**旧 key 无需清理**
- **TTL 语义真实存在**（交接声称"旧 key 按 TTL 自然失效"核验成立）：agg_cache.py 双层——进程内 `monotonic() - hit[0] < ttl`（L69-70）+ Redis `setex(redis_key, int(ttl)+5, ...)`（L84-90），cockpit ttl=240 → 旧 key 最长 245s 自然过期，跨范围不共缓存（单测三 key 互异 + 见登记项 2 边界）

### A4. 越权反例 — 单测复跑 + 真实库端到端复跑（交接自认 mock 级，本节为真库补验）

单测独立复跑：`CLPM_TEST_MODE=1 uv run pytest tests/test_cockpit.py tests/test_node_performance.py -q` → **59 passed**（含 P5 新增收窄用例，实测 6 例：TestOverviewScopeNarrowing 4 + TestP5NodesOverviewScopeNarrowing 2，断言覆盖 SQL 谓词/loopTotal 行为捕获/GLOBAL 行零调用/缓存三 key 互异/等级分布透传/域外节点缺席）。

真实库复跑（脚本 `/tmp/verify_scope_narrowing.py`，直调 `_build_overview` 绕过 agg_cache——**零 Redis 写**；`get_nodes_overview` 直查；连 dev 库 17102 全程只读 SELECT；未启动 uvicorn/lifespan）：

| 口径 | loopTotal | 独立 SQL 基线 | 判定 |
|---|---|---|---|
| ALL（unit_ids=None） | 1030 | 活跃回路总数=1030（含 1 个 NULL-unit） | ✓ |
| NODE_SUBTREE=EO 工厂子树（真实装置树闭包 6 节点，经 `collect_descendant_node_ids` 生产同款展开） | 29 | 递归 CTE 独立复算=29 | ✓ |
| NODE_SUBTREE=PERF-T-AREA1-U1 | 50 | 该 unit 活跃=50 | ✓ |
| NODE_SUBTREE=精馏塔单元 | 9 | 该 unit 活跃=9 | ✓ |

- **范围口径数字不含域外装置回路**：EO 范围 29 vs 全厂 1030（1000 PERF 合成回路一个不进）；PERF-U1 范围 50 vs 其余 980；精馏塔 9。**越权反例在真实库成立**。
- **NULL-unit 仅 ALL 可见**：REF-XIC-901（unit=NULL）计入 ALL 的 1030，不计入任何范围口径。
- nodes overview 两口径：ALL totalNodes=26（含 PERF-T 节点）；EO 范围 totalNodes=6 且**零 PERF-T 节点**（域外装置不暴露）；PERF-U1 范围 1 节点全 PERF-T。
- score 口径：ALL=84.634（GLOBAL 预计算行）；EO 范围=84.63（范围内加权，与全厂接近因真实回路集中在 EO 侧）；PERF-U1 范围=None（范围内无 unit_kpi_summary 数据，诚实 None 非 0）。
- 说明：验收脚本首跑一条"EO=30"断言失败，经递归 CTE 独立复算确认 **EO 子树真实值=29**（醛化反应单元 4 回路属致联工厂子树不在 EO 闭包），系验收者预期值设定错误，被验实现与独立 SQL 完全一致，非缺陷。

---

## B. 容量工具修正 — 通过

`backend/scripts/kpi_capacity_benchmark.py`（全量重写）五缺陷逐项核验：

| 缺陷 | 修复证据 | 判定 |
|---|---|---|
| #1 `size` 被静默忽略 | `_load_loops`（L166-201）：参数名 `pageSize`、`max(1, min(page_size, 100))`、逐页拉全量+跨页去重+total 提前停+空页停；交叉核对 API 侧 `pageSize: Query(20, ge=1, le=100)`（loops.py L122）→ 参数名与 le=100 封顶全部对上 | ✓ |
| #2 默认端口 7101 | `DEFAULT_BASE_URL="http://localhost:17101/api/v1"`（L52）；`perf_test_attention_summary.py` BASE_URL 同步 17101（diff 见）；`benchmark_kpi_optimization.py` grep 无任何 localhost/端口/httpx——进程内基准登记属实 | ✓ |
| #3 少量 ID 重放凑数 | standard-hourly 前置 `_preflight_eligible` 三条件（is_active+READY+include_in_evaluation，DB 权威）≠ `--expect-eligible` 即 `SystemExit("…禁止重放凑数")`（L378-383）+ eligible 去重断言（L376）；custom 重放需显式 `--allow-replay`（L502-508），报告标注 unique_loops/replay 并声明"不计 standard-hourly 600s SLO" | ✓ |
| #4 standard-hourly Celery 入口+DB 完成屏障 | 投递 `calculate_hourly_kpi.delay(ts_start=iso)`（L410）——任务签名 `(self, ts_start=None, task_id=None)`（kpi_calc.py L154）匹配、不传 task_id → source=SCHEDULED ∈ `_ALLOWED_SOURCES={SCHEDULED, MANUAL_STANDARD}` 与 kpi_calc 写入口径（L198/L356）一致；屏障三条件全部 DB 回查（小时账本行数+source 断言 / loop_confidence_latest.data_ts_start / 递归 CTE 预定节点→kpi_node_snapshot_hourly），**父任务终态不作屏障**（L442 注释+实现）；子任务 ID `celery inspect active` 尽力取证、错过登记 not-observed（L455）；各条件首达时刻单列（first_met） | ✓ |
| #5 dry-run 零投递零写入 | dry-run 分支（L392-401）位于 submit 之前 return，代码路径无 delay/无写；单测 `submit_spy.delay.assert_not_called()` | ✓ |

**dry-run 真实实跑**（冻结窗 2026-10-12T20:00、`--barrier-loop-prefix PERF- --expect-eligible 1000`，只读）：

```
eligible(is_active+READY+include_in_evaluation): 1000
独立 loopId 断言: 1000 unique = expect ✓
投递前既有同窗行: ledger=0 latest=0 node=6
[dry-run] preflight 通过，standard-hourly 就绪     EXIT=0
```

零投递零写入达成，且顺带实证屏障 SQL（`ANY(:ids)` 绑定/递归 CTE）在真实 dev 库可用；node=6 为真实回路既有节点行（PERF 集未投递），与交接 §1.4 自报一致。

**单测独立复跑**：`uv run pytest tests/test_kpi_capacity_benchmark.py tests/test_generate_capacity_dataset.py -q` → **28 passed**（benchmark 11 例 + 生成器 17 例），11 例清单与交接描述逐项吻合（pageSize 翻页/total 停/去重/整点校验/三条件拒绝/dry-run 零投递/屏障常量/replay 门禁）。

---

## C. 1000 独立回路数据生成独立核验 — 通过

（全部独立 SQL/taos 复算，不采信实施者自检输出）

### C1. PG 台账（docker exec clpm-mvp-postgres psql -U clpm -d clpm）

| 断言 | 结果 |
|---|---|
| PERF- 前缀回路 | 1000 |
| 三条件 eligible（is_active+READY+include_in_evaluation） | **1000**（perf_not_active=0、perf_not_include=0） |
| PERF-T 装置树 | 26 节点（1 FACTORY+5 AREA+20 UNIT，name LIKE 'PERF-T%'），回路分布于 20 UNIT |
| 合成声明 | `sys_config:capacity.perf_dataset.declaration` 在案：synthetic=true、mark、prefix=PERF-T、totalLoops=1000、scenario=b、composition 800/100/50/50、windowStart=2026-10-12T20:00:00、manifestSha256、redline、generatedBy 全字段 |
| manifest sha256 | `shasum -a 256 manifest_b_2026101220.json` = `e2a91f3dc931bbad2488ffcebf6db596e7d9ea8d2daf6d4406a6cf8be0d603a2` == declaration.manifestSha256 **完全一致** |
| remark 标注 | 1000/1000 回路 remark 含 synthetic 标注 |
| 创建时刻 | 1000 回路同一时刻 2026-10-12 22:51:37（批量 import 特征） |

### C2. TDengine 抽样（≥5 回路，四构成类别覆盖；point_id 用 PG tag_registry.id UUID → 子表 `p_<hex>`，规避按位号查恒 0 陷阱）

| 回路（构成） | 子表行数实测 | manifest 预期 |
|---|---|---|
| PERF-T0001（normal/steady） | PV=3600、OP=3600 | 3600 ✓ |
| PERF-T0007（normal/oscillation） | PV=3600 | 3600 ✓ |
| PERF-T0801（insufficient） | PV=3600、OP=3600 | 3600 ✓（行数满额，70% BAD 为质量码层面） |
| PERF-T0901（manual） | PV=3600、OP=3600、MODE 1 行 value=**0** | 3600 + MODE=0 ✓ |
| PERF-T0951（gap） | PV=**900** | 缺口截断 900 ✓（前 15min） |

- 窗口实证：T0801_OP ts 范围 04:00:00.000~04:59:59.000（taos CLI 本地 +8 展示）= UTC 2026-10-12 20:00~20:59:59 冻结窗 ✓
- 总账自洽：tdWritten PV=3,465,000 = 950×3600 + 50×900 ✓、总计 6,936,900 ✓
- 备注（非缺陷）：manifest `pointsPerTag` 对 gap 类记录名义窗口值 3600，实际写入 900（实际写入以 `tdWritten` 汇总为权威）；γ 批对账时留意该字段语义。

### C3. 50 手动回路全量核验

50 个 manual 回路（PERF-T0901~T0950）MODE 子表逐个 `FIRST(value)`：**50/50 值=0**，无 NULL、无缺表、无非零值；三条件 eligible 已含于全量 1000 → "MODE=0 且 eligible 内终态"（DEC-08a）落实 ✓。

---

## D. 门禁复核（真实退出码）

| 项 | 命令 | 结果 |
|---|---|---|
| ruff check | `uv run ruff check .` | **exit 0**（All checks passed!） |
| ruff format | `uv run ruff format --check .` | **exit 0**（874 files already formatted） |
| alembic heads | `uv run alembic heads` | **单头 `p403scope01`，exit 0**（α 零迁移零模型改动，git status 无 migrations/models 文件） |
| alembic check | `uv run alembic check` | `Can't locate revision 'p502attm01'` FAILED——共享 dev 库被并行批 β 置版所致（α 树 5ec4e284 无此 revision），与交接 §3-① 登记完全一致，**环境共享现象非 α 缺陷**，合并时主协调仲裁 |
| pytest 全量 | `uv run pytest -q --tb=no` | **exit 0：6339 passed / 335 skipped / 129 deselected / 12 xfailed / 4 xpassed（0 failed），299.68s**——分母 6339 ≥6339 ✓，与交接自报逐项一致（自报 302s） |

---

## E. 红线核查 — 通过

1. **生成器未停用/关参评**：PG 实证 `perf_not_active=0`、`perf_not_include=0`（1000 回路两标志全真）；代码侧 `generate_capacity_dataset.py` 中 is_active/include_in_evaluation 仅出现于读取/断言（L732/752/813 self-check），grep 无任何对回路表的 UPDATE/patch 写路径——代码与数据互证 ✓
2. **30 真实回路零污染**：非 PERF- 活跃回路=30（25 原始真实 + 5 个 REF-\* 为 2026-10-11 14:51-52 前批遗留、早于 α 生成时刻 10-12 22:51，非 α 产物）；**created_at > 2026-10-12 20:00 的非 PERF- 新回路 = 0**——α 生成期间对既有回路零触碰；任务口径"生成前=30 生成后=30"成立 ✓
3. **diff 不含诊断/整定专属文件**：8 改（cockpit/node_performance 服务与端点、容量两脚本、两测试文件）+4 新（生成器、两测试、manifest）逐个核对，无诊断/整定前后端文件删改 ✓
4. **验收过程纪律**：未启动 uvicorn/Celery/Worker/Beat（未触发 lifespan）；对 dev 库/TDengine 全部只读（SELECT/COUNT/FIRST）；真库反例绕过 agg_cache 零 Redis 写；未 commit/push、未改 p5prep/p502 任何代码（验收脚本在 /tmp）

---

## 登记项（不构成 REWORK）

1. **交接自报测试数高报**：交接 §1.1 称收窄反例"共 8 例全过"，实测新增用例 **6 例**（test_cockpit.py TestOverviewScopeNarrowing 4 例 + test_node_performance.py TestP5NodesOverviewScopeNarrowing 2 例，grep 精确计数）。6 例覆盖面（SQL 谓词/行为/缓存 key）与交接描述的行为等价、全部通过，实质无缺口；自报数字不实，按诚实化原则登记。
2. **空集范围 hash 理论边界**：`_unit_ids_hash([])` 返回 "all" 与 None 同 key（cockpit_overview.py L161-163，workbench 先例同构）。若未来 NODE_SUBTREE 用户 `allowed_node_ids` 出现空集，范围口径查询严格为 0（正确）但**缓存 key 与 ALL 相同**——ALL 先算则空范围用户命中全厂数字。当前可达性：`_expand_subtree` 恒含绑定节点自身（authz_scope.py L285-287），allowed_node_ids 恒非空 → **不可达**。建议后续批（P5 收尾或重构时）将空集 hash 与 None 区分（如空集 hash 为 "empty"），或端点层把空集短路为显式无权限响应。

## 未验边界（如实登记）

- standard-hourly 真实投递+屏障计时+SLO：归批 γ（任务书【4】明确本批不跑正式五批测量）
- scenario a（全正常窗）数据生成：交接已声明未生成，待 γ 需要时报 keeper
- 性能数字（冷/热×5、CPU/RSS/IO/队列）：归批 γ
- 交接 §3-3 的 γ 批测量纪律（SETNX 锁 TTL 15000s、DEC-09 迟到窗 latest 不回写→`--barrier-loop-prefix PERF-` 单列对账）为操作时注意事项，本批仅核工具支持已内置（参数与屏障逻辑在案）

## 结论

批 α 四项范围（聚合收窄两处/容量工具五缺陷/1000 回路生成+准备就绪自检/门禁）全部核验通过，**PASS**。两条登记项（自报测试数 8→实测 6、空集 hash 理论边界）移交主协调知悉，不阻断批 α 晋级；alembic check 共享库现象按交接 §3-① 既定口径留待合并仲裁。
