<script lang="ts" setup>
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
  if (path === '/metric/engine-config') return 'engine';
  if (path === '/metric/type-weight') return 'type-weight';
  if (path === '/metric/level-weight') return 'level-weight';
  if (path === '/metric/tasks') return 'tasks';
  return 'definition';
});

function handleChange(key: Key) {
  const map: Record<string, string> = {
    definition: '/metric/config',
    engine: '/metric/engine-config',
    'level-weight': '/metric/level-weight',
    tasks: '/metric/tasks',
    'type-weight': '/metric/type-weight',
  };
  router.push(map[key] || '/metric/config');
}
</script>

<template>
  <Tabs :active-key="activeKey" class="metric-config-tabs" @change="handleChange">
    <TabPane key="definition" tab="指标定义" />
    <TabPane key="engine" tab="引擎规则" />
    <TabPane key="type-weight" tab="类型权重" />
    <TabPane key="level-weight" tab="级别权重" />
    <TabPane key="tasks" tab="执行记录" />
  </Tabs>
</template>

<style scoped>
.metric-config-tabs {
  margin-bottom: 12px;
}
</style>
