# E2E 既有失败用例独立修复计划

| 字段 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 制定日期 | 2026-07-28 |
| 修复范围 | 6 个既有 E2E 失败用例（performance / confidence / metric-tasks-fixes） |
| 失败来源 | 2026-07-08 性能评估模块系统性重构（commit `772d99a0`）后 E2E 未同步对齐 |
| 修复性质 | **全部为 E2E 测试侧修复**，无前端代码 Bug |

---

## 一、根因总览

经基线对比验证（在 `10d96f02` 上同样 6 个失败）+ 前端源码交叉核查，6 个失败用例的根因分为三类：

| 根因类型 | 用例数 | 说明 |
|---|---|---|
| 旧路由 404 | 4 | E2E 访问的路径在 `772d99a0` 重构中被删除，未同步更新 |
| 正则不匹配 | 1 | E2E 期望的可信度文案与 ConfidenceBadge 实际渲染不一致 |
| 数据容差过紧 | 1 | 升序排序后首页非 E 级行不足 2 个，断言失败 |

**关键结论**：前端代码行为均符合 `772d99a0` 重构后的设计意图，**无需修改前端代码**。

### 路由变迁对照表

| E2E 测试访问路径 | 实际路由 | 状态 |
|---|---|---|
| `/metric/dashboard` | `/metric/pid-dashboard` | 路由删除，重定向到 pid-dashboard |
| `/metric/weight-config` | `/metric/config`（含 weight-config 为子 Tab） | 路由删除，降级为 config 的 Tab |
| `/metric/ranking` | 无（功能废弃，部分并入 pid-dashboard TOP5） | 路由删除，无替代 |
| `/metric/loop-performance` | `/metric/loop-performance` | 路由存在（非路由问题） |
| `/metric/tasks` | `/metric/tasks` | 路由存在（非路由问题） |

---

## 二、逐项修复方案

### FIX-1: E2E-PERF-002 全局看板（旧路由 + 期望元素失效）

| 项 | 内容 |
|---|---|
| 文件 | `e2e/tests/performance.spec.ts:71` |
| 根因 | 访问 `/metric/dashboard`（已删除）→ 404；即使改路径，期望的 `.clpm-kpi-grid`/"综合性能"/"平均自控率"/"稳定率"/AutoRateGauge 在 pid-dashboard.vue 中均不存在 |
| 修复方向 | **重写**：路径改 `/metric/pid-dashboard`，期望元素改为实际渲染的 `.clpm-pid-dashboard__gauge-card` + 6 个仪表盘标题（实时自控率/性能评分/自控率/平稳率/好值率/仪表故障率） |
| 验收标准 | 用例通过；断言的标题文本与 pid-dashboard.vue 实际渲染一致 |

### FIX-2: E2E-PERF-004 装置级 KPI + 仪表盘（同 FIX-1）

| 项 | 内容 |
|---|---|
| 文件 | `e2e/tests/performance.spec.ts:140` |
| 根因 | 同 FIX-1，访问 `/metric/dashboard` → 404 |
| 修复方向 | **重写**：路径改 `/metric/pid-dashboard`；验证 6 个仪表盘卡片 + "TOP5回路"预览表格（非"低效回路 Top 10 预览"）；canvas/ECharts 容器验证保留 |
| 验收标准 | 用例通过；"TOP5回路"表格可见 |

### FIX-3: E2E-PERF-005 权重配置管理（旧路由 + Tab 结构变更）

| 项 | 内容 |
|---|---|
| 文件 | `e2e/tests/performance.spec.ts:187` |
| 根因 | 访问 `/metric/weight-config`（已删除）→ 404；期望的 3 个内部 Tab（控制类型权重模板/性能定级阈值/版本历史）已重构为 config.vue 的 5 个顶层 Tab |
| 修复方向 | **重写**：路径改 `/metric/config`；断言 5 个顶层 Tab（指标定义/权重配置/定级阈值/数据可信度/参数配置）；切到"权重配置"Tab 后断言"恢复国标默认值"按钮存在；"版本历史"功能已废弃，删除相关断言 |
| 验收标准 | 用例通过；5 个 Tab 文案与 config.vue 实际一致 |

### FIX-4: E2E-PERF-006 低效排行参评过滤（功能废弃）

| 项 | 内容 |
|---|---|
| 文件 | `e2e/tests/performance.spec.ts:245` |
| 根因 | `/metric/ranking` 路由删除，"包含不参评回路"/"仅显示有效评分"开关在前端已不存在 |
| 修复方向 | **重写为 pid-dashboard TOP5 验证**：路径改 `/metric/pid-dashboard`；验证"TOP5回路"表格存在 + 升降序切换按钮可用；删除已废弃的开关断言。**注意**：同文件 E2E-PERF-003（:95）也访问 `/metric/ranking`，需同步处理 |
| 验收标准 | 用例通过；TOP5 表格与排序切换验证通过 |

### FIX-5: E2E-CONF-001 可信度单元格正则不匹配

| 项 | 内容 |
|---|---|
| 文件 | `e2e/tests/confidence.spec.ts:37,45` |
| 根因 | E2E 正则 `/^([A-E]\s*(优秀\|良好\|一般\|较差\|不足)\|—)$/` 期望"全称+中文后缀"，但 ConfidenceBadge 组件实际渲染 A→"A"/B→"B"/C→"C"/D→"D"/E→"INCONCLUSIVE" |
| 修复方向 | **改正则**：`CONFIDENCE_RE` 改为 `/^(A\|B\|C\|D\|INCONCLUSIVE\|—)$/`；同步修正文件头注释（第 15-16 行）说明表格单元格用 ConfidenceBadge 渲染（缩写），详情抽屉用 CONFIDENCE_LABEL_MAP（全称） |
| 验收标准 | 用例通过；正则覆盖 ConfidenceBadge 的 5 种等级 + 空值占位 |
| 注意 | E2E-CONF-003（评估历史 Tab，:109）用 history-snapshots.vue 的 Tag 渲染（全称"A 优秀"），需用不同正则，不可统一 |

### FIX-6: F3 评估历史综合评分列排序（数据容差过紧）

| 项 | 内容 |
|---|---|
| 文件 | `e2e/tests/metric-tasks-fixes.spec.ts:150` |
| 根因 | 升序排序后首页 20 行中非 E 级行仅 1 个（E 级评分被 F5 掩码为"—"），`readScores()` 过滤后长度=1，不满足 `>= 2` |
| 修复方向 | **加可信度筛选**：排序前先用"可信度"Select 筛选 A/B/C/D（排除 E 级掩码行），确保可见评分行 ≥ 2，再点击排序头校验单调性 |
| 验收标准 | 用例通过；排序后至少 2 个可见数字评分，且单调性校验通过 |
| 备选方案 | 换日期窗口选非 E 级快照更多的日期（需先调研 DB 数据分布） |

---

## 三、修复时间表

| 阶段 | 用例 | 预估时长 | 关键节点 |
|---|---|---|---|
| 阶段 1：路由类修复 | FIX-1/2/3/4 | 40 min | 4 个旧路由用例重写完成 |
| 阶段 1 验证 | — | 10 min | `playwright test performance.spec.ts` 通过 |
| 阶段 2：正则类修复 | FIX-5 | 10 min | CONF-001 正则修正 |
| 阶段 2 验证 | — | 10 min | `playwright test confidence.spec.ts` 通过 |
| 阶段 3：数据容差修复 | FIX-6 | 20 min | F3 加可信度筛选 |
| 阶段 3 验证 | — | 10 min | `playwright test metric-tasks-fixes.spec.ts` 通过 |
| 全量回归 | — | 15 min | 全部 E2E 通过，无新增失败 |

---

## 四、验证流程

```bash
# 阶段验证
cd e2e && pnpm exec playwright test --reporter=list tests/performance.spec.ts
cd e2e && pnpm exec playwright test --reporter=list tests/confidence.spec.ts
cd e2e && pnpm exec playwright test --reporter=list tests/metric-tasks-fixes.spec.ts

# 全量回归
cd e2e && pnpm exec playwright test --reporter=list
```

**验收标准**：原 6 个失败用例全部通过，且不引入新的失败用例。

---

## 五、风险评估

| 风险 | 等级 | 应对 |
|---|---|---|
| pid-dashboard 仪表盘数据为空导致 canvas 未渲染 | 中 | E2E 容忍数据为空，验证容器存在即可（沿用原用例兜底逻辑） |
| F3 加可信度筛选后仍不足 2 行 | 低 | 备选方案：换日期窗口或放宽容差到 >= 1 |
| E2E-PERF-003（同文件未运行用例）也访问旧路由 | 中 | 阶段 1 一并修复 |
| 重写后断言与未来 UI 变更再次脱节 | 低 | 在测试文件头注释标注"基于 pid-dashboard.vue / config.vue 实际实现" |

---

## 六、预防措施

| 措施 | 说明 |
|---|---|
| 路由变更同步 E2E | 前端路由重构（如 `772d99a0`）时，PR 检查清单增加"E2E 测试路径同步"项 |
| E2E 选择器对齐设计契约 | E2E 期望的文案/类名应引用实现契约 v2.1，而非凭记忆编写 |
| E2E 定期全量运行 | 每次合并前运行全量 E2E，避免"测试写了不跑"导致长期失效 |
| ConfidenceBadge 渲染文档化 | 在 confidence-badge.vue 补充渲染输出说明，避免 E2E 正则与实际脱节 |

---

*计划制定：2026-07-28*
*根因调查依据：前端源码 + E2E 测试源码 + test-results/error-context.md 三方交叉验证*
