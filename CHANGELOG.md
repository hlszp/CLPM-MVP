# Changelog

本文件记录 CLPM-MVP 的版本锁定基线。版本线自原 CLPM v6.2.0 派生精简重建后延续（v6.2.0 → v7.0.0），设计事实来源为 `docs/MVP设计/`。

## [Unreleased] - 2026-08-31 生产部署就绪收口

v7.0.0 锁定后、首次生产交付前的收口变更（main 自 `b8525ee2` 起）：

- **CI 红灯修复**：`test_health` 版本断言与 `settings.APP_VERSION` 同源；`TestTaskSkeletonExecution` 5 用例纳入 integration 标记（CI 无 PG 自动排除）；vitest 根配置固定 `TZ=Asia/Shanghai`（修复 CI UTC 环境 2 用例）
- **实时订阅 Leader 锁**：`uvicorn --workers 4` 下多进程重复 SignalR 订阅/回写 TDengine 风险收口——Redis Leader 锁（SETNX+TTL+Lua CAS 续期/释放），仅 Leader 订阅，故障自动接管
- **强制改密功能移除**：`must_change_password` 全链路下线（列/deps 403 拦截/登录标志/测试），迁移 `g7b8c9d0e1f2` drop 列；个人中心自愿改密端点保留
- **监控关注队列修复**：表格行数据剥离 children，避免 antd 误入树形模式渲染多余展开按钮
- **交付包**：`releases/clpm-delivery-20260831-111954.tar.gz`（788M，9 镜像，`v7.0.0-10-gb8525ee2`），manifest 已登记
- 已知残留增量：`tests/golden/openapi_baseline.json` 仍含 `mustChangePassword`（对应漂移测试已全文件 skip，基线刷新待后续）

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
