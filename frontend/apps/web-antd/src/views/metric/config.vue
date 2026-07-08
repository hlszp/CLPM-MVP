<script lang="ts" setup>
import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Tabs, TabPane } from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';

defineOptions({ name: 'MetricConfig' });

const activeTab = ref<'definition' | 'weight'>('definition');

const definitionKey = ref(0);
const weightKey = ref(0);

function handleTabChange(key: string | number) {
  activeTab.value = key as 'definition' | 'weight';
  if (key === 'definition') definitionKey.value++;
  if (key === 'weight') weightKey.value++;
}
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="指标配置"
      subtitle="管理性能指标定义、权重模板与性能定级阈值（对齐 GB/T 44693.2-2024）"
    />
    <div class="mt-4">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <TabPane key="definition" tab="指标定义">
          <component :is="(() => import('./config-definition.vue'))()" :key="definitionKey" />
        </TabPane>
        <TabPane key="weight" tab="权重配置">
          <component :is="(() => import('./weight-config.vue'))()" :key="weightKey" />
        </TabPane>
      </Tabs>
    </div>
  </Page>
</template>
