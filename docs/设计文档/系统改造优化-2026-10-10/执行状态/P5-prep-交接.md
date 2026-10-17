# P5 批 α 交接（前置收窄 + 容量准备）

- **worktree**：`/Users/zhangping/DEV/CLPM-MVP-p5prep`，分支 `feat/p5-prep-capacity`，基线 `5ec4e284`（feat/p1-integration），未提交 diff（不 commit/push）
- **diff hash**：`git diff HEAD | shasum` = `495c662f94dddf78c9b7e585f5ec9ab0292835f8`（8 已跟踪文件 +771/−140；4 未跟踪新文件：generate_capacity_dataset.py / 2 测试文件 / capacity_dataset 目录）
- **实施者**：P5 批 α 执行智能体（goal 模式）；**状态：待非作者验收**

## 1 范围与产出（任务书批 α 四项）

### 1.1 聚合收窄两处（P4-03 验收登记立卡项）✓

- `app/services/cockpit_overview.py`：`build_overview(db, window, unit_ids=None)`——各统计块（loopTotal/环比加权/等级分布/待办/预警/漏斗/积压）按 `loop_ledger.unit_id ∈ 范围集` 收窄（NULL unit 仅 ALL 可见，与 ScopeContext.node_in_scope 同语义）；**收窄口径下 GLOBAL 预计算行不被消费**（score/autoRate 改取范围内 unit_kpi_summary 当前窗加权均值，与 delta 同源同标度）；缓存 key 升级 `cockpit-overview:{window}:{unit_hash}:{policyRevision}`（原 key 只有 window——跨范围共缓存即泄漏，必须含范围 hash；旧 key 按 TTL 自然失效无需清）
- `app/services/node_performance.py`：`get_nodes_overview(db, start, end, unit_ids=None)`——is_kpi_enabled 节点按范围集过滤
- 消费方传参 3 端点：`cockpit.py /overview` 与 `node_performance.py /nodes/overview` 新接 `get_scope_ctx` 传 `sorted(allowed_node_ids)`（NODE_SUBTREE）/None（ALL）；第三端点 `workbench.py /workbench/overview` 为 P4-03 既有先例（同款 unit_ids 注入，本次核验在案）
- **越权反例结果**（tests/test_cockpit.py TestOverviewScopeNarrowing + tests/test_node_performance.py TestP5NodesOverviewScopeNarrowing，共 8 例全过）：
  - SQL 级：`_active_loop_ids(["uA1","uA2"])` 编译含 `loop_ledger.unit_id IN`（ALL 口径无此谓词）；loopTotal 计数语句同款收窄；nodes overview 节点查询含 `plant_node.id IN`
  - 行为级：范围口径下 `_query_global_summary_row` **零调用**（全厂行不进范围数字，score=61.0 来自范围内加权而非全厂 99.0）；等级分布 `plant_node_ids=范围集` 逐窗透传；域外节点 node-B 不出现在总览 nodes/状态分布；缓存 key ALL/范围A/范围B 三 key 互异（hash 段不同）
  - 注：mock 级反例（SQL 谓词+数据流）；真实 PG 端到端反例留验收者按 05 号文协议复跑（C25 集成栈可参照）

### 1.2 容量工具修正（03 §8 P5-05 前半）✓

`backend/scripts/kpi_capacity_benchmark.py` **全量重写**（P0-03 §3.4 五缺陷逐项）：

| 缺陷 | 修复 |
|---|---|
| #1 `size` 参数被静默忽略→实际只拉 ~20 回路 | `pageSize`（le=100 封顶）+ 逐页拉全量 + total 提前停 + 去重 + 取回断言 |
| #2 默认端口 7101（同机另一项目活系统） | 默认 17101；`perf_test_attention_summary.py` BASE_URL 同步修 17101；`benchmark_kpi_optimization.py` 核验为进程内基准无 HTTP 端口（登记在案） |
| #3 少量 ID 多窗口重放凑数 | standard-hourly 前置断言三条件 eligible 数=预期（默认 1000）+ 独立 loopId 断言，不足即拒绝（"禁止重放凑数"）；重放仅存 custom 且需显式 `--allow-replay`，报告标注 unique_loops/loop_evals，**不计 600s 标准 SLO**（custom 单列完成） |
| P5-05 卡新增 | 独立 **standard-hourly 模式**：`calculate_hourly_kpi.delay(ts_start=冻结窗)` 经既有 Celery 入口（复用唯一 lifespan Worker，不新增开放触发 API）；**完成屏障=DB 回查非父任务终态**——①小时账本（eligible 集行数+source∈{SCHEDULED,MANUAL_STANDARD} 分布断言）②latest 投影（loop_confidence_latest.data_ts_start=冻结窗；DEC-09 迟到窗不覆盖在报告单列）③预定节点（递归 CTE 预测 is_kpi_enabled×子树×本窗快照回路 → kpi_node_snapshot_hourly 行数）；**子任务 ID 记录**=轮询 `celery inspect active` 取证 calculate_node_kpi_hourly（尽力，错过登记 not-observed）；各屏障条件首达时刻单列；`--dry-run` 零投递零写入 |
| 既有 custom 路径 | 保留 `_post_with_retry`/429 退避；超时/状态语义不变 |

单测：`tests/test_kpi_capacity_benchmark.py` 11 例（pageSize 翻页/去重/total 提前停/整点校验/三条件计数拒绝/dry-run 零投递/屏障常量/replay 门禁）全过。

### 1.3 1000 独立回路数据生成（DEC-08a 混合通道）✓ 已执行（keeper 确认后）

生成器：`backend/scripts/generate_capacity_dataset.py`（`--plan/--execute/--self-check`）；单测 `tests/test_generate_capacity_dataset.py` 17 例（构成边界/seed 可再生/质量三态/缺口截断/MODE=0/行构建/manifest 字段）全过。

**执行记录**（keeper 2026-10-13 确认，冻结窗 2026-10-12T20:00 naive UTC）：

- 通道 1 API 组态：装置树 `PERF-T-FACTORY/5 AREA/20 UNIT`（幂等复用）+ 测点 `POST /tags/import` 2×3500 行（**0 failed**；7000 行>单文件 5000 行上限→分批，首跑 422 修复）+ 回路 `POST /loops/import` 1000 行（inserted=1000, **0 failed**，参评=是→READY 推导）
- 通道 2 TD 直写：`st_point_data_v1` 点子表（p_\<tag_id hex\>，source_kind=4，payload_hash 同 data_import 公式）**6,936,900 行 / 93s**（PV 3,465,000=950×3600+50×900、OP 3,465,000、SP COV 2,900、MODE/PID 各 1,000）
- 通道 3 声明入库：`sys_config:capacity.perf_dataset.declaration`（JSON：synthetic/构成/窗口/seed/manifest sha256/红线）+ 全部回路 remark 标注；manifest=`backend/capacity_dataset/manifest_b_2026101220.json`（P0-03 §1.2 字段全量×1000 行，构成 800/100/50/50 核对一致）
- 场景 b 构成（全 1000 三条件 eligible）：800 正常（steady/oscillation/drift/noisy 轮转登记）+100 数据不足（PV 70% 质量 BAD）+50 手动（**MODE=0，eligible 内终态**——DEC-08a 裁决落实）+50 缺口（前 15min 数据）

### 1.4 准备就绪自检 ✓（不跑正式五批测量，归批 γ）

`--self-check`（全只读）**全绿**：台账 1000/三条件参评 1000/测点 7000/≥4 角色绑定 1000/合成声明在案 ✓；TS 抽查 11 回路（四构成类别各≥2，normal/insufficient/manual=3600 行、gap=900 行，逐回路核对预期）；50 手动抽样 5/5 MODE=0 且三条件成立、总数 50 ✓。

**工具 dry-run 双口径实证**（零投递零写入）：真实回路集（expect 30，ledger=30/latest=0——**DEC-09 迟到窗实证**：历史窗真实回路 latest 不回写，工具已单列该条件）；PERF-T 集（expect 1000 prefix 口径，ledger=0 待 γ 批投递，node=6 为真实回路既有行）。

## 2 门禁（真实退出码）

| 项 | 命令 | 结果 |
|---|---|---|
| ruff check | `uv run ruff check .` | 0 ✓ |
| ruff format | `uv run ruff format --check .` | 0 ✓（874 files） |
| pytest | `uv run pytest -q --tb=no` | **0 failed：6339 passed / 335 skipped / 129 deselected / 12 xfailed / 4 xpassed**（302s）✓ |
| alembic heads | `uv run alembic heads` | 单头 `p403scope01` ✓（批 α 零迁移零模型改动） |
| alembic check | `uv run alembic check` | **NOT_RUN（α 链）——阻塞项见 §3-①** |

## 3 关键发现/风险（报主协调）

1. **alembic check 撞批 β 并行迁移**：共享 dev 库 `alembic_version` 已被批 β 置于 `p502attm01`（attempt 表迁移，α 分支 5ec4e284 无此版本）→ `Can't locate revision` 。批 α 零 schema 改动（`git status` 无 models/migrations），α 链自身单头无漂移；**合并 α+β 时迁移链需主协调仲裁**，且 dev 库版本归属（β 已应用）需登记。
2. **Beat 环境影响（keeper 已知悉接受）**：生成后每整点 Beat 评估 1030 回路；1000 合成回路在非冻结窗无数据→持续产出 INCONCLUSIVE 快照并入 latest/驾驶舱统计（γ 批测量环境预期状态，批间不清除）。
3. **γ 批测量窗口纪律**（工具已内置支持，操作时注意）：同窗 SETNX 锁 TTL 15000s（Beat/手动互斥，重投抛 HourlyWindowBusy——须选无冲突窗或等锁过期）；DEC-09 迟到窗真实回路 latest 不回写→标准屏障 latest 条件用 `--barrier-loop-prefix PERF-T` 单列 PERF 集对账（PERF 集首次评估 latest 必写）。
4. 生成首跑两处工具侧 bug 已修：plant-nodes 树响应键为 `id` 非 `nodeId`；TD SQL `value` 列为保留字需反引号（与 point_history_repository 同款）。自检 falsy-0 判断 bug 修复（MODE=0 误判 None）。
5. worktree `backend/.env` 自主仓拷贝（untracked，指向 dev 栈 17102/17104/17103）；`backend/capacity_dataset/` 为 manifest 产物目录（untracked，含 610KB manifest）。

## 4 文件清单

改动：`backend/app/services/{cockpit_overview,node_performance}.py`、`backend/app/api/v1/endpoints/{cockpit,node_performance}.py`、`backend/scripts/{kpi_capacity_benchmark,perf_test_attention_summary}.py`、`backend/tests/{test_cockpit,test_node_performance}.py`
新增：`backend/scripts/generate_capacity_dataset.py`、`backend/tests/{test_kpi_capacity_benchmark,test_generate_capacity_dataset}.py`、`backend/capacity_dataset/manifest_b_2026101220.json`

## 5 下一步

- 非作者验收（05 号文 §5 协议；聚合收窄建议含真实 PG 反例复跑）
- 批 γ（P5-01~04 落地后）执行正式五批测量：standard-hourly 实投+屏障计时+冷/热×5；scenario a 全正常窗数据本批未生成（生成器 `--scenario a --ts-start <另一窗>` 即可，待 γ 需要时另报 keeper）
