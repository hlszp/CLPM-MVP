# CLPM 原型工程

## 定位

本工程是 CLPM（控制回路绩效治理）平台的前端原型基线，用于验证 UI/UX 设计规范的可实现性与交互逻辑。

## 唯一设计输入

**所有视觉、交互、组件、页面设计决策以以下文档为准：**

→ [`docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`](../../06-UIUX/ui-ux-design-guidelines.md) (v3.0)

该文档整合了 PRD (v2.2) / FDS (v2.0) / ADS (v2.0) / DDS (v2.0) / IDS (v2.0) 的核心内容，是后续原型系统开发与正式软件开发的**唯一 UI/UX 输入性文件**。

## 技术栈

- React 19 + TypeScript + Vite
- ECharts 6（时序波形、散点图、趋势图）
- Lucide React（语义化 SVG 图标库，禁止使用 Emoji 作功能图标）
- React Router DOM 7（路由）

## 基线状态

当前 `src/` 已重置为干净基线：

- `index.css`：UI/UX §3 设计令牌（色彩/字体/间距/圆角/阴影）的 CSS 变量定义 + 壳层布局样式
- `App.tsx`：壳层骨架（左侧导航 + 顶部状态栏 + 内容区）+ 14 条路由占位，菜单结构与角色过滤逻辑对齐 UI/UX §4.2 / §5.2
- `main.tsx`：入口（BrowserRouter 已配置）

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
