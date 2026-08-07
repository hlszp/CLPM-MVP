<script lang="ts" setup>
/**
 * 诊断任务中心（V62-P1-018：tasks/records 合并为进行中/历史 Tabs）
 *
 * 对齐 PRD §4.4 + 实现契约 v2.3
 * - Tab「进行中」：未归档诊断任务管理（触发诊断 / 取消 / 详情 / 归档）
 * - Tab「历史」：已归档诊断记录 + 诊断标签面板
 * - 旧路由 /diagnosis/records 通过 redirect 兼容重定向到本页面 ?tab=history
 * - 旧路由 /diagnosis/tasks 保持不变，直接渲染本页面
 *
 * 设计原则：
 * - 不修改 tasks.vue / records.vue 内部逻辑，仅做 Tabs 包装
 * - task-center 不再套 <Page>：子页 tasks.vue/records.vue 各自持有 <Page>，
 *   避免双重 Page 嵌套（双倍 padding / 滚动容器）
 * - records.vue 内部已有的 Tabs（归档记录/诊断标签）作为"历史"分区下的二级导航保留
 * - activeTab 与 URL query 双向同步，支持深链和浏览器前进后退
 */
import { defineAsyncComponent, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Tabs } from 'ant-design-vue';

import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { usePageToolbar, showPageHelp } from '#/composables/use-page-toolbar';

defineOptions({ name: 'DiagnosisTaskCenter' });

const route = useRoute();
const router = useRouter();

const activeTab = ref<'active' | 'history'>('active');

/** 各 Tab 组件 key，切换/刷新时自增以强制重载 */
const tabKeys = ref<{ active: number; history: number }>({
  active: 0,
  history: 0,
});

/** 工具栏刷新态 */
const loading = ref(false);

// 延迟加载子组件（避免首屏加载全部诊断数据）
const DiagnosisTasksView = defineAsyncComponent(() => import('./tasks.vue'));
const DiagnosisRecordsView = defineAsyncComponent(
  () => import('./records.vue'),
);

// URL query → activeTab 同步（支持深链 / 前进后退）
onMounted(() => {
  if (route.query.tab === 'history') {
    activeTab.value = 'history';
  }
});

// activeTab → URL query 同步（replace，不污染历史）
watch(activeTab, (val) => {
  if (val === 'history') {
    router.replace({ query: { ...route.query, tab: 'history' } });
  } else {
    const { tab: _tab, ...rest } = route.query;
    router.replace({ query: rest });
  }
});

/** 工具栏刷新：强制重载当前 Tab（子组件各自重新拉取数据） */
function handleRefresh() {
  loading.value = true;
  tabKeys.value[activeTab.value] += 1;
  setTimeout(() => {
    loading.value = false;
  }, 300);
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '诊断任务中心 帮助',
    content:
      '诊断任务中心：「进行中」Tab 管理未归档诊断任务（触发诊断 / 取消 / 详情 / 归档 / 删除），「历史」Tab 查看已归档诊断记录与诊断标签面板。刷新按钮重载当前 Tab 内容。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <div>
    <ClpmPageToolbar
      title="诊断任务中心"
      subtitle="诊断任务执行管理与历史记录查询"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <Tabs v-model:active-key="activeTab" class="mt-4">
      <Tabs.TabPane key="active" tab="进行中" force-render>
        <DiagnosisTasksView :key="tabKeys.active" />
      </Tabs.TabPane>
      <Tabs.TabPane key="history" tab="历史" force-render>
        <DiagnosisRecordsView :key="tabKeys.history" />
      </Tabs.TabPane>
    </Tabs>
  </div>
</template>
