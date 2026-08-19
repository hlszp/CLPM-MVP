<script lang="ts" setup>
import type { TuningWorkbenchContext } from '../composables/use-tuning-workbench';

/**
 * 整定工作台 · 锚点① 过程辨识（09 设计方案 §4.1/§6.2）
 *
 * 双路径：历史数据自动辨识（默认，Celery 异步 + 细粒度进度）
 *        / 阶跃实验辨识（兜底，同步）。
 * 结果卡：模型类型/参数/拟合度/可信度徽标；D/E 级警示（下游置灰由 ctx 门禁驱动）。
 */
import type { TuningApi } from '#/api/tuning';

import { computed, reactive, ref, watch } from 'vue';

import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  InputNumber,
  Progress,
  RadioButton,
  RadioGroup,
  RangePicker,
  Select,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

import { ClpmConfidenceBadge } from '#/components/clpm';

import { fmtNum2 } from '../constants';

const props = defineProps<{ ctx: TuningWorkbenchContext }>();

dayjs.extend(utc);

const { ctx } = props;

const rangeValue = ref<[dayjs.Dayjs, dayjs.Dayjs] | undefined>([
  dayjs().subtract(7, 'day'),
  dayjs(),
]);

/** 路径切换默认窗口：历史=近 7 天；阶跃=近 6 小时（窄窗更易满足单阶跃前提） */
watch(
  () => ctx.identifyPath.value,
  (path) => {
    rangeValue.value =
      path === 'STEP'
        ? [dayjs().subtract(6, 'hour'), dayjs()]
        : [dayjs().subtract(7, 'day'), dayjs()];
  },
);

const MODEL_TYPE_LABEL: Record<string, string> = {
  FOPDT: '一阶滞后（FOPDT）',
  SOPDT: '二阶滞后（SOPDT）',
  IPDT: '积分对象（IPDT）',
};

const outcome = computed(() => ctx.outcome.value);
const isLowConfidence = computed(
  () =>
    outcome.value?.confidenceLevel === 'D' ||
    outcome.value?.confidenceLevel === 'E',
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
  if (p.K != null) items.push({ label: '增益 K', value: fmtNum2(p.K) });
  if (p.tau != null) items.push({ label: '时间常数 τ', value: `${fmtNum2(p.tau)} s` });
  if (p.T1 != null) items.push({ label: 'T1', value: `${fmtNum2(p.T1)} s` });
  if (p.T2 != null) items.push({ label: 'T2', value: `${fmtNum2(p.T2)} s` });
  if (p.theta != null) items.push({ label: '纯滞后 θ', value: `${fmtNum2(p.theta)} s` });
  return items;
});

// ===== 手动修改过程模型（辨识后可人工选择模型类型/调整参数） =====
const editing = ref(false);
const MODEL_TYPE_OPTIONS = [
  { label: '一阶滞后（FOPDT）', value: 'FOPDT' },
  { label: '二阶滞后（SOPDT）', value: 'SOPDT' },
  { label: '积分对象（IPDT）', value: 'IPDT' },
];
const editForm = reactive({
  modelType: 'FOPDT' as TuningApi.ModelType,
  K: 1,
  tau: 10,
  T1: 10,
  T2: 1,
  theta: 0,
});

function startEdit() {
  const o = outcome.value;
  if (!o) return;
  editForm.modelType = o.modelType;
  editForm.K = o.params.K ?? 1;
  editForm.tau = o.params.tau ?? 10;
  editForm.T1 = o.params.T1 ?? 10;
  editForm.T2 = o.params.T2 ?? 1;
  editForm.theta = o.params.theta ?? 0;
  editing.value = true;
}

async function applyEdit() {
  editing.value = false;
  const params =
    editForm.modelType === 'SOPDT'
      ? { K: editForm.K, T1: editForm.T1, T2: editForm.T2, theta: editForm.theta }
      : (editForm.modelType === 'IPDT'
        ? { K: editForm.K, theta: editForm.theta }
        : { K: editForm.K, tau: editForm.tau, theta: editForm.theta });
  await ctx.applyManualModel(editForm.modelType, params);
}
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

    <Alert
      v-if="ctx.identifyPath.value === 'STEP'"
      class="mt-3"
      type="info"
      message="阶跃辨识要求窗口内仅含一次显著 OP 阶跃"
      description="请选择阶跃实验前后的窄时间窗（如 10~30 分钟）；窗口内包含多次调节变化将被拒绝（ERR_TUNING_STEP_INVALID）。正常运行数据请改用「历史数据辨识」。"
      show-icon
    />

    <!-- 异步进度（历史路径） -->
    <div
      v-if="ctx.identifying.value && ctx.identifyPath.value === 'HISTORY'"
      class="mt-3"
    >
      <Progress
        :percent="Math.round(ctx.identifyProgress.value)"
        size="small"
      />
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
        <Tag color="blue">{{
          MODEL_TYPE_LABEL[outcome.modelType] ?? outcome.modelType
        }}</Tag>
        <span v-if="outcome.dataSource !== 'MANUAL'" class="text-sm"
          >拟合度 <b>{{ outcome.fittingScore.toFixed(1) }}%</b></span
        >
        <ClpmConfidenceBadge v-if="badgeLevel" :level="badgeLevel" />
        <span class="text-xs text-neutral-400">
          {{
            outcome.dataSource === 'HISTORY'
              ? '历史数据'
              : outcome.dataSource === 'STEP_EXPERIMENT'
                ? '阶跃实验'
                : '人工修改'
          }}
        </span>
        <Button
          size="small"
          class="ml-auto"
          :disabled="ctx.identifying.value"
          @click="startEdit"
        >
          手动修改模型
        </Button>
      </div>
      <Alert
        v-if="isLowConfidence"
        class="mt-2"
        type="warning"
        message="可信度不足（D/E 级），辨识结果不建议用于整定；请更换时间窗或补充数据后重试"
        show-icon
      />
      <Descriptions class="mt-2" size="small" :column="4" bordered>
        <DescriptionsItem
          v-for="item in paramItems"
          :key="item.label"
          :label="item.label"
        >
          {{ item.value }}
        </DescriptionsItem>
      </Descriptions>

      <!-- 手动修改模型编辑区 -->
      <div v-if="editing" class="mt-2 model-edit">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-xs font-medium text-neutral-500">模型类型</span>
          <Select
            v-model:value="editForm.modelType"
            :options="MODEL_TYPE_OPTIONS"
            size="small"
            style="width: 180px"
          />
          <span class="ml-2 text-xs font-medium text-neutral-500">增益 K</span>
          <InputNumber
            v-model:value="editForm.K"
            size="small"
            :precision="2"
            :min="0.01"
            :step="0.1"
            style="width: 96px"
          />
          <template v-if="editForm.modelType === 'FOPDT'">
            <span class="text-xs font-medium text-neutral-500">时间常数 τ (s)</span>
            <InputNumber
              v-model:value="editForm.tau"
              size="small"
              :precision="2"
              :min="0.1"
              :step="1"
              style="width: 96px"
            />
          </template>
          <template v-if="editForm.modelType === 'SOPDT'">
            <span class="text-xs font-medium text-neutral-500">T1 (s)</span>
            <InputNumber
              v-model:value="editForm.T1"
              size="small"
              :precision="2"
              :min="0.1"
              :step="1"
              style="width: 96px"
            />
            <span class="text-xs font-medium text-neutral-500">T2 (s)</span>
            <InputNumber
              v-model:value="editForm.T2"
              size="small"
              :precision="2"
              :min="0.1"
              :step="1"
              style="width: 96px"
            />
          </template>
          <span class="text-xs font-medium text-neutral-500">纯滞后 θ (s)</span>
          <InputNumber
            v-model:value="editForm.theta"
            size="small"
            :precision="2"
            :min="0"
            :step="1"
            style="width: 96px"
          />
          <Button
            type="primary"
            size="small"
            :disabled="editForm.K == null || (editForm.modelType === 'FOPDT' && editForm.tau == null)"
            @click="applyEdit"
          >
            应用
          </Button>
          <Button size="small" @click="editing = false">取消</Button>
        </div>
        <div class="mt-1 text-xs text-neutral-400">
          应用后将脱离辨识记录（模型来源=人工，需确认风险），矩阵与仿真按新模型重算
        </div>
      </div>
    </template>
  </Card>
</template>

<style scoped>
.section-title {
  font-size: 13px;
  font-weight: 600;
}

.model-edit {
  padding: 8px 10px;
  background: hsl(var(--accent));
  border: 1px dashed hsl(var(--border));
  border-radius: 4px;
}
</style>
