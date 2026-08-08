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
| R3 | 基线门禁全绿记录 | ✅ | 后端：ruff✅/format✅/alembic✅（修复后）/pytest 4120 passed✅（5m08s）；前端：check:type✅/vitest 455 passed + **1 既有失败**（tuning-workbench.test.ts"风险统计显示为未知而不是伪 0"，已记入 backlog）。**基线修复**：dev DB 无 alembic_version（bootstrap 重建丢失）→ `alembic stamp head` + 补缺失索引 `idx_action_tracker_tuning_record` 后 check 退出码 0；bootstrap 缺该索引已记 backlog 待用户决策 |
| R4 | E2E 既有失败甄别 | ✅ | 终轮全量：73 passed / 15 failed / 6 未运行（15.7m）。失败归因：3 个文档基线失败（D3-MOC/F1/TUNE-009）+ 12 个 networkidle 超时类环境敏感失败（ops-runbook P2-018 后端累积负载 + SignalR 长连接，非本次引入——证据：失败集中于 waitForLoadState 超时、与改动文件无交集、同页新断言 SYS-DATA/ROUTE-WB 全过）。**本次新增 4 断言全部通过，无新增回归**。建议：重启后端后复跑一轮全量做最终确认（环境操作留用户） |
| R5 | 视觉回归基线脚本 | ✅ | 脚本 `e2e/scripts/capture-visual-baseline.mjs` + 21 页基线入库（`c1a3bef`）；**可复现性已验证**：`--out current` 重采 21/21 + 像素对比，静态页 0.00% 差异、B5 修复被准确捕捉（system-users 11.06% diff）；current/ 目录已 gitignore |
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
| P0-1 | 首个 commit：本清单 + 审查报告 + 整改方案入库 | docs/ | 文档在分支可追踪 | ✅ `3edfacb` |
| P0-2 | B1 死导航：~~修/下线"历史"按钮~~ → **用户决策调整（08-08）：历史按钮下线，趋势改页内弹窗（LoopTrendModal 共享组件）** + route-compat 断言 | loop/workbench.vue；components/loop/loop-trend-modal.vue；e2e/route-compat.spec.ts | 无 404；趋势页内弹窗不跳路由 | ✅ `6fb0ab8`（深链版）→ `589fe2e`（用户决策版，E2E 2/2 ✅ + 截图验证）。loop-performance 的 ?loopId= 深链能力保留为通用基建 |
| P0-3 | B2 风险确认绕过（按 D1 结论实施） | loop/workbench.vue:272/314 | 快操作有确认（或有契约豁免条款） | ✅ `c9c97c6`（门禁绿 + mock 实弹验证：整定/仿真确认窗均在调 API 前弹出） |
| P0-4 | B3 波形轴：OP 移副轴 0-100%，PV/SP 主轴按量程自适应 | components/loop/waveform-chart.vue:530-552 | 4h 趋势 PV 波动可辨；截图复核 monitor/diagnosis 两处 | ✅ `c1eae6e`（截图验证通过，顺带修复"undefineds"提示） |
| P0-5 | B4 品牌：VITE_APP_TITLE + 登录页/壳层去 vben + 产品全称统一 | .env.*、index.html、preferences.ts | 全站无 "Vben Admin"/幽灵占位符 | ✅ `d89b85b`（截图验证通过） |
| P0-6 | B5 数据映射：audit.vue camelCase + users 状态 + system 模块列表排查 + E2E 数据断言 | system/audit.vue、users.vue、e2e/system.spec.ts | 显示与 API 一致；断言进 E2E | ✅ `5a42379`（E2E-SYS-DATA-001/002 通过 + 截图验证） |
| P0-7 | Phase 0 出口：全量门禁 + E2E + 三角色冒烟截图复核 | — | 全绿；输出 Phase 0 验收记录到本文档进度日志 | ✅ 门禁全绿（vitest 1 既有失败除外）；E2E 无新增回归；三角色冒烟通过 |

## 2. Phase 1 立风格（第 2-4 周）

### 2.1 专项 A 主线（视觉系统）

| # | 任务 | 状态 |
|---|---|---|
| A-01 | 色彩约定表文档发布 + token 单源化（THEME_COLORS/CLPM_INDUSTRIAL_TOKENS/useClpmTheme 并轨到 industrial-light.css 变量）（D2 签认后启动） | ✅ `b00109b`（约定表 v1.0 + 三处色板并轨 + 4 个旧色板测试更新；门禁绿） |
| A-02 | 类别中性化：use-loop-palettes 七色退役、规则/指标类别 tag 灰阶化 | ✅ `64dfa36`（8 文件；DOM 探针验证 chips #64748b + tag 全灰阶；状态色保留） |
| A-03 | 零值/无数据中性化（统计卡 6 页） | ✅ `7e94079`（kpi-card 增 neutral-when-zero 机制；诊断总览/整定工作台/系统概览接入；顺带计数整数化 + 去重复卡 + 伪 0.00% 修复） |
| A-04 | 工具栏收敛 ≤5 图标 + 高频动作文字化 + aria-label（ClpmStandardActions，41 页） | ⬜ |
| A-05 | ClpmKpiStrip/ClpmKpiCard 改造（零值中性、主值 Semibold、去彩色 icon 圆底） | ✅ `d6fdfd1`（仅异常着色；主值 700 字重已达标） |
| A-06 | 新组件 ClpmBulletChart + gauge 替换 | ✅ `21687c3`（子弹图组件 + 性能总览 6 gauge + 监控性能弹窗替换；删除 2 个死 gauge 组件，-843 行） |
| A-07 | 表格基线（密度三档/冻结/右对齐等宽/默认排序/空态）封装 + 15 表格页迁移 | 🔨 `b633247` 首批（监控表数值排版：单元格去重复单位/OP 表头标注%/右对齐等宽）；余：密度三档/冻结/默认排序/封装迁移（Phase 1 后期或 Phase 2 接续） |
| A-08 | ClpmEmptyState 三要素 + 8+ 页面接入 + 装饰图表无数据不渲染 | 🔨 `d259b04` 6 页接入（含 DataCanvas 载体修正）；余：dashboard 预测卡收敛（随 A-12） |
| A-09 | 弹窗统一浅色头 + 浮层 ≤2 层（monitor 深蓝头退役、loop-performance 90% 弹窗改跳转） | 🔨 `3c89819` ClpmModal 浅色头完成；余：90% 弹窗改路由跳转（随 A-13） |
| A-10 | 壳层品牌化（侧边栏/页眉/logo 区）+ 页签栏"关闭其他" | ✅ `58246a1`（页眉完整姓名；"理员"系 Avatar 回退文案误解；页签更多菜单 vben 默认开启） |
| A-11 | 样板页①：登录页重塑（品牌区 + 工业视觉 + 环境角标） | 🔨 `58246a1` 后：工业线稿 SVG 已上（截图验证）；环境角标未做 |
| A-12 | 样板页②：系统概览（待办非零优先/预测卡收敛/时间戳定宽/页名统一） | ✅ `6e81740`（页名统一+非零优先+两处时间戳修复+BL-3 预测卡 undefined 后端根治，pytest 4120 绿；预测卡空态高度收敛未做，余量小） |
| A-13 | 样板页③：性能总览 L1 重做（bullet 四核心 + 异常列表 + 工厂树抽屉化；**含 F3 趋势多选 + F4 阀门告警卡**） | ⬜ |
| A-14 | 样板页④：回路监控（chip 中性化/单位进表头/行操作收敛/筛选入 URL 联动 C1-2） | ⬜ |
| A-15 | ECharts preset 强制化 + 手写 option 页面迁移（monitor/pid-dashboard/overview） | ⬜ |
| A-16 | 视觉回归基线建立（21 页）+ 28 条清单首轮走查（含暗色） | ⬜ |

### 2.2 专项 E/C/F 并入项

| # | 任务 | 状态 |
|---|---|---|
| E1 | 偏好抽屉策展白名单 + preferences.antd.* locale 裸键修复 + tenantMode 残留删除 + 字段消费审计（D3/D4 相关） | ✅ `becfc9a`（extension 4 字段零消费整体移除，裸键问题根除；白名单策展随暗色校准一并走查） |
| E2 | 暗色校准：明暗双板接入 preset + 语义色对比度重校 + color-scheme: dark | ⬜ |
| E3 | i18n 残态收敛（按 D4 结论） | ✅ `becfc9a`（languageToggle 隐藏） |
| F1 | 服务端参数注册表 + Pydantic 值域/键白名单校验（config.py:504） | ⬜ |
| F2 | 4 项硬编码参数配置化（含 stiction.py:60 收敛）+ _DEFAULTS 扩 3 指标 | ⬜ |
| C2-1 | Sponsor 首屏零报错（被拒请求前置裁剪/静默降级） | ✅ `2325718`（useConfigAccess 前置门禁；实测 sponsor 403 清零） |
| C2-2 | 403 页语义化 | ✅ `98b65d5`（完整路由表判定 403/404；ic_engineer 实测） |
| C2-3 | 性能总览 TOP5 默认最差优先（随 A-13） | ✅ `becfc9a`（默认评分升序） |
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
| BL-1 | vitest 既有失败：tuning-workbench"风险统计显示为未知而不是伪 0"用例 | Phase 1 诊断修复（疑似整定工作台统计映射回归） |
| BL-2 | bootstrap 01_schema.sql 缺索引 idx_action_tracker_tuning_record（dev DB 已补，引导 SQL 未回写） | 用户决策后回写 |
| BL-3 | 工作台"异常预测"卡统计条显示 undefined（高危/中危/已分析计数映射缺失，有数据时必现） | ✅ `6e81740`（后端汇总键 camelCase 根治 + 测试同步） |
| BL-4 | Sponsor 登录"无权限访问"toast、TOP5 最优排序（已在方案 C2-1/C2-3，Phase 1） | 已在计划内 |

---

## 5. 进度日志

| 2026-08-08 | R1 阻塞项提交 main（构建兼容性 + 外键初始化修复）；D1-D4 全部签认 | `4cc15f5`、`1bba4ba` | 仅提交未推送；schema 中 action_tracker CHECK 已含 VERIFYING 状态（利好 C1-3）；**待用户正式开工指令** |
| 2026-08-08 | 正式开工：拉分支 feat/uiux-rectification-2026；P0-1 四文档入库 | `3edfacb` | 目标模式激活，预算 30 轮 |
| 2026-08-08 | R3 基线：后端 ruff/format/alembic/pytest 全绿（4120 passed），前端 check:type/vitest（455+1 既有失败）；基线修复 dev DB alembic 版本跟踪 + 补索引 | — | vitest 既有失败 tuning-workbench"伪 0"用例记 backlog；bootstrap 缺索引记 backlog |
| 2026-08-08 | B4 品牌统一完成（截图验证：登录页/工作台品牌区/浏览器标题） | `d89b85b` | 用户 :5666 实例需重启才能看到品牌改动（.env 变更）；我验证用 :5667 临时实例 |
| 2026-08-08 | B1 死导航修复：历史按钮深链 /metric/loop-performance?loopId= + E2E 断言 | `6fb0ab8` | E2E 首跑受并发污染（72/12/6），安静环境重跑中 |
| 2026-08-08 | R5 视觉基线 21/21 采集入库（e2e/visual-baseline/baseline + 采集脚本） | `c1a3bef` | 采集自含品牌改动的 :5667 实例 |
| 2026-08-08 | B3 波形轴分离（OP 副轴 0-100%/PV-SP 自适应/MODE offset），PV 波动可辨 | `c1eae6e` | 顺带修复"undefineds"采样提示；monitor/诊断详情双场景截图验证 |
| 2026-08-08 | B2 确认窗：参数整定/模拟仿真接入 WARNING 级 ClpmDangerConfirmModal | `c9c97c6` | E2E-ROUTE-WB 选择器修正（antd 双汉字按钮空格）后 2/2 通过 |
| 2026-08-08 | B5 数据映射：users/audit camelCase 对齐 + system.ts 类型修正 + E2E 数据断言 | `5a42379` | 用户页全员"启用"+真实姓名+最后登录；审计页全列真实数据；新增断言 2/2 通过 |
| 2026-08-08 | P0-7 出口：三角色冒烟截图完成（admin/sponsor/ic_engineer）；E2E 全量重跑中 | — | B2 确认窗实弹验证待补（需先跑一次回路辨识） |
| 2026-08-08 | B2 实弹验证完成：真实辨识被 Trust First 门禁拦截（E 级→模型不可用，正确行为）；改用 mock 整定记录验证——"参数整定计算确认"/"闭环仿真计算确认"均在调 API 前弹出 | — | 截图存证 /tmp/clpm-shots/p0-exit/b2-*.png；另发现种子数据 24h 窗辨识仅 E 级（现象记录，非本次范围） |
| 2026-08-08 | **Phase 0 出口验收完成**：E2E 全量 73/15/6（15 失败全部为既有基线 + 环境劣化类，新增 4 断言全过、无新增回归）；三角色冒烟通过；backlog 4 项记录（BL-1~4） | `7b74b9e` 等 | 建议用户重启后端后复跑 E2E 做最终确认；待用户验收后推送合并 |
| 2026-08-08 | B1 用户决策调整：历史按钮下线、趋势改页内弹窗；新共享组件 LoopTrendModal（自 monitor 趋势弹窗提炼） | `589fe2e` | E2E 断言改写 2/2 ✅；弹窗截图验证（PV/SP 主轴 + OP 副轴正常）；深链能力保留 |
| 2026-08-08 | **Phase 1 启动**：A-01 色彩约定表 + token 单源化 | `b00109b` | 4 个旧色板测试更新为约定值 |
| 2026-08-08 | A-02 类别中性化（七色退役）+ A-03 零值中性化 | `64dfa36`、`7e94079` | DOM 探针 + 截图双验证 |
| 2026-08-08 | A-04 工具栏机制改造（未声明不渲染）+ A-05 KPI 卡仅异常着色 | `3dcb91a`、`d6fdfd1` | 工具栏 9+ → 2-5 图标 |
| 2026-08-08 | A-06 子弹图组件 + gauge 全站退役 | `21687c3` | -843 行；性能总览/监控弹窗截图验证 |
| 2026-08-08 | A-08 空态三要素 6 页（含 DataCanvas 载体修正） | `d259b04` | 强制空态截图验证 |
| 2026-08-08 | A-09 弹窗浅色头 + A-07 首批（监控表数值排版） | `3c89819`、`b633247` | 弹窗全站统一浅色 |
| 2026-08-08 | E1 偏好抽屉清理 + E3 i18n 隐藏 + C2-3 TOP5 最差优先 | `becfc9a` | extension 零消费移除 |
| 2026-08-08 | C2-1 Sponsor 403 前置门禁 + C2-2 403 语义化 | `2325718`、`98b65d5` | sponsor 403 清零实测 |
