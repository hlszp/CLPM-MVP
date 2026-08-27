---
kind: frontend_style
name: 基于 Vben Admin + Tailwind v4 + CSS 变量的主题化前端样式体系
category: frontend_style
scope:
    - '**'
source_files:
    - frontend/internal/tailwind-config/src/theme.css
    - frontend/packages/@core/base/design/src/design-tokens/default.css
    - frontend/packages/@core/base/design/src/design-tokens/dark.css
    - frontend/packages/@core/base/design/src/design-tokens/index.ts
    - frontend/packages/styles/src/index.ts
    - frontend/apps/web-antd/src/adapter/component/index.ts
    - frontend/stylelint.config.mjs
    - frontend/internal/lint-configs/oxlint-config/src/configs/tailwindcss.ts
    - frontend/apps/web-antd/package.json
    - frontend/apps/web-antd/src/styles/industrial-light.css
---

## 1. 采用的系统与工具

CLPM 前端采用 **Vben Admin 5.x**（`vben-admin-monorepo`，版本 5.7.0）作为基础框架，构建在 Vue 3 + Vite + pnpm workspace + Turbo 之上。样式体系由三层组成：
- **UI 组件层**：以 `ant-design-vue` 为默认 UI 库，通过 `apps/web-antd/src/adapter/component/index.ts` 中的异步按需引入方式统一接入；同时保留 `packages/styles/src/antdv-next/index.css` 等兼容入口。
- **设计系统层**：`@vben-core/design`（位于 `frontend/packages/@core/base/design`）提供 shadcn-ui 风格的语义化 CSS 变量与多主题 token，并通过 `@vben/styles` 包对外暴露。
- **原子样式层**：Tailwind CSS v4（通过 `@vben/tailwind-config` 共享配置），使用 `@import 'tailwindcss'` 的 v4 新语法、`@source` 扫描 monorepo 源码、`@theme inline` 注入语义色、`@utility` 自定义工具类以及 `@layer base/components` 组织层级。

## 2. 关键文件与包

| 位置 | 作用 |
|---|---|
| `frontend/internal/tailwind-config/src/theme.css` | Tailwind v4 全局主题入口：定义 `@source`、`dark` 变体、`@theme inline` 语义色（primary / destructive / success / warning 及 green/red/yellow 别名）、`@layer base/components` 基础样式与动画 |
| `frontend/packages/@core/base/design/src/design-tokens/default.css` | 浅色主题 CSS 变量集合（`--background`、`--primary`、`--card`、`--border`、`--ring`、`--sidebar`、`--header` 等），支持 `data-theme` 切换（default/violet/pink/rose/sky-blue/deep-blue/green/deep-green/orange/yellow/zinc/neutral/slate/gray） |
| `frontend/packages/@core/base/design/src/design-tokens/dark.css` | 深色主题对应变量覆盖，与浅色一一对应 |
| `frontend/packages/@core/base/design/src/design-tokens/index.ts` | 统一导入 default.css 与 dark.css |
| `frontend/packages/styles/src/index.ts` | 导出 `@vben-core/design`，是应用样式入口 |
| `frontend/apps/web-antd/package.json` | 声明依赖 `ant-design-vue`、`echarts`、`pinia`、`vue-router` 及全部 `@vben/*` 内部包 |
| `frontend/stylelint.config.mjs` | 继承 `@vben/stylelint-config`，并针对 Tailwind 工具类关闭 BEM 命名检查 |
| `frontend/internal/lint-configs/oxlint-config/src/configs/tailwindcss.ts` | 通过 `eslint-plugin-better-tailwindcss` 强制 Tailwind 类顺序一致 |
| `frontend/apps/web-antd/src/adapter/component/index.ts` | 统一按需异步加载 ant-design-vue 子模块，屏蔽完整引入 |
| `frontend/apps/web-antd/src/styles/industrial-light.css` | 工业场景专用样式覆盖（业务级补充） |

## 3. 架构与设计约定

### 3.1 设计令牌（Design Tokens）优先
所有颜色、圆角、阴影、字体均通过 CSS 自定义属性（`--primary`、`--destructive`、`--radius`、`--shadow-float` 等）表达，而非硬编码十六进制值。`@vben/tailwind-config` 中通过 `hsl(var(--xxx))` 将 CSS 变量映射到 Tailwind 语义色，使 `bg-primary`、`text-destructive` 等类名自动跟随主题。

### 3.2 多主题策略
主题通过 `<html>` 或根节点上的 `class="light"` / `class="dark"` 以及 `data-theme="violet|pink|..."` 组合切换。`default.css` 与 `dark.css` 分别定义两套完整的 HSL 变量族，新增主题只需追加一个 `[data-theme='xxx']` 选择器块即可。

### 3.3 Tailwind v4 用法规范
- 使用 `@import 'tailwindcss'` 替代旧版 `tailwind.config.js` 配置文件；主题集中在 `internal/tailwind-config/src/theme.css`。
- 使用 `@source '../../../packages/'`、`'../../../apps/'`、`'../../../docs/'`、`'../../../playground/'` 扫描 monorepo 全量源码生成 utility。
- 使用 `@custom-variant dark (&:is(.dark *))` 实现基于 `.dark` 类的暗色模式。
- 使用 `@utility flex-center`、`flex-col-center` 等封装业务常用布局组合。
- 组件样式放入 `@layer components`，基础 reset 放入 `@layer base`，避免层叠冲突。

### 3.4 组件样式来源分层
- 通用 UI 组件来自 `ant-design-vue`，通过 `defineAsyncComponent` 按需加载，减少打包体积。
- 业务级卡片、链接、选中框等复用 `@vben-core/design` 提供的 `card-box`、`outline-box`、`vben-link` 等 class。
- 页面级样式放在 `apps/web-antd/src/views/**` 的 SCSS/CSS 文件中，遵循 BEM（stylelint 规则由 `@vben/stylelint-config` 约束，仅对 Tailwind 工具类豁免）。

### 3.5 响应式策略
未引入独立的媒体查询断点库；响应式通过 Tailwind 内置的 `sm:`、`md:`、`lg:` 前缀工具类实现，配合 `use-is-mobile` 等 composables 在逻辑层判断设备类型。

## 4. 约定与约束

| 约定 | 说明 | 依据 |
|---|---|---|
| 颜色必须走 CSS 变量 | 禁止在组件中直接写死 `#xxxxxx` 色值，应使用 `--color-*` 语义变量 | `theme.css` 中所有 `--color-*` 均映射至 `hsl(var(--xxx))` |
| 主题切换通过 class/data-theme | 新增主题需同时在 `default.css` 和 `dark.css` 中成对定义 | `design-tokens/default.css` 与 `dark.css` 的结构对称性 |
| Tailwind 类顺序一致 | 同一元素上多个 Tailwind 类按固定顺序排列 | `eslint-plugin-better-tailwindcss` 启用 `enforce-consistent-class-order` |
| 禁用 BEM 强校验 | Tailwind 工具类不遵循 BEM 命名，stylelint 已显式关闭该规则 | `stylelint.config.mjs` 中 `'selector-class-pattern': null` |
| 组件按需引入 | 通过 `ant-design-vue/es/*` 路径异步加载，避免全量引入 | `adapter/component/index.ts` 中每个组件均为 `defineAsyncComponent(() => import(...))` |
| 暗色模式基于 `.dark` 类 | 不使用 `prefers-color-scheme`，统一通过 `.dark` 选择器控制 | `theme.css` 中 `@custom-variant dark (&:is(.dark *))` |
| 动画通过 Tailwind v4 @keyframes | 进入动画、浮动动画等集中定义在 `theme.css` 的 `@keyframes` 区块 | `theme.css` 中 `enter-x-animation`、`enter-y-animation`、`float` 等 |

## 5. 总结

CLPM 前端样式体系以 **Vben Admin 5.x** 为骨架，**Ant Design Vue** 为交互组件底座，**shadcn-ui 风格 CSS 变量** 为设计令牌中心，**Tailwind CSS v4** 为原子样式引擎，通过 monorepo 内 `@vben/tailwind-config` 与 `@vben-core/design` 两个共享包实现跨应用一致的主题与视觉规范。新增主题、语义色或业务组件样式时，应优先扩展 CSS 变量与 Tailwind 语义类，而非编写独立样式文件。