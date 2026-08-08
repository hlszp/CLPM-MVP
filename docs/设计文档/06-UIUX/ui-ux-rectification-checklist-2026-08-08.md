# CLPM UI/UX 整改工作清单（实时进度跟踪）

| 项目 | 内容 |
|---|---|
| 创建日期 | 2026-08-08 |
| 执行方案 | `docs/设计文档/06-UIUX/ui-ux-rectification-plan-2026-08-07.md`（专项 A-F） |
| 依据报告 | `docs/设计文档/06-UIUX/ui-ux-critical-review-2026-08-07.md` v2.0 |
| 工作分支 | `feat/uiux-rectification-2026`（自 main `a874d2c` 拉出，**只提交、不推送**；开发环境验证通过后再推送合并） |
| 提交规范 | Conventional Commits，单 commit ≤500 行，按任务卡逻辑单元拆分 |
| 门禁 | 每任务卡完成后跑对应门禁（前端 vitest + check:type；后端 ruff + pytest + alembic check）；Phase 出口跑 E2E |
| 状态图例 | ⬜ 待开始 ｜ 🔨 进行中 ｜ 👀 待验收 ｜ ✅ 完成（附 commit） |

---

## 0. 启动条件检查（开工前置）

| # | 条件 | 状态 | 说明 |
|---|---|---|---|
| R1 | **工作区残留处置**（已完成） | ✅ | 2026-08-08 已提交 main：`4cc15f5` fix(build) 前端镜像构建兼容性、`1bba4ba` fix(db) 外键延迟初始化修复；`Dockerfile.frontend.bak` 鉴定为 HEAD 纯备份，按惯例不入库（保持未跟踪）；4 份整改文档随分支首个 commit 入库（P0-1） |
| R2 | 决策签认 D1-D4（见下表） | ✅ | 2026-08-08 用户全部签认：D1=a 补确认窗、D2=a 按 A.3、D3=a 不完整引用、D4=a 隐藏 languageToggle |
| R3 | 基线门禁全绿记录 | ⬜ | 开工前在 main 跑一次 ruff/pytest/check:type/vitest 并记录耗时与结果，作为回归对照 |
| R4 | E2E 既有失败甄别 | ⬜ | 3 个既有失败（D3-MOC/F4-F5/TUNE-009）+ 2 flaky 记录存档，整改期间新增失败与之区分 |
| R5 | 视觉回归基线脚本 | ⬜ | 21 页截图基线（Playwright），Phase 1 风格改动前必须就绪（方案 A.8） |
| R6 | 开发环境确认 | ✅ | 2026-08-07 实测：前端 :5666 / 后端 :7101 / docker infra 均可用，admin/sponsor/ic_engineer 三账号可登录 |

## 0.1 决策签认结果（2026-08-08 用户签认 ✅）

| # | 决策点 | 签认结论 | 影响任务 |
|---|---|---|---|
| D1 | 工作台快操作 `riskConfirmed:true` 绕过风险确认 | ✅ **补 `ClpmDangerConfirmModal` 确认窗** | B2 按此实施 |
| D2 | 色彩约定表 | ✅ **按方案 A.3 签认**（warning 深琥珀文字+浅琥珀底、类别七色退役、零值中性） | Phase 1 色彩工作解锁 |
| D3 | vben 系统管理 | ✅ **不完整引用**（仅借鉴模式，理由见审查报告 §10.4） | E7 闭环 |
| D4 | i18n 残态收敛 | ✅ **隐藏 languageToggle**，定位中文优先 | E3 按此实施 |

---

## 1. Phase 0 修信任（第 1 周）

| # | 任务 | 关键文件 | 验收标准 | 状态 |
|---|---|---|---|---|
| P0-1 | 首个 commit：本清单 + 审查报告 + 整改方案入库 | docs/ | 文档在分支可追踪 | ⬜ |
| P0-2 | B1 死导航：修/下线"历史"按钮 + route-compat 路由可达断言 | loop/workbench.vue:471；e2e/route-compat.spec.ts | 点击不 404，E2E 绿 | ⬜ |
| P0-3 | B2 风险确认绕过（按 D1 结论实施） | loop/workbench.vue:272/314 | 快操作有确认（或有契约豁免条款） | ⬜ |
| P0-4 | B3 波形轴：OP 移副轴 0-100%，PV/SP 主轴按量程自适应 | components/loop/waveform-chart.vue:530-552 | 4h 趋势 PV 波动可辨；截图复核 monitor/diagnosis 两处 | ⬜ |
| P0-5 | B4 品牌：VITE_APP_TITLE + 登录页/壳层去 vben + 产品全称统一 | .env.*、index.html、preferences.ts | 全站无 "Vben Admin"/幽灵占位符 | ⬜ |
| P0-6 | B5 数据映射：audit.vue camelCase + users 状态 + system 模块列表排查 + E2E 数据断言 | system/audit.vue、users.vue、e2e/system.spec.ts | 显示与 API 一致；断言进 E2E | ⬜ |
| P0-7 | Phase 0 出口：全量门禁 + E2E + 三角色冒烟截图复核 | — | 全绿；输出 Phase 0 验收记录到本文档进度日志 | ⬜ |

## 2. Phase 1 立风格（第 2-4 周）

### 2.1 专项 A 主线（视觉系统）

| # | 任务 | 状态 |
|---|---|---|
| A-01 | 色彩约定表文档发布 + token 单源化（THEME_COLORS/CLPM_INDUSTRIAL_TOKENS/useClpmTheme 并轨到 industrial-light.css 变量）（D2 签认后启动） | ⬜ |
| A-02 | 类别中性化：use-loop-palettes 七色退役、规则/指标类别 tag 灰阶化 | ⬜ |
| A-03 | 零值/无数据中性化（统计卡 6 页） | ⬜ |
| A-04 | 工具栏收敛 ≤5 图标 + 高频动作文字化 + aria-label（ClpmStandardActions，41 页） | ⬜ |
| A-05 | ClpmKpiStrip/ClpmKpiCard 改造（零值中性、主值 Semibold、去彩色 icon 圆底） | ⬜ |
| A-06 | 新组件 ClpmBulletChart + gauge 替换 | ⬜ |
| A-07 | 表格基线（密度三档/冻结/右对齐等宽/默认排序/空态）封装 + 15 表格页迁移 | ⬜ |
| A-08 | ClpmEmptyState 三要素 + 8+ 页面接入 + 装饰图表无数据不渲染 | ⬜ |
| A-09 | 弹窗统一浅色头 + 浮层 ≤2 层（monitor 深蓝头退役、loop-performance 90% 弹窗改跳转） | ⬜ |
| A-10 | 壳层品牌化（侧边栏/页眉/logo 区）+ 页签栏"关闭其他" | ⬜ |
| A-11 | 样板页①：登录页重塑（品牌区 + 工业视觉 + 环境角标） | ⬜ |
| A-12 | 样板页②：系统概览（待办非零优先/预测卡收敛/时间戳定宽/页名统一） | ⬜ |
| A-13 | 样板页③：性能总览 L1 重做（bullet 四核心 + 异常列表 + 工厂树抽屉化；**含 F3 趋势多选 + F4 阀门告警卡**） | ⬜ |
| A-14 | 样板页④：回路监控（chip 中性化/单位进表头/行操作收敛/筛选入 URL 联动 C1-2） | ⬜ |
| A-15 | ECharts preset 强制化 + 手写 option 页面迁移（monitor/pid-dashboard/overview） | ⬜ |
| A-16 | 视觉回归基线建立（21 页）+ 28 条清单首轮走查（含暗色） | ⬜ |

### 2.2 专项 E/C/F 并入项

| # | 任务 | 状态 |
|---|---|---|
| E1 | 偏好抽屉策展白名单 + preferences.antd.* locale 裸键修复 + tenantMode 残留删除 + 字段消费审计（D3/D4 相关） | ⬜ |
| E2 | 暗色校准：明暗双板接入 preset + 语义色对比度重校 + color-scheme: dark | ⬜ |
| E3 | i18n 残态收敛（按 D4 结论） | ⬜ |
| F1 | 服务端参数注册表 + Pydantic 值域/键白名单校验（config.py:504） | ⬜ |
| F2 | 4 项硬编码参数配置化（含 stiction.py:60 收敛）+ _DEFAULTS 扩 3 指标 | ⬜ |
| C2-1 | Sponsor 首屏零报错（被拒请求前置裁剪/静默降级） | ⬜ |
| C2-2 | 403 页语义化 | ⬜ |
| C2-3 | 性能总览 TOP5 默认最差优先（随 A-13） | ⬜ |
| C2-5 | 报表空态"从模板新建"引导 | ⬜ |
| C1-2 | monitor 筛选/分页入 URL | ⬜ |
| P1-出口 | Phase 1 出口：全门禁 + E2E + 28 条走查 + UI/UX 规范 v6.2 回写 | ⬜ |

## 3. Phase 2 通动线（第 5-8 周）

| # | 任务 | 状态 |
|---|---|---|
| D1 | 可访问性：viewport 放开缩放、aria/键盘/heading、transition:all×4、reduced-motion | ⬜ |
| D2 | 交互一致性：确认双轨归并、预警事件防重复提交/误报确认、文案词表落地 | ⬜ |
| D3 | 样式债：hex 白名单 lint（155→0）+ 死代码清除（9 页面+3 组件+重复 badge）+ **F7 MIN_GOOD_RATIO 清理** + 菜单 order 修正 | ⬜ |
| D4 | 虚拟滚动（左栏/大表格） | ⬜ |
| C1-1 | 增量巡检："较昨日"徽标列 + 默认"最需关注"排序（先确认快照 API 字段） | ⬜ |
| C1-3 | Action Tracker"验证中"状态 + 到期进待办（后端状态机字段 + alembic 迁移） | ⬜ |
| C1-4 | 空态引导全量收尾 | ⬜ |
| C2-4 | Bad Actor 治理台账视图（Top5↔Tracker 关联，评分+责任人+处置+验证一表） | ⬜ |
| E4 | 通知铃铛接 /ws/alerts 预警推送 | ⬜ |
| E5 | 锁屏评估与开启 | ⬜ |
| E6 | v-access 按钮级权限 | ⬜ |
| F5 | time_constant 计算器（复用 tuning_identification，L1 DISPLAY_ONLY） | ⬜ |
| F6 | 算法参数配置页收尾（重置默认/category 分组/新指标元数据消费注册表） | ⬜ |
| F8 | 批量配置评价周期 | ⬜ |
| F11 | HiaMonitor 设计文档回写（正向偏离同步） | ⬜ |
| P2-出口 | Phase 2 出口：Nielsen 复评（目标 ≥28）+ 度量指标表全量复核 + 全门禁 + E2E | ⬜ |

## 4. Backlog（暂不排期，触发条件驱动）

| # | 事项 | 触发条件 |
|---|---|---|
| F9 | 回路级算法参数覆盖层 + 批量算法参数 | 复杂回路投用或单回路调优诉求出现 |
| F10 | 复杂回路树形展示 + 类型枚举扩展 | 超驰/NooM 立项 |
| P3-1 | 交接班摘要/备注 | Phase 3 预研评审 |
| P3-2 | 波形多时间窗叠加 + 事件标注 | Phase 3 预研评审 |
| P3-3 | DCS 在运参数对照视图 | Phase 3 预研评审 |
| P3-4 | 中控室大屏深色专项 | 立项评审 |
| P3-5 | ⌘K 位号直达扩展 | Phase 3 |

---

## 5. 进度日志

| 日期 | 事项 | commit | 备注 |
|---|---|---|---|
| 2026-08-08 | 工作清单建立，等待开工指令 | — | R1 残留处置 + D1-D4 签认待定 |
| 2026-08-08 | R1 阻塞项提交 main（构建兼容性 + 外键初始化修复）；D1-D4 全部签认 | `4cc15f5`、`1bba4ba` | 仅提交未推送；schema 中 action_tracker CHECK 已含 VERIFYING 状态（利好 C1-3）；**待用户正式开工指令** |
