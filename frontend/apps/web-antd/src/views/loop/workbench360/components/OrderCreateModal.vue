<!--
  创建处置项弹窗（workbench360 P4；v3 §6.3/§6.4 创建处置项弹窗，原型 md-order）

  页内弹窗零跳转（红线②：整定 confirm 步不跳旧处置页；处置剖面动作条同款）。
  提交 POST /handling/orders（source=MANUAL；整定来源在 actionDetail 携带
  tuningRecordId/推荐参数，与处置详情 actionDetail 结构对齐）。
  诊断建议来源不在此弹窗 —— 走处置剖面建议表「通过→转工单」（convert 链路，
  建议须先审核通过，语义更诚实；移交文件已记录裁决）。
-->
<script setup lang="ts">
import type { Dayjs } from 'dayjs';

import type { TuningApi } from '#/api/tuning';

import { computed, reactive, ref, watch } from 'vue';

import {
  DatePicker,
  Input,
  message,
  Modal,
  Select,
} from 'ant-design-vue';

import { createOrderApi } from '#/api/handling';
import { ACTION_TYPE_OPTIONS } from '#/views/handling/constants';

const props = defineProps<{
  /** 当前回路 ID（提交目标） */
  loopId: null | string;
  /** 当前回路位号（标题） */
  loopTagName: null | string;
  /** 是否有已保存整定方案（有 →「整定方案确认」来源可选） */
  open: boolean;
  tuning?: null | {
    algorithm: string;
    currentPid: null | TuningApi.PidParams;
    loopId: string;
    recommendedPid: TuningApi.PidParams;
    tuningRecordId: string;
  };
}>();

const emit = defineEmits<{
  (e: 'created', orderNo: string): void;
  (e: 'update:open', v: boolean): void;
}>();

type SourceMode = 'manual' | 'tuning';

const form = reactive({
  actionType: 'TUNING' as string,
  handler: '',
  plannedAt: undefined as Dayjs | undefined,
  sourceMode: 'tuning' as SourceMode,
  title: '',
});

const submitting = ref(false);

/** 整定来源可用性（须已保存整定方案） */
const tuningReady = computed(
  () =>
    !!props.tuning &&
    !!props.tuning.tuningRecordId &&
    props.tuning.loopId === props.loopId,
);

const sourceOptions = computed(() => {
  const opts: Array<{ disabled?: boolean; label: string; value: SourceMode }> = [
    {
      disabled: !tuningReady.value,
      label: tuningReady.value
        ? `整定方案确认（${props.tuning!.algorithm} 推荐）`
        : '整定方案确认（需先在④确认步保存方案）',
      value: 'tuning',
    },
    { label: '手工创建', value: 'manual' },
  ];
  return opts;
});

/** 推荐参数说明行（原型 note：建议参数 vs 当前参数） */
const pidNote = computed(() => {
  if (form.sourceMode !== 'tuning' || !tuningReady.value) return null;
  const t = props.tuning!;
  const fmt = (p: null | TuningApi.PidParams) =>
    p
      ? `P ${p.kp} / I ${p.ti}s / D ${p.td}`
      : '当前 PID 缺失（回路未绑定 P/I/D 位号）';
  return `建议参数：${fmt(t.recommendedPid)}（当前 ${fmt(t.currentPid)}）。授权人员在 DCS 侧人工实施并回填，平台不直写参数。`;
});

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    form.sourceMode = tuningReady.value ? 'tuning' : 'manual';
    form.actionType = tuningReady.value ? 'TUNING' : 'OTHER';
    form.handler = '';
    form.plannedAt = undefined;
    form.title = tuningReady.value
      ? `${props.loopTagName ?? ''} 参数整定实施（${props.tuning!.algorithm} 推荐值）`
      : '';
  },
);

function onSourceChange(v: unknown) {
  const mode = v as SourceMode;
  form.sourceMode = mode;
  form.actionType = mode === 'tuning' ? 'TUNING' : 'OTHER';
  form.title =
    mode === 'tuning' && tuningReady.value
      ? `${props.loopTagName ?? ''} 参数整定实施（${props.tuning!.algorithm} 推荐值）`
      : '';
}

async function submit() {
  if (!props.loopId) {
    message.warning('尚未选中回路');
    return;
  }
  if (form.sourceMode === 'tuning' && !tuningReady.value) {
    message.warning('整定方案尚未保存，请先在④确认步保存方案');
    return;
  }
  submitting.value = true;
  try {
    const actionDetail: Record<string, any> = {};
    if (form.sourceMode === 'tuning' && props.tuning) {
      actionDetail.tuningRecordId = props.tuning.tuningRecordId;
      actionDetail.algorithm = props.tuning.algorithm;
      actionDetail.recommendedPid = { ...props.tuning.recommendedPid };
      if (props.tuning.currentPid)
        actionDetail.currentPid = { ...props.tuning.currentPid };
    }
    const order = await createOrderApi({
      loopId: props.loopId,
      actionType: form.actionType as never,
      title: form.title.trim() || undefined,
      plannedAt: form.plannedAt?.toISOString(),
      handler: form.handler.trim() || undefined,
      actionDetail,
      // 2026-10-04 P0：显式顶层字段（后端写 handling_order.tuning_record_id，
      // submit→APPLIED / verify→VERIFIED|ROLLED_BACK 状态推进依赖此关联）
      tuningRecordId:
        form.sourceMode === 'tuning' && props.tuning
          ? props.tuning.tuningRecordId
          : undefined,
    });
    message.success(`处置工单已创建：${order.orderNo}（待授权人员实施）`);
    emit('created', order.orderNo);
    emit('update:open', false);
  } catch (error: any) {
    message.error(error?.message ?? '创建处置项失败');
  } finally {
    submitting.value = false;
  }
}

defineExpose({
  /** 整定来源可用（供父级决定「创建处置项」按钮是否可点） */
  isTuningReady: () => tuningReady.value,
});
</script>

<template>
  <Modal
    :confirm-loading="submitting"
    :open="open"
    :title="`创建处置项 · ${loopTagName ?? '未选中回路'}`"
    ok-text="创建工单"
    cancel-text="取消"
    width="520px"
    @cancel="emit('update:open', false)"
    @ok="submit"
  >
    <div class="order-form">
      <div class="frow">
        <label>来源</label>
        <Select
          :options="sourceOptions"
          :value="form.sourceMode"
          size="small"
          style="width: 260px"
          @change="onSourceChange"
        />
      </div>
      <div class="frow">
        <label>类型</label>
        <Select
          v-model:value="form.actionType"
          :disabled="form.sourceMode === 'tuning'"
          :options="ACTION_TYPE_OPTIONS"
          size="small"
          style="width: 260px"
        />
      </div>
      <div class="frow">
        <label>标题</label>
        <Input
          v-model:value="form.title"
          placeholder="工单标题（可留空由系统生成）"
          size="small"
          style="flex: 1"
        />
      </div>
      <div class="frow">
        <label>执行人</label>
        <Input
          v-model:value="form.handler"
          placeholder="授权实施人员（可留空）"
          size="small"
          style="width: 260px"
        />
      </div>
      <div class="frow">
        <label>计划实施</label>
        <DatePicker
          v-model:value="form.plannedAt"
          size="small"
          style="width: 260px"
        />
      </div>
      <div v-if="pidNote" class="note">{{ pidNote }}</div>
      <div v-else class="note"
        >工单由授权人员在 DCS 侧人工实施并在处置剖面回填闭环，平台不直写参数。</div
      >
    </div>
  </Modal>
</template>

<style scoped>
.order-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 4px;
}

.frow {
  display: flex;
  gap: 10px;
  align-items: center;
}

.frow label {
  flex: none;
  width: 60px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.note {
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--accent) / 40%);
  border-radius: 4px;
}
</style>
