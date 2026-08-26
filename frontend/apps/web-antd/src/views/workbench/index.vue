<script setup lang="ts">
/**
 * 工作台 v2.0 统一框架页（单路由 + v-show 组件切换）
 *
 * 架构：Shell（HeaderBar/ModuleBanner/TabBar/StatusBar）一次挂载常驻，
 * 切 Tab 只切换内容区 v-show，不整页刷新、不重渲染 Shell。
 * 5 Tab 内容组件各自 watch store.scopeParams 联动刷新（v-show 保活，已 mounted）。
 * 跨 Tab 跳转（如评估→诊断）用 store.setActiveTab，非路由。
 *
 * 高度规范（@1600×900）：36 tabbar(含筛选) + ≈828 content + 28 statusbar
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted } from 'vue';

import { Page } from '@vben/common-ui';

import { useWorkbenchStore } from '#/store/workbench';

import HeaderBar from './components/HeaderBar.vue';
import ModuleBanner from './components/ModuleBanner.vue';
import ModuleStatusDot from './components/ModuleStatusDot.vue';
import ModuleVeil from './components/ModuleVeil.vue';
import StatusBar from './components/StatusBar.vue';
import Assessment from './tabs/assessment.vue';
import Diagnosis from './tabs/diagnosis.vue';
import Handling from './tabs/handling.vue';
import Overview from './tabs/overview.vue';
import Tuning from './tabs/tuning.vue';

const store = useWorkbenchStore();

onMounted(async () => {
  await Promise.all([store.loadPlugins(), store.loadScopeTree()]);
  store.markRefreshed();
});

/** 5 Tab 定义（moduleKey 映射 A-10 plugins 的 module_key，渲染 4 态 dot） */
const TABS: { key: string; moduleKey: string; name: string }[] = [
  { key: 'overview', moduleKey: 'monitor', name: '系统总览' },
  { key: 'assessment', moduleKey: 'assess', name: '性能评估' },
  { key: 'diagnosis', moduleKey: 'diagnosis', name: '回路诊断' },
  { key: 'tuning', moduleKey: 'tuning', name: '参数整定' },
  { key: 'handling', moduleKey: 'handling', name: '问题处置' },
];

function getPlugin(key: string): undefined | WorkbenchApi.Plugin {
  return store.plugins.find((p) => p.module_key === key);
}

/** 当前 Tab 对应模块 plugin（用于维护面纱覆盖当前 Tab） */
const currentPlugin = computed<undefined | WorkbenchApi.Plugin>(() => {
  const tab = TABS.find((t) => t.key === store.activeTab);
  return tab ? getPlugin(tab.moduleKey) : undefined;
});
</script>

<template>
  <Page auto-content-height :height-offset="8">
    <div class="flex h-full w-full min-h-0 flex-col overflow-hidden bg-[#F5F7FA]">
      <!-- 维护横幅（MAINTENANCE 模块提示，自适应高度；无维护时不渲染） -->
      <ModuleBanner />

      <!-- Tab 栏 36px：左侧 5 Tab + 4 态 dot，右侧筛选区 + 铃铛（button 切换 activeTab，非路由） -->
      <nav
        class="flex h-9 flex-none items-center justify-between border-b border-[#E4E7ED] bg-white px-4"
      >
        <div class="flex h-full items-center gap-1">
          <button
            v-for="tab in TABS"
            :key="tab.key"
            class="flex h-full items-center gap-1.5 border-b-2 px-3 text-sm transition-colors"
            :class="
              store.activeTab === tab.key
                ? 'border-[#1F4E79] font-medium text-[#1F4E79]'
                : 'border-transparent text-gray-600 hover:text-[#1F4E79]'
            "
            @click="store.setActiveTab(tab.key)"
          >
            <ModuleStatusDot
              :size="6"
              :status="getPlugin(tab.moduleKey)?.status ?? 'UNINSTALLED'"
            />
            {{ tab.name }}
          </button>
        </div>
        <HeaderBar />
      </nav>

      <!-- 内容区 flex-1：v-show 切换 5 Tab 内容组件（保活，切 Tab 不重载，scope 变 watch 联动刷新） -->
      <div class="relative flex-1 min-h-0 overflow-hidden bg-[#F5F7FA]">
        <Overview v-show="store.activeTab === 'overview'" />
        <Assessment v-show="store.activeTab === 'assessment'" />
        <Diagnosis v-show="store.activeTab === 'diagnosis'" />
        <Tuning v-show="store.activeTab === 'tuning'" />
        <Handling v-show="store.activeTab === 'handling'" />
        <!-- 维护面纱覆盖当前 Tab（MAINTENANCE 模块时显示） -->
        <ModuleVeil v-if="currentPlugin" :plugin="currentPlugin" />
      </div>

      <!-- 状态栏 28px -->
      <StatusBar />
    </div>
  </Page>
</template>
