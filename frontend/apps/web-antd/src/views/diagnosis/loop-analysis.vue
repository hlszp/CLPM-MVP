<script lang="ts" setup>
/**
 * F2d 回路分析主页面 — 4 步引导式工作流
 *
 * Step 1 选回路 → Step 2 KPI 评估 → Step 3 诊断分析 → Step 4 A/B 对比
 * 支持 query.loopId 预填（F4 overview Top5 一键诊断入口）。
 */
import type { Component } from 'vue';

import { computed, markRaw, onMounted, ref, shallowRef } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Step, Steps } from 'ant-design-vue';

import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

import StepAbCompare from './loop-analysis/step-ab-compare.vue';
import StepDiagnosisResult from './loop-analysis/step-diagnosis-result.vue';
import StepKpiEvaluation from './loop-analysis/step-kpi-evaluation.vue';
import StepLoopSelector from './loop-analysis/step-loop-selector.vue';
import { useLoopAnalysis } from './loop-analysis/use-loop-analysis';

defineOptions({ name: 'DiagnosisLoopAnalysis' });

const route = useRoute();
const state = useLoopAnalysis();

const stepComponents = shallowRef<Record<number, Component>>({
  1: markRaw(StepLoopSelector),
  2: markRaw(StepKpiEvaluation),
  3: markRaw(StepDiagnosisResult),
  4: markRaw(StepAbCompare),
});

const currentStepComponent = computed(
  () => stepComponents.value[state.current] ?? StepLoopSelector,
);

function handleNext() {
  if (state.current < 4) {
    state.current += 1;
  }
}

function onStepChange(target: number) {
  // 仅允许跳到已完成的步或当前步
  if (target <= state.current) {
    state.current = target;
  }
}

onMounted(() => {
  const loopId = route.query.loopId as string;
  if (loopId) {
    state.config.loopId = loopId;
  }
});

/** 工具栏刷新态 */
const loading = ref(false);
/** 强制重载当前步骤组件（刷新时自增） */
const reloadKey = ref(0);

/** 工具栏刷新：强制重载当前步骤 */
function handleRefresh() {
  loading.value = true;
  reloadKey.value += 1;
  setTimeout(() => {
    loading.value = false;
  }, 300);
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '回路分析 帮助',
    content:
      '手动评估-诊断-分析一体化工作流：① 选择回路与时间范围 → ② KPI 评估（12 项指标计算） → ③ 诊断分析（标签与可视化） → ④ A/B 对比（处置前后验证）。步骤导航仅允许回退到已完成步骤。',
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
      title="回路分析"
      subtitle="手动评估-诊断-分析一体化工作流：选回路 → KPI 评估 → 诊断分析 → A/B 对比"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <div class="mt-4">
      <Steps
        :current="state.current - 1"
        type="navigation"
        size="small"
        @change="onStepChange(($event as number) + 1)"
      >
        <Step title="选择回路" description="回路与时间范围" />
        <Step title="KPI 评估" description="12 项指标计算" />
        <Step title="诊断分析" description="标签与可视化" />
        <Step title="A/B 对比" description="处置前后验证" />
      </Steps>
    </div>

    <keep-alive class="mt-4">
      <component
        :is="currentStepComponent"
        :key="reloadKey"
        :state="state"
        @next="handleNext"
      />
    </keep-alive>
  </Page>
</template>
