<!--
  就地发起诊断弹窗（workbench360 P3，P3-1；v3 §6.2 / 原型 md-diag）

  复用原诊断工作台既有语义（该页已于 2026-10-04 并入本剖面，行为口径一致）：
  - 时间窗：24h（默认）/7d/30d 预设 → {preset}；自定义 → start 整点化 + end 截当前
    时刻（跨度 ≤31 天）→ {start,end}；
  - 算子：operatorGroup 恒 'full' + 细选白名单（全选=不传 operators，
    快速组/部分勾选=传 operators 名单），与旧页 submitDiagnosis 完全一致；
  - 预填：消费 use-diag-prefill 单一事实源（评估剖面「诊断此窗」带来的窗口，
    打开时初始化为自定义模式），不建第二套预填通道；
  - 门禁徽标：F5 预检（数据充足性）+ fitness 等级（L1/L2 警告放行文案；
    L0 在 DiagSection 已被阻断，本弹窗不可达）。
-->
<script setup lang="ts">
import type { Dayjs } from 'dayjs';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, ref, shallowRef, watch } from 'vue';

import {
  Alert,
  Button,
  Checkbox,
  CheckboxGroup,
  DatePicker,
  Form,
  FormItem,
  Modal,
  Radio,
  RadioGroup,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisOperatorsApi } from '#/api/diagnosis';
import { fitnessTagToLabel } from '#/constants/clpm-ui';
import DiagnosisPrecheckBadge from '#/views/diagnosis/components/precheck-badge.vue';

import { diagWithZone } from '../composables/use-diag-data';
import { useDiagPrefill } from '../composables/use-diag-prefill';

defineOptions({ name: 'Wb360DiagTriggerModal' });

const props = defineProps<{
  /** fitness 等级（monitor 清单行；null=待评估，按放行处理） */
  fitnessLevel: null | string;
  /** fitness 原因标签（中文映射经 fitnessTagToLabel 单源） */
  fitnessTags: string[];
  /** 选中回路位号（标题展示） */
  loopTagName: null | string;
  open: boolean;
  /** F5 预检徽标项（undefined=未取到/不可用，徽标不渲染） */
  precheckItem?: DiagnosisApi.PrecheckItem;
}>();

const emit = defineEmits<{
  (e: 'trigger', payload: TriggerPayload): void;
  (e: 'update:open', val: boolean): void;
}>();

/** 提交载荷（回路由剖面填入：单回路剖面，loopIds=[选中回路]） */
interface TriggerPayload {
  operators?: string[];
  timeWindow: { end?: string; preset?: string; start?: string };
}

const prefill = useDiagPrefill();

/* ── 时间窗（对齐旧页 TimeWindowKey/时间窗映射） ── */
type TimeWindowKey = '7d' | '24h' | '30d' | 'custom';
const TIME_WINDOW_MAP: Record<Exclude<TimeWindowKey, 'custom'>, string> = {
  '24h': 'last_24h',
  '30d': 'last_30d',
  '7d': 'last_7d',
};
const timeWindow = ref<TimeWindowKey>('24h');
const RangePicker = DatePicker.RangePicker;
const MAX_CUSTOM_DAYS = 31;
const customRange = ref<[Dayjs, Dayjs]>([
  dayjs().subtract(24, 'hour').startOf('hour'),
  dayjs().startOf('hour'),
]);

const customRangeValid = computed(() => {
  if (timeWindow.value !== 'custom') return true;
  const [s, e] = customRange.value ?? [];
  return Boolean(s && e && e.isAfter(s) && e.diff(s, 'day') <= MAX_CUSTOM_DAYS);
});

function onCustomRangeChange(val: unknown): void {
  customRange.value = val as [Dayjs, Dayjs];
}

/* ── 算子目录（懒加载；失败显式可重试，禁止静默置灰） ── */
const operatorCatalog = shallowRef<DiagnosisApi.OperatorInfo[]>([]);
const operatorLoading = ref(false);
const operatorLoadError = ref(false);
let operatorLoaded = false;

async function loadOperators(): Promise<void> {
  operatorLoadError.value = false;
  operatorLoading.value = true;
  try {
    operatorCatalog.value = await getDiagnosisOperatorsApi();
    operatorLoaded = true;
    // 默认全量勾选（对齐旧页 loadOperators → checkAllOperators）
    checkedOperators.value = operatorCatalog.value.map((o) => o.name);
  } catch {
    operatorCatalog.value = [];
    operatorLoadError.value = true;
  } finally {
    operatorLoading.value = false;
  }
}

const checkedOperators = ref<string[]>([]);
const fastGroupNames = computed(() =>
  operatorCatalog.value.filter((o) => o.fastGroup).map((o) => o.name),
);

function checkAllOperators(): void {
  checkedOperators.value = operatorCatalog.value.map((o) => o.name);
}

function checkFastGroup(): void {
  checkedOperators.value = [...fastGroupNames.value];
}

const allChecked = computed(
  () =>
    operatorCatalog.value.length > 0 &&
    checkedOperators.value.length === operatorCatalog.value.length,
);

/* ── fitness 门禁展示（行为口径同旧页：L0 阻断在外层；此处仅 L1/L2 警告） ── */
const fitnessNote = computed(() => {
  const lv = props.fitnessLevel;
  if (!lv) return null;
  const tags = props.fitnessTags.map((t) => fitnessTagToLabel(t)).join('、');
  if (lv === 'L1' || lv === 'L2') {
    return {
      text: `适用性 ${lv}（${tags || '条件异常'}）：允许发起，结论将附带条件警告`,
      type: 'warning' as const,
    };
  }
  if (lv === 'L3' || lv === 'L4') {
    return {
      text: `适用性 ${lv} 就绪${tags ? `（${tags}）` : ''}`,
      type: 'success' as const,
    };
  }
  return null;
});

/* ── 提交（body 组装与旧页 handleTrigger/submitDiagnosis 一致） ── */
const validationError = computed(() => {
  if (!customRangeValid.value)
    return `自定义时间范围无效：需起<止且跨度 ≤${MAX_CUSTOM_DAYS} 天`;
  if (operatorCatalog.value.length > 0 && checkedOperators.value.length === 0)
    return '请至少勾选一个诊断算子';
  return '';
});

function handleSubmit(): void {
  if (validationError.value) return;
  const timeWindowBody =
    timeWindow.value === 'custom'
      ? (() => {
          const [s, e] = customRange.value!;
          const end = e.isAfter(dayjs()) ? dayjs() : e;
          return {
            start: s.startOf('hour').toISOString(),
            end: end.toISOString(),
          };
        })()
      : {
          preset:
            TIME_WINDOW_MAP[timeWindow.value as Exclude<TimeWindowKey, 'custom'>],
        };
  emit('trigger', {
    timeWindow: timeWindowBody,
    // 全部勾选=全量（不传 operators）；部分勾选=细选白名单（旧页同口径）
    ...(allChecked.value ? {} : { operators: [...checkedOperators.value] }),
  });
  emit('update:open', false);
}

function handleClose(): void {
  emit('update:open', false);
}

/* ── 打开时初始化：默认 24h+全量；评估剖面预填 → 自定义窗 ── */
watch(
  () => props.open,
  (val) => {
    if (!val) return;
    timeWindow.value = '24h';
    customRange.value = [
      dayjs().subtract(24, 'hour').startOf('hour'),
      dayjs().startOf('hour'),
    ];
    // 消费 use-diag-prefill（单一事实源）：评估快照窗（UTC）→ 本地自定义窗
    const ps = diagWithZone(prefill.tsStart);
    const pe = diagWithZone(prefill.tsEnd);
    if (prefill.from === 'assess' && ps && pe) {
      const s = dayjs(ps);
      const e = dayjs(pe);
      if (e.isAfter(s)) {
        timeWindow.value = 'custom';
        customRange.value = [s, e];
      }
    }
    if (!operatorLoaded && !operatorLoading.value) loadOperators();
  },
);
</script>

<template>
  <Modal
    cancel-text="取消"
    :ok-button-props="{ disabled: !!validationError }"
    ok-text="发起诊断"
    :open="open"
    title="发起诊断"
    :width="560"
    @cancel="handleClose"
    @ok="handleSubmit"
  >
    <div class="mb-3 flex items-center gap-2 text-sm text-gray-500">
      <span>
        回路：<span class="font-medium text-gray-700">{{
          loopTagName ?? '—'
        }}</span>
      </span>
      <DiagnosisPrecheckBadge v-if="precheckItem" :item="precheckItem" />
      <span v-if="precheckItem" class="text-xs text-gray-400">
        预检：近 24h 快照 {{ precheckItem.rowCount }}/{{
          precheckItem.expectedRows
        }}
        行
      </span>
    </div>

    <Alert
      v-if="fitnessNote"
      :message="fitnessNote.text"
      show-icon
      :type="fitnessNote.type"
      class="mb-3"
    />

    <Alert
      v-if="prefill.from === 'assess' && timeWindow === 'custom'"
      class="mb-3"
      message="已带入评估剖面预填时间窗（UTC 快照口径转本地），可自行调整"
      show-icon
      type="info"
    />

    <Form layout="vertical" size="small">
      <FormItem label="时间窗">
        <RadioGroup v-model:value="timeWindow">
          <Radio value="24h">最近 24 小时</Radio>
          <Radio value="7d">最近 7 天</Radio>
          <Radio value="30d">最近 30 天</Radio>
          <Radio value="custom">自定义</Radio>
        </RadioGroup>
        <template v-if="timeWindow === 'custom'">
          <RangePicker
            :allow-clear="false"
            class="mt-1"
            :disabled-date="(d: Dayjs) => d.isAfter(dayjs(), 'day')"
            format="MM-DD HH:00"
            :show-time="{ format: 'HH', hideDisabledOptions: true }"
            style="width: 100%"
            :value="customRange"
            @change="onCustomRangeChange"
          />
          <div class="mt-1 text-xs text-gray-400">
            起点整点化、终点截当前时刻；跨度 ≤{{ MAX_CUSTOM_DAYS }} 天。
          </div>
        </template>
      </FormItem>

      <FormItem label="算子组">
        <div class="mb-1 flex items-center gap-2">
          <Button size="small" type="link" @click="checkAllOperators">
            全量（{{ operatorCatalog.length }} 算子）
          </Button>
          <Button size="small" type="link" @click="checkFastGroup">
            快速组（{{ fastGroupNames.length }} 算子）
          </Button>
          <span class="text-xs text-gray-400">
            已勾选 {{ checkedOperators.length }}/{{ operatorCatalog.length }}
          </span>
        </div>
        <Alert
          v-if="operatorLoadError"
          message="诊断算子目录加载失败，「发起诊断」暂不可用"
          show-icon
          type="warning"
        >
          <template #action>
            <Button danger size="small" @click="loadOperators">重试</Button>
          </template>
        </Alert>
        <div v-else-if="operatorLoading" class="text-xs text-gray-400">
          算子目录加载中…
        </div>
        <CheckboxGroup
          v-else
          v-model:value="checkedOperators"
          class="flex flex-wrap gap-x-4 gap-y-1"
        >
          <Checkbox
            v-for="o in operatorCatalog"
            :key="o.name"
            :title="`${o.description}｜置信口径：${o.confidenceBasis ?? '—'}`"
            :value="o.name"
          >
            {{ o.displayName }}{{ o.fastGroup ? '（快速）' : '' }}
          </Checkbox>
        </CheckboxGroup>
      </FormItem>

      <div
        v-if="validationError"
        class="rounded border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-600"
      >
        {{ validationError }}
      </div>

      <div class="mt-2 text-xs text-gray-400">
        诊断在后台异步执行，完成后结论就地呈现在本页诊断剖面；全程不离开回路工作台。
      </div>
    </Form>
  </Modal>
</template>
