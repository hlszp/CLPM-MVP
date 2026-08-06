# CLPM IA 重构 Phase A 实施计划

> **实施前必读**：`docs/过程文档/clpm-ia-refactor-and-optimization-plan-2026-08-06.md` §3/§5/§7/§8/§11；`AGENTS.md` 铁律。
> **分支**：`IA-PhaseA`（从 `IA` 创建）。**后端零改动**。

**Goal:** 一级菜单重组为 7 项 + 帮助 FAB；8 项结构性配置路径重命名集中到配置模块；AI 洞察由 4 处内嵌改为工具栏图标触发的右抽屉（两级门禁）；跨模块上下文 `useLoopContext()` 基建；E2E 同步全绿。

**Architecture:** vue-router 绝对子路径 reparent（leaf URL 稳定）+ 配置路径重命名 + legacy redirect 兼容；AI 抽屉复用 `ClpmAiInsight` 的 API/fallback 逻辑，新增 `ClpmAiDrawer` 外壳 + `useAiInsightGate` 两级门禁 composable；E2E 改写受影响路由引用并扩充 `route-compat.spec.ts`。

**Tech Stack:** Vue 3 + Vite + vue-vben-admin + Ant Design Vue + TypeScript + Playwright。

---

## 文件结构

### 新建
- `frontend/apps/web-antd/src/router/routes/modules/monitor.ts` — 监控组（/dashboard/workbench）
- `frontend/apps/web-antd/src/router/routes/modules/assess.ts` — 评估组（/metric/* 绝对子路径）
- `frontend/apps/web-antd/src/router/routes/modules/config.ts` — 配置组（8 项重命名 + legacy redirect）
- `frontend/apps/web-antd/src/composables/use-loop-context.ts` — 跨模块上下文 composable
- `frontend/apps/web-antd/src/composables/use-ai-insight-gate.ts` — AI 两级门禁 composable
- `frontend/apps/web-antd/src/components/clpm/ai-drawer.vue` — AI 右抽屉外壳
- `frontend/apps/web-antd/src/components/clpm/help-fab.vue` — 帮助悬浮按钮

### 修改
- `frontend/apps/web-antd/src/router/routes/modules/loop.ts` — 回路组（/loop/detail/:id 占位 + 组 redirect）
- `frontend/apps/web-antd/src/router/routes/modules/diagnosis.ts` — 移除 /diagnosis/config（迁配置），title 诊断
- `frontend/apps/web-antd/src/router/routes/modules/tuning.ts` — title 整定
- `frontend/apps/web-antd/src/router/routes/modules/system.ts` — title 系统；pid-template redirect 目标改 /config/link
- **删除** `frontend/apps/web-antd/src/router/routes/modules/dashboard.ts` — 并入 monitor.ts
- **删除** `frontend/apps/web-antd/src/router/routes/modules/metric.ts` — 由 assess.ts 替代
- `frontend/apps/web-antd/src/components/clpm/toolbar-config.ts` — 新增 ai/help 工具项
- `frontend/apps/web-antd/src/components/clpm/index.ts` — 导出新组件
- `frontend/apps/web-antd/src/components/clpm/loop-link.vue` — 改用 useLoopContext
- `frontend/apps/web-antd/src/views/dashboard/workbench.vue` — 移除内嵌 AI，加工具栏 AI 按钮
- `frontend/apps/web-antd/src/views/metric/loop-performance.vue` — 移除抽屉内嵌 AI，加工具栏 AI 按钮
- `frontend/apps/web-antd/src/views/diagnosis/detail.vue` — 移除 Tab5 AI，加工具栏 AI 按钮
- `frontend/apps/web-antd/src/views/tuning/simulation.vue` — 移除内嵌 AI（整定 AI 下线）
- `e2e/tests/*.spec.ts` — 同步 8 项重命名路由引用
- `e2e/tests/route-compat.spec.ts` — 扩充新 redirect 用例

---

## Task 1: 路由模块重组（7 菜单 + 帮助 FAB 占位）

### 1.1 新建 monitor.ts（监控组）

**File:** `frontend/apps/web-antd/src/router/routes/modules/monitor.ts`

```ts
import type { RouteRecordRaw } from 'vue-router';

/**
 * 监控路由模块（IA 重构 Phase A）
 * 定位：运行驾驶舱（系统概览/待办/数据链路健康）
 * 角色权限：全角色可见（EXPERT 默认首页 /diagnosis，不进监控）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Monitor',
    path: '/monitor',
    redirect: '/dashboard/workbench',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:activity',
      order: 1,
      title: '监控',
    },
    children: [
      {
        name: 'MonitorOverview',
        path: '/dashboard/workbench',
        component: () => import('#/views/dashboard/workbench.vue'),
        meta: {
          affixTab: true,
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:layout-dashboard',
          title: '系统概览',
        },
      },
    ],
  },
  // 旧 /dashboard 父路径兼容 redirect（保护书签/E2E）
  {
    name: 'DashboardLegacy',
    path: '/dashboard',
    redirect: '/dashboard/workbench',
    meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'], hideInMenu: true, title: '工作台' },
  },
];

export default routes;
```

**删除** `dashboard.ts`。

### 1.2 改写 loop.ts（回路组·占位）

**File:** `frontend/apps/web-antd/src/router/routes/modules/loop.ts`

```ts
import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路路由模块（IA 重构 Phase A·实体轴占位）
 * Phase A：组 redirect 到 /loop/monitor（防空菜单），/loop/detail/:id 靠 loopId 跳转进入
 * Phase B：升级为 /loop/workbench 6 Tab 工作台
 * 角色权限：ADMIN/IC_ENGINEER/EXPERT 可编辑；PE_ENGINEER 只读
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Loop',
    path: '/loop',
    redirect: '/loop/monitor',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
      icon: 'lucide:network',
      order: 2,
      title: '回路',
    },
    children: [
      {
        name: 'LoopDetail',
        path: '/loop/detail/:id',
        component: () => import('#/views/loop/detail.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          activePath: '/loop',
          title: '回路详情',
        },
      },
    ],
  },
];

export default routes;
```

> 注：`/loop/monitor` 路由本身保留在监控组下未迁入；Phase A 回路菜单点击跳到 /loop/monitor（跨组跳转，Phase B 由 /loop/workbench 取代）。`activePath: '/loop'` 让详情页打开时高亮"回路"菜单而非"监控"。

### 1.3 新建 assess.ts（评估组）替代 metric.ts

**File:** `frontend/apps/web-antd/src/router/routes/modules/assess.ts`

```ts
import type { RouteRecordRaw } from 'vue-router';

/**
 * 评估路由模块（IA 重构 Phase A·职能轴）
 * 子菜单：性能总览 / 回路性能 / 评估任务 / KPI报表
 * 指标配置已迁入配置模块（/config/metric）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Assess',
    path: '/assess',
    redirect: '/metric/pid-dashboard',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:gauge',
      order: 3,
      title: '评估',
    },
    children: [
      {
        name: 'AssessOverview',
        path: '/metric/pid-dashboard',
        component: () => import('#/views/metric/pid-dashboard.vue'),
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'], icon: 'lucide:layout-dashboard', title: '性能总览' },
      },
      {
        name: 'AssessLoopPerformance',
        path: '/metric/loop-performance',
        component: () => import('#/views/metric/loop-performance.vue'),
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'], icon: 'lucide:git-branch', title: '回路性能' },
      },
      {
        name: 'AssessTasks',
        path: '/metric/tasks',
        component: () => import('#/views/metric/tasks.vue'),
        meta: { authority: ['ADMIN', 'IC_ENGINEER'], icon: 'lucide:list-checks', title: '评估任务' },
      },
      {
        name: 'AssessKpiReport',
        path: '/metric/kpi-report',
        component: () => import('#/views/metric/kpi-report.vue'),
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'], icon: 'lucide:file-bar-chart', title: 'KPI报表' },
      },
    ],
  },
  // 旧 /metric 父路径兼容 redirect
  {
    name: 'MetricLegacy',
    path: '/metric',
    redirect: '/metric/pid-dashboard',
    meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'], hideInMenu: true, title: '性能评估' },
  },
];

export default routes;
```

**删除** `metric.ts`。**保留** `task.ts`（任务详情隐藏路由，不动）。

### 1.4 改写 diagnosis.ts（诊断组·移除 config）

移除 `/diagnosis/config` 子路由（迁入 config.ts 为 `/config/diagnosis`），其余保留；`title` 改 '诊断'，`name` 改 'Diagnose'，组 `path` 保持 `/diagnosis`（leaf URL 全部稳定）。新增 `/diagnosis/config` legacy redirect → `/config/diagnosis`（hideInMenu）。

### 1.5 改写 tuning.ts（整定组）

仅改 `title: '整定'`、`name: 'Tune'`；组 `path` 保持 `/tuning`；子路由全部不动（Phase D 再整合）。

### 1.6 改写 system.ts（系统组）

仅改 `title: '系统'`；`/system/pid-template` redirect 目标由 `/loop/aas-sync` 改为 `/config/link`；其余不动。

### 1.7 新建 config.ts（配置组·8 项重命名 + legacy redirect）

**File:** `frontend/apps/web-antd/src/router/routes/modules/config.ts`

```ts
import type { RouteRecordRaw } from 'vue-router';

/**
 * 配置路由模块（IA 重构 Phase A·结构性配置集中）
 * 来源：原散落于 loop/tag/metric/diagnosis/system 的结构性配置页
 * 权限：仅 ADMIN
 * 操作性调参（阈值微调/算法参数/时间窗/列设置）保留在各业务页内联，不迁入
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Config',
    path: '/config',
    redirect: '/config/loop',
    meta: { authority: ['ADMIN'], icon: 'lucide:settings-2', order: 6, title: '配置' },
    children: [
      {
        name: 'ConfigLink',
        path: '/config/link',
        component: () => import('#/views/loop/aas.vue'),
        meta: { authority: ['ADMIN'], icon: 'lucide:refresh-cw', order: 1, title: '链路配置' },
      },
      {
        name: 'ConfigTag',
        path: '/config/tag',
        component: () => import('#/views/tag/list.vue'),
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], icon: 'lucide:list', order: 2, title: '测点配置' },
      },
      {
        name: 'ConfigLoop',
        path: '/config/loop',
        component: () => import('#/views/loop/manage.vue'),
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], icon: 'lucide:network', order: 3, title: '回路配置' },
      },
      {
        name: 'ConfigFactory',
        path: '/config/factory',
        redirect: '/config/loop',
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '工厂模型' },
      },
      {
        name: 'ConfigLedger',
        path: '/config/ledger',
        redirect: '/config/loop',
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '回路台账' },
      },
      {
        name: 'ConfigDatasource',
        path: '/config/datasource',
        component: () => import('#/views/loop/data.vue'),
        meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], icon: 'lucide:database', order: 6, title: '数据源管理' },
      },
      {
        name: 'ConfigMetric',
        path: '/config/metric',
        component: () => import('#/views/metric/config.vue'),
        meta: { authority: ['ADMIN'], icon: 'lucide:settings', order: 7, title: '指标配置' },
      },
      {
        name: 'ConfigDiagnosis',
        path: '/config/diagnosis',
        component: () => import('#/views/diagnosis/config.vue'),
        meta: { authority: ['ADMIN'], icon: 'lucide:settings-2', order: 8, title: '诊断配置' },
      },
      {
        name: 'ConfigPidTemplate',
        path: '/config/pid-template',
        redirect: '/config/link',
        meta: { authority: ['ADMIN'], hideInMenu: true, title: 'PID 结构模板' },
      },
      // ===== legacy redirect（保护旧书签/E2E） =====
      { name: 'LegacyLoopAasSync', path: '/loop/aas-sync', redirect: '/config/link', meta: { authority: ['ADMIN'], hideInMenu: true, title: '链路配置' } },
      { name: 'LegacyTagList', path: '/tag/list', redirect: '/config/tag', meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '测点配置' } },
      { name: 'LegacyTagListRoot', path: '/tag', redirect: '/config/tag', meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '测点配置' } },
      { name: 'LegacyLoopManage', path: '/loop/manage', redirect: '/config/loop', meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '回路配置' } },
      { name: 'LegacyLoopFactory', path: '/loop/factory', redirect: '/config/loop', meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '工厂模型' } },
      { name: 'LegacyLoopLedger', path: '/loop/ledger', redirect: '/config/loop', meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '回路台账' } },
      { name: 'LegacyLoopData', path: '/loop/data', redirect: '/config/datasource', meta: { authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'], hideInMenu: true, title: '数据管理' } },
      { name: 'LegacyMetricConfig', path: '/metric/config', redirect: '/config/metric', meta: { authority: ['ADMIN'], hideInMenu: true, title: '指标配置' } },
      { name: 'LegacyDiagnosisConfig', path: '/diagnosis/config', redirect: '/config/diagnosis', meta: { authority: ['ADMIN'], hideInMenu: true, title: '诊断配置' } },
      { name: 'LegacySystemPidTemplate', path: '/system/pid-template', redirect: '/config/link', meta: { authority: ['ADMIN'], hideInMenu: true, title: 'PID 结构模板' } },
    ],
  },
];

export default routes;
```

> **关键**：原 `loop.ts` 中的 `/loop/aas-sync`、`/loop/manage`、`/loop/factory`、`/loop/ledger`、`/loop/data`、`/tag/list` 子路由**删除**（已迁入 config.ts 为新路径 + legacy redirect）。原 `metric.ts` 的 `/metric/config` 删除（迁入 config.ts）。原 `diagnosis.ts` 的 `/diagnosis/config` 删除。原 `system.ts` 的 `/system/pid-template` 删除（由 config.ts legacy redirect 接管）。

### 1.8 验证

- [ ] `cd frontend && pnpm run check:type` 全绿
- [ ] 启动前端，菜单显示 7 项：监控/回路/评估/诊断/整定/配置/系统
- [ ] 直链访问 `/loop/aas-sync` → redirect 到 `/config/link`，页面正常
- [ ] 直链访问 `/metric/config` → redirect 到 `/config/metric`，页面正常
- [ ] `/loop/detail/:id` 可从 loop-link 跳转进入，高亮"回路"菜单
- [ ] 提交：`feat(ia): Phase A 路由重组 7 菜单 + 配置集中化`

---

## Task 2: useLoopContext composable + loop-link 重构

### 2.1 新建 composable

**File:** `frontend/apps/web-antd/src/composables/use-loop-context.ts`

```ts
/**
 * 跨模块回路上下文 composable（IA 重构 Phase A）
 *
 * 统一 ?loopId= / ?taskId= query 规范，封装跨模块跳转。
 * 所有跨模块跳转（评估→诊断→整定→回路）统一走本 composable。
 */
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';

export interface LoopContext {
  loopId: string | null;
  taskId: string | null;
  hasLoop: boolean;
  hasTask: boolean;
}

export function useLoopContext() {
  const route = useRoute();
  const router = useRouter();

  const loopId = computed(() => {
    const v = route.query.loopId;
    return typeof v === 'string' && v.length > 0 ? v : null;
  });
  const taskId = computed(() => {
    const v = route.query.taskId;
    return typeof v === 'string' && v.length > 0 ? v : null;
  });

  const context = computed<LoopContext>(() => ({
    loopId: loopId.value,
    taskId: taskId.value,
    hasLoop: loopId.value !== null,
    hasTask: taskId.value !== null,
  }));

  /** 携带 loopId 跳转（保留 loopId，丢弃其他 query） */
  function navigateWithLoop(target: string, lid: string | null = loopId.value) {
    if (!lid) {
      router.push(target);
      return;
    }
    router.push({ path: target, query: { loopId: lid } });
  }

  /** 携带 taskId 跳转 */
  function navigateWithTask(target: string, tid: string) {
    router.push({ path: target, query: { taskId: tid } });
  }

  /** 构造带 loopId 的路径字符串（用于 router-link :to） */
  function withLoop(target: string, lid: string | null = loopId.value): string {
    if (!lid) return target;
    return `${target}?loopId=${lid}`;
  }

  return { loopId, taskId, context, navigateWithLoop, navigateWithTask, withLoop };
}
```

### 2.2 重构 loop-link.vue

将 `loop-link.vue` 内 `detailPath`/`handleMenuClick` 的硬编码路径构造改为复用 `withLoop`/`navigateWithLoop`，路径常量保持不变（/loop/detail/:id、/diagnosis/detail/:loopId、/tuning/workbench、/metric/loop-performance、/loop/monitor、/diagnosis/tracker）。

### 2.3 验证
- [ ] check:type 全绿
- [ ] loop-link 下拉菜单各项跳转带 loopId，刷新后不丢
- [ ] 提交：`feat(ia): useLoopContext composable + loop-link 复用`

---

## Task 3: AI 两级门禁 composable + toolbar-config 扩展

### 3.1 新建 use-ai-insight-gate.ts

**File:** `frontend/apps/web-antd/src/composables/use-ai-insight-gate.ts`

```ts
/**
 * AI 洞察两级门禁 composable（IA 重构 Phase A·§5.2）
 *
 * 门禁1（全局 LLM）：endpoint/apiKey/model 非空 且 llm.enabled=true
 * 门禁2（页面上下文）：场景需 loopId 时已选回路；无需上下文场景（workbench）恒通过
 *
 * 两级均通过 → 图标激活；否则灰显 + tooltip。
 * LLM 配置查询全应用共享缓存（模块级 ref），配置变更后可手动 refresh。
 */
import { computed, ref } from 'vue';
import { getLlmConfigApi } from '#/api/llm';

interface LlmConfigState {
  enabled: boolean;
  configured: boolean; // endpoint+apiKey+model 均非空
  loaded: boolean;
}

const sharedState = ref<LlmConfigState>({ enabled: false, configured: false, loaded: false });
let loadPromise: Promise<void> | null = null;

async function loadLlmConfig(): Promise<void> {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    try {
      const cfg = await getLlmConfigApi();
      const configured = Boolean(cfg.endpoint && cfg.apiKey && cfg.model);
      sharedState.value = { enabled: cfg.enabled, configured, loaded: true };
    } catch {
      sharedState.value = { enabled: false, configured: false, loaded: true };
    } finally {
      loadPromise = null;
    }
  })();
  return loadPromise;
}

export type AiGateStatus = 'disabled-llm' | 'disabled-context' | 'active';

export function useAiInsightGate() {
  /** 触发加载（在 onMounted 调用） */
  function init() {
    if (!sharedState.value.loaded) void loadLlmConfig();
  }

  /** 强制刷新（LLM 配置页修改后） */
  function refresh() {
    sharedState.value.loaded = false;
    return loadLlmConfig();
  }

  /** 门禁1是否通过 */
  const llmReady = computed(() => sharedState.value.enabled && sharedState.value.configured);

  /**
   * 计算门禁状态
   * @param loopId 当前页面选中的回路（场景需 loopId 时传入；null 表示未选）
   * @param requiresLoop 该场景是否需要 loopId
   */
  function gateStatus(loopId: string | null, requiresLoop: boolean): AiGateStatus {
    if (!llmReady.value) return 'disabled-llm';
    if (requiresLoop && !loopId) return 'disabled-context';
    return 'active';
  }

  /** tooltip 文案（§5.2.1） */
  function gateTooltip(status: AiGateStatus): string {
    switch (status) {
      case 'disabled-llm': return '请先在系统管理配置并启用 LLM';
      case 'disabled-context': return '请先选择回路';
      default: return '生成 AI 洞察';
    }
  }

  return { llmReady, init, refresh, gateStatus, gateTooltip, state: sharedState };
}
```

> **注**：模块级 `sharedState` ref 是前端标准模式（响应式共享），与 AGENTS.md 禁止的"模块级 asyncio.Lock"（后端事件循环绑定问题）无关。

### 3.2 扩展 toolbar-config.ts

在 `ToolbarAction` 联合类型追加 `'ai' | 'help'`；`TOOLBAR_ICON_MAP` 追加：
```ts
ai: 'ant-design:robot-outlined',
help: 'ant-design:question-circle-outlined',
```
`TOOLBAR_DEFAULT_VARIANT` 追加 `ai: 'default', help: 'default'`。

### 3.3 验证
- [ ] check:type 全绿
- [ ] 提交：`feat(ia): AI 两级门禁 composable + 工具栏 ai/help 图标`

---

## Task 4: ClpmAiDrawer 右抽屉组件

### 4.1 新建 ai-drawer.vue

**File:** `frontend/apps/web-antd/src/components/clpm/ai-drawer.vue`

```vue
<script lang="ts" setup>
/**
 * ClpmAiDrawer — AI 洞察右抽屉（IA 重构 Phase A·§5.2.3）
 *
 * 右侧 overlay 抽屉，动画 ≤300ms（ease-out-quint），遮罩可关、Esc 可关。
 * 内部复用 ClpmAiInsight 渲染洞察正文（autoLoad，LLM 失败 fallback 模板）。
 * 调用方：工具栏 AI 图标（已通过两级门禁）点击后 open=true。
 */
import type { AiInsightApi } from '#/api/ai-insight';

import { ClpmAiInsight } from './index';

import { Drawer } from 'ant-design-vue';

defineOptions({ name: 'ClpmAiDrawer' });

interface Props {
  /** v-model:open */
  open: boolean;
  /** 场景：diagnosis/performance/tuning/workbench */
  scene: AiInsightApi.SceneId;
  /** 回路 ID（diagnosis/performance/tuning 需要） */
  loopId?: null | string;
  /** 整定任务 ID（tuning 需要） */
  taskId?: null | string;
  /** 抽屉标题，默认按 scene */
  title?: string;
}

const props = withDefaults(defineProps<Props>(), { loopId: null, taskId: null, title: '' });
const emit = defineEmits<{ (e: 'update:open', v: boolean): void }>();

const SCENE_TITLE: Record<AiInsightApi.SceneId, string> = {
  diagnosis: 'AI 诊断洞察',
  performance: 'AI 性能分析',
  tuning: 'AI 整定建议',
  workbench: 'AI 运维洞察',
};

function handleClose() {
  emit('update:open', false);
}
</script>

<template>
  <Drawer
    :open="open"
    placement="right"
    :width="480"
    :mask="true"
    :mask-closable="true"
    :body-style="{ padding: '16px' }"
    :title="title || SCENE_TITLE[props.scene]"
    :root-style="{ '--drawer-transition': '300ms cubic-bezier(0.16,1,0.3,1)' }"
    @close="handleClose"
  >
    <ClpmAiInsight
      :scene="scene"
      :loop-id="loopId"
      :task-id="taskId"
      variant="tab"
      :auto-load="true"
      :hide-when-disabled="false"
    />
  </Drawer>
</template>

<style scoped>
:deep(.ant-drawer-content-wrapper) {
  transition: transform var(--drawer-transition, 300ms cubic-bezier(0.16,1,0.3,1));
}
</style>
```

### 4.2 index.ts 导出

追加 `export { default as ClpmAiDrawer } from './ai-drawer.vue';`

### 4.3 验证
- [ ] check:type 全绿
- [ ] 提交：`feat(ia): ClpmAiDrawer 右抽屉组件`

---

## Task 5: 替换 4 处 AI 内嵌 + 工具栏 AI 按钮

### 5.1 dashboard/workbench.vue

- **移除**：`<ClpmAiInsight class="mt-4" scene="workbench" variant="card" />`（约 line 238）
- **工具栏加 AI 按钮**：在页面工具栏 actions 区追加：
```vue
<ClpmToolbarButton
  icon="ai"
  icon-only
  :disabled="aiStatus !== 'active'"
  :disabled-reason="aiTooltip"
  tooltip="AI 运维洞察"
  @click="aiDrawerOpen = true"
/>
<ClpmAiDrawer v-model:open="aiDrawerOpen" scene="workbench" />
```
- **script**：
```ts
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { ref } from 'vue';
const { init: initAiGate, gateStatus, gateTooltip } = useAiInsightGate();
initAiGate();
const aiDrawerOpen = ref(false);
const aiStatus = computed(() => gateStatus(null, false)); // workbench 无需 loopId
const aiTooltip = computed(() => gateTooltip(aiStatus.value));
```
> scene=workbench 不需要 loopId，requiresLoop=false。

### 5.2 metric/loop-performance.vue

- **移除**：Drawer 内的 `<ClpmAiInsight scene="performance" :loop-id="drawerRecord?.loopId" />`（约 line 1931）
- **工具栏加 AI 按钮**：requiresLoop=true，loopId 取当前选中回路（`drawerRecord?.loopId` 或表格选中行）。点击时若未选回路则灰显。
```ts
const aiStatus = computed(() => gateStatus(selectedLoopId.value, true));
```
- 抽屉 `:loop-id="selectedLoopId"`。

### 5.3 diagnosis/detail.vue

- **移除**：Tab5「AI 洞察」整个 TabPane（约 line 1273-1290），Tab 数从 5→4。
- **工具栏加 AI 按钮**：scene=diagnosis，loopId=路由 params `loopId`，requiresLoop=true（本页恒有 loopId，故门禁2恒通过，仅门禁1生效）。
- 抽屉 `:loop-id="loopId"`。

### 5.4 tuning/simulation.vue

- **移除**：`<ClpmAiInsight v-if="savedTaskId" scene="tuning" ... />`（约 line 1301）
- **整定 AI 本轮下线**：不添加工具栏 AI 按钮（或添加但恒灰显，tooltip"整定 AI 洞察即将上线"）。采用方案：不添加 AI 按钮，保持工具栏整洁；后端 tuning 策略保留供后续复用。

### 5.5 验证
- [ ] check:type 全绿
- [ ] 4 处内嵌已移除（grep `ClpmAiInsight` 仅在 ai-drawer.vue 内部引用）
- [ ] workbench/loop-performance/diagnosis-detail 工具栏 AI 图标：LLM 未启用时灰显 tooltip"请先在系统管理配置并启用 LLM"；启用后可点击展开右抽屉（≤300ms，遮罩可关）
- [ ] 提交：`feat(ia): AI 内嵌改工具栏右抽屉 + 整定 AI 下线`

---

## Task 6: 帮助悬浮按钮 FAB

### 6.1 新建 help-fab.vue

**File:** `frontend/apps/web-antd/src/components/clpm/help-fab.vue`

```vue
<script lang="ts" setup>
/**
 * ClpmHelpFab — 帮助悬浮按钮（IA 重构 Phase A·§3.2 帮助悬浮）
 * 右下角 FAB，点击展开 Popover：术语表/国标引用/操作指引/Onboarding 引导。
 * 复用 ClpmOnboardingTour。
 */
import { IconifyIcon } from '@vben/icons';
import { Popover } from 'ant-design-vue';
import { ref } from 'vue';
import { ClpmOnboardingTour } from './index';

defineOptions({ name: 'ClpmHelpFab' });
const open = ref(false);
const tourOpen = ref(false);
</script>

<template>
  <div class="clpm-help-fab">
    <Popover v-model:open="open" placement="topRight" trigger="click">
      <template #content>
        <div class="clpm-help-fab__menu">
          <button class="clpm-help-fab__item" @click="tourOpen = true; open = false">
            <IconifyIcon icon="ant-design:compass-outlined" :size="14" /> 新手引导
          </button>
          <button class="clpm-help-fab__item" @click="open = false">
            <IconifyIcon icon="ant-design:book-outlined" :size="14" /> 术语表
          </button>
          <button class="clpm-help-fab__item" @click="open = false">
            <IconifyIcon icon="ant-design:file-search-outlined" :size="14" /> 国标引用
          </button>
          <button class="clpm-help-fab__item" @click="open = false">
            <IconifyIcon icon="ant-design:question-circle-outlined" :size="14" /> 操作指引
          </button>
        </div>
      </template>
      <button class="clpm-help-fab__btn" aria-label="帮助">
        <IconifyIcon icon="ant-design:question-circle-outlined" :size="22" />
      </button>
    </Popover>
    <ClpmOnboardingTour v-model:open="tourOpen" />
  </div>
</template>

<style scoped>
.clpm-help-fab { position: fixed; right: 24px; bottom: 24px; z-index: 1000; }
.clpm-help-fab__btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 50%;
  background: hsl(var(--primary)); color: hsl(var(--primary-foreground));
  border: none; cursor: pointer; box-shadow: 0 4px 12px rgb(0 0 0 / 15%);
  transition: transform 0.2s;
}
.clpm-help-fab__btn:hover { transform: scale(1.08); }
.clpm-help-fab__menu { display: flex; flex-direction: column; gap: 4px; }
.clpm-help-fab__item {
  display: flex; gap: 8px; align-items: center;
  padding: 6px 12px; background: transparent; border: none; cursor: pointer;
  font-size: 13px; color: hsl(var(--foreground)); border-radius: 4px;
}
.clpm-help-fab__item:hover { background: hsl(var(--accent)); }
</style>
```

### 6.2 挂载到布局

在 `apps/web-antd/src/layouts/default.vue`（或 BasicLayout 的内容区）末尾追加 `<ClpmHelpFab />`，全局常驻。需先确认布局文件位置后定位。

### 6.3 验证
- [ ] check:type 全绿
- [ ] 右下角帮助 FAB 常驻，点击展开 4 项菜单
- [ ] 提交：`feat(ia): 帮助悬浮按钮 FAB`

---

## Task 7: E2E 同步（13 spec + route-compat 扩充）

### 7.1 路由引用批量替换

全量替换以下路径（`e2e/tests/*.spec.ts`）：

| 旧 | 新 |
|---|---|
| `/loop/aas-sync` | `/config/link` |
| `/tag/list` | `/config/tag` |
| `/loop/manage` | `/config/loop` |
| `/loop/data` | `/config/datasource` |
| `/metric/config` | `/config/metric` |
| `/diagnosis/config` | `/config/diagnosis` |

**保留不变**（验证 redirect 的用例除外）：
- `/loop/ledger` → 仍是 redirect 测试，但断言目标改 `/config/loop`（原 `/loop/manage`）
- `/loop/factory` → redirect 断言改 `/config/loop`
- `/loop/detail/:id`、`/loop/monitor`、`/metric/pid-dashboard`、`/metric/loop-performance`、`/metric/tasks`、`/metric/kpi-report`、`/diagnosis/*`、`/tuning/*`、`/system/*`、`/dashboard/workbench` → 不变

**受影响 spec 与改动量**（按 grep 统计）：
- `loop.spec.ts`：/loop/manage(5)、/tag/list(3)、/loop/aas-sync(3)、/loop/ledger(1) redirect 断言
- `performance.spec.ts`：/metric/config(6)
- `performance-coverage.spec.ts`：/metric/config(9)
- `roles.spec.ts`：/loop/ledger(1) redirect 断言
- `confidence.spec.ts`：无 config 路由（仅 /metric/loop-performance、/metric/tasks、/loop/monitor，均不变）
- 其余 spec 无 config 路由引用

### 7.2 route-compat.spec.ts 扩充

新增一组「配置集中化旧路由 redirect」用例（8 项 legacy path × 直链/刷新/前进后退 3 维，参考现有 TUNING_LEGACY_ROUTES 模式）。最小化为直链 redirect 不白屏 + 刷新保持 2 维，控制用例数。

```ts
const CONFIG_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/loop/aas-sync', target: /\/config\/link/ },
  { legacy: '/tag/list', target: /\/config\/tag/ },
  { legacy: '/loop/manage', target: /\/config\/loop/ },
  { legacy: '/loop/data', target: /\/config\/datasource/ },
  { legacy: '/metric/config', target: /\/config\/metric/ },
  { legacy: '/diagnosis/config', target: /\/config\/diagnosis/ },
  { legacy: '/loop/factory', target: /\/config\/loop/ },
  { legacy: '/loop/ledger', target: /\/config\/loop/ },
];
```

### 7.3 验证
- [ ] `cd e2e && pnpm exec playwright test` 全绿（55+ 用例，含新增 redirect 用例）
- [ ] 提交：`test(e2e): Phase A 路由重组 E2E 同步 + 配置 redirect 用例`

---

## Task 8: 全门禁 + 文档同步

### 8.1 全门禁
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check .`（应零改动，验证后端未动）
- [ ] `cd backend && uv run pytest -q`（全绿，验证未破坏）
- [ ] `cd backend && uv run alembic check`（退出码 0，无 schema 漂移）
- [ ] `cd frontend && pnpm run check:type`（全绿）
- [ ] `cd frontend && pnpm run format`（格式化）

### 8.2 文档同步
- [ ] `AGENTS.md`：基线表「重构后实现契约」版本 v2.5→v2.6，标注 Phase A 已完成；Git 工作流 IA 重构分支策略进度更新
- [ ] `docs/设计文档/00-BASELINE/implementation-contract.md`：§2 信息架构契约表更新为 7 模块；§3 路由命名决策新增配置集中化与 legacy redirect 说明；版本 v2.5→v2.6，追加 v2.6 修订摘要
- [ ] 提交：`docs(ia): Phase A 基线同步（契约 v2.6 + AGENTS.md）`

### 8.3 合并回 IA + 推送
- [ ] `git checkout IA && git merge --no-ff IA-PhaseA -m "feat(ia): Phase A 菜单重组+配置集中化+AI抽屉+上下文基建"`
- [ ] `git push origin IA && git push github IA`
- [ ] 报告并等待用户确认后进 Phase B

---

## 验收自检（对照 §11）

| 项 | 标准 | 自检 |
|---|---|---|
| V1 | 一级菜单 7 项 + 帮助悬浮 | ☐ |
| V3 | 统一工具栏部署，灰显逻辑正确 | ☐ |
| V4 | AI 图标三态灰显 + 激活可点 | ☐ |
| V5 | AI 右抽屉右侧 overlay ≤300ms 遮罩可关 | ☐ |
| V8 | 后端零改动 | ☐ |
| V9 | E2E 全绿（55+） | ☐ |

## 风险与回退

- **E2E 批量改写引入测试 bug**：逐 spec 改动后单独运行该 spec 验证，再全量回归
- **vue-vben 菜单对绝对子路径 reparent 的渲染**：若回路面板不显示，回退为回路面板仅含 redirect 无子路由（/loop/detail/:id 临时挂到监控组下）
- **AI 抽屉动画时长**：用浏览器 DevTools Performance 面板验证 ≤300ms
- **legacy redirect 链断裂**：route-compat.spec 逐项守护
