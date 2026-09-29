# Changelog

本文件记录 CLPM-MVP 的版本锁定基线。版本线自原 CLPM v6.2.0 派生精简重建后延续（v6.2.0 → v7.0.0），设计事实来源为 `docs/MVP设计/`。

## [Unreleased] - 2026-09-29

全站产品/IA 诊断后的**总览收敛 + 死代码集中清理 + 口径单源化**批次（用户四项裁决：总览留三屏、工作台定位聚合+下钻、死代码本轮清理、AGENTS.md 补行为红线）。

### 一、总览三屏收敛（IA 变更）

- **下线装置总览 `/dashboard/workbench` 与管理总览 `/reports/overview`**（页面+路由删除），全站总览收敛为**驾驶舱（管理者）+ 工作台（工程任务台，定位=聚合+下钻薄层）+ 性能总览（评估看板）**三屏；旧书签经 redirect 落回路监视 / 绩效报告
- **登录落地页调整**：前端 `defaultHomePath` `/dashboard`→`/cockpit`；ADMIN→`/workbench`（前后端 `ROLE_DEFAULT_HOME` 两张表逐角色对齐，test_auth 校验）；监控模块默认首页非 EXPERT→**回路监视**（原装置总览）
- 管理总览的独有能力（阶段锁定 /reports/stage-lock、PDF 导出）后端端点保留，前端入口随页下线——两者在 P3 报表做实（生成占位收敛中）前本就不可用，恢复时随新载体重建

### 二、死代码集中清理

- **前端**：16 个零引用组件（workbench 11 + loop 3 + prediction-card + llm-config 孤儿页）、`views/dashboard/` 整目录、`reports/overview.vue` 及其测试随页面删除
- **后端**：workbench BFF 五个空壳端点（A-05 /handling、A-07 /flags、A-08 /staff-load、A-09 /lane-more、A-11 /aggregate——恒返回硬编码空结构，TODO M2 从未落地，前端已绕行）、`/dashboard/system-overview`、`/dashboard/predictions`、`/performance/board`、`GET/PUT /configs/metrics` 架空链删除；`api-contract-allowlist.json` 同步摘除六条（含"待清理评估"全部出清）
- **前端死封装**：getWorkbenchHandlingApi/getWorkbenchAggregateApi/getPredictionsApi/getSystemOverviewApi 及配套死类型删除
- 旧版回路监视表 `/loop/monitor/legacy` 本轮**保留**（唯一带批量导出的视图，待回路监视页补导出后下线）；诊断/整定专属文件一律未动（仓库纪律）

### 三、口径单源化（横向一致性）

- **性能定级**：五份档位定义（use-score-color/cockpit/pid-dashboard/回路监视/指标矩阵）收敛到 `constants/clpm-ui GRADE_THRESHOLDS` 唯一源；回路工作台侧栏 A–E 阈值对齐 GB/T（原 90/80/**70**/60 自成一派）；指标矩阵补 40 分警告档、图例四档文案改五档
- **时区**：修复 6 处 naive UTC 显示 8 小时偏移（指标矩阵 3 处、诊断记录窗口/时间列、诊断报告、收益报告、数据导入）+ 评估记录 `[UTC+8]` 硬编码标签，统一走 `formatLocalTime`
- **权限**：预警事件页 `roles[0]`（只看第一个角色，多角色用户误判只读）改 `useClpmRoles.hasAnyRole`；管理总览的 localStorage 假读随页删除
- **诚实化（AGENTS.md 新红线首次执行）**：FunnelStats"近 6 周闭环数"演示柱图、HeaderBar 恒真"数据可信"徽章移除；工作台整定/处置断言角标 `scopeParams.plantName`（不存在的字段，恒显"全厂/全局"）改 `scopeDisplayName` 真实解析
- **性能总览阀门越限**：只查前 50 条（系统性漏报）改全量翻页 + `latestOnly`（同时修复历史快照重复命中）

### 四、后端逻辑漏洞与交互缺陷（第三批）

- **预警工况恢复自动解除（陈旧报警治理，ISA-18.2）**：巡检对回路未决事件（ACTIVE/ACKNOWLEDGED）逐条重估其规则，连续 3 次未再触发 → RESOLVED（`system:auto-recovery`，Redis 计数防抖）；规则已删的事件不自动恢复留人工。此前 ACTIVE 事件无任何自动恢复链路，工况恢复后永久滞留关注队列
- **误报置位联动**：标记误报时未决事件联动 RESOLVED（此前置误报不改状态，事件仍占队列/徽标计数，与"误报"语义自相矛盾）
- **预警状态机清理**：SUPPRESSED 为不可达状态（无代码写入，抑制在独立表），移出确认白名单
- **处置建议单查端点**：`GET /handling/suggestions/{id}` 新增；前端建议详情抽屉替换"分页扫描最多 5 页"降级方案（终态建议排后面页时深链静默打不开）
- **关注队列降级可见**：来源聚合失败时响应携带 `unavailableSections`，前端显式提示"部分来源不可用"（此前仅日志留痕，队列静默少数据）；"已加载 N 回路组"分页算式修正（原 `page×pageSize` 在第 2 页起恒错）
- **交互缺陷批量修**：任务详情通知列表 row-key 复合化（原 taskId 对多条通知重复）、错误信息 Alert 去重、取消任务加确认；诊断记录筛选变更回第 1 页（原第 5 页改筛请求空页）；诊断/整定工作台回路清单 100 条截断改全量分页（诚实化）；评估任务 RUNNING 徽章随列表轮询同步（原长期 stale）

### 五、机制

- AGENTS.md 行为红线新增：**下钻契约**（目标页必须消费全部下钻参数并回显）、**诚实化原则**（禁演示数据/静默截断/静默降级/恒真徽章）
- openapi 基线随端点删除重新固化（`scripts/export_openapi.py`），契约漂移测试实跑守护（0929 前后端 273 个封装对账脚本 [OK]）
- 已知残留增量：无（本轮净删除；`/reports/overview` 后端端点为唯一保留的已下线前端对应端点，已在 allowlist 注明恢复条件）

## [7.1.0] - 2026-09-13

v7.0.0 锁定后的**生产交付收口 + 数据链路重构**版本（main 自 `b8525ee2` 起；含 08-31 部署就绪收口与 09-01~09-13 测点子表重构）。tag `v7.1.0`（annotated）。

### 一、生产部署就绪收口（08-31）

- **CI 红灯修复**：`test_health` 版本断言与 `settings.APP_VERSION` 同源；`TestTaskSkeletonExecution` 5 用例纳入 integration 标记（CI 无 PG 自动排除）；vitest 根配置固定 `TZ=Asia/Shanghai`（修复 CI UTC 环境 2 用例）
- **实时订阅 Leader 锁**：`uvicorn --workers 4` 下多进程重复 SignalR 订阅/回写 TDengine 风险收口——Redis Leader 锁（SETNX+TTL+Lua CAS 续期/释放），仅 Leader 订阅，故障自动接管
- **强制改密功能移除**：`must_change_password` 全链路下线（列/deps 403 拦截/登录标志/测试），迁移 `g7b8c9d0e1f2` drop 列；个人中心自愿改密端点保留
- **监控关注队列修复**：表格行数据剥离 children，避免 antd 误入树形模式渲染多余展开按钮
- **zpdev 演练实测修复（首装阻断级）**：`01_schema.sql` 外键 VARCHAR(36) 引用 UUID 主键导致 initdb 在 ON_ERROR_STOP 下中途中止、尾部对象永久缺失——已修复并与 alembic head 列级对齐（diff 为空）；`lib-migrate.sh` TDengine 校验补"库在表不在"盲区 + REST 就绪等待；deploy 脚本新增 TDengine 密码规则校验（8-16 字符、四类字符至少三类）
- **交付包**：最终包 `releases/clpm-delivery-20260831-144618.tar.gz`（788M，9 镜像，`v7.0.0-17-g424c779f`），manifest 已登记；已在 zpdev（192.168.13.111）全新部署演练通过（登录/回路数据/TDengine 实时回写/Leader 锁/备份全链路）
- 已知残留增量：`tests/golden/openapi_baseline.json` 仍含 `mustChangePassword`（对应漂移测试已全文件 skip，基线刷新待后续）

### 二、测点子表重构与宽表退役（09-01~09-09）

- **四阶段重构**：P0 契约基线与独立参考数据 → P1 测点子表存储与持久元数据 → P2 实时订阅与导入统一写点表 → P3 逻辑宽表构建与布局路由读链路 → P4 验收矩阵与阶段状态记录（迁移 `r1p0int00001` point history metadata tables）
- **Phase 2 宽表退役（本版关键口径变更）**：历史导入锁定 point-only；种子口径 `realtime_writeback_enabled=false` + `history.storage_mode=point`，并新增 `history_layout_manifest` global/point 段——**缺此行则读取路由回退 legacy 查已退役宽表、历史数据查询为空**
- **TDengine fresh 部署修复**：`01_supertable.sql` 的 `CREATE STABLE` 压为单行，修复 3.3.x entrypoint 以 `taos -f` 逐行执行导致的 `Incomplete SQL statement`（原多行写法 fresh 部署必现超级表缺失）
- **数据接缝适配**：AD01-AD08 接缝与缓存数据版本；PointHistoryWriter 启动幂等建表与表缺失自愈；点表源时间按 +8 墙钟解析对齐宽表口径

### 三、历史导入链路整改

- **算法演进**：v2 角色分层 + `pointOnly` 开关 → v3 单相逐位号直导
- **幂等口径收敛**：下线完整性检查，导入收敛为幂等覆盖；修复稀疏角色 COV 去重失效与写入行数双重计数；导入落点表恢复远端质量戳
- **性能**：分块上限 3h→24h、回路并发 2→4；新增批量导入前置探测与远端异常可观测化
- **超时误杀修复**：取消 Celery 软超时误杀，并修复任务被全局 1800s 硬超时误杀
- **前端**：数据导入任务列表新增窗口小时数与导入时长列

### 四、实时订阅（SignalR）链路整改

- **采集可靠性 R01-R11**：共享数值契约与预算配置、时间语义与容错整改
- **连接治理**：分片连接池 + 应用层心跳保活、分片大小默认 1450（AAS 订阅上限安全带）、改走 negotiate 标准流程获取 connectionToken、多 worker Leader 锁防重复回写、热停后待命实例抢锁续采
- **保活多轮修正（含一次误判回退，如实记录）**：停发 type=6 应用层 ping → 恢复 type=6 ping → ping 15s 真正保活 + 停滞看门狗 300s→60s → 停滞看门狗改由连接活性驱动修复 unbound 空转误判
- **前端实时消费 R06/R17-R20**：WS 建连 token 主动刷新、订阅过滤接线、重连恢复；补 `bindLoopInterest` 修复实时值长期冻结

### 五、诊断 / 报告 / 工作台 / 监控

- **诊断 16 号文 Phase B/C 落地**：后端覆盖台账/回路组/预检/复核统计；前端健康度台账/回路组对比/预检徽标/复核卡片
- **报告 P2 闭环增强**：处置升级、收益整定执行、逐工单对比
- **工作台**：precalc M2 真实聚合、驾驶舱 KPI 去演示化（注：工作台部分图表仍存在前端派生/静态值，见"已知残留增量"）
- **监控**：监视页统计卡片跟随筛选联动、筛选改草稿态（点查询才发起）、趋势窗口补 24H 档并防旧响应回填、关注队列表格修复
- **AAS 位号同步口径反复**：09-03 下线前后端功能 → 09-05 恢复路由注册开放入口；**当前口径为开放**

### 六、部署与 CI

- `01_schema.sql` initdb 中断与列级漂移修复（外键 VARCHAR(36) 引用 UUID 主键导致 `ON_ERROR_STOP` 下中途中止）
- 本机 dev TDengine 升级 3.3.6.6；部署前备份支持 `SKIP_BACKUP` 跳过（验证环境）
- 前端镜像 corepack 与锁文件 `@jspm/fetch` tarball 改走 npmmirror
- **vitest 单元测试纳入 CI 阻塞门禁**；vitest 根配置固定 `TZ=Asia/Shanghai`
- 新增部署运维移交说明书与 Windows 宿主机 + Linux 虚拟机部署拓扑文档

### 版本口径

- 后端 7.1.0（`pyproject.toml` / `app/__init__.py` / `APP_VERSION` / `uv.lock`，`/health` 与启动日志展示）
- 前端沿用 vben monorepo 5.7.0，未本地化（决策 2026-08-28）
- 生产镜像 `APP_VERSION` 构建参数默认 dev，部署时建议显式传入 7.1.0

### 已知残留增量（本版本快照）

- **API 契约漂移无守护**：`tests/golden/openapi_baseline.json` 仍含已下线的 `mustChangePassword`，且 `test_openapi_contract_drift.py` 以 `pytestmark` **全文件 skip**——前后端契约当前无自动化强制
- **工作台首屏存在前端派生/静态值**：`ScoreTrendChart.vue` 以主序列算术派生"上一周期""催化裂化"两条序列并硬编码图例数值（`催化裂化（82.1）`）；`EvalTrendChart.vue` 同款派生；`AbnormalLoopsTable.vue` 以 severity 模拟工单状态。`views/workbench/` 59 组件零单测
- **数据链路**：`conflict_strategy` 参数已从代码移除（AGENTS.md 对应红线失效）；导入批量写点表 `payload_hash` 写空串；点表读路径 `logical_wide_builder` 恒 1s 网格不降采样 + `read_events` 单 chunk `LIMIT 50000` 静默截断
- **调度层**：`beat_init` 修改 `celery_app.conf.beat_schedule` 不生效（运行中 Beat 读 `scheduler.schedule`），模块热插拔与周期覆盖在调度层未实际生效
- 其余见 [7.0.0] 已知残留基线（未变）

## [7.0.0] - 2026-08-28

CLPM-MVP 首个部署前锁定版本（tag `v7.0.0`，annotated）。

### 已落地能力

- **闭环六模块**：监控 → 评估 → 诊断 → 整定 → 处置全链路闭环 + 统计报告，模块热插拔（诊断/整定/处置可弹性启停，禁用模块联动隐藏）
- **工作台 v2.0**：`/workbench` 单屏 5 Tab（总览/评估/诊断/整定/处置），order=0 全角色可见
- **驾驶舱**：`/cockpit` 两页 Tab 满屏只读总览（方案 11 号文），SPONSOR/IC/PE 角色默认落地
- **诊断**：两页式（工作台 + 记录），v2 引擎 `diagnosis_run` 单一事实源（14 号文统一，旧引擎退役归档）；16 号文 Phase A 已落地（F1 回路诊断档案 + F2 双模式复诊对比）
- **整定**：三页式，全算法矩阵、仿真对比、效果验证（前后窗曲线 + X-Y），L0~L4 适用性门禁
- **处置 v2.0**：双实体（loop_action_item 建议 5 态审核 + handling_order 工单 6 态，KPI 前后对比验证）
- **评估**：回路性能 + 指标分析页 + 指标矩阵页（15 号文，含 E2E）
- **报告**：一级菜单 6 子页；P0（订阅止血）+ P1（数据质量页 + 预警统计页，含 E2E）已实施
- **监控**：三来源关注队列（含 HANDLING 处置工单来源，闭环断点已修复）+ 预警预设规则/三级阈值
- **适用性评估 L0~L4**：诊断 L0/L1 阻止 + L2 横幅；整定 L3 以下 ERR_TUNING_FITNESS_INSUFFICIENT 门禁
- **系统管理**：基础信息 + 字典管理（MEASURE_TYPE/TAG_TYPE/LOOP_TYPE）+ 模块管理

### 已知残留基线（本版本快照）

- `workbench_summary.py` 诊断/整定/tracker 摘要恒 None（唯一有真实信息损失的 stub，前端已绕行），是否恢复待人工决策
- `dashboard.py` / `anomaly_prediction.py`：无前端消费者的架空链（端点已注册）
- 报告订阅自动生成为占位实现（PDF 极简版、无文件落盘、Beat 四周期已摘除），P3 做实后恢复
- 工作台 BFF 5 个空壳端点（A-05/07/08/09/11，前端已绕行无用户可见影响）+ 铃铛事件桩（后端 A-12 已实现、前端未接）+ 数据流转图静态值
- 未启动排期：16 号文 Phase B（F3/F4）/ Phase C（F5/F6）、报告 P2/P3、预警 Phase 2 阈值能力（百分比/量程引用/RATE_OF_CHANGE）、ARMA MA(1)、处置统计端点与周/月界口径、`monitor/loops` fitnessLevel 字段
- 冗余代码登记（保留待下周期集中清理，均经引用分析确认零生产引用，不违反"不删诊断/整定文件"纪律）：
  - 后端：`app/utils/ideal_settling_time.py`（与 metric_calculator 重复）、`app/services/pid_conversion.py`、`app/aas_integration/`（空壳）
  - 前端：clpm 组件库 8 个零引用组件（prediction-card / alert-dsl-editor / operational-context-provider / evidence-canvas / state-face / state-overlay / object-summary-bar / kpi-strip / severity-badge / tag-association-badge）、workbench 14 个原型死组件、`use-section-visibility.ts`

### 版本口径

- 后端 7.0.0（`pyproject.toml` / `app/__init__.py` / `APP_VERSION`，`/health` 与启动日志展示）
- 前端沿用 vben monorepo 5.7.0，未本地化（决策 2026-08-28）
- 生产镜像 `APP_VERSION` 构建参数默认 dev，部署时建议显式传入 7.0.0

### 技术基线

- Alembic：86 个迁移，单 head `f5a6b7c8d9e0`（sync_valve_nonlinearity_depends_on）
- 门禁全绿：ruff check/format、pytest 4497 passed / 382 skipped / 32 xfailed（覆盖率门槛 60%）、vue-tsc
- 端口：开发隔离 17101（API）/ 15666（前端）/ 17106（mock），容器 `clpm-mvp-*`；生产 compose（`docker-compose.prod.yml`）仍为原项目口径（7101/7141、`clpm-*` 命名），隔离改造未执行——与原 CLPM 生产环境同机部署会冲突，部署前需人工确认
