<script lang="ts" setup>
/**
 * 整定流程 Stepper 容器（V62-P1-019）
 *
 * 将 model→algorithm→simulation 三页合并为可恢复步骤流：
 * - 顶部 Steps（navigation 模式）显示三步进度，点击可切换（受门禁约束）
 * - 当前步骤由路由路径推导，与 store.currentStep 双向同步
 * - 子页面通过 <router-view> 渲染（三页各自保留 <Page>，本容器不套 Page 避免双重嵌套）
 * - 恢复策略：URL 带 taskId → 后端回显；否则 sessionStorage 恢复；皆无 → 新流程
 *
 * P1-021：LoopContextHeader 统一上下文头（回路/时间窗/返回来源）已接管占位，
 * 步骤 0（辨识）可编辑选择，步骤 1/2（整定/仿真）只读展示。
 */
import { computed, onMounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Steps, message } from 'ant-design-vue';

import { ClpmLoopContextHeader } from '#/components/clpm';
import { useTuningStore } from '#/store/tuning';

defineOptions({ name: 'TuningFlow' });

const StepsStep = Steps.Step;

const route = useRoute();
const router = useRouter();
const store = useTuningStore();

/** 三步对应的子路由路径 */
const STEP_PATHS = [
  '/tuning/flow/model',
  '/tuning/flow/algorithm',
  '/tuning/flow/simulation',
] as const;

/** 当前步骤由路由路径推导 */
const currentStep = computed<number>(() => {
  if (route.path.endsWith('/algorithm')) return 1;
  if (route.path.endsWith('/simulation')) return 2;
  return 0;
});

/** 步骤门禁：未辨识完成不能进整定算法 */
const canAccessAlgorithm = computed(
  () => !!store.identifyResult || currentStep.value >= 1,
);

/** 步骤门禁：未整定完成（无候选 PID）不能进仿真 */
const canAccessSimulation = computed(
  () => store.pidCandidates.length > 0 || currentStep.value >= 2,
);

/** Steps 切换（受门禁约束） */
function handleStepChange(current: number) {
  if (current === 1 && !canAccessAlgorithm.value) {
    message.warning('请先完成模型辨识');
    return;
  }
  if (current === 2 && !canAccessSimulation.value) {
    message.warning('请先完成整定算法，生成候选 PID');
    return;
  }
  router.push(STEP_PATHS[current]!);
}

/** 路由变化时同步 currentStep 到 store */
watch(currentStep, (step) => {
  if (store.currentStep !== step) {
    store.currentStep = step;
  }
});

onMounted(async () => {
  const taskId = route.query.taskId as string | undefined;
  if (taskId) {
    // 从 workbench「继续未完成任务」进入 → 后端 taskId 回显
    const ok = await store.restoreFromTask(taskId);
    if (!ok) {
      message.warning('任务回显失败，请重新选择任务或新建整定流程');
    }
  } else {
    // 同标签页刷新 → sessionStorage 恢复
    store.restoreFromSession();
  }
  // 同步当前步骤到 store
  store.currentStep = currentStep.value;
});
</script>

<template>
  <div class="tuning-flow">
    <!-- 流程步骤头 -->
    <div class="flow-header border-b bg-content px-4 py-3">
      <Steps
        :current="currentStep"
        type="navigation"
        size="small"
        @change="handleStepChange"
      >
        <StepsStep title="模型辨识" sub-title="辨识过程对象模型" />
        <StepsStep
          title="整定算法"
          sub-title="计算推荐 PID"
          :disabled="!canAccessAlgorithm"
        />
        <StepsStep
          title="闭环仿真"
          sub-title="对比响应性能"
          :disabled="!canAccessSimulation"
        />
      </Steps>

      <!-- P1-021：统一 Loop 上下文头（回路/时间窗/返回来源） -->
      <ClpmLoopContextHeader
        :editable="currentStep === 0"
        :show-time-window="currentStep === 0"
        back-to="/tuning/workbench"
        back-label="返回整定工作台"
      />
    </div>

    <!-- 子页面（model.vue / algorithm.vue / simulation.vue） -->
    <router-view />
  </div>
</template>

<style scoped>
.tuning-flow {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.flow-header {
  position: sticky;
  top: 0;
  z-index: 10;
}
</style>
