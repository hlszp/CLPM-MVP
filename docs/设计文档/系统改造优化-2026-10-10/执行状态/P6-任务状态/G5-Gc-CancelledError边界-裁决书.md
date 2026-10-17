# G5-Gc CancelledError 批量成功计数边界 — 裁决书

> 2026-10-14，任务 G-c（G5-D04 分组，静态核查员）。依据 `G5-D01-04-实证方案.md` D04/G-c 节执行。
> **全部离线**：静态枚举对象 = G5 固定候选运行源码 `/private/tmp/clpm-g5-followup-20261014/backend`（只读）；
> 离线表征 = P6 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014/backend`（git HEAD=`6c92cf69`，与 G5 候选同 SHA；
> 两涉事文件 `app/tasks/kpi_calc.py`、`app/services/node_performance.py` 经 `diff -q` 逐字节相同）。
> 未连共享 DB/Redis/TDengine，未改任何源码，未跑迁移，未投递任务。临时产物全部在 `/tmp/g5gc/`。

## 结论（先行）

**存在 CancelledError 计数边界，且为两种不同形态：**

| # | 位置 | 形态 | 后果 |
|---|---|---|---|
| B1 | `app/services/node_performance.py:2126-2132`（节点级聚合汇总，hourly 级联链） | **静默误收为成功**（else 分支无 `.get` 拦截） | 取消节点计入 success、failed 漏报、该节点窗口快照缺失且无告警 |
| B2 | `app/tasks/kpi_calc.py:1247→1257`（回路级 `_summarize_batch_results`） | 非误收——`.get` 触发 **AttributeError 崩溃** | hourly 链：TaskRecord=FAILED 与 Celery=SUCCESS 终态矛盾 + **整批回退二次全量重跑**；backfill 链：窗口全回路误计 failed |

外加 B3（外层取消痕迹悬挂）：壳层 `except Exception` 不捕 CancelledError → TaskRecord 永 RUNNING、attempt 行永 STARTED。

**对 G5 已记录结论（b1000：850 SUCCESS/150 INCONCLUSIVE/failed=0）有效性：不影响**（论证见 §4）。

---

## 1. 静态枚举（行号均以 6c92cf69 候选源码为准）

### 1.0 gather 点位与 return_exceptions 口径

hourly 批计算链（回路级 → 级联节点级）共 3 处 `asyncio.gather`：

| 点位 | return_exceptions | 说明 |
|---|---|---|
| `app/tasks/kpi_calc.py:1146-1148` | **显式 True** | 回路级主批 gather，结果进 `_log_batch_phase_timings`(1149) → `_summarize_batch_results`(1411) → `_assert_batch_health`(1413) |
| `app/services/node_performance.py:2120` | **显式 True** | 节点级聚合 gather（`calculate_node_kpi_hourly` 级联链 `kpi_calc.py:4494`、backfill 节点聚合 `kpi_calc.py:5373/5636` 共用），结果在 2125-2132 逐元素分类 |
| `app/tasks/kpi_calc.py:942` | **默认 False**（预热 `_prewarm_cache_for_loops`） | 非计数点：`errors` 列表只收子函数返回的字符串；子任务 CancelledError 不被 `_prewarm_one` 的 `except Exception`(932) 捕获 → gather 首异常直接抛出，**无逐项误计可能**（影响为预热整体中止，异常上行） |

> 默认口径回答：`asyncio.gather` 默认 `return_exceptions=False`；链上两处显式 True 的才是结果汇总点。

### 1.1 B2：回路级汇总崩溃链（`kpi_calc.py:1225-1261`）

分类链逐支（行号原文）：

```
1247  if isinstance(result, Exception):      → failed      # ← 漏 BaseException 系
1254  elif result is None:                   → failed
1257  elif result.get("status") == "INCONCLUSIVE":  → inconclusive   # ← CancelledError 在此撞 AttributeError
1259  else:                                  → success     # ← 对 CancelledError 不可达（1257 先崩）
```

- `asyncio.CancelledError` 自 py3.8 起继承 `BaseException`（本仓 `requires-python >=3.12`，实测 venv 3.12.13，MRO=`[CancelledError, BaseException, object]`）→ 不命中 1247；非 None 不命中 1254；`BaseException` 无 `.get` → **1257 抛 `AttributeError: 'CancelledError' object has no attribute 'get'`**。`else`(1259-1260) 对 CancelledError **不可达**——"误计 success"在回路级不成立，但代之以汇总器崩溃。KeyboardInterrupt/SystemExit 同路径（理论项）。
- 崩溃后数据流（按调用点分列）：
  - **`kpi_calc.py:1411`（hourly 主链 `_do_calculate`）**：1384 的批调用与 1411 的 summarize 均无 try 包裹 → AttributeError 上行 → `_do_hourly_with_tracking:453 except Exception` → 写 TaskRecord=FAILED(454-459) 后 raise → `calculate_hourly_kpi._body:217 except Exception` 命中（AttributeError 是 Exception）→ **触发 218-227"回退到直接计算"：整批第二次全量重跑**。回退跑干净则 Celery=SUCCESS，但 TaskRecord 已在 454 写成 FAILED —— **双终态矛盾 + 批级双跑**（G30/止血③在 206-216 注释中为 HourlyWindowBusy/BatchSystemicFailure 修掉的正是这一类"回退放大"，AttributeError 无专用类型仍可漏入）。第一趟已逐回路 commit 的结果（1076-1084 每回路独立 commit）按 UPSERT 幂等被第二趟覆盖，不重复计数，但墙钟 ×2。
  - **`kpi_calc.py:1803`（自定义批量 `_do_calculate_custom_batch`，Celery 入口 544 `calculate_custom_batch_kpi`）**：函数内无 try 包裹 → AttributeError 上抛 → 命中 Celery `autoretry_for=(Exception,)`(537) → 整批重试（同为双跑放大，非静默成功）。
  - **`kpi_calc.py:4738` / `5336`（backfill 两链）**：分别被 4755 / 5342 `except Exception` 捕获 → 该窗按"窗口失败"处理，4738 路径 `agg["failed"] += len(loops)`(4757) 把**整窗回路（含已 commit 成功者）误计 failed**——失败方向多计，非成功虚高，但掩盖真实原因且与已落库结果矛盾。
- `_assert_batch_health`(1202-1222) 消费的是 summary dict；崩溃发生在 summarize，熔断器**永远接触不到** CancelledError 信息。

### 1.2 B1：节点级汇总静默误收（`node_performance.py:2119-2141`）

```
2119  tasks = [asyncio.create_task(_process_node(node)) for node in nodes]
2120  results = await asyncio.gather(*tasks, return_exceptions=True)
2125  for r in results:
2126      if isinstance(r, Exception):   → failed_count    # ← 同样漏 BaseException 系
2129      elif r is None:                → skipped_count
2131      else:                          → success_count    # ← 无 .get 拦截，CancelledError 直落 else
```

- 与 B2 的决定性差异：**分类链没有 `.get` 调用**，CancelledError 实例（非 Exception、非 None）直接落 2131 else → **`success_count += 1` 静默误收**。
- 上游：`_process_node` 的 `except Exception`(2115) 不捕 CancelledError → 该节点无 rollback、无 `save_node_snapshot`/commit（2112-2113 被跳过）→ **快照缺失但计数为成功**。
- 下游：`calculate_node_kpi_hourly`(`kpi_calc.py:4393-4419`) 只对结果 dict 打日志(4501-4507)并返回，无健康熔断 → Celery=SUCCESS。**failed=0 可被"取消节点"保持成立**。hourly 级联（1421）、backfill 节点聚合（5373/5636）共用此函数，三链同边界。

### 1.3 B3：外层取消（gather await 点被取消）痕迹悬挂

外层任务在 `kpi_calc.py:1146` await 被取消时，gather 取消全部子任务并直接抛 CancelledError，**results 根本不产生**（离线表征 §2 实证）。随后：

- `_calculate_once:1085 except Exception` 不捕 → 子任务无 rollback 尝试、无终末 FAILED record（1094-1125 跳过）；`finally`(1127-1144) 仍执行（进度回调 + `close_session_guarded`）——已在此前 commit 的回路结果保留但被整批丢弃，无逐回路伪造 SUCCESS record 产生。
- `_do_hourly_with_tracking:453 except Exception` 不捕 → **TaskRecord 停在 RUNNING 永不终态**（锁在 461-467 finally 正常释放，不阻碍重投）。
- `_attempt_log_wrap:251 except Exception` 不捕 → **attempt 行只落 start(246) 不落 finish(252-258)**，`calculation_result_attempt` 悬挂 STARTED。
- `calculate_hourly_kpi._body:217 except Exception` 不捕 → **不触发回退双跑**（此点行为正确）；CancelledError 经 `AsyncTask.run_async`(`celery_app.py:146-154`) 原样出 loop，`autoretry_for=(Exception,)` 不匹配 CancelledError → 不自动重试。Celery trace 层对 BaseException 的终态落法属运行时行为，留给 G-c 动态对账（独立资源 revoke 实测）确认。
- `run_async` 每任务新建/关闭 event loop（150-154），无模块级 lock 绑定问题。

### 1.4 壳层 `except Exception` 全清单（不捕 BaseException 的逐点确认）

`kpi_calc.py:217`（回退分支）、`:251`（attempt 收尾）、`:453`（TaskRecord FAILED 终态）、`:1085`（回路级 rollback/终态）、`:1023`（连接耗尽重试）、`node_performance.py:2115`（节点 rollback）、`celery_app.py:170/216…`（死信发送等）——全部 `except Exception`，均不捕 CancelledError/KeyboardInterrupt/SystemExit。其中产生"边界"语义的为 §1.1–§1.3 所列；其余（如 429/466/778/1088 等 best-effort 分支）不捕 CancelledError 仅致跳过着陆日志，无计数后果。

---

## 2. 离线表征（`/tmp/g5gc/repro_gc.py`，P6 venv py3.12.13，`CLPM_TEST_MODE=1`，真实源码函数导入调用，零 I/O）

15/15 PASS（脚本与完整输出留档 `/tmp/g5gc/`）：

| # | 表征 | 结果 |
|---|---|---|
| 0 | 语言基线 | CancelledError MRO 不含 Exception；无 `.get`；KInterrupt/SystemExit 同 |
| 1 | 子任务自发 CancelledError + dict/ValueError 混合喂 `gather(return_exceptions=True)` | CancelledError 实例**确实落进 results**（types=`[dict, CancelledError, ValueError]`） |
| 2 | 外层取消 gather await | 抛 CancelledError 且**无 results 返回**（子结果整体丢弃） |
| 3a | 真实 `_summarize_batch_results([ok, inc, ValueError, None])` | 基线正确 `{success:1, inconclusive:1, failed:2}` |
| 3b | 真实 `_summarize_batch_results([ok, CancelledError])` | **抛 `AttributeError: 'CancelledError' object has no attribute 'get'`**（证实 1257 崩溃、非误计 success） |
| 3c | KInterrupt/SystemExit 单独喂入 | 同 AttributeError 崩溃 |
| 3d | `_assert_batch_health({failed:0,...})` | 不熔断——若 summarize 不崩则取消无痕迹（佐证熔断器对取消盲区） |
| 3e | 真实 `_log_batch_phase_timings([ok, CancelledError, inc])` | 不崩；isinstance-dict 过滤静默跳过，日志分母如实显示 `回路=2/3` |
| 4 | 真实 `_attempt_log_wrap`（内存 fake result_ledger，不改源码） | Exception → `[(start),(finish)]`；**CancelledError → 只有 `(start)`，finish 不落**（attempt 行悬挂实证） |
| 5 | `node_performance.py:2122-2132` 逐字节源码块 exec，results 混入 CancelledError | **`success_count=2`（含 1 取消节点）**，failed=1, skipped=1——B1 静默误收实证 |
| 6 | 壳层 `except Exception` 语义 | 不捕 CancelledError |

## 3. 影响面评估

| 场景 | 现行行为 | 业务后果 |
|---|---|---|
| 节点聚合期取消（worker 关停/revoke/超时击中 2120 批） | **B1 静默误收**：节点 success 虚高、failed 漏报、该节点该窗快照缺失 | 聚合层"看起来算过其实没算"——快照缺失要到读端才发现；**failed=0 记录可能含取消节点** |
| 回路级批中子任务自发 CancelledError（显式 cancel/内部控制流泄漏） | **B2 AttributeError 崩溃**：hourly→FAILED/SUCCESS 双终态矛盾 + 整批二次全量重跑；custom→autoretry 整批重跑；backfill→整窗误计 failed | 故障窗内双跑放大负载（连接风暴类教训 206-216 注释）；自动任务页状态与 Celery 终态矛盾 |
| 外层整任务取消（1146 await 点） | **B3 痕迹悬挂**：TaskRecord 永 RUNNING、attempt 行永 STARTED；不回退双跑（正确）；已 commit 回路保留但无批级记录 | 自动任务页"一直在跑"假相；attempt 账本缺收尾行，对账口径失真 |
| 预热链（942，默认 False） | 子取消 → gather 直接抛出，预热整体中止 | 无计数边界；异常上行按各调用点壳层处理 |

**修复建议（按方案口径本项不绑定修复，供另行裁决）：**

1. 节点级（B1，必须修）：`node_performance.py:2126` 改 `isinstance(r, BaseException)`，或在 2126 前加显式 `except/asyncio.CancelledError` 分支单列 `cancelled_count`（诚实化原则：单列比并入 failed 更可观测，且返回 dict 增键须同步 `kpi_calc.py:4501-4507` 日志与读方口径）。
2. 回路级（B2）：`_summarize_batch_results` 1247 前加 `isinstance(result, asyncio.CancelledError)` 显式分支（计 cancelled/failed 并留 failed_detail），使 1257 不再被 BaseException 撞崩；或 1247 放宽为 `BaseException`（注意 KeyboardInterrupt/SystemExit 语义随之一并归入 failed，需评审）。
3. 壳层（B3）：`_attempt_log_wrap:251` 增 `except asyncio.CancelledError`（落 finish(error=cancelled) 后原样 raise）；`_do_hourly_with_tracking:453` 同款（TaskRecord→CANCELLED/FAILED 终态）；`calculate_hourly_kpi._body:217` 回退分支前显式排除非 tracker 故障类异常（止血③同款专用类型思路，防双跑）。

## 4. 对 G5 已记录 "failed=0" 结论有效性的裁定

**不影响。** 三条独立理由：

1. 移交登记"本批无取消证据"成立且可证伪：B2 一旦触发必留大声痕迹（AttributeError 堆栈 + TaskRecord FAILED + 整批双跑 ⇒ 墙钟 ~2×、trace 行数 ~2×），与 b1000 单趟 789.442s/850+150 分布/trace 量级全部矛盾；未见任何此类痕迹。
2. b1000 的 failed=0 出自回路级 `_summarize_batch_results` 正常返回路径；该路径下每个 results 元素都是 dict（SUCCESS/INCONCLUSIVE），不存在 BaseException 实例（否则必崩，见 §2-3b）。
3. 节点级 B1 虽可静默误收，但 b1000 的 26 节点证据为**直接只读观察**（屏障九条件之 nodes 条件，观察边界 918.018s 如实登记），非计数推导——即使计数层有盲区，已观察到的节点行不因本边界失效。

边界定性：**未来取消场景下的潜伏缺陷**（B1 静默误收最危险，B2 崩溃双跑次之，B3 痕迹悬挂再次），对已留档批次无追溯性影响。

## 5. 留档与后续

- 离线脚本与输出：`/tmp/g5gc/repro_gc.py`（15/15 PASS，可随时重跑复核）。
- 静态基线 SHA：`6c92cf6944d40bc2c92c9296110711933be28ec7`（两涉事文件 G5 运行源码与 P6 工作树逐字节相同，diff -q 实证）。
- 本裁决覆盖方案 G-c 节的"静态枚举 + 离线表征"两层；**动态对账层（独立 PG/TD/Worker 上 revoke(terminate=True) 与取消 API 的两时点实测）按方案仍属后续项**，本裁决未执行、不代为关闭；Celery trace 层对 CancelledError 的终态落法与重投幂等恢复以该层实测为准。
