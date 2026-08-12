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

import { message, Step, Steps } from 'ant-design-vue';

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
  if (state.current >= 4) return;

  // ========== P3-26：步骤切换前校验前置数据完整性 ==========
  // 校验目标：当前步的"产出数据"必须齐全，即下一步的"输入前置条件"
  const nextStep = state.current + 1;

  // Step 1 → 2：需要已选择回路 + 时间范围（Step 1 本地也有双校验，父级兜底）
  if (nextStep === 2) {
    if (!state.config.loopId) {
      message.warning('请先选择回路，再进入下一步');
      return;
    }
    if (!state.config.startTime || !state.config.endTime) {
      message.warning('请先设置时间范围，再进入下一步');
      return;
    }
  }

  // Step 2 → 3：需要 KPI 评估任务成功并拿到结果（status=SUCCESS + results 非空）
  if (nextStep === 3) {
    if (!state.kpi.taskId) {
      message.warning('请先点击"开始 KPI 评估"触发评估任务，再进入下一步');
      return;
    }
    const running =
      state.kpi.status === 'PENDING' || state.kpi.status === 'RUNNING';
    const failed =
      state.kpi.status === 'FAILED' || state.kpi.status === 'CANCELLED';
    if (running) {
      message.warning('KPI 评估仍在进行中，请等待任务完成后再进入下一步');
      return;
    }
    if (failed) {
      message.warning(
        `KPI 评估已${state.kpi.status === 'CANCELLED' ? '取消' : '失败'}，请重新触发评估后再进入下一步`,
      );
      return;
    }
    if (state.kpi.status !== 'SUCCESS' || state.kpi.results.length === 0) {
      message.warning('KPI 评估尚未产出完整结果，请等待评估完成后再进入下一步');
      return;
    }
  }

  // Step 3 → 4：需要诊断任务成功并拿到诊断详情
  if (nextStep === 4) {
    if (!state.diag.taskId) {
      message.warning('请先点击"开始诊断分析"触发诊断任务，再进入下一步');
      return;
    }
    const running =
      state.diag.status === 'PENDING' || state.diag.status === 'RUNNING';
    const failed =
      state.diag.status === 'FAILED' || state.diag.status === 'CANCELLED';
    if (running) {
      message.warning('诊断分析仍在进行中，请等待任务完成后再进入下一步');
      return;
    }
    if (failed) {
      message.warning(
        `诊断任务已${state.diag.status === 'CANCELLED' ? '取消' : '失败'}，请重新触发诊断后再进入下一步`,
      );
      return;
    }
    if (state.diag.status !== 'SUCCESS' || !state.diag.detail) {
      message.warning('诊断尚未产出完整结果，请等待诊断完成后再进入下一步');
      return;
    }
  }

  // 所有校验通过 → 推进到下一步
  state.current = nextStep;
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

/** P3-01：当前步骤子组件 ref，替代 reloadKey 强制重建 */
interface StepRef {
  refresh?: () => Promise<void> | void;
}

const stepRef = ref<null | StepRef>(null);

/** P3-01：工具栏刷新：调用当前步骤子组件 refresh() 方法 */
async function handleRefresh() {
  loading.value = true;
  try {
    await stepRef.value?.refresh?.();
  } finally {
    loading.value = false;
  }
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

    <!-- P3-01：用 ref 绑定替代 :key="reloadKey" 强制重建 -->
    <keep-alive class="mt-4">
      <component
        :is="currentStepComponent"
        ref="stepRef"
        :state="state"
        @next="handleNext"
      />
    </keep-alive>
  </Page>
</template>
