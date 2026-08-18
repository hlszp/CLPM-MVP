<script lang="ts" setup>
/**
 * 整定工作台 · 锚点④ 方案确认（09 设计方案 §4.4/§6.2）
 *
 * 从仿真组中选定 1 组最终方案 → 「保存方案」落 tuning_record（SIMULATED）
 * → 引导线下实施 + 「创建处置项」快捷入口（跳转处置，关联 tuning_record_id）。
 * 决策 #6：显式保存才落记录，未保存的中间结果离开页面即丢弃。
 */
import type { TuningWorkbenchContext } from '../composables/use-tuning-workbench';

import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { Alert, Button, Card, Radio, RadioGroup } from 'ant-design-vue';
import { message } from 'ant-design-vue';

const props = defineProps<{ ctx: TuningWorkbenchContext }>();
const { ctx } = props;
const router = useRouter();

/** 可选方案组（推荐组，不含当前 PID） */
const options = computed(() =>
  ctx.simCandidates.value.filter((c) => !c.isCurrent).map((c) => c.label),
);

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

function goHandling() {
  router.push({
    path: '/handling',
    query: {
      create: 'tuning',
      loopId: ctx.loopId.value,
      tuningRecordId: ctx.savedRecordId.value,
    },
  });
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
        message="方案已保存。请线下实施后在处置模块记录闭环（平台不直接下写 DCS 参数）"
      >
        <template #action>
          <Button size="small" type="link" @click="goHandling"
            >创建处置项 →</Button
          >
        </template>
      </Alert>
    </template>
  </Card>
</template>

<style scoped>
.section-title {
  font-size: 13px;
  font-weight: 600;
}
</style>
