# CLPM-MVP 评审整改收口报告（S7 验收登记）

日期：2026-09-13
整改方案：docs/过程文档/2026-09-13-review-remediation-plan.md
问题来源：2026-09-13 四路只读评审（接口 356 条路由 / 算法约 1.7 万行 / 前端 12 万行 / 数据链路与 Celery）

---

## 1. 结论摘要

**已落地 31 项**，覆盖 S0/S1/S2/S4/S5/S6 六条轨道；S3（算法契约与量纲）因受保护清单
未获解冻授权而**整段未启动**。

门禁状态：ruff check + format、pytest 4843 passed / 340 skipped / 34 xfailed、
前端 eslint 0 error + typecheck + vitest 全绿。

---

## 2. 已落地清单（按轨道）

### S0 止血（9 项）

| 编号 | 内容 | 关键证据 |
|---|---|---|
| G01 | fast_rate 指数溢出（ratio>709 抛 OverflowError → 整回路无评分） | 新增溢出回归用例 |
| G02 | auto_rate 量纲错配（UTILIZATION 分支实质永不触发） | 修在编排器边界，既有夹具语义即正确 |
| G03 | BizError 关键字误用（本该 400 抛 TypeError 变 500） | 全量回归 |
| G04 | GET /loops 的「HTTP 200 携失败码」（全仓唯一） | 改写固化旧行为的用例 |
| G05 | 无效角色 ENGINEER（该端点实际仅 ADMIN 可用） | 保守收敛，行为不变 |
| G06 | 权重和 0 返回「0 分 + 可信度 A」 | 改写固化旧行为的用例 |
| G07 | 幂等键跨用户/跨端点回放（/auth/login 含 token） | 新增跨调用方隔离用例 |
| G09 | tags pageSize 上限 10000 | — |
| G10 | 批量入参无数组上限 | — |

### S1 守护网（3 项）

| 编号 | 内容 | 关键证据 |
|---|---|---|
| G47 | CI 加 PostgreSQL + 生产引导路径门禁 | 真实 PG 上「引导 SQL → stamp head → alembic check」零漂移 |
| G39 | 恢复 OpenAPI 契约漂移守护 | 由整文件 skip 转为 18 项实跑；新增 10 项检测器自检 |
| G48 | 核心指标数值金标准 | 5 项硬断言 + 2 项 strict xfail 实证缺陷 |

### S2 数据可信度（7 项）

| 编号 | 内容 | 关键证据 |
|---|---|---|
| G11 | payload_hash 空串（同 ts 实时事件被永久判冲突丢弃） | 7 项回归含「空 hash 不再丢实时值」 |
| G12 | 读路径静默截断 + 恒 1s 网格不降采样 | 5 项翻页回归；网格改用 interval_s + 源码级守护 |
| G13 | 导入缺 tsEnd<=now-5min 背压 | 6 项回归（端点 4 + 服务 2） |
| G14 | point 布局并发共享 AsyncSession（既有红线被绕过） | 三处改独立短会话 |
| G15 | precalc 读侧不取最新行（排名重复 64 份） | DISTINCT ON 取最新 |
| G16 | 「回路数」按「回路×小时行数」统计 | 改 COUNT(DISTINCT loop_id) |
| G17 | 缓存读写失败无降级 | 2 项回归（Redis 故障降级为未命中） |

### S4 调度与任务终态（5 项）

| 编号 | 内容 | 关键证据 |
|---|---|---|
| G28 | beat 条件化改 conf 不生效（热插拔/周期/热重载三处静默失效） | **真实 Scheduler 行为测试**（含 conf 未被改动的反向证明） |
| G29 | 批量异常被吞，全失败仍记 SUCCESS | 失败率熔断 + 5 项回归 |
| G30 | 锁 TTL 7200s > 硬超时 1800s（重投副本静默 skipped） | TTL 不变量 + 锁冲突改抛错 |
| G31 | Beat pidfile 路径不一致 + PID 复用即永不启动 | 新增「陈旧 pidfile 不得阻止启动」回归 |
| G32 | visibility_timeout 9000s < 导入 time_limit 86400s | 不变量断言 visibility_timeout > max(time_limit) |

### S5 接口契约与权限（2 项）

| 编号 | 内容 | 关键证据 |
|---|---|---|
| G33 | 错误处理旁路（DSL 校验成 500、reports/modules 裸 HTTPException） | DSL 全局注册 + 6 处换 BizError |
| G38 | /health/db-connections 未鉴权且经 nginx 前缀匹配暴露 | 2 项边界回归（含 liveness 必须保持公开） |

### S6 前端可信与体验（2 项）

| 编号 | 内容 | 关键证据 |
|---|---|---|
| G40 | 工作台造数（伪造序列/硬编码图例数值/编造工单状态/demo 占比/无源指标） | 源码级守护扫描全部 .vue（含注释内的反模式） |
| G41 | 首屏请求风暴（5 Tab 全挂载 → 12 并发） | Tab 懒挂载 + 移除 5 处冗余 deep watch |

---

## 3. 未落地清单与原因

### 3.1 因受保护清单未解冻（需用户授权）

S3 算法契约与量纲**整段未启动**，涉及 10 项，其中两项已有可执行证据：

- **G18** accuracy 的 e_max 口径倒挂：恒定余差走 0.05U 分支，而数据驱动分支使 r 成峰均比
  ——单个大偏差抬高 max|E|、压低 r，反而**提高**准确率。*（S1-c 已用 xfail(strict=True) 固化实证）*
- **G20** _clamp(nan) 因 Python 的 min(100,nan)==100 / max(0,100)==100 被静默放大为上界，
  accuracy/stability 等「越高越好」指标会把 NaN 报成**满分**。*（同上，已实证）*
- **G19** stiction 双门控方向相反：R² 取线性相关平方、b/a 取 PCA 轴比，可检出带仅
  |rho| 在 [0.707, 0.835)，正圆（最严重粘滞）恒不检出。
- 另有 G21（SOPDT 缺 tau 致推荐 Kp 差 2 个数量级）、G22（融合非 D-S 且只取正证据）、
  G23（输入契约与量纲单一事实层缺失）、G24~G27。

受影响文件：metric_calculator/{accuracy,stiction,base}.py、diagnosis_operators/、
preprocessing/pipeline.py 等，均在 tests/golden/refactor_protected_manifest.json 冻结清单内。

**解冻方式**：S0 已有先例——按授权用 REFACTOR_UPDATE_GOLDEN=1 重新固化，清单 diff 可控
（S0 那次仅 2 个哈希变动、其余 37 个文件零变动），并在提交信息中逐项登记理由。

### 3.2 因范围/成本未做（无阻塞，可后续排期）

| 编号 | 未做部分 |
|---|---|
| G08 | 非归一化信号仍套用 PV 量程（MODE/PID_* 被判超量程 → 自控率类指标 INCONCLUSIVE） |
| G12 | 缺端到端断言（30 天窗口断言 len(timestamps) <= maxPoints）；各调用点 interval_s 是否已按 maxPoints 反推未逐一核对 |
| G14 | point 布局**无结构性守护**（既有 test_runtime_regressions 只覆盖 legacy） |
| G15/G16 | **无行为测试**：DISTINCT 聚合由数据库执行，mock 会话验不了效果，需真实 PG 集成断言 |
| G17 | 缓存键仍缺运行时算法参数版本（改阈值后最长 1h 脏命中） |
| G29 | 端到端失败注入未覆盖；failed 明细未写入 TaskRecord.result，UI 看不到 |
| G30 | Beat 路径该小时仍无快照（现为 FAILED 终态可见，但「算没算」需窗口完成标记才能对外可查） |
| G31 | 只修「该启动却被短路」，未处理「该停止却被漏杀」 |
| G32 | celery_task_total **仍无埋点**（只有定义）：需 task_postrun/task_failure 接线，且 worker 侧须配 Prometheus multi-process 或 Pushgateway |
| G33 | 错误码集中定义（app/core/error_codes.py）未做，182 个字面量错误码仍分散 |
| G38 | nginx location /health 仍为前缀匹配（由后端鉴权兜住）；/health/ready 失败信息仍回显异常类别名 |
| G40 | 守护为**源码级**，不能证明渲染结果正确 |
| G41 | **无自动化测试**（请求次数需挂载组件 + mock 请求断言） |
| G51 | 迁移链不能从空库构建：已按生产口径规避，补 base 迁移未做 |

---

## 4. 待用户决策（阻塞项）

1. **受保护清单解冻授权**（阻塞 S3 全部 10 项，已连续 14 轮报告）：
   - 选项 A：授权 S0.1(G08) + S3 所需解冻，逐项登记理由；
   - 选项 B：仅授权 G08 的 pipeline.py；
   - 选项 C：维持冻结，S3 本周期不启动。
2. **G51 修法**：补真正的 base 迁移使「空库+迁移」与「引导 SQL」等价，还是固化
   「01_schema.sql 为唯一基线」口径（现为后者）。
3. 其余方案内决策点（st_loop_data 最终处置、L5 幻影等级、是否引入 OpenAPI→TS 类型生成、
   回路数口径定名、CI 是否引入 TDengine、审计口径、IA 四入口收敛）见方案 §5。

---

## 5. 本周期提交记录（最近 32 条）

```
5cb732ad perf(S2): 逻辑宽表网格改用 interval_s，恢复降采样能力（G12 降采样部分）
a4094bc8 perf(S6): 工作台 5 个 Tab 移除多余的 deep watch（G41 完成）
2751f854 fix(S6): 数据流转图停止断言未知状态与无源指标（G40 完成）
2fde4681 fix(S6): 适用性卡停止 demo 占比兜底，无数据即不渲染（G40 续）
fa694ea2 docs(process): 补登 G40/G41 并清除过期占位行（S7 台账对齐）
5a8ebe97 perf(S6): 工作台 Tab 懒挂载，首屏请求从 12 降到 2~3（G41）
3d82f0cf fix(S6): 异常回路表停止用 severity 编造工单状态（G40 续）
63b14a01 chore(S6): 修 G40 守护用例的 import 排序，并更正上一提交信息
a295a558 fix(S6): 工作台停止前端造数（G40）
a38a48d4 fix(S5): 模块守卫换 BizError，收口错误处理旁路（G33 完成）
126dc37a style: ruff format app/core/exceptions.py（G33 提交遗漏格式化）
440e32df fix(S5): 错误处理旁路收口——DSL 校验 400、reports 换 BizError（G33 部分）
85661a99 fix(S5): 运维端点加 ADMIN 鉴权并脱敏异常原文（G38）
8b149b45 fix(S4): broker visibility_timeout 覆盖最长任务，消除导入并发双跑（G32）
c72c7ab9 fix(S4): Beat pidfile 路径单源 + 启动检查核对 PID 归属（G31）
2567478e docs(process): G31 尝试后回滚登记（含下一轮入口）
10dbec99 fix(S4): 小时评估锁 TTL 对齐硬超时，锁冲突改为抛错（G30）
6ff02554 fix(S4): 批量失败熔断，系统性故障不再被记成 SUCCESS（G29）
7492f238 fix(S4): beat 条件化改作用于运行中的 Scheduler（G28）
3930a1c3 fix(S2): point 布局元数据查询改走独立短会话，消除并发 AsyncSession 红线（G14）
5584630a fix(S2): 投自动回路占比改按去重回路口径（G16）
6ecd5e49 fix(S2): L1/L2 缓存故障降级，不再让 Redis 抖动拖垮 KPI 计算（G17 降级部分）
73fd7600 fix(S2): 工作台预计算读侧只取最新行（G15）
5d4d862a fix(S2): read_events 分片内翻页，消除静默截断（G12 截断部分）
8ca0dbe9 fix(S2): 导入写入真实 payload_hash，空 hash 不再静默丢弃实时值（G11）
e0451b13 fix(S2): 历史导入窗口背压校验（G13）
7a7ddcf4 test(S1-c): 核心指标数值金标准骨架（G48）
e1a2c51d test(S1-b): 恢复 OpenAPI 契约漂移守护（G39）
38b2f524 ci(S1-a): CI 加 PostgreSQL 并验证生产引导路径
22aa8e0c docs(process): 新增评审整改方案与 S0 状态登记
b49e70f1 fix: S0 止血——9 项静默错误修复（算法/接口/安全）
3fb42a90 chore(release): 锁定 v7.1.0 基线
```

---

## 6. 交付状态说明

- 本地 main 领先远端 **8 个提交**：github.com 在本周期多轮不可达
  （20.205.243.166 间歇被黑洞、140.82.112.3 时通时断），门禁全绿但未能推送。
- **未执行任何部署、未做任何生产数据修复、未向 origin 推送**（pushurl 锁定）。
- 全部改动均经本地门禁（ruff / pytest / eslint / typecheck / vitest / alembic check）。
