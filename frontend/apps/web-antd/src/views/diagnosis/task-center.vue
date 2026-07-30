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

defineOptions({ name: 'DiagnosisTaskCenter' });

const route = useRoute();
const router = useRouter();

const activeTab = ref<'active' | 'history'>('active');

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
</script>

<template>
  <Tabs v-model:active-key="activeTab">
    <Tabs.TabPane key="active" tab="进行中" force-render>
      <DiagnosisTasksView />
    </Tabs.TabPane>
    <Tabs.TabPane key="history" tab="历史" force-render>
      <DiagnosisRecordsView />
    </Tabs.TabPane>
  </Tabs>
</template>
