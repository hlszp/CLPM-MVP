# CLPM 全维度评审与优化整改计划（v1.0）

> 日期：2026-07-28 ｜ 基线：`a738147c` ｜ 方法：5 路并行只读评审（后端架构/API、算法、数据层、前端/UI-UX、部署/安全/测试）+ 主 agent 对 P0 结论抽查复核
> 输入：`docs/过程文档/vibcoding-handoff-prompt.md`、`README.md`、全量代码

---

## 执行进度（2026-07-28 更新）

**Phase 0：✅ 完成**
- T0.1：P0-3 实证确认（dev TDengine REST：naive 字符串按 +8 解释；写入 +8 墙钟 × 读取 naive UTC → 计算窗口偏移 8h）。**D1 已定**：查询边界统一带 Z UTC ISO 串
- T0.2：Cohen-Coon 代码=设计文档 §6.6，不动；面积法 §6.1.3 文档本身有误，按经典修正 τ=A1*−θ；SIMC 代码偏离文档 §6.7 属真 bug
- T0.3：**D4 已定**：EXPERT 仅诊断+整定（首页 `/diagnosis`）、SPONSOR 仅汇总视图（首页 `/performance`），以契约 §5 + UI/UX §4.2 为准
- T0.4：代码层事实确认（backup.sh 无凭据静默 SKIP），生产侧现状待用户验证

**Phase 1：✅ 完成（两波 9 个子任务，全量 pytest 2691+ 通过）**

第一波（7 个子任务）：

| 任务 | 状态 | 交付摘要 |
|---|---|---|
| T1.1 稳态时间语义（P0-1） | ✅ | arma.py 新增 SettlingStatus 四态；never_settles 快速率≈0 分；identification_failed → INCONCLUSIVE；14 新用例 |
| T1.3+T1.9 指标包 | ✅ | 恒定余差按量程扣分；trip 6 位精度；stability exp(-x)；stiction r² 门控；长度不齐/解析失败/振荡三处；8 测试文件 112 用例 |
| T1.4+T1.5 预处理 | ✅ | JUMP/SPIKE 量纲按 is_normalized；FROZEN 改 MARK_ONLY + instrument_fault 复合判据（≥5min 且 OP 变 PV 不动）；387 用例 |
| T1.2/1.6/1.7 诊断核心（P0-2） | ✅ | 饱和诊断数值 MODE（含 APC）+ op/mode 成对过滤；Kano flatnonzero 索引映射；D-S 改同标签融合；热路径向量化；26 新用例 |
| T1.8 整定 | ✅ | 面积法 τ=a1*−θ（文档 §6.1.3 同步修正）；两点法/负 tau 失败显式化；SOPDT R²≥0.5 闸口；SIMC PI min 规则；RK4 微分对 PV；15 新用例 |
| T1.10 部分（综合评分 D2） | ✅ | **D2 已定**：核心指标缺失/INCONCLUSIVE/E 级 → 综合评分整体 INCONCLUSIVE；D 级标注 low_confidence_inputs；trend/monitor millis 与 TZ 无关 |
| T2.1（P0-3 提前） | ✅ | _format_ts 带 Z；taosrest 连接 timezone=UTC；Redis 1h 缓存 epoch 比较可命中；data_integrity 桶键对齐；实证 curl 验证通过 |

第二波（T1.10 剩余 + 扰动删失）：

| 任务 | 状态 | 交付摘要 |
|---|---|---|
| 振荡两套实现统一 | ✅ | 诊断侧复用 KPI 侧 OscillationRateCalculator 核心函数，同口径判定；FFT 保留走同标签融合 |
| FFT 修复 | ✅ | 幅值 2·\|X(k)\|/Σw + Hann 窗（bin 中心正弦幅值精确恢复 10.0→9.998）；阈值入配置；golden 基线再生 |
| 硬编码阈值配置化 | ✅ | Choudhury NGI/NLI、阶跃过冲/衰减比/SSE、响应迟缓 ratio、FFT 阈值全部入 _THRESHOLD_SCHEMA + 种子迁移 `c3d4e5f6a7b8`（已 upgrade 应用）；响应迟缓改真实秒 τ 按回路类型经验值，不随窗口漂移 |
| 扰动恢复删失事件 | ✅ | 未恢复事件 censored=True 不计入均值，censored_count 单列；fast_rate 回落逻辑天然兼容 |

文档同步：§6.1.3 面积法公式修正、§3.4.3 FROZEN 改 MARK_ONLY 口径、§6.8.1 增量 PID 微分对 PV，均已加注 2026-07-28 修订。

遗留（转 Phase 2-4）：fusedConfidence 语义变化（跨标签融合值→最高标签置信度）需前端知悉；stiction 低相关 INCONCLUSIVE 需前端知悉；`frozen_fault_min_minutes` 未接 sys_config 覆盖链路（outlier_params.py）；诊断 IAE min/max_ratio 未接 algorithm_config 配置链（默认一致）。

---

## 一、评审结论总览

共确认 **~90 项问题**，其中 P0 × 3、P1 × ~30、P2 × ~40、P3 若干。全部带文件:行号证据。项目工程化基础（异常处理体系、断点续传幂等、任务跟踪 Lua 原子化、统一组件体系）较好，但存在**算法方向性错误**和**数据正确性隐患**两类必须最先处理的问题。

### 三大 P0（方向性错误，先于一切修复）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| P0-1 | **ARMA 稳态时间"永不收敛/辨识失败"返回 0 → 快速率误判满分** | `arma.py:271-299`（未收敛返回 0.0）+ `fast_rate.py:130`（`actual_t<=0` → 100 分 "already_stable"） | 持续振荡/近不稳定回路快速率得满分，综合评分被虚高 |
| P0-2 | **输出饱和诊断 MODE 数值/字符串错配 → 诊断永久失效** | `diagnosis_engine.py:2336-2349`（`"AUTO" in str(1)` 恒假）vs `constants/mode.py:25-38`（数值 0-4）；附带 op/mode 索引错位（1061-1069 分别过滤后按位置配对） | OUTPUT_SATURATION 标签事实死亡 |
| P0-3 | **TDengine 读路径时区口径疑似错 8 小时（需先验证）** | 写入侧转 +8 墙钟（`data_import.py:48,1061-1089`）；读取过滤 naive UTC strftime（`tdengine_provider.py:252-258`）拼入 WHERE 被服务器按 +8 解释；趋势路径（`trend_service.py:219-224` 透传带 Z ISO）口径不同旁证 | KPI/诊断可能用 8 小时前的数据算当前小时快照 |

### 分维度重点发现（摘要）

**算法层**（除 P0 外）：
- P1：恒定余差 → 准确率 100%（`accuracy.py:81-98`）；JUMP/SPIKE 阈值量纲错配（原始量程×归一化值，量程≠100 时检测失效，`outlier_detection.py:483-487`）；PV 冻结检测误伤控制良好回路 → 全 KPI INCONCLUSIVE + 误报仪表故障（`outlier_detection.py:35,137-156`）；Kano 粘滞分段索引坐标系错乱（`diagnosis_engine.py:2645-2678`）；跨标签赔率乘积冒充 D-S 证据融合（`1588-1591, 3590-3635`）；FOPDT 面积法 τ=τ+θ 双重计入滞后（`tuning_algorithms.py:204-213`）；stiction 拟合度数学不可达死代码 + 圆团散点误报 SEVERE（`stiction.py:134-170`）；output_trip 被 round2 抹零恒 0（`output_trip.py:75`）；逐点 naive `.timestamp()` 违反性能红线（`diagnosis_engine.py:1152-1155, 3813-3849`）
- P2：SIMC 缺 min 规则（`tuning_algorithms.py:577-581`）；Cohen-Coon PID 系数存疑（`548-551`）；stability exp 溢出风险（`stability.py:99-100`）；两套振荡算法口径不一致；FFT 幅值少乘 2 无加窗；诊断阈值多处硬编码绕过配置体系；缺失核心指标"分子计 0 分母留权重"口径未定

**数据层**：
- P1：Redis 1h 实时缓存两侧墙钟口径不同永不命中（`tdengine_provider.py:161`）；gap backfill 遇无映射回路永久失败循环+告警风暴（`realtime_subscriber.py:583-587`）；完整性检查把 COV 稀疏列判缺失、结果系统性失真（`data_integrity.py:147-157`）；手工导入默认 overwrite 且不限 tsEnd 贴实时边缘可删实时行（`schemas/loop_data.py:53`）；宽表 subtable 名无白名单校验（注入面，`tdengine_native.py:230,312,411`）；tag 重关联导致历史数据孤儿化无处理（`loop.py:2130`）；空 DataBlock 负缓存 1h 回填不清除（`data_planner.py:542-551`）；`alembic check` 实测失败：target_id 类型漂移、生产索引缺失于模型（下次 autogen 会 DROP 生产索引）
- P2：TDengine 故障静默吞成"无数据"（`core/tdengine.py:205-256`）→ 误导完整性检查与 KPI；L2/L3 缓存 key 无 cfg_version；大窗口查询无内存控制（30 天 2.6M 行全量物化）

**后端架构/API**：
- P1：任务列表全量扫描+串行同步 `AsyncResult` 阻塞 event loop（`tasks.py:1003-1045,425`）；logout 不吊销 refresh token（`auth.py:389-411`）；手动标准评估双写任务记录且与 beat 无互斥锁（`tasks.py:485-528` × `kpi_calc.py:170-222`）；Prometheus label 用原始 path 基数爆炸 + `/metrics` 无认证（`core/metrics.py:79-80,91`）
- P2：权限码体系只定义不执行（服务端 0 处校验，SPONSOR/EXPERT 可读全部数据，`services/auth.py:47-92`）；dead_letter 队列无人消费 + worker 无 max_tasks 回收；改密 PUT 限流死配置 + 限流 IP 口径与代理不符；WS 认证接受 refresh token、不查黑名单、token 走 query；worker/beat pgrep 模式过宽可误判跳过启动、崩溃无看门狗；readiness 恒 200（`health.py:66-70`）；Excel 导入无大小上限

**前端/UI-UX**：
- P1：11 处 formatTime 重复实现 + 三套时间语义并存（`audit.vue:161` 等）；`/system/reports` 路由放行 IC_ENGINEER 但后端仅 ADMIN → 整页 403；`/loop/aas-sync` 路由/后端错位 + 0 个 v-permission；双重错误 toast（6+ 处）；`manage.vue` 3323 行 + 42 处 as any
- P2：scoreColor 两页阈值不一致且 null→红色（INCONCLUSIVE 被渲染成故障红，工业场景严重状态误报）；硬编码 hex 180+ 处违反 token 规范；轮询无可见性管理且单次失败永久停轮询（`use-loop-analysis.ts:207-209`）；全量拉取客户端统计/分页；空状态无原因无动作入口（数据导入引导断链）；EXPERT/SPONSOR 默认首页与可见范围和规范 §4.2 偏差

**部署/安全/测试**：
- P1：生产部署路径（`build-and-deploy.sh:415-441`）不跑 alembic 迁移、`deploy.sh:204` 迁移失败仅警告；TDengine 备份无凭据生产静默 SKIP（`backup.sh:76-77`）；回滚不可用（镜像从不打版本 tag）；生产零监控告警（grafana 未接入）；默认密码 admin123×5 无强制改密；celery-beat 健康检查探测错对象（`docker-compose.prod.yml:283`）；登录接口无速率限制
- P2：质量门禁纯人肉（lefthook 为空模板，部署路径无测试步骤）；单测全 mock 不触真实 DB/Redis（集成测试默认排除）；nginx 无 `client_max_body_size`（>1MB Excel 导入 413）无 CSP

---

## 二、优化目标（验收口径）

1. **正确性**：消除全部 P0/P1 算法方向性错误；KPI 对"振荡回路、恒定余差回路、饱和回路"三类已知故障注入数据集的计算结果符合控制工程预期；GB/T 44693.2 附录 B/F 用例通过率 ≥90%（Phase E 目标）
2. **数据可信**：计算窗口时区口径全链路统一并有实证验证；完整性检查结果与 COV 存储设计一致；缓存失效链路闭环（配置变更→L1/L2/L3 全失效）
3. **健壮性**：任务系统无 event loop 阻塞、无重复计算、失败可告警可追踪；TDengine/外部依赖故障与"无数据"可区分
4. **安全性**：logout/改密 token 吊销闭环；服务端权限码校验落地；登录限流+强制改密；WS 认证对齐 HTTP 口径
5. **专业性**：前端三套时间语义归一；INCONCLUSIVE 中性呈现；权限矩阵前后端+规范三方一致；色彩走设计 token
6. **可运维**：部署=迁移+验证一体（失败即中止）；镜像可回滚；TDengine 备份真实生效；基础监控告警接入

## 三、整改方案（六阶段）

### Phase 0：存疑项验证（0.5 天，只读/小改验证）

- T0.1 验证 TDengine 8h 偏移：比对某回路最近 KPI 快照窗口与其 TDengine 实际数据时段 + 服务器时区配置 → 确认后决定修复口径（统一 astimezone 到服务器时区 或 带 Z ISO 透传）
- T0.2 对照设计文档核实两处"存疑公式"：Cohen-Coon PID 系数（`tuning_algorithms.py:548-551` vs 设计文档 §6.6）、FOPDT 面积法 τ 定义（§6.1）——区分"笔误"与"有意对齐内部文档"
- T0.3 确认 EXPERT/SPONSOR 默认首页与可见范围：以 UI/UX §4.2 为准还是规范已过期 → 锁定权限矩阵整改基准
- T0.4 生产 TDengine 备份现状确认（是否真的从未成功备份）

### Phase 1：算法正确性专项（P0+P1 算法，~1 周）

按"问题定位→修复→测试验证→效果确认"闭环，每项配算法场景测试（复用现有 7 场景数据 + 新增故障注入用例）：

1. T1.1（P0-1）稳态时间不收敛语义：ARMA 辨识失败/未收敛返回 None+reason（never_settles/identification_failed/already_stable 三分支）；fast_rate 对 never_settles 按 0 分或上限时间代入衰减公式，identification_failed → INCONCLUSIVE
2. T1.2（P0-2）饱和诊断 MODE 判定统一走 `StandardMode` 数值（含 APC=4）；op/mode 同循环成对过滤修索引错位
3. T1.3（P1）准确率恒定余差退化：e_max=0 且 mean|E|>0 时按量程百分比扣分或 INCONCLUSIVE
4. T1.4（P1）JUMP/SPIKE 阈值量纲：按 is_normalized 传 (0,100) 或原始量程
5. T1.5（P1）FROZEN 误伤平稳回路：改 MARK_ONLY 或加"持续 N 分钟+OP 变 PV 不动"复合判据；instrument_fault 口径同步
6. T1.6（P1）Kano 分段索引映射修复；stiction 拟合度改椭圆宽度比+有效性门控，删不可达死代码
7. T1.7（P1）D-S 融合口径修正：仅同标签多算法融合，或语义降级为"综合异常度"且不写入各标签证据链
8. T1.8（P1）FOPDT 面积法 τ=a1*−θ；两点法/负 tau 兜底改"失败返回 None+reason"，禁止带病参数进整定
9. T1.9（P1）output_trip 精度改 4-6 位或改单位；stability exp 溢出改 `exp(-x)`；逐点 `.timestamp()` 热路径向量化（diagnosis_engine/kpi_calc/trend_service/monitor 四处）
10. T1.10（P2 打包）SIMC min 规则、Cohen-Coon 系数核实、FFT 幅值×2+加窗、振荡两套实现统一、诊断硬编码阈值纳入 DiagnosisConfig、综合评分缺失指标口径决策（整体 INCONCLUSIVE vs 权重重归一化，二选一并与 confidence 联动）

### Phase 2：数据正确性与一致性（~1 周）

1. T2.1（P0-3）按 T0.1 结论统一读路径时区；同步修 Redis 1h 缓存墙钟比较（改 epoch 比较）使其真正命中
2. T2.2 TDengine 故障与"无数据"区分：execute_sql/query 错误上抛或带错误标志，完整性检查/KPI 据此报错而非判缺失；`_parse_ts_str` 禁用 `datetime.now()` 兜底
3. T2.3 完整性检查口径：COV 列按前向填充后判定，仅 PV/OP 按点数；修 `_parse_dt` 时区与桶键错位
4. T2.4 手工导入：overwrite 强制 tsEnd ≤ now-5min（或默认改 skip）
5. T2.5 宽表 subtable 白名单归一化 + tag 名入口 pattern 校验（AAS 同步/Excel/schemas）
6. T2.6 tag 重关联治理：检测 subtable 名变化→确认告警+缓存失效；提供改名/搬迁工具的决策（可先告警+文档）
7. T2.7 缓存闭环：空 DataBlock 不写 L1；L2 key 加 cfg_version；失效器覆盖 `pdb*` 全前缀并挂到配置变更写路径；backfill 完成主动失效相关 key
8. T2.8 alembic 收敛迁移：对齐 target_id 类型/nullable、把 idx_kpi_snapshot_ts_loop 等生产索引补进模型 metadata；`alembic check` 纳入提交门禁
9. T2.9 gap backfill 过滤无映射回路（剔出 failed 口径，消除告警风暴）；实时写回子表补真实 TAG 元数据；实时行 ts 统一取 PV 角色 collectTime
10. T2.10 大窗口查询分片（>7 天按日分片流式处理）；NaN/Inf 写入拦截

### Phase 3：后端健壮性与安全（~1 周）

1. T3.1 任务列表分页改造：索引按时间窗截取+先分页后取详情+pipeline 批量 HGETALL；`_sync_task_status` 移出列表路径
2. T3.2 评估任务 RUNNING 超时清扫（对齐导入任务 sweep_stale_running_tasks）；result_expires 配置
3. T3.3 手动标准评估去重：`calculate_hourly_kpi` 接受外部 task_id；按小时窗口 SETNX 计算锁（手动 vs beat 互斥）
4. T3.4 权限码服务端落地：`require_perms("模块:操作")` 依赖，按实现契约 §5 逐端点收敛（先读端点后写端点）；tasks 列表越权可见性收敛
5. T3.5 token 生命周期：logout 吊销配套 refresh；黑名单 TTL 取 token 实际剩余寿命（修 30 天 remember-me 漏洞）
6. T3.6 WS 认证：校验 token type + 黑名单，token 改 subprotocol/header
7. T3.7 metrics：label 用路由模板；`/metrics` 加认证；readiness degraded 返回 503
8. T3.8 限流：改密 PUT 限流修复；限流 key 统一走 `get_client_ip`；登录加 IP+账号双维度限流（配合 Phase 5 强制改密）
9. T3.9 worker 治理：`-Q default,dead_letter` 或死信写 PG+告警；`worker_max_tasks_per_child`；pgrep 模式收窄+定期探活告警；日志 fd 泄漏修复
10. T3.10 Excel 导入大小/行数上限 + 流式处理；并发上限 TOCTOU 改 Redis 原子计数；任务进度计数修正

### Phase 4：前端专业性与一致性（~1 周）

1. T4.1 时间语义归一：删除 11 处本地 formatTime + task/detail dayjs 实现，统一 `utils/format.ts`；明确 naive 时间戳唯一解析约定写入 util 注释
2. T4.2 权限三方对齐：`/system/reports` 路由收紧 ADMIN；`/loop/aas-sync` 路由+页面 v-permission；EXPERT/SPONSOR 首页与可见范围按 T0.3 结论；按钮级权限补齐（recompute/触发诊断/导入）；v-permission 指令缺陷修复（Comment 占位替代 el.remove）
3. T4.3 错误处理收敛：消除 6+ 处双重 toast；静默失败处补降级态；错误三态（loading/error/empty+retry）按规范 §7.9 统一
4. T4.4 状态呈现专业性：scoreColor 抽公共 composable（动态阈值、null→中性灰）；INCONCLUSIVE 中文"数据不足"统一（badge/monitor/文案三分裂收敛）；confidence-badge 色板对齐规范 §3.1.6；硬编码 hex 迁移设计 token（优先 pid-dashboard/monitor/loop-performance 三页 166 处）
5. T4.5 轮询治理：`usePolling` composable（可见性暂停/恢复+连续 N 次失败才停并提示）；loop/data 无条件轮询改按需
6. T4.6 性能：等级分布/排行统计下推后端 group-by；等级筛选服务端分页；批量删除用 runWithConcurrency
7. T4.7 空状态引导：ClpmDataCanvas empty 支持原因+动作入口（链到数据导入）；aas.vue 迁移 Clpm 统一组件（含 ClpmDangerConfirmModal）
8. T4.8 manage.vue 拆分（3323 行 → composable + 子组件）+ 补 LoopApi 类型消 any（本阶段可只做编辑抽屉与变更对比块，控制范围）

### Phase 5：部署、安全基线与可观测（~3-4 天）

1. T5.1 部署=迁移一体：迁移抽公共函数，两条部署路径强制调用、失败即中止；部署后 celery `inspect ping`+`inspect scheduled` 断言
2. T5.2 镜像版本 tag（git short SHA）+ rollback 可用化；构建注入 APP_VERSION（git tag/commit）
3. T5.3 TDengine 备份带凭据、SKIP 改硬失败、部署前自动备份
4. T5.4 celery-beat 健康检查改探 beat 进程；dead-letter 消费者启用（与 T3.9 协同）
5. T5.5 安全基线：must_change_password 首次登录强制改密；nginx `client_max_body_size 20m` + 基础 CSP + 移除废弃 X-XSS-Protection；`ENV=production` 部署校验；`check_no_placeholder` 实际调用；`.env.prod.example` 删 DATA_SOURCE_TYPE 废止项；CORS `__AUTO__` 改幂等
6. T5.6 监控最小闭环：接入 deploy/grafana 到 prod compose + Prometheus（修 T3.7 后）+ 关键告警（celery 失败率、容器 unhealthy、磁盘）
7. T5.7 门禁自动化：lefthook 配真实 pre-push（ruff+pytest -x+check:type）；build-and-deploy.sh 前置测试门禁

### Phase 6：GB/T 44693.2 符合性验证（Phase E，~1 周，依赖 Phase 1/2 完成）

按算法评审输出的验证清单执行：附录 B.1-B.6 公式级用例（含恒定余差、永不收敛 Green 函数、混合 MODE 时长、权重模板四套）、附录 F.1-F.5（振荡两实现一致性、粘滞椭圆含圆团用例、饱和数值 MODE、ARMA 误差带、输出行程精度）、数值健壮性矩阵（空/单点/全同/NaN/Inf/大量程/时间戳缺口）、8 类异常检测边界值、故障注入召回率/误报率基线、整定辨识精度与查表比对。目标：用例通过率 ≥90%，结果固化进 `docs/过程文档/` 验证报告。

## 四、执行约束

- **顺序**：Phase 0 → 1 → 2 → 3/4/5 可并行 → 6。P0 三项必须在任何其他工作前修复
- **门禁**：每阶段完成后跑全量（ruff + pytest + check:type + vitest + E2E）+ `alembic check`（T2.8 后新增）
- **红线**：模型变更与迁移同批；Celery 任务代码改动后重启后端验证；断点续传保持 skip；计算数据源不引入远端降级
- **测试新增**：Phase 1 每项修复配故障注入用例；Phase 2/3 关键路径补集成测试（真实 TDengine/Redis，打破全 mock 现状从核心链路开始）
- **文档同步**：每阶段更新 AGENTS.md 基线表与对应过程文档；算法口径变更同步 FDS/IDS
- **范围控制**：本计划不含回路整定 Phase 2（生产级算法闭环）与公网延迟抖动优化（低优先级），另行立项

## 五、风险与决策点

| 决策点 | 待决内容 | 解决时机 |
|---|---|---|
| D1 | TDengine 时区修复口径（astimezone vs 带 Z ISO） | T0.1 验证后 |
| D2 | 综合评分缺失核心指标口径（整体 INCONCLUSIVE vs 权重重归一化） | T1.10 前，需对照 GB/T 44693.2 条文 |
| D3 | D-S 融合修正路线（同标签融合 vs 语义降级） | T1.7 前 |
| D4 | EXPERT/SPONSOR 可见范围以哪份文档为准 | T0.3 |
| D5 | 权限码服务端收敛力度（全端点 vs 先敏感端点） | T3.4 启动前 |
