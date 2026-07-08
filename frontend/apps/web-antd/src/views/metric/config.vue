<script lang="ts" setup>
import { defineAsyncComponent, ref } from 'vue';

import { TabPane, Tabs } from 'ant-design-vue';

import { Page } from '@vben/common-ui';

defineOptions({ name: 'MetricConfig' });

const activeTab = ref('definition');

const DefinitionTab = defineAsyncComponent(() => import('./config-definition.vue'));
const WeightTab = defineAsyncComponent(() => import('./weight-config.vue'));
const GradingTab = defineAsyncComponent(() => import('./grading-threshold.vue'));
</script>

<template>
  <Page>
    <Tabs v-model:active-key="activeTab">
      <TabPane key="definition" tab="指标定义">
        <DefinitionTab />
      </TabPane>
      <TabPane key="weight" tab="权重配置">
        <WeightTab />
      </TabPane>
      <TabPane key="grading" tab="定级阈值">
        <GradingTab />
      </TabPane>
    </Tabs>
  </Page>
</template>
