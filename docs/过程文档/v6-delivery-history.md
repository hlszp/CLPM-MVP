# CLPM v6 交付历史

> 来源：原 AGENTS.md §v6.1 文档与设计对齐状态（2026-07-21 拆分）。仅作历史追溯，现行需求与设计以 AGENTS.md §必读入口/§当前基线所列文档为准。

7 阶段重构全部交付，后端测试全绿，文档体系统一升级至 v6.1（含 ZL 工业设计规范对齐）：

| 阶段 | 核心交付 | Commit |
|---|---|---|
| Phase 0 | ORM 模型层更新 | `02f3c5a` |
| Phase 1 | 数据预处理模块（8步Pipeline + 8类异常值检测 + 180 单元测试） | `bdde45b` |
| Phase 2+3 | DataPlanner+Cache 与 12 个 KPI 指标计算器（3+1+8 体系） | `11d13e6` |
| Phase 4 | kpi_calc.py 整合 DataPlanner + MetricCalculator | `53fc21f` |
| Phase 5 | API 接口层扩展（17 端点 + 任务跟踪/通知 + OpenAPI 文档） | `39859e5` `0dfd37b` |
| Phase 6 | 前端适配（4层架构：类型/API → 组件 → 页面 → 路由） | `86f356c` `3516641` `4bff65b` |
| 修复 | Celery worker 任务注册修复（include 参数替代 autodiscover_tasks） | `207c882` |
| v6.0 升级 | 文档统一升级：PRD/FDS/ADS/DDS/IDS/UIUX → v6.0；实现契约 v1.0 → v2.0；DESIGN v2.1 → v3.0；测试数 1762；TS 错误 0 | 见 `docs/过程文档/superpowers/plans/v6-consistency-check.md` |
| v6.1 升级 | ZL 工业设计规范对齐：诊断中心/指标管理页面清除硬编码 Tailwind 色类；高危操作确认统一改用 ClpmDangerConfirmModal；监控页面 KPI 指标按时间范围聚合 | `1585a7e` `4aea6b8` `80c38ef` `d5f532f` |
| 网络模式切换 | 链路配置应用层局域网/公网动态切换：Tailscale subnet router + sudoers 免密 + sys_config 真相源 + lifespan 预载；.env 移除业务 URL/Token，统一由 sys_config 管理 | `6730b7f8` `ae0dff0c` `b09c816a` `ce5f4142` `b239b8b` `6a5fa30`（PR #75） |
| 数据导入韧性 | 历史导入 chunk 级重试（502/503/504/429 + 超时/网络异常，指数退避 1/2/4s 最多 3 次）+ 回路并发 5→2 + chunk 跨度 24h→3h + 远端超时默认 30s→120s | `b74a6b4`（PR #74） |
| 数据链路修复 | Celery worker 经 `worker_process_init` 预载 sys_config（此前仅 API lifespan 预载，worker 取不到业务 URL 导致导入/远端取数全失败）；实时自控率/回路状态统计改读 Redis 实时缓存（原只读 PG `tag_registry.current_value`，AAS 停更后数据过期）；装置/单元性能明细表改为当前节点+直接子节点 | PR #79 |
| 远端调用保护 | RemoteApiProvider 全局限流（per-loop 信号量默认 4 并发，覆盖 DataPlanner 无界 gather）+ 熔断器（连续失败 5 次熔断 300s 快速失败、半开探测）；SignalR 重连指数退避 5s→30s 封顶。背景：2026-07-19 回填 8 worker × ~54 并发压垮远端边缘 API | PR #80 |
| 性能评估四页治理 | 全面检查装置性能/回路性能/评估任务/KPI报表 4 页并修复 27 项：DataPlanner 契约查询 steady_rate→stability_rate 别名（快照只剩 PARTIAL 的回归）；装置聚合仪表盘全厂 null；快照服务端排序；_parse_dt 时区；DataLineage snake_case 血缘；策略配置裸数组解析；评估历史复合行键/日期 endOfDay；E 级评分掩码；kpi-report latestOnly=false + UTC 窗口；危险操作统一 ClpmDangerConfirmModal | PR #81 |
| 可信度与异常检测配置化 | ① 回路级理想稳态时间配置（`loop_ledger.ideal_settling_time`，留空按控制类型默认 FC30/PC60/TC180/LC600/CC300/其他120，优先级 手动>模型>类型默认）；② 8 类异常检测参数 + 启停开关（sys_config `outlier_params.current` 存储，指标配置页"参数配置"Tab，API `GET/PUT /configs/outlier-params`，worker_process_init 预载生效）；③ 回路最新可信度结果表 `loop_confidence_latest`（每回路一条，随小时快照 UPSERT，12 子指标值+可信度 JSONB）+ 回路性能页可信度单元格点击抽屉（迁移 `z1a2b3c4d5e6`） | PR #98 |
| 诊断整改 Phase A+B | 诊断中心全面审查与整改（总计划：`docs/过程文档/diagnosis-module-review-rectification-plan-2026-07-19.md`）。Phase A 止血：前端死链、Tracker PDF 假链路改同步下载、A/B 对比端点实现（前后 7 天 8 项 KPI 聚合）、统计口径改后端聚合、阈值种子键名对齐+迁移 v6p1diag002、is_enabled 真正禁用算法、Beat crontab 化+任务去重、INCONCLUSIVE 漏诊修复、diagnosis_tag 写入方+标签面板接线。Phase B 自助诊断：体检轨每 8h 全回路体检、按需诊断 labels 子集、传感器故障算法组（卡死/噪声突增/漂移，归入质量异常子类型）、Harris 指数模型失配评估、异常值剔除预处理（SPIKE/JUMP/OUT_OF_RANGE/NAN）、可信度 A-E 随结论落库并展示、可视化存储瘦身（全量数组仅入主标签记录）、推荐映射修正。另：Beat 双触发修复（pidfile+pgrep 双重单例）、metric 种子对齐 CALCULATOR_REGISTRY、Alembic 合并迁移 v6p1merge002 | PR #86-#96 |
| 数据完整性检查 | 回路管理→数据管理页新增「检查完整性」功能：选定时间范围后对本地 TDengine 宽表按小时分桶，对 7 个数据列（pv/sp/op/mode/pid_p/pid_i/pid_d）分别 `COUNT(col)` 统计列级缺失；缺失定义按 2026-07-22 用户口径——"该时间戳无记录或列为空值（NULL），质量码非 Good 但有值不算缺失"；首尾不足整点的小时桶按实际秒数算预期点数（不用固定 3600）；支持非整点时间范围（如 10:30~12:20）与整点范围；前端抽屉展示整体完整度 + 按回路/按时间双 Tab 表格 + 行展开列级明细 + 一键补齐缺失数据（复用 `import_history_data` + `conflict_strategy="skip"`）。API：`POST /api/v1/loops/data-import/integrity-check` | `b4a98b5c` `df8606e8` `b7bfdde8` |
