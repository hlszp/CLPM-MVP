<script setup lang="ts">
import type { WorkbenchApi } from '#/api/workbench';

/**
 * 工作台 v2.0 单屏 100vh 主布局（方案 §5.1 F-GL-01）
 *
 * 高度规范（@1600×900）：56 header + 48 tabbar + ≈760 content + 28 statusbar
 * = 892px 一屏无滚动。content 用 flex-1 自适应剩余空间。
 *
 * 结构：
 *   HeaderBar（56）— 范围/时间/可信/铃铛/人员
 *   ModuleBanner   — 维护横幅（MAINTENANCE 模块提示，自适应高度）
 *   TabBar（48）   — 5 Tab + 4 态 dot（A-10 真实数据）
 *   Content（flex-1）— RouterView + ModuleVeil（维护面纱覆盖当前 Tab）
 *   StatusBar（28）— 刷新时间/评估周期/时延/插件在线
 */
import { computed, onMounted } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import { useWorkbenchStore } from '#/store/workbench';

import HeaderBar from './components/HeaderBar.vue';
import ModuleBanner from './components/ModuleBanner.vue';
import ModuleStatusDot from './components/ModuleStatusDot.vue';
import ModuleVeil from './components/ModuleVeil.vue';
import StatusBar from './components/StatusBar.vue';

const store = useWorkbenchStore();
const route = useRoute();

onMounted(async () => {
  await store.loadPlugins();
  store.markRefreshed();
});

/** 5 Tab 定义（moduleKey 映射 A-10 plugins 的 module_key，渲染 4 态 dot） */
const TABS: { key: string; moduleKey: string; name: string; to: string }[] = [
  { key: 'overview', moduleKey: 'monitor', name: '系统总览', to: '/workbench/overview' },
  { key: 'assessment', moduleKey: 'assess', name: '性能评估', to: '/workbench/assessment' },
  { key: 'diagnosis', moduleKey: 'diagnosis', name: '回路诊断', to: '/workbench/diagnosis' },
  { key: 'tuning', moduleKey: 'tuning', name: '参数整定', to: '/workbench/tuning' },
  { key: 'handling', moduleKey: 'handling', name: '问题处置', to: '/workbench/handling' },
];

function getPlugin(key: string): undefined | WorkbenchApi.Plugin {
  return store.plugins.find((p) => p.module_key === key);
}

function isActive(to: string): boolean {
  return route.path.startsWith(to);
}

/** 当前路由对应的模块 plugin（用于维护面纱覆盖当前 Tab） */
const currentPlugin = computed<undefined | WorkbenchApi.Plugin>(() => {
  const tab = TABS.find((t) => isActive(t.to));
  return tab ? getPlugin(tab.moduleKey) : undefined;
});
</script>

<template>
  <Page auto-content-height>
    <div class="flex h-full w-full flex-col overflow-hidden bg-[#F5F7FA]">
    <!-- 顶栏 56px -->
    <HeaderBar />

    <!-- 维护横幅（MAINTENANCE 模块提示，自适应高度；无维护时不渲染） -->
    <ModuleBanner />

    <!-- Tab 栏 48px（每个 Tab 前置 4 态 dot） -->
    <nav
      class="flex h-12 flex-none items-center gap-1 border-b border-[#E4E7ED] bg-white px-4"
    >
      <RouterLink
        v-for="tab in TABS"
        :key="tab.key"
        :to="tab.to"
        class="flex h-full items-center gap-1.5 border-b-2 px-3 text-sm transition-colors"
        :class="
          isActive(tab.to)
            ? 'border-[#1F4E79] font-medium text-[#1F4E79]'
            : 'border-transparent text-gray-600 hover:text-[#1F4E79]'
        "
      >
        <ModuleStatusDot
          :size="6"
          :status="getPlugin(tab.moduleKey)?.status ?? 'UNINSTALLED'"
        />
        {{ tab.name }}
      </RouterLink>
    </nav>

    <!-- 内容区 flex-1（含维护面纱覆盖当前 Tab） -->
    <div class="relative flex-1 overflow-auto bg-[#F5F7FA]">
      <RouterView />
      <ModuleVeil v-if="currentPlugin" :plugin="currentPlugin" />
    </div>

    <!-- 状态栏 28px -->
    <StatusBar />
    </div>
  </Page>
</template>
