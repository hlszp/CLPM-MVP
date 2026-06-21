# CLPM 原型工程

## 定位

本工程是 CLPM（控制回路绩效治理）平台的前端原型基线，用于验证 UI/UX 设计规范的可实现性与交互逻辑。

## 唯一设计输入

**所有视觉、交互、组件、页面设计决策以以下文档为准：**

 [`docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`](../../06-UIUX/ui-ux-design-guidelines.md) (v4.0)

该文档整合了 PRD (v3.0) / FDS (v3.0) / ADS (v3.0) / DDS (v3.0) / IDS (v3.0) 的核心内容，是后续原型系统开发与正式软件开发的**唯一 UI/UX 输入性文件**。

## 技术栈

- React 19 + TypeScript + Vite
- ECharts 6（时序波形、散点图、趋势图，LTTB 降采样）
- Lucide React（语义化 SVG 图标库，禁止使用 Emoji 作功能图标）
- React Router DOM 7（路由）
- 原生 HTML5 拖拽 API（Tag 关联拖拽交互，后续可升级 dnd-kit）

## v4.0 基线状态

当前 `src/` 已对齐 UI/UX v4.0 基线：

### 文件结构

```
src/
├── App.tsx                          # 壳层骨架 + 25 条路由 + 5 角色权限过滤
├── main.tsx                         # 入口（BrowserRouter 已配置）
├── index.css                        # UI/UX §3 设计令牌 + §7 核心组件样式
├── routes/
│   └── menuConfig.ts                # 菜单结构单一来源（6 模块 + 门户 25 项）
└── components/
    ├── TagAssociationSelector.tsx   # §7.7 Tag 关联选择器（7 槽位，拖拽 + 下拉）
    ├── ConfigConfirmDialog.tsx      # §7.8 配置变更确认弹窗
    ├── PVQualityBadge.tsx           # §7.2.4 PV 质量码徽章 + 波形样式辅助
    └── StatusBadge.tsx              # §7.2 统一状态标签（计算/处理/控制模式/预诊/评分）
```

### v4.0 核心特性已落实

| 特性 | 规范章节 | 实现位置 |
|---|---|---|
| 6 模块 + 1 门户 25 页面 | §4.2 / §12.2 | `menuConfig.ts` + `App.tsx` |
| 菜单结构单一来源 | §4.2 | `routes/menuConfig.ts` |
| 5 角色权限过滤 | §5.2 | `menuConfig.ts` `getVisibleMenuGroups` + `App.tsx` 路由守卫 |
| 角色默认首页 | §5.1 / §12.4 | `menuConfig.ts` `ROLE_DEFAULT_HOME` + `App.tsx` `setRole` |
| Tag 关联选择器（7 槽位） | §7.7 / §6.2.3 / §9.5 | `TagAssociationSelector.tsx` |
| 配置变更确认弹窗 | §7.8 / §9.6 | `ConfigConfirmDialog.tsx` |
| PV 质量码徽章 | §3.1.5 / §7.2.4 / §10.5 | `PVQualityBadge.tsx` |
| 统一状态标签（三维组合） | §7.2 | `StatusBadge.tsx` |
| 设计令牌（CSS 变量） | §3 | `index.css` `:root` |
| 反 AI Slop 禁忌 | §2.2 | 全局样式（无渐变/无 Emoji/无卡片瀑布） |

### 25 条路由清单（UI/UX §12.2）

- **工作台**（1）：`/`
- **回路管理**（5）：`/loop/factory`、`/loop/ledger`、`/loop/mapping`、`/loop/monitor`、`/loop/monitor/:loopId`
- **性能评估**（5）：`/performance`、`/performance/ranking`、`/performance/metrics`、`/performance/rules`、`/performance/analytics`
- **诊断中心**（5）：`/diagnosis`、`/diagnosis/metrics`、`/diagnosis/:loopId`、`/diagnosis/tracker`、`/diagnosis/analytics`
- **回路整定**（5，Phase 2 原型）：`/tuning`、`/tuning/identification`、`/tuning/algorithm`、`/tuning/simulation`、`/tuning/analytics`
- **系统管理**（4）：`/system/users`、`/system/audit`、`/system/reports`、`/system/safety`

各页面为占位状态，待按 UI/UX §6（页面规范）与 §7（核心组件）逐步实现。

## 开发命令

```bash
npm install      # 安装依赖
npm run dev      # 启动开发服务器
npm run build    # 类型检查 + 生产构建
npm run lint     # ESLint 检查
npm run preview  # 预览生产构建
```

## 实现指引

实现新页面时，必须遵循：

1. **设计令牌**：所有颜色/字体/间距从 `index.css` CSS 变量引用，禁止散落写死 hex
2. **反 AI Slop**：禁止大面积渐变、Emoji 图标、圆角卡片瀑布流（参见 UI/UX §2.2）
3. **页面结构**：按 UI/UX §4.3 四类页面结构模式选择（工作台页/数据表页/配置页/审计页）
4. **角色权限**：菜单项与操作按钮按 UI/UX §5.2 权限矩阵过滤可见性
5. **状态表达**：Loading/Empty/Error/Success/Partial 五态按 UI/UX §8.1 实现
6. **等宽规则**：位号/评分/KPI 数值/时间戳必须使用 `.mono` 类（`--font-mono`）
7. **PV 质量码**：波形中 PV 线按质量码分段渲染（Good 实线/Bad 灰色虚线断线/Uncertain 琥珀虚线），SP/OP 线不受影响（§10.5）
8. **配置变更**：所有配置页保存操作必须弹 `ConfigConfirmDialog`，填写变更说明后才能确认（§9.6）
9. **Tag 关联**：使用 `TagAssociationSelector` 组件，支持拖拽 + 下拉双模式，必填槽位缺失标红（§7.7）
