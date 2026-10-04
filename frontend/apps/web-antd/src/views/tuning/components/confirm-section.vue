<script lang="ts" setup>
/**
 * 整定工作台 · 锚点④ 方案确认（09 设计方案 §4.4/§6.2）
 *
 * 从仿真组中选定 1 组最终方案 → 「保存方案」落 tuning_record（SIMULATED）
 * → 「创建处置项」页内弹窗（复用 workbench360 OrderCreateModal，零跳转且
 * 与回路工作台同一实现；工单创建时显式携带 tuningRecordId 建立关联——
 * 2026-10-04 P0：原 /handling?create=tuning 深链处置页从不消费参数，属断链）。
 * 决策 #6：显式保存才落记录，未保存的中间结果离开页面即丢弃。
 */
import type { TuningWorkbenchContext } from '../composables/use-tuning-workbench';

import { computed, ref } from 'vue';

import { Alert, Button, Card, Radio, RadioGroup } from 'ant-design-vue';
import { message } from 'ant-design-vue';

import OrderCreateModal from '#/views/loop/workbench360/components/OrderCreateModal.vue';

const props = defineProps<{ ctx: TuningWorkbenchContext }>();
const { ctx } = props;

/** 当前回路位号（保存成功提示/弹窗标题兜底用；三页式无 wb360 头部，直接用 loopId） */
const orderOpen = ref(false);

/** 可选方案组（推荐组，不含当前 PID） */
const options = computed(() =>
  ctx.simCandidates.value.filter((c) => !c.isCurrent).map((c) => c.label),
);

/** 选中的最终方案（供创建处置项弹窗携带推荐参数） */
const chosenPlan = computed(() => {
  const chosen = ctx.simCandidates.value.find(
    (c) => c.label === ctx.finalLabel.value && !c.isCurrent,
  );
  return chosen ?? null;
});

async function handleSave() {
  try {
    const id = await ctx.savePlan();
    if (id) {
      message.success('整定方案已保存（状态：已仿真）');
    }
  } catch (error: any) {
    message.error(error?.message || '保存失败');
  }
}
</script>

<template>
  <Card id="tuning-anchor-confirm" size="small" class="tuning-section">
    <template #title>
      <span class="section-title">④ 方案确认</span>
    </template>

    <Alert
      v-if="!ctx.simResult.value"
      type="info"
      message="完成③仿真对比后在此确认最终方案"
      show-icon
    />
    <template v-else>
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-xs text-neutral-500">最终方案</span>
        <RadioGroup v-model:value="ctx.finalLabel.value" size="small">
          <Radio v-for="label in options" :key="label" :value="label">{{
            label
          }}</Radio>
        </RadioGroup>
        <Button
          type="primary"
          size="small"
          :loading="ctx.saving.value"
          :disabled="!ctx.canConfirm.value || !!ctx.savedRecordId.value"
          @click="handleSave"
        >
          保存方案
        </Button>
      </div>

      <Alert
        v-if="ctx.savedRecordId.value"
        class="mt-3"
        type="success"
        show-icon
        message="方案已保存。请线下实施后在处置工单记录闭环（平台不直接下写 DCS 参数）"
      >
        <template #action>
          <Button size="small" type="link" @click="orderOpen = true"
            >创建处置项 →</Button
          >
        </template>
      </Alert>
    </template>

    <OrderCreateModal
      v-model:open="orderOpen"
      :loop-id="ctx.loopId.value || null"
      :loop-tag-name="ctx.loopId.value || null"
      :tuning="
        chosenPlan && ctx.savedRecordId.value
          ? {
              algorithm: chosenPlan.algorithm ?? 'IMC',
              currentPid: ctx.currentPid.value,
              loopId: ctx.loopId.value,
              recommendedPid: chosenPlan.pid,
              tuningRecordId: ctx.savedRecordId.value,
            }
          : null
      "
    />
  </Card>
</template>

<style scoped>
.section-title {
  font-size: 13px;
  font-weight: 600;
}
</style>
