<script lang="ts" setup>
import type { TuningApi } from '#/api/tuning';

/**
 * 工作台 · 参数整定弹窗（单页四区重构 v2 · 2026-08-07）
 *
 * 基于已有过程模型 G(s) 调用 tunePidApi（同步），输出推荐 PID。
 * 前置条件：回路已存在辨识模型（tuningLatest.modelParams 非空）。
 *
 * 用户选择整定算法（SIMC/IMC/LAMBDA/ZN/Cohen-Coon），系统以当前模型
 * + 当前 PID 计算推荐 PID，结果反写回整定行。
 */
import { computed, ref, watch } from 'vue';

import { Form, FormItem, Modal, Select } from 'ant-design-vue';

defineOptions({ name: 'TuneParamModal' });

const props = defineProps<{
  currentPid?: null | TuningApi.PidParams;
  loopTagName?: string;
  /** 辨识模型参数（无模型时禁用提交） */
  modelParams?: null | TuningApi.ModelParams;
  modelType?: null | TuningApi.ModelType;
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'tune', payload: { algorithm: TuningApi.Algorithm }): void;
  (e: 'update:open', val: boolean): void;
}>();

const ALGORITHM_OPTIONS: Array<{ label: string; value: TuningApi.Algorithm }> =
  [
    { label: 'SIMC（推荐）', value: 'SIMC' },
    { label: 'IMC 内模控制', value: 'IMC' },
    { label: 'Lambda', value: 'LAMBDA' },
    { label: 'Ziegler-Nichols', value: 'ZN' },
    { label: 'Cohen-Coon', value: 'COHEN_COON' },
  ];

const algorithm = ref<TuningApi.Algorithm>('SIMC');

const hasModel = computed(
  () => !!props.modelType && !!props.modelParams && props.modelParams.K != null,
);

const modelText = computed(() => {
  if (!hasModel.value) return '无可用模型';
  const p = props.modelParams!;
  const parts: string[] = [];
  if (p.K != null) parts.push(`K=${Number(p.K).toFixed(3)}`);
  if (p.tau != null) parts.push(`τ=${Number(p.tau).toFixed(1)}s`);
  if (p.theta != null) parts.push(`θ=${Number(p.theta).toFixed(1)}s`);
  return `${props.modelType} · ${parts.join(' / ')}`;
});

const currentPidText = computed(() => {
  const p = props.currentPid;
  if (!p) return '—';
  return `P=${p.kp}, Ti=${p.ti}s, Td=${p.td}s`;
});

function handleSubmit() {
  if (!hasModel.value) return;
  emit('tune', { algorithm: algorithm.value });
  emit('update:open', false);
}

function handleClose() {
  emit('update:open', false);
}

watch(
  () => props.open,
  (val) => {
    if (val) algorithm.value = 'SIMC';
  },
);
</script>

<template>
  <Modal
    :open="open"
    title="参数整定"
    :width="480"
    ok-text="开始整定"
    :ok-button-props="{ disabled: !hasModel }"
    cancel-text="取消"
    @ok="handleSubmit"
    @cancel="handleClose"
  >
    <div v-if="loopTagName" class="mb-3 text-sm text-gray-500">
      回路：<span class="font-medium text-gray-700">{{ loopTagName }}</span>
    </div>

    <Form layout="vertical" size="small">
      <FormItem label="过程模型">
        <div class="text-sm">{{ modelText }}</div>
      </FormItem>
      <FormItem label="当前 PID">
        <div class="text-sm">{{ currentPidText }}</div>
      </FormItem>
      <FormItem label="整定算法">
        <Select
          v-model:value="algorithm"
          :options="ALGORITHM_OPTIONS"
          style="width: 100%"
        />
      </FormItem>

      <div
        v-if="!hasModel"
        class="rounded border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-600"
      >
        暂无可用辨识模型，请先点击「回路辨识」生成过程模型。
      </div>
      <div v-else class="text-xs text-gray-400">
        系统将以当前模型 + 当前 PID 为输入，按所选算法计算推荐 PID 参数。
      </div>
    </Form>
  </Modal>
</template>
