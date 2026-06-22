# CLPM 前端定制说明

本目录基于 [vue-vben-admin](https://github.com/vbenjs/vue-vben-admin) v5.7.0 作为前端基座。

## 基础信息

- **上游版本**: vbenjs/vue-vben-admin v5.7.0
- **UI 框架**: Ant Design Vue 4.x（使用 `apps/web-antd` 应用）
- **构建工具**: Vite 8.x + Turbo monorepo
- **包管理器**: pnpm 10+（catalog 模式管理依赖版本）
- **Node 要求**: ^22.18.0 || ^24.0.0（实际开发环境使用 v25.9.0 也可运行）

## CLPM 定制内容

### 1. 环境变量配置

| 文件 | 变量 | 值 | 说明 |
| --- | --- | --- | --- |
| `.env` | `VITE_APP_TITLE` | `CLPM 控制回路性能管理系统` | 应用标题 |
| `.env` | `VITE_APP_NAMESPACE` | `clpm-web-antd` | 应用命名空间（缓存前缀） |
| `.env.development` | `VITE_GLOB_API_URL` | `http://localhost:8001/api/v1` | 后端开发服务器地址 |
| `.env.development` | `VITE_NITRO_MOCK` | `false` | 关闭内置 Mock 服务 |
| `.env.production` | `VITE_GLOB_API_URL` | `/api/v1` | 生产环境通过反向代理转发 |

> **注意**: vue-vben-admin 使用 `VITE_APP_TITLE` 作为应用标题变量（非 `VITE_GLOB_APP_TITLE`）。

### 2. Vite 代理配置

`apps/web-antd/vite.config.ts` 中的 `/api` 代理目标已改为 `http://localhost:8001`（CLPM 后端）。

### 3. Demo 内容清理

已清理以下 demo 内容，保留框架骨架：

**已删除**:

- `apps/web-antd/src/views/demos/` — demo 页面
- `apps/web-antd/src/router/routes/modules/demos.ts` — demo 路由
- `apps/backend-mock/api/demo/` — demo 接口
- `apps/backend-mock/api/table/` — 表格 demo 接口
- `apps/backend-mock/api/system/` — 系统管理 demo 接口
- `apps/backend-mock/api/test.get.ts`、`test.post.ts`、`upload.ts` — 测试接口

**已精简**:

- `apps/web-antd/src/router/routes/modules/vben.ts` — 移除 vben 项目展示链接，保留"关于"和"个人中心"
- `apps/backend-mock/utils/mock-data.ts` — 移除 demo 菜单数据，保留 dashboard 菜单
- `apps/web-antd/src/locales/langs/*/demos.json` — 移除 demo 国际化条目

**保留的框架核心能力**:

- ✅ 路由系统与动态路由加载（`src/router/`）
- ✅ 权限控制 — 按钮级 / 路由级（`@vben/access`、`src/router/access.ts`）
- ✅ HTTP 请求封装 — Axios（`src/api/request.ts`、`@vben/request`）
- ✅ 布局组件 — 侧边栏 / 顶栏 / 多标签页（`src/layouts/`、`@vben/layouts`）
- ✅ 主题系统（`@vben/preferences`、`@core/base/design`）
- ✅ 国际化 — vue-i18n（`src/locales/`）
- ✅ Pinia Store（`src/store/`、`@vben/stores`）
- ✅ Dashboard 示例页面（`src/views/dashboard/`）

### 4. 额外依赖

| 依赖      | 版本               | 用途                                    |
| --------- | ------------------ | --------------------------------------- |
| `echarts` | catalog (^6.1.0)   | 图表库 — 波形图 / KPI 看板 / 诊断散点图 |
| `dayjs`   | catalog (^1.11.20) | 日期处理（上游已包含）                  |

## 常用命令

```bash
# 安装依赖
pnpm install

# 启动开发服务器（端口 5666）
pnpm dev:antd

# 构建生产包
pnpm build:antd

# 代码检查
pnpm lint

# 类型检查
pnpm -F @vben/web-antd run typecheck
```

## 目录结构概览

```
frontend/
├── apps/
│   ├── web-antd/          # CLPM 主应用（Ant Design Vue）
│   │   ├── src/
│   │   │   ├── adapter/       # 组件适配器
│   │   │   ├── api/           # API 接口封装
│   │   │   ├── layouts/       # 布局组件
│   │   │   ├── locales/       # 国际化
│   │   │   ├── router/        # 路由配置
│   │   │   ├── store/         # Pinia Store
│   │   │   └── views/         # 页面视图
│   │   ├── .env               # 应用基础配置
│   │   ├── .env.development   # 开发环境
│   │   └── .env.production    # 生产环境
│   └── backend-mock/      # Mock 服务（已关闭，保留备用）
├── packages/              # monorepo 共享包
│   ├── @core/             # 核心库（design/icons/shared/typings/composables/preferences/ui-kit）
│   ├── effects/           # 效果组件
│   ├── business/          # 业务组件
│   └── ...
├── internal/              # 内部工具（lint-configs/vite-config/tsconfig 等）
├── playground/            # 调试沙箱
└── pnpm-workspace.yaml    # monorepo 工作区配置（勿改）
```

## 后续开发指引

1. **新增页面**: 在 `apps/web-antd/src/views/` 下创建，并在 `src/router/routes/modules/` 添加路由
2. **API 对接**: 在 `apps/web-antd/src/api/` 下按模块创建 API 文件，后端基址为 `http://localhost:8001/api/v1`
3. **权限配置**: 通过路由 `meta.authority` 字段和 `@vben/access` 的 `AccessControl` 组件实现
4. **图表开发**: 使用 `echarts` 直接开发，或参考 `@vben/plugins` 中的 `useEcharts` 封装
