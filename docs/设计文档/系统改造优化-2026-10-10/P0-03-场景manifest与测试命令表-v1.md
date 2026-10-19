# P0-03 场景 manifest 与测试命令表 v1

> **状态：v1.1（非作者复核B PASS；待用户裁决 DEC-05/DEC-08 后冻结；所有命令本轮 NOT_RUN）**
>
> - 起草子智能体：P0-03 文档起草代理（业务代码/配置/DB/服务全程只读，未运行任何测试/基准/pytest/playwright）
> - 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（main；开工与收工各执行一次 `git rev-parse HEAD`，一致）
> - 输入：《04-验收与容量基线》§4/§5/§6/§7、《03-阶段任务计划》P0-03 任务卡与 §10 P7-02、《执行状态/P0-03-容量与测试现状盘点.md》（核心事实素材，下称"盘点报告"）
> - 本文是 03 §3 P0-03 任务卡的产出物；对应 G0 门（04§7）中的"测试样本、拟运行命令、写入目标、清理策略与环境 Owner 明确"各项。历史证据不冒充当前基线结果；本轮一切执行类条目状态为 **NOT_RUN**
> - 所有性能预算数字均引用《04》§4.2 原值，**建议目标待 DEC-05 冻结**；本文不发明任何新预算

---

## 1. 场景 manifest 草案

### 1.1 场景定义（04§4.1 口径）

| 场景 | 构成 | 参评口径 |
|---|---|---|
| A（正常组） | 1000 条可评正常输入回路 | eligible=1000，应有 1000 个有效评分结果；台账总数固定 1000 |
| B（混合组） | 800 可评 + 100 数据不足 + 50 手动 + 50 含缺口 | 台账总数固定 1000；数据不足是明确 INCONCLUSIVE 终态而非漏写；不可参评者单列 excluded 及原因，不算执行成功 |

通用约束（04§4.1 原则，冻结项）：

- 1000 个**不同** ACTIVE/READY 且参评回路；**独立 loop IDs、绑定真实创建，禁止少量 ID 多窗口重放凑数**（现行 `kpi_capacity_benchmark.py` 的 `_replay_windows` 前缀重放方式不满足本定义，仅可在 custom 独立模式单列，见 §3.3）。
- 1 小时 1s 数据网格；PV/SP/OP/MODE 及必要质量码/参数完整。
- 数据内容允许相同特征的不同回路，但**必须说明是合成；合成数据不得称"1000 现场真实回路"**。
- 参评资格在提交前由 P0 规则冻结；实际 eligible 对象逐一产出终态账本证据；具体状态/投影写条件冻结，不为凑数把失败算不可评。
- 每组冷/热各至少 5 批；冷热定义含输入/预计算/Redis/进程缓存；安全清缓存只限测试 namespace，禁止 FLUSHALL（清理策略裁决见 §6 第 4 项）。

### 1.2 数据 manifest 字段表（04§4.1 冻结字段）

| 字段 | 含义 | 备注 |
|---|---|---|
| loopId | 独立回路标识 | 1000 个互不相同；写入前登记台账 |
| 节点（nodeId） | 回路归属的 plant node | 节点级联（`kpi_node_snapshot_hourly`）屏障取预定 nodeIds |
| 量程 | PV/OP 工程量程上下限 | 归一化与 fitness counter（C08）依赖 |
| 响应类别 | 合成响应特征类别（良好稳态/振荡/缓变漂移/高噪声/手动等） | 类别清单与配比在 P0-02 profile 冻结后定（占位，不预发明配比） |
| 控制结构 | P/PI/PID | 与 PID_P/PID_I/PID_D 绑定一致 |
| 窗口 | ts_start/ts_end（1 小时） | 冻结窗口与 standard-hourly 投递的 ts_start 一致 |
| 点数 | 每 tag 网格点数 | 1s×3600=3600 点/tag |
| 场景 | A / B（B 内细分可评/数据不足/手动/缺口） | 对应 expected 终态 |
| expected 业务状态 | EVALUATED / INCONCLUSIVE(DATA_INSUFFICIENT) / EXCLUDED+原因 | 提交前冻结；账本对账用（04§4.3-1/2：台账 1000=eligible+excluded；成功+数据不足+失败=eligible） |
| 配置 hash | 生成时冻结的算法配置 revision/hash | 结果等价核对（04§4.3-3）与复现链（C12b）依赖 |

manifest 附加登记：生成器 seed（可再生）、写入通道标记（见 §1.3）、环境 manifest 引用（§5）。manifest 自身 hash 计入结果记录（04§1 统一记录项）。

### 1.3 1000 独立回路合成生成方案建议

**生成器规格要点**（供 P5-05 前实施，本轮不实现）：

1. 每回路：唯一 loopId；**7 个 OPC tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）绑定真实创建**（现行为回路由用户创建并关联 7 tag 的数据模型）；量程、控制结构、节点归属按登记分布分配。
2. 时序数据：冻结窗口内 1 小时 1s 网格（3600 点/tag）；生成 PV/SP/OP/MODE 曲线；PV 携带质量码（Good/Bad/Uncertain——现行数据质量口径主要针对 PV）。
3. 响应特征类别分布：不同回路覆盖不同响应特征（振荡/稳态/缓变/高噪声等）；**类别清单与配比占位，待 P0-02 数值 profile 冻结后一并冻结**，不在本文预发明。
4. 场景 B 特化构造：
   - 100 数据不足：PV 大比例 Bad/Uncertain 或窗口覆盖不足 → expected INCONCLUSIVE（明确终态，不是漏写）；
   - 50 手动：MODE=MAN → expected 不参评/excluded（按现行 L0/适用性规则冻结口径）；
   - 50 含缺口：构造长缺口段 → expected 按缺口语义（held_mask/sparse_admission 契约）。
5. 幂等：同 seed 可完整再生；seed 与参数入 manifest。
6. 输出物：回路台账登记 + 数据 manifest（§1.2 字段全量）+ 配置 hash。

**写入通道对比与推荐**：

| 通道 | 优点 | 缺点 |
|---|---|---|
| 全 API 组态 | 走真实业务校验与组态链路（与 C30 岗位批量组态同路径）；台账/tag 绑定天然合法 | 1000×7 tag 组态耗时长；依赖服务可用与并发限制 |
| 全直写 DB | 快、确定性强、可离线 | 绕过业务校验，status/绑定一致性无保证；外键/ID 手工维护易错；与"用户自助组态"产品路径偏离 |
| **混合（推荐）**：回路/节点/tag 元数据经 API 组态 + 历史时序直写本地 TDengine | 元数据合法性由真实链路保证；时序本无组态 API，且红线规定计算唯一历史源是本地 TDengine，直写贴合真实计算路径 | 仍需隔离环境（DEC-08）执行；两通道需在 manifest 分别登记 |

推荐**混合通道**，理由：容量实验的对象合法性（台账、READY 状态、7 tag 绑定）必须经真实组态产生，否则 eligible 分母本身不可信；时序数据按"计算全本地"的现行架构直写本地 TDengine 即为计算任务将消费的真实路径。无论选哪条通道，均须在 DEC-08 裁决的隔离库执行，且 manifest 登记写入通道。

### 1.4 诚实化约束

- 所有由生成器产出的数据与报告**显式标注"合成（synthetic）"**；任何对外表述不得称"1000 现场真实回路"（04§4.1 原文口径）。
- 合成集成层证据的能力边界按 04§1 层级表理解：可证明指定场景的端到端与故障恢复，**不能替代现场误报/召回与真实工况效果**（后者属 P7-01 现场校准）。

---

## 2. 精确测试命令表

> 纪律（04§6）：仅在明确启动实施/测试后运行；命令逐条执行并取**本命令退出码**，不用 `| tail` 之后的退出码判断；先查 env 与服务归属。**本轮全部 NOT_RUN。**

### 2.1 门禁命令表（04§6 逐条落成）

| # | 工作目录 | 完整命令 | 预期退出码 | 本轮状态 | 备注 |
|---|---|---|---|---|---|
| 1 | `CLPM_ROOT/backend` | `uv run ruff check .` | 0 | NOT_RUN | — |
| 2 | `CLPM_ROOT/backend` | `uv run ruff format --check .` | 0 | NOT_RUN | — |
| 3 | `CLPM_ROOT/backend` | `uv run pytest -q` | 0 | NOT_RUN | 默认 `addopts = "-m 'not integration'"` 排除 integration（pyproject.toml 已核实）；即本命令**不**覆盖 §2.2 集成集 |
| 4 | `CLPM_ROOT/backend` | `uv run alembic check` | 0 | NOT_RUN | schema 漂移检查，结构性漂移即失败 |
| 5 | `CLPM_ROOT/backend` | `uv run python ../scripts/check_api_contract.py` | 0 | NOT_RUN | 脚本位于根 `scripts/`（已核实）；CI 中为阻塞项 |
| 6 | `CLPM_ROOT/frontend` | `pnpm exec eslint apps/web-antd --cache` | 0 | NOT_RUN | `@vben/web-antd` 无 lint 脚本，用此形态（AGENTS 口径） |
| 7 | `CLPM_ROOT/frontend` | `pnpm run test:unit` | 0 | NOT_RUN | vitest 全量，CI 阻塞门禁 |
| 8 | `CLPM_ROOT/frontend` | `pnpm run check:type` | 0 | NOT_RUN | — |
| 9 | `CLPM_ROOT/frontend` | `pnpm run build:antd` | 0 | NOT_RUN | — |
| 10 | `CLPM_ROOT/e2e` | `pnpm exec playwright test` | 0 | NOT_RUN | **先确认 playwright.config 端口/env**：`baseURL` 默认 `http://localhost:15666`（MVP 前端口，已对齐）；后端 API base 在 `e2e/fixtures/auth.ts`，可 `E2E_API_BASE_URL` 覆盖，执行前核对默认值指向 17101（盘点 §3.3/§5.3） |

补充登记（非 04§6 门禁、CI 现状参照）：CI 后端另有 `uv run mypy app/ ...`（非阻塞）与 `pytest --cov=app --cov-fail-under=60`；前端 CI 中应用内 E2E 两步均 `continue-on-error: true` 非阻塞，且**根目录 `e2e/` 业务套件在 CI 中完全未被调用**（盘点 §3.1）——接入时机按 TEST-01 → P7-02（§6 第 6 项）。

### 2.2 后端 integration 显式选择命令草案

现状事实（本轮只读核实）：

- `backend/pyproject.toml`：`addopts = "-m 'not integration'"`，marker 注册于 markers（描述"需真实 AAS API 等外部服务，CI 跳过"）。
- 带 `pytest.mark.integration` 的文件共 **15 个**（`backend/tests/integration/` 目录 14 个 + 顶层 `backend/tests/test_workbench_tasks.py`）：

```
tests/integration/test_aas_api.py
tests/integration/test_alembic_convergence.py
tests/integration/test_grade_distribution_pg.py
tests/integration/test_loop_confidence_latest_pg.py
tests/integration/test_process_model_version_concurrency.py
tests/integration/test_production_bootstrap.py
tests/integration/test_refactor_acceptance.py
tests/integration/test_refactor_amendment_e2e.py
tests/integration/test_refactor_baseline.py
tests/integration/test_refactor_builder_equivalence.py
tests/integration/test_refactor_metadata.py
tests/integration/test_refactor_point_store.py
tests/integration/test_refactor_provider_differential.py
tests/integration/test_refactor_writer_and_import.py
tests/test_workbench_tasks.py
```

**关键机制提醒**：`addopts` 中的 `-m 'not integration'` 同样作用于按文件路径指定的运行——`uv run pytest tests/integration/test_x.py` 会因默认过滤跳过全部 case。因此**任何 integration 选择都必须显式 `-m integration`（命令行 `-m` 覆盖 addopts 中的同名选项）或 `--override-ini`**。

命令草案（形态冻结，本轮 NOT_RUN；环境前置待 DEC-08）：

```sh
# 工作目录 CLPM_ROOT/backend

# ① 清点集成分母（先于执行，核对 15 文件 collect 数）
uv run pytest -m integration --collect-only -q

# ② 全量 integration（需 POSTGRES_* 指向隔离真实 PG + 本地 Redis，参照 CI env 形态）
uv run pytest -m integration -q

# ③ 按文件显式选择（示例：真实 PG 相关子集）
uv run pytest -m integration -q \
  tests/integration/test_alembic_convergence.py \
  tests/integration/test_grade_distribution_pg.py \
  tests/integration/test_loop_confidence_latest_pg.py \
  tests/integration/test_process_model_version_concurrency.py \
  tests/integration/test_production_bootstrap.py
```

前置与限制登记：

- `test_aas_api.py` 依赖真实 AAS API（marker 描述）；环境不可得时该文件如实登记 NOT_RUN/外部依赖，不得记为已执行（04§6：`pytest -m integration` 只运行真正标记的 case，不能证明所有必要集成都执行了；04§1：环境不可用写 NOT_RUN，不改成 PASS）。
- 04§3 纪律同样适用于 integration：文件存在 ≠ case 覆盖已验，执行前核名称与 case 覆盖。
- 04§3 复用测试清单 17 项存在性已由盘点 §四 核对（17/17 存在），不在本节重复；其执行随各 Gate（G1–G3）进行，不由 P0 执行。
- 精确到"哪个 Gate 用哪条 ③ 型命令"的冻结表，在 DEC-08 环境落定后随 P0 收口补齐（§8 未决项）。

### 2.3 两条真实 PG 迁移路径演练命令草案

> **均为隔离库演练，需 DEC-08 确认环境**（现状 `perf_test_attention_summary.py` 直连运行库造删数据的前车之鉴见盘点 §1.3）。ORM 与迁移同批、先迁移后新代码（AGENTS 红线）。

口径依据（CI 与生产一致，ci.yml 注释明示）：本仓库迁移链**不能从空库 upgrade**——早期迁移以 `01_schema.sql` 已建表为前提，空库 `alembic upgrade head` 会 UndefinedTableError。正确新装口径 = 应用引导 SQL → stamp head → 漂移检查。

**路径一：新装（工作目录 `CLPM_ROOT/backend`，env `POSTGRES_*` 指向隔离空库）**

```sh
uv run python scripts/apply_bootstrap_sql.py   # 预期退出码 0（脚本位于 backend/scripts/，已核实）
uv run alembic stamp head                       # 预期退出码 0
uv run alembic check                            # 预期退出码 0（引导 SQL 与 ORM 元数据无漂移）
```

（与 CI「引导 SQL + schema 漂移检查（真实 PG）」步骤及生产 `deploy/lib-migrate.sh` 顺序一致。）

**路径二：存量升级（工作目录 `CLPM_ROOT/backend`，env `POSTGRES_*` 指向隔离库）**

```sh
# ① 将隔离库置于记录的旧 head 状态：
#    方式 a) 旧版本引导 SQL + uv run alembic stamp <旧head>
#    方式 b) 从现有库快照恢复（需 DEC-08 批准的隔离拷贝渠道）
#    具体供备方式待 P7-03 细化（§8 未决项）
# ② 从旧 head 前进到候选 head：
uv run alembic upgrade head                     # 预期退出码 0
uv run alembic check                            # 预期退出码 0
```

约束（04§6 原文）：不对现有库 stamp 跳迁移；演练只发生在隔离库。`<旧head>` 取值来源：候选冻结时的 `uv run alembic current` 记录或台账登记。

---

## 3. standard-hourly 容量模式规格草案（P5-05 的输入）

> 现状（盘点 §一/§二）：三个既有脚本均不支持 standard-hourly；custom 路径不更新 latest、不级联节点；standard-hourly 父任务终态 ≠ 节点持久化完成。本节为 P5-05「添加独立 standard-hourly 模式」的规格建议。

### 3.1 投递方式

- **入口**：经 Celery 注册任务 `app.tasks.kpi_calc.calculate_hourly_kpi`（取证签名：`def calculate_hourly_kpi(self, ts_start: str | None = None, task_id: str | None = None) -> dict`；`name="app.tasks.kpi_calc.calculate_hourly_kpi"`）。
- **投递形态（推荐）**：基准脚本内直接 `calculate_hourly_kpi.delay(ts_start=<冻结窗口 ISO 字符串>)`——语义精确、不依赖 API 认证态；备选：经既有 API `POST /tasks/standard/evaluate`（body `tsStart=冻结窗口`，该 API 内部即 `calculate_hourly_kpi.delay`，且带 TaskRecord 语境）。两形态择一冻结，不得混用出两套计时口径。
- **约束**：
  - 复用 lifespan 唯一 Worker/Beat，**不另起 Worker、不新增开放触发 API**（04§4.3-5 原文口径）；
  - 同小时窗 Redis SETNX 互斥锁（key `task:hourly_calc_lock:YYYYMMDDHH`，TTL 15000s）：基准重投同窗会抛 `HourlyWindowBusy`——**基准须选无冲突冻结窗口或等待锁释放，不得吞异常绕锁**；
  - 任务自带 autoretry（Exception，max_retries=3，countdown=60，backoff max 600 jitter）与 time_limit 14400 / soft 13800——基准计时须把重试计入总时长，不得剔除重试批次只留最快一次；
  - 计时起点 = 投递提交开始（04§4.2"提交开始到……完成屏障"）。

### 3.2 完成屏障判定（全部 DB 回查；父终态不作为屏障）

依据取证：父任务 `calculate_hourly_kpi` → `_do_calculate` 末尾 `calculate_node_kpi_hourly.delay()` 为 fire-and-forget（投递失败仅 warning），父任务 SUCCESS 只表示回路快照已 commit + 节点任务已投递，**不等待 `kpi_node_snapshot_hourly` 持久化完成**。

屏障条件（AND，全部 DB 回查，不得以任务状态字段代替）：

1. **小时账本**：`kpi_snapshot_hourly` 中 `loop_id ∈ 预定 eligible 集` 且 `ts_start = 冻结窗口` 的行数 = eligible 数（唯一键 `uq_kpi_snapshot_hourly_loop_ts`，upsert 幂等），且结果记录的算法 version 与候选一致；
2. **latest 投影**：`loop_confidence_latest` 中预定回路行更新至该窗口。取证警示：现行 `_save_confidence_latest` 被 try 包裹、写失败仅日志不阻断（kpi_calc.py L3131-3136 弱保证）——**推荐将 latest 纳入屏障**（04§4.2 计时定义本就含"同窗当前投影"），缺失即屏障失败上报；基准需能区分"尚未写"与"写失败"（超时窗口内轮询 + 失败原因留痕）；
3. **节点结果持久化**：预定 nodeIds 在 `kpi_node_snapshot_hourly` 有 `ts_start = 冻结窗口` 的行；
4. **分母断言**（04§4.3-1/2）：台账 1000 = eligible + excluded；执行成功 + 数据不足 + 失败 = eligible；场景 A 正常组 eligible=1000 且 1000 有效评分；非预期失败 = 0；无重复行（唯一键核对）、无漏写。

基准记录项：父任务 ID、节点子任务 ID、logicalRunId、预定 nodeIds、各屏障条件首次满足时间戳、冷/热标记。

**预算引用（04§4.2 原值，建议目标待冻结 DEC-05）**：1000 回路小时 KPI 每批 ≤600s——计时为"standard-hourly 提交开始到父/节点子任务及全部 eligible 结果 commit、同窗当前投影和预定节点结果完成屏障；正常组 1000 有效结果。冷/热分别报告，任一必要批超限即未通过"。

### 3.3 custom 独立模式（保留但单列）

- custom（`POST /tasks/custom/evaluate` → `calculate_custom_batch_kpi.delay`）**不计 600s 标准 SLO**；重放吞吐只称 loop-evals，不得表述为标准小时容量（04§4.3-6 原文口径）。
- 现行事实登记（基准设计须知）：custom 恒写 `kpi_snapshot_custom`（source=MANUAL_CUSTOM，唯一键 task_id+loop_id），不更新 latest、不级联节点；API body `metrics` 仅非空校验、不进 Celery 任务参数，指标范围实际恒为全量 MetricConfig（脚本送 3 项 metrics 是无效参数——盘点 §2.4）。
- 用途边界：custom 模式仅作吞吐参考与优化对比；`benchmark_kpi_optimization.py` 基准 1–4 纯函数微基准可继续用于热点回归（P5-01 复用），不冒充端到端容量。

### 3.4 既有容量脚本修复清单（P5-05 前置，本轮只登记不实施）

| # | 缺陷（取证） | 修复要求 |
|---|---|---|
| 1 | `kpi_capacity_benchmark.py` 分页参数名 `size` ≠ API 的 `pageSize`（le=100），API 静默忽略回退默认 20，且 `len(items)<page_size` 早停——**实际只拉到约 20 个回路（静默早停）** | 改用正确参数名 `pageSize`；单页上限 100，须循环翻页拉全量；断言实际取回 loopId 数 = 目标 |
| 2 | 三个脚本默认端口 7101（原项目端口；MVP 为 17101）：`kpi_capacity_benchmark.py` L28、`perf_test_attention_summary.py` L42，`benchmark_kpi_optimization.py` 需逐项核对 | 默认值改 17101 或强制显式 `--base-url`；执行时禁止裸跑误打另一项目（04§4.1 红线） |
| 3 | `_replay_windows` 少量真实 ID × 多窗口重放凑数 | standard-hourly 模式必须 1000 独立 loopId（§1.1）；重放仅存于 custom 单列模式并在报告显式标注 |
| 4 | `benchmark_kpi_optimization.py` 端到端段进程内直调且 session 退出无 commit——写入回滚、落库不生效、无回读验证 | 正式端到端段须含 commit、状态与持久化回读核对；节点投递/执行失败不能作为正常完成（04§4.3-6） |
| 5 | custom 指标参数无效（§3.3） | 基准报告不得声称"仅 3 项指标"；如需限定指标范围须先修后端参数传递（另行登记，不属 P5-05 必做） |

---

## 4. C11 合法进程拓扑与 PID 取证方法

### 4.1 合法拓扑定义（04§2 C11 原文口径）

- **一个 API（17101）+ 其 lifespan 拉起的唯一 Worker master 内至少两个不同 prefork 子 PID**；记录 master/child PID、hostname 与 task→PID 映射；**不额外启动 master/Beat**（AGENTS 红线：严禁手工再启动 worker/beat，多实例导致任务重复消费/双触发）。
- Windows solo 以暖缓存+重启/持久版本验收另报，不冒称跨进程；第二 API 仅在另行批准其生命周期拓扑后扩测。
- 进程内 mock 不能替代本取证（04§3：`test_confidence_threshold_sync.py` 进程内模拟不替代 C11）。

现行 dev 形态（取证）：lifespan `subprocess.Popen([sys.executable, "-m", "celery", "-A", "app.tasks.celery_app", "worker", "-l", "info", "--concurrency", "4", "-Q", "default,dead_letter", "--hostname", "CLPM-MVP@%h", ...], start_new_session=True)`（main.py；非 Windows 平台 prefork + concurrency 4；hostname tag = 仓库名 `CLPM-MVP`）。Beat 同由 lifespan 拉起。

### 4.2 取证步骤建议（本轮 NOT_RUN，仅登记方法）

```sh
# 工作目录 CLPM_ROOT/backend；先唯一启动后端（lifespan 自动拉 Worker+Beat，严禁手工再启）
uv run uvicorn app.main:app --host 0.0.0.0 --port 17101

# ① 定位 Worker master（pgrep 特征：本项目 Celery 应用入口 + 项目 tag hostname）
pgrep -fl "celery.*app\.tasks\.celery_app.*CLPM-MVP@"
# 记录 master PID 与 hostname（CLPM-MVP@<host>）

# ② 列 prefork 子进程（按父 PID；--concurrency 4 → 预期 ≥4 个子 PID，取证 ≥2 满足 C11）
pgrep -P <master_pid> -fl

# ③ broker 视角核对：单 worker、池类型、并发数、当前活跃任务所在进程
uv run celery -A app.tasks.celery_app inspect stats
uv run celery -A app.tasks.celery_app inspect active
```

- task→PID 映射取证：`inspect active` 输出运行中任务；如任务侧已有 PID 记录渠道（task 记录/日志含 worker 进程标识）以代码现况为准，执行前核对；无现成渠道则由 `inspect active` 快照 + 子进程 PID 表交叉取证。
- 全部输出（PID 表、hostname、时间戳、命令原文）按 04§1 统一记录项存证。
- 红线复查：取证全程若发现 >1 个 master 或手工 worker/beat，按"重复 Worker"风险上报（04§4.2 资源行：无重复 Worker）。

### 4.3 v1 运行中发布 v2 的操作序列（P2 执行，本轮登记）

1. 冻结配置 revision v1；投递一个长运行任务 T1（确保执行期跨越发布时刻，落在已取证的 prefork 子 PID 上并记录 task→PID）；
2. T1 运行中经配置发布通道发布 v2（原子 revision）；
3. 投递新任务 T2 → 断言 T2 全程使用 v2；
4. 断言 T1 完整以 v1 完成不受影响（结果快照记录配置版本）；
5. 并发发布双方以 expectedRevision 冲突，一方 409；
6. 记录各 task→PID 与 master/child PID 表。

### 4.4 Redis 断连模拟方式（仅登记，不执行）

- **方式 A（推荐）**：`docker pause <dev compose redis 容器>`（redis:7-alpine；保留容器内存态、可 `docker unpause` 无损恢复）→ 期间执行发布/新任务 → 恢复 → 断言版本校验仍按持久 revision 可靠（不得以 pub/sub 代替持久版本检查，03 P2-02 约束）。
- **方式 B**：`docker compose -f deploy/docker/docker-compose.dev.yml stop redis` 后重启容器（需核容器卷持久化策略，恢复期更长）。
- 风险登记：Redis 暂停影响 Worker 心跳/结果后端/SETNX 锁，恢复后须核对 worker 自愈、无任务重复消费；**严禁借断连窗口手工补启 worker/beat**。

---

## 5. 环境 manifest

### 5.1 本机/dev 环境事实（盘点 §5.1 整理；P0 已取样或代码可查）

| 条件 | 当前事实 |
|---|---|
| CPU/核 | Apple Silicon arm64；物理核 16 |
| RAM | 128 GB |
| OS | macOS 27.0.1 (26A434) |
| PostgreSQL | postgres:16（dev compose） |
| TDengine | tdengine/tdengine:3.3.6.6 |
| Redis | redis:7-alpine |
| Celery | 5.6.3；dev 拓扑 = lifespan 单 master prefork `--concurrency 4`（Windows 才 solo） |
| 任务批内并发 | DB 并发 12、prewarm 5、backfill 8（kpi_calc.py 取证） |
| DB 连接池 | NullPool、command_timeout 60s、idle_in_transaction 300s、会话时区 UTC |
| 关键依赖 | sqlalchemy 2.0.51 / asyncpg 0.31.0 / alembic 1.18.4 / taospy 2.8.5 / fastapi 0.138.0 / uvicorn 0.49.0 |
| 端口 | API 17101 / 前端 15666 / mock 17106；**三个容量脚本默认值均为 7101，执行时必须显式 `--base-url` 或先修默认值** |
| Worker 池类型/数量 | prefork，1 master × concurrency 4（lifespan 拉起；无手工实例） |
| 前端浏览器版本 | 执行时取得（§5.2） |
| 现有容量入口行为 | custom 重放不更新 latest、不级联节点；standard-hourly 父终态 ≠ 节点持久化完成——P5-05 改造前不可作为 600s 标准小时 SLO 证据（建议目标待冻结） |

### 5.2 执行时才取得的条件（P0 不执行）

- 网络 RTT（04§4.2 回路墙/聚合页"测试局域网"口径下实测）
- 前端浏览器版本（固定配置后登记）
- 磁盘型号/余量
- 冷/热各批次实测数值（含输入/预计算/Redis/进程缓存四层冷的定义核对）

### 5.3 环境 Owner

- **占位：待 DEC-08**。容量实验 PG/TDengine 实例选择（dev 共享库 vs 独立隔离库）、端口 Owner、隔离库供给方式均随该裁决落定；推荐口径见 §6 第 3 项。

---

## 6. DEC-05 / DEC-08 裁决问题清单（7 项）

> 推荐口径与 01§5 一致：**采用《04》§4.2 建议预算表原值、隔离环境、唯一环境 keeper**。每项裁决人：用户；影响到的阻断阶段列于表内。

| # | 待裁决项 | 推荐默认 | 备选 | 影响 |
|---|---|---|---|---|
| 1 | 1000 独立回路数据来源与生成方式 | 合成生成器（§1.3 规格：7 OPC tag 真实创建、1h 1s 网格、质量码、类别分布待 P0-02 profile）+ 混合写入通道（元数据 API 组态、时序直写本地 TDengine）+ manifest（§1.2）与场景 B 配比 800+100+50+50 参评资格同批冻结 | 全直写 DB（快但绕过业务校验）；全 API 组态（慢但链路全） | DEC-05/DEC-08；阻断 P5-05 全部容量 case |
| 2 | 硬件规格 | DEC-05 按 04§4.2 预算表**原值**冻结（不得事后放宽）；首期在本机（16 核/128GB Mac）实测并如实报告瓶颈，硬件不足时按 04§4.2 末段流程：报告证据与瓶颈 → 明确调整方案 → DEC-05 批准 | 直接指定服务器（需用户提供规格与窗口期） | DEC-05；阻断 P5 全部预算生效 |
| 3 | 环境 keeper 与库隔离 | DEC-08 指定**唯一环境 keeper**；容量实验用独立隔离 PG/TDengine 实例；`perf_test_attention_summary.py` 直连运行库造删数据必须改隔离库或显式授权 | dev 共享库 + 安全前缀清理（风险：污染共享数据，不推荐） | DEC-08；阻断 G0"环境 Owner 明确"项与 P5 全部实验 |
| 4 | 清缓存安全 namespace | 允许清理的 Redis key 前缀白名单（测试 namespace）；冷复位至"重启 API+Worker（由 lifespan 单点拉起）"级别须 keeper 授权执行；**禁止 FLUSHALL；严禁手工补启 worker/beat**（红线） | 独立 Redis 实例仅供测试（更彻底，资源成本高） | DEC-05/DEC-08；阻断冷/热 ≥5 批定义可行性 |
| 5 | standard-hourly 完成屏障定义 | 采纳 §3.2 三条件全 DB 回查屏障（小时账本 + latest 投影 + 预定 nodeIds 节点结果），**latest 弱保证纳入屏障**（缺失=屏障失败上报）；父任务终态不作为屏障 | latest 不纳入屏障（弱化，与 04§4.2 计时定义"同窗当前投影完成"冲突，不推荐） | 04§4.3-5；阻断 P5-05 实施与 600s（建议目标待冻结）计时终点 |
| 6 | 测试命令冻结 | 本文 §2 表为 P0 收口草案；integration 精确选择命令在 DEC-08 环境落定后补齐 Gate 映射冻结；根 `e2e/` 业务套件接入按 TEST-01 → P7-02（环境稳定、非 flaky 后才可升阻塞门禁） | 立即把根 e2e 接入 CI 阻塞（不推荐：环境稳定性未解决，现状应用内 E2E 非阻塞且根套件未被 CI 调用） | 04§6/P0 收口；阻断 P7-02 门禁收口 |
| 7 | 长窗组预算 | 采用 §7 框架占位，具体数值由 DEC-05 **单独冻结**（不沿用 600s 推算；24h/30d 窗口不同分别报告） | 暂缓长窗组至 P5 之后再裁 | DEC-05；阻断动态算法长窗 case（≥100 回路 24h / ≥10 回路 30d） |

---

## 7. 长窗组登记（24h / 30d 边界试验）

依据 04§4.1：24 小时 1s 与 30 天查询**另建动态算法长窗组**，记录预算/转异步/不可判定；不把长窗静默截成 1 小时以通过；首期**至少 100 回路 24h 动态分析与 10 回路 30d 边界试验**；窗口不同时分别报告。

### 7.1 规模与预算占位

| 组 | 最低规模 | 单回路数据规模（1s 网格推算） | 预算 |
|---|---|---|---|
| 24h 组 | ≥100 回路 | 86400 点/回路/tag | **占位：___ s/批，建议目标待 DEC-05 冻结**（不得沿用小时批 600s 线性外推冒充） |
| 30d 组 | ≥10 回路 | 2,592,000 点/回路/tag（实际查询分辨率/降采样路径执行前核对；现行性能边界参照：LTTB 降采样 maxPoints=2000、30 天时间窗口） | **占位：___ s/回路，建议目标待 DEC-05 冻结** |

### 7.2 转异步判据框架（引用既有口径，不发明新阈值）

1. **同步预算线**：单请求/批超过【待冻结】同步时限 → 转异步任务并即时返回受理与阶段进度（参照 04§4.2"人工历史辨识：重算异步；阶段进度首响应≤2s，典型窗 P95≤120s"模式，超长窗另报预算）；
2. **资源预算线**：点数/内存超限 → 明确拒绝并给出不可判定原因（参照 C20"超预算转异步或拒绝"），**不静默截断**；
3. **三态报告**：通过 / 转异步 / 不可判定分别记录分母，转异步不计入同步通过，不可判定不冒充成功（04§4.1 原文"记录预算/转异步/不可判定"）。

执行阶段：P5 容量期内边界试验，P7 复核；冷/热批次与清理策略同 §1.1/§6 第 4 项口径。

---

## 8. 未决项清单

| # | 未决项 | 归属 | 阻断 |
|---|---|---|---|
| 1 | §6 七项 DEC-05/DEC-08 裁决（数据来源与生成、硬件、keeper 与隔离、清缓存 namespace、完成屏障、命令冻结、长窗预算） | 用户裁决 | G0 收口、P5/P7 |
| 2 | 环境 Owner / 端口 Owner 确认（§5.3 占位） | DEC-08 | G0"环境 Owner 明确" |
| 3 | integration 精确命令的 Gate 映射冻结表（§2.2 ③ 型命令按 Gate 细化） | P0 收口（DEC-08 后） | 各 Gate 执行 |
| 4 | 存量迁移演练"隔离库置于旧 head"的供备方式细化（旧引导 SQL+stamp vs 库快照恢复） | P7-03 | 迁移演练 |
| 5 | C11 任务侧 PID 记录渠道核对（代码现况是否有现成 task→PID 留痕，决定取证是否仅靠 `inspect active`） | P2 执行前核对 | C11 取证 |
| 6 | 30d 查询实际数据分辨率/降采样路径核对（§7.1 占位推算的实测确认） | P5 边界试验 | 长窗组 |
| 7 | `e2e/fixtures/auth.ts` 的 `E2E_API_BASE_URL` 默认值核对（执行前） | e2e 执行前 | §2.1 #10 |
| 8 | 长窗组预算数值（§7.1 占位 ___） | DEC-05 | 长窗组 |
| 9 | 场景 B 参评资格与响应类别配比（依赖 P0-02 数值 profile 冻结） | P0-02 交付后 | 场景 manifest 冻结 |
| 10 | 本文档非作者复核 | 主协调安排 | 文档冻结（不影响实现 Gate） |

---

## 附：本轮执行纪律确认

- 业务代码/配置/DB/服务只读；未运行任何测试/基准/pytest/playwright；§2 全部命令状态 NOT_RUN。
- 唯一写入文件：本文档。
- git 仅只读命令；开工/收工 HEAD 见文首。
