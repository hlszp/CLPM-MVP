<script lang="ts" setup>
import { defineAsyncComponent, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { TabPane, Tabs } from 'ant-design-vue';

import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { usePageToolbar, showPageHelp } from '#/composables/use-page-toolbar';

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
const AlgorithmParamsTab = defineAsyncComponent(
  () => import('./algorithm-params.vue'),
);
const ThresholdTemplateTab = defineAsyncComponent(
  () => import('./threshold-template.vue'),
);

/** 各 Tab 组件 key，切换/刷新时自增以强制重载 */
const tabKeys = ref<Record<string, number>>({
  definition: 0,
  weight: 0,
  grading: 0,
  confidence: 0,
  outlier: 0,
  algorithm: 0,
  'threshold-template': 0,
});

/** 工具栏刷新态（刷新时短暂保持供工具栏反馈） */
const loading = ref(false);

/** 工具栏刷新：强制重载当前 Tab（子组件各自重新拉取数据） */
function handleRefresh() {
  loading.value = true;
  tabKeys.value[activeTab.value] = (tabKeys.value[activeTab.value] ?? 0) + 1;
  setTimeout(() => {
    loading.value = false;
  }, 300);
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '指标配置 帮助',
    content:
      '指标配置页：指标定义（KPI 指标元数据与公式）、权重配置（综合评分各指标权重）、定级阈值（性能等级分档）、数据可信度（valid_rate 阈值 A/B/C/D/E）、异常值检测参数、KPI 算法参数、诊断阈值模板。刷新按钮重载当前 Tab 内容。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="指标配置"
      subtitle="指标定义 / 权重 / 定级阈值 / 可信度 / 异常值 / 算法参数 / 诊断阈值模板"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <div class="mt-4">
      <Tabs v-model:active-key="activeTab">
        <TabPane key="definition" tab="指标定义">
          <DefinitionTab :key="tabKeys.definition" />
        </TabPane>
        <TabPane key="weight" tab="权重配置">
          <WeightTab :key="tabKeys.weight" />
        </TabPane>
        <TabPane key="grading" tab="定级阈值">
          <GradingTab :key="tabKeys.grading" />
        </TabPane>
        <TabPane key="confidence" tab="数据可信度">
          <ConfidenceTab :key="tabKeys.confidence" />
        </TabPane>
        <TabPane key="outlier" tab="异常值检测参数">
          <OutlierParamsTab :key="tabKeys.outlier" />
        </TabPane>
        <TabPane key="algorithm" tab="KPI 算法参数">
          <AlgorithmParamsTab :key="tabKeys.algorithm" />
        </TabPane>
        <TabPane key="threshold-template" tab="诊断阈值模板">
          <ThresholdTemplateTab :key="tabKeys['threshold-template']" />
        </TabPane>
      </Tabs>
    </div>
  </Page>
</template>
