<!--
  效果验证抽屉（workbench360 P4-2；v3 §7 效果验证抽屉，原型 dr-verify）

  唤起源：整定剖面动作条「效果验证」/ 推荐方案卡 ↗（统一动线 D19）。
  数据：GET /tuning/verification/data（实时拉取不落库）——前后窗波形 + KPI
  before/after/Δ/判定 + afterTruncated（后窗数据截至当前时刻）标注全部复用
  共享组件 #/components/clpm/tuning-verify-compare（零分叉）。
  窗口 7 档（契约 §1.5：windowHours ∈ 1/2/4/8/24/72/168，禁编造其它档）。
  对比时点：调用方反查（最近 TUNING 工单 submittedAt / 整定记录 createdAt）
  带入，可改；无建议时点时显式默认当前时间并要求人工确认（对齐旧效果验证页
  2026-09-24 反查失败修复口径，禁静默错切时点）。
-->
<script setup lang="ts">
import { ref, watch } from 'vue';

import { Button, DatePicker, Select } from 'ant-design-vue';
import dayjs, { type Dayjs } from 'dayjs';
import utc from 'dayjs/plugin/utc';

import TuningVerifyCompare from '#/components/clpm/tuning-verify-compare.vue';
import { WB360_VERIFY_WINDOW_OPTIONS } from '#/constants/clpm-ui';
import { normalizeUtcTimestamp } from '#/utils/format';

import WbDrawer from './WbDrawer.vue';

const props = defineProps<{
  /** 回路 ID（当前选中） */
  loopId: null | string;
  /** 回路位号（标题） */
  loopTagName: null | string;
  /** 调用方反查的建议时点（naive-UTC ISO + 来源文案；null=无可信时点） */
  suggested: null | { pointTimeIso: string; sourceLabel: string };
}>();

dayjs.extend(utc);

const pointTime = ref<Dayjs | undefined>();
const pointTimeSource = ref('');
const windowHours = ref(24);

/** 已提交查询（驱动共享组件；null=未发起） */
const query = ref<null | {
  loopId: string;
  pointTime: string;
  windowHours: number;
}>(null);

const sourceIsFallback = ref(false);

const open = defineModel<boolean>('open', { default: false });

watch(open, (isOpen) => {
  if (!isOpen) return;
  if (query.value && query.value.loopId === props.loopId) return; // 保留会话内查询
  if (props.suggested?.pointTimeIso) {
    pointTime.value = dayjs(normalizeUtcTimestamp(props.suggested.pointTimeIso));
    pointTimeSource.value = props.suggested.sourceLabel;
    sourceIsFallback.value = false;
  } else {
    pointTime.value = dayjs();
    pointTimeSource.value = '无可信时点，已默认当前时间（请手动确认）';
    sourceIsFallback.value = true;
  }
});

// 回路切换：清空旧回路查询（禁跨回路残留数据）
watch(
  () => props.loopId,
  () => {
    query.value = null;
  },
);

function onPointChange() {
  pointTimeSource.value = '手动指定';
  sourceIsFallback.value = false;
}

function runCompare() {
  if (!props.loopId || !pointTime.value) return;
  query.value = {
    loopId: props.loopId,
    // 本地时间 → UTC ISO（Z 后缀，naive-UTC 口径；同旧效果验证页）
    pointTime: pointTime.value.utc().format('YYYY-MM-DDTHH:mm:ss[Z]'),
    windowHours: windowHours.value,
  };
}
</script>

<template>
  <WbDrawer
    :aria-label="`效果验证 ${loopTagName ?? ''}`"
    :open="open"
    :title="`效果验证 · ${loopTagName ?? ''}`"
    @close="open = false"
  >
    <div class="verify-ctrl">
      <DatePicker
        v-model:value="pointTime"
        format="YYYY-MM-DD HH:mm"
        placeholder="对比时点"
        size="small"
        @change="onPointChange"
      />
      <span
        class="src-note"
        :class="{ fallback: sourceIsFallback }"
      >
        时点来源：{{ pointTimeSource || '未指定' }}
      </span>
      <Select
        v-model:value="windowHours"
        :options="WB360_VERIFY_WINDOW_OPTIONS"
        size="small"
        style="width: 88px"
      />
      <Button
        :disabled="!loopId || !pointTime"
        size="small"
        type="primary"
        @click="runCompare"
      >
        对比
      </Button>
    </div>

    <TuningVerifyCompare
      v-if="query"
      :key="`${query.loopId}-${query.pointTime}-${query.windowHours}`"
      :loop-id="query.loopId"
      :point-time="query.pointTime"
      :window-hours="query.windowHours"
    />
    <div v-else class="empty-tip">
      选择对比时点与窗口后点击「对比」——时点=参数实施时刻，前后窗各
      {{ windowHours }}h 的波形与 KPI 将实时拉取（不落库）。
    </div>
  </WbDrawer>
</template>

<style scoped>
.verify-ctrl {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}

.src-note {
  font-size: 12px;
  color: hsl(var(--muted-foreground) / 80%);
}

.src-note.fallback {
  color: hsl(var(--warning));
}

.empty-tip {
  padding: 32px 0;
  font-size: 12px;
  line-height: 1.8;
  color: hsl(var(--muted-foreground));
  text-align: center;
}
</style>
