<script lang="ts" setup>
import { defineAsyncComponent, nextTick, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { TabPane, Tabs, Tooltip } from 'ant-design-vue';

import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

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
const VersionHistoryTab = defineAsyncComponent(
  () => import('./config-version-history.vue'),
);

/** P3-01：子组件 ref Map，替代 tabKeys 自增强制重建 */
interface TabRef {
  refresh?: () => Promise<void> | void;
}

const definitionRef = ref<null | TabRef>(null);
const weightRef = ref<null | TabRef>(null);
const gradingRef = ref<null | TabRef>(null);
const confidenceRef = ref<null | TabRef>(null);
const outlierRef = ref<null | TabRef>(null);
const algorithmRef = ref<null | TabRef>(null);
const thresholdTemplateRef = ref<null | TabRef>(null);
const versionHistoryRef = ref<null | TabRef>(null);

/** 按 activeTab 获取对应子组件 ref */
function getActiveTabRef(): null | TabRef {
  const refMap: Record<string, typeof definitionRef> = {
    algorithm: algorithmRef,
    confidence: confidenceRef,
    definition: definitionRef,
    grading: gradingRef,
    outlier: outlierRef,
    'threshold-template': thresholdTemplateRef,
    version: versionHistoryRef,
    weight: weightRef,
  };
  return refMap[activeTab.value]?.value ?? null;
}

/** P3-16：各 Tab 说明文案（hover Tooltip 展示） */
const TAB_DESCRIPTIONS: Record<string, string> = {
  algorithm: 'KPI 计算用算法参数（采样窗口、滤波系数等）',
  confidence: 'valid_rate 阈值 A/B/C/D/E 等级配置',
  definition: 'KPI 指标元数据、计算公式与单位定义',
  grading: '性能等级分档阈值（优/良/中/差/劣）',
  outlier: '异常值检测算法参数（Z-Score/IQR/3σ 等）',
  'threshold-template': '诊断特征阈值模板（预警/异常门限）',
  version: '各类配置版本变更记录，ADMIN 可回滚到历史版本',
  weight: '综合评分各 KPI 指标权重配置',
};

/** 工具栏刷新态（刷新时短暂保持供工具栏反馈） */
const loading = ref(false);

/** P3-01：工具栏刷新：调用当前活动 Tab 子组件 refresh() 方法 */
async function handleRefresh() {
  loading.value = true;
  try {
    const tabRef = getActiveTabRef();
    // 等待 nextTick 确保异步 Tab 组件已挂载并完成 defineExpose
    await nextTick();
    await tabRef?.refresh?.();
  } finally {
    loading.value = false;
  }
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '指标配置 帮助',
    content:
      '指标配置页：指标定义（KPI 指标元数据与公式）、权重配置（综合评分各指标权重）、定级阈值（性能等级分档）、数据可信度（valid_rate 阈值 A/B/C/D/E）、异常值检测参数、KPI 算法参数、诊断阈值模板、版本历史（P3-06：各类配置版本变更与回滚）。刷新按钮调用当前 Tab 的 refresh() 方法重新拉取数据。',
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
      subtitle="指标定义 / 权重 / 定级阈值 / 可信度 / 异常值 / 算法参数 / 诊断阈值模板 / 版本历史"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <!-- P3-01：所有 Tab 用 ref 绑定替代 :key 强制重建，刷新调用子组件 refresh() -->
    <div class="mt-4">
      <Tabs v-model:active-key="activeTab">
        <TabPane key="definition">
          <template #tab>
            <Tooltip :title="TAB_DESCRIPTIONS.definition" placement="top">
              <span>指标定义</span>
            </Tooltip>
          </template>
          <DefinitionTab ref="definitionRef" />
        </TabPane>
        <TabPane key="weight">
          <template #tab>
            <Tooltip :title="TAB_DESCRIPTIONS.weight" placement="top">
              <span>权重配置</span>
            </Tooltip>
          </template>
          <WeightTab ref="weightRef" />
        </TabPane>
        <TabPane key="grading">
          <template #tab>
            <Tooltip :title="TAB_DESCRIPTIONS.grading" placement="top">
              <span>定级阈值</span>
            </Tooltip>
          </template>
          <GradingTab ref="gradingRef" />
        </TabPane>
        <TabPane key="confidence">
          <template #tab>
            <Tooltip :title="TAB_DESCRIPTIONS.confidence" placement="top">
              <span>数据可信度</span>
            </Tooltip>
          </template>
          <ConfidenceTab ref="confidenceRef" />
        </TabPane>
        <TabPane key="outlier">
          <template #tab>
            <Tooltip :title="TAB_DESCRIPTIONS.outlier" placement="top">
              <span>异常值检测参数</span>
            </Tooltip>
          </template>
          <OutlierParamsTab ref="outlierRef" />
        </TabPane>
        <TabPane key="algorithm">
          <template #tab>
            <Tooltip :title="TAB_DESCRIPTIONS.algorithm" placement="top">
              <span>KPI 算法参数</span>
            </Tooltip>
          </template>
          <AlgorithmParamsTab ref="algorithmRef" />
        </TabPane>
        <TabPane key="threshold-template">
          <template #tab>
            <Tooltip
              :title="TAB_DESCRIPTIONS['threshold-template']"
              placement="top"
            >
              <span>诊断阈值模板</span>
            </Tooltip>
          </template>
          <ThresholdTemplateTab ref="thresholdTemplateRef" />
        </TabPane>
        <TabPane key="version">
          <template #tab>
            <Tooltip :title="TAB_DESCRIPTIONS.version" placement="top">
              <span>版本历史</span>
            </Tooltip>
          </template>
          <VersionHistoryTab ref="versionHistoryRef" />
        </TabPane>
      </Tabs>
    </div>
  </Page>
</template>
