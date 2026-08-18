<script lang="ts" setup>
/**
 * 整定工作台 · 锚点① 过程辨识（09 设计方案 §4.1/§6.2）
 *
 * 双路径：历史数据自动辨识（默认，Celery 异步 + 细粒度进度）
 *        / 阶跃实验辨识（兜底，同步）。
 * 结果卡：模型类型/参数/拟合度/可信度徽标；D/E 级警示（下游置灰由 ctx 门禁驱动）。
 */
import type { TuningWorkbenchContext } from '../composables/use-tuning-workbench';

import { computed, ref } from 'vue';

import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Progress,
  RadioButton,
  RadioGroup,
  RangePicker,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

import { ClpmConfidenceBadge } from '#/components/clpm';

dayjs.extend(utc);

const props = defineProps<{ ctx: TuningWorkbenchContext }>();
const { ctx } = props;

const rangeValue = ref<[dayjs.Dayjs, dayjs.Dayjs] | undefined>([
  dayjs().subtract(7, 'day'),
  dayjs(),
]);

const MODEL_TYPE_LABEL: Record<string, string> = {
  FOPDT: '一阶滞后（FOPDT）',
  SOPDT: '二阶滞后（SOPDT）',
  IPDT: '积分对象（IPDT）',
};

const outcome = computed(() => ctx.outcome.value);
const isLowConfidence = computed(
  () => outcome.value?.confidenceLevel === 'D' || outcome.value?.confidenceLevel === 'E',
);

/** 徽标等级（ClpmConfidenceBadge 不含 INCONCLUSIVE，映射为 null 不显示） */
const badgeLevel = computed(() => {
  const lv = outcome.value?.confidenceLevel;
  return !lv || lv === 'INCONCLUSIVE' ? null : lv;
});

function handleRun() {
  if (!rangeValue.value) return;
  // 本地时间 → UTC ISO（Z 后缀，naive UTC 口径，同诊断模块）
  ctx.timeRange.value = [
    rangeValue.value[0].utc().format('YYYY-MM-DDTHH:mm:ss[Z]'),
    rangeValue.value[1].utc().format('YYYY-MM-DDTHH:mm:ss[Z]'),
  ];
  ctx.runIdentify();
}

const paramItems = computed(() => {
  const o = outcome.value;
  if (!o) return [];
  const items: { label: string; value: string }[] = [];
  const p = o.params;
  if (p.K != null) items.push({ label: '增益 K', value: String(p.K) });
  if (p.tau != null) items.push({ label: '时间常数 τ', value: `${p.tau} s` });
  if (p.T1 != null) items.push({ label: 'T1', value: `${p.T1} s` });
  if (p.T2 != null) items.push({ label: 'T2', value: `${p.T2} s` });
  if (p.theta != null) items.push({ label: '纯滞后 θ', value: `${p.theta} s` });
  return items;
});
</script>

<template>
  <Card id="tuning-anchor-identify" size="small" class="tuning-section">
    <template #title>
      <span class="section-title">① 过程辨识</span>
    </template>
    <div class="flex flex-wrap items-center gap-3">
      <RadioGroup v-model:value="ctx.identifyPath.value" size="small">
        <RadioButton value="HISTORY">历史数据辨识</RadioButton>
        <RadioButton value="STEP">阶跃实验辨识</RadioButton>
      </RadioGroup>
      <RangePicker
        v-model:value="rangeValue"
        show-time
        size="small"
        format="YYYY-MM-DD HH:mm"
        :placeholder="['开始时间', '结束时间']"
      />
      <Button
        type="primary"
        size="small"
        :loading="ctx.identifying.value"
        :disabled="!ctx.loopId.value || !rangeValue"
        @click="handleRun"
      >
        开始辨识
      </Button>
    </div>

    <!-- 异步进度（历史路径） -->
    <div v-if="ctx.identifying.value && ctx.identifyPath.value === 'HISTORY'" class="mt-3">
      <Progress :percent="Math.round(ctx.identifyProgress.value)" size="small" />
      <div class="mt-1 text-xs text-neutral-400">
        {{ ctx.identifyStage.value || '任务排队中…' }}
      </div>
    </div>

    <Alert
      v-if="ctx.identifyError.value"
      class="mt-3"
      type="error"
      :message="ctx.identifyError.value"
      show-icon
    />

    <!-- 辨识结果卡 -->
    <template v-if="outcome">
      <div class="mt-3 flex items-center gap-3">
        <Tag color="blue">{{ MODEL_TYPE_LABEL[outcome.modelType] ?? outcome.modelType }}</Tag>
        <span class="text-sm">拟合度 <b>{{ outcome.fittingScore.toFixed(1) }}%</b></span>
        <ClpmConfidenceBadge v-if="badgeLevel" :level="badgeLevel" />
        <span class="text-xs text-neutral-400">
          {{ outcome.dataSource === 'HISTORY' ? '历史数据' : '阶跃实验' }}
        </span>
      </div>
      <Alert
        v-if="isLowConfidence"
        class="mt-2"
        type="warning"
        message="可信度不足（D/E 级），辨识结果不建议用于整定；请更换时间窗或补充数据后重试"
        show-icon
      />
      <Descriptions class="mt-2" size="small" :column="4" bordered>
        <DescriptionsItem v-for="item in paramItems" :key="item.label" :label="item.label">
          {{ item.value }}
        </DescriptionsItem>
      </Descriptions>
    </template>
  </Card>
</template>

<style scoped>
.section-title {
  font-size: 13px;
  font-weight: 600;
}
</style>
