<script lang="ts" setup>
/**
 * 指标配置 Tab 导航（B2.5 重构）
 *
 * 对齐 UI/UX 改造方案 §6.1.4 + 设计要求 5 Tab：
 * 指标定义 / 权重配置 / 引擎规则 / 任务策略 / 执行记录
 *
 * 合并原"类型权重 + 级别权重"为"权重配置"单 Tab，
 * 内部子 Tab 切换由 weight-config.vue 容器实现。
 */
import { computed } from 'vue';
import type { Key } from 'ant-design-vue/es/_util/type';
import { useRoute, useRouter } from 'vue-router';

import { Tabs, TabPane } from 'ant-design-vue';

defineOptions({ name: 'MetricConfigTabs' });

const router = useRouter();
const route = useRoute();

const activeKey = computed(() => {
  const path = route.path;
  if (path === '/metric/config') return 'definition';
  if (path === '/metric/weight-config') return 'weight';
  if (path === '/metric/engine-config') return 'engine';
  if (path === '/metric/task-strategy') return 'task-strategy';
  if (path === '/metric/tasks') return 'tasks';
  if (path === '/metric/grading-threshold') return 'grading';
  if (path === '/metric/version-history') return 'version';
  return 'definition';
});

function handleChange(key: Key) {
  const map: Record<string, string> = {
    definition: '/metric/config',
    engine: '/metric/engine-config',
    'task-strategy': '/metric/task-strategy',
    tasks: '/metric/tasks',
    weight: '/metric/weight-config',
    grading: '/metric/grading-threshold',
    version: '/metric/version-history',
  };
  router.push(map[key] || '/metric/config');
}
</script>

<template>
  <Tabs
    :active-key="activeKey"
    class="metric-config-tabs"
    @change="handleChange"
  >
    <TabPane key="definition" tab="指标定义" />
    <TabPane key="weight" tab="权重配置" />
    <TabPane key="grading" tab="定级阈值" />
    <TabPane key="engine" tab="引擎规则" />
    <TabPane key="task-strategy" tab="任务策略" />
    <TabPane key="tasks" tab="执行记录" />
    <TabPane key="version" tab="版本管理" />
  </Tabs>
</template>

<style scoped>
.metric-config-tabs {
  margin-bottom: 12px;
}
</style>
