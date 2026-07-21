<script lang="ts" setup>
import { defineAsyncComponent, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { TabPane, Tabs } from 'ant-design-vue';

defineOptions({ name: 'MetricConfig' });

const activeTab = ref('definition');

const DefinitionTab = defineAsyncComponent(
  () => import('./config-definition.vue'),
);
const WeightTab = defineAsyncComponent(() => import('./weight-config.vue'));
const GradingTab = defineAsyncComponent(
  () => import('./grading-threshold.vue'),
);
const ConfidenceTab = defineAsyncComponent(
  () => import('./confidence-threshold.vue'),
);
const OutlierParamsTab = defineAsyncComponent(
  () => import('./outlier-params.vue'),
);
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
      <TabPane key="confidence" tab="数据可信度">
        <ConfidenceTab />
      </TabPane>
      <TabPane key="outlier" tab="参数配置">
        <OutlierParamsTab />
      </TabPane>
    </Tabs>
  </Page>
</template>
