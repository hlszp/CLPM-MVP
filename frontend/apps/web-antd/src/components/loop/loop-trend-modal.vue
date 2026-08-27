<script lang="ts" setup>
/**
 * LoopTrendModal 回路趋势弹窗（共享组件 v2）
 *
 * 从回路实时监控页趋势弹窗提炼（monitor.vue），供回路监视列表、回路工作台等页面复用：
 * - 历史/实时双模式切换
 *   - 历史：时间窗档位（10M/30M/1H/4H/8H/72H）+ 自定义起止时间（默认结束于当前时间，跨度 ≤30 天）
 *   - 实时：按当前时间窗加载种子数据后，每秒将 WS 推送的 PV/SP/OP/MODE 追加到趋势尾部
 * - 图例开关、X/Y 轴缩放（WaveformChart 内置 dataZoom inside+slider）
 * - 当前值快照（PV/SP/OP + 读取时间）+ 当前控制方式
 *
 * 用法：
 * ```vue
 * <LoopTrendModal v-model:open="open" :loop-id="loopId" :tag-name="tagName" />
 * ```
 */
import type { Dayjs } from 'dayjs';

import type { LoopApi } from '#/api/loop';
import type { RealtimeMessage } from '#/composables/use-loop-realtime';

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';

import { DatePicker, RadioGroup, Spin, Switch, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopMonitorDetailApi } from '#/api/loop';
import { ClpmModal, ClpmNumeric } from '#/components/clpm';
import {
  parseTagCode,
  useLoopRealtime,
} from '#/composables/use-loop-realtime';
import { formatTime } from '#/utils/format';
import { mapQualityToLabel } from '#/utils/quality-code';

import WaveformChart from './waveform-chart.vue';

defineOptions({ name: 'LoopTrendModal' });

const props = withDefaults(defineProps<Props>(), {
  loopId: null,
  tagName: '',
});

const emit = defineEmits<{
  'update:open': [value: boolean];
}>();

const RangePicker = DatePicker.RangePicker;

interface Props {
  /** 是否打开（v-model:open） */
  open: boolean;
  /** 回路 ID */
  loopId?: null | string;
  /** 回路位号（标题显示 + 实时消息 tagCode 匹配） */
  tagName?: string;
}

type ViewMode = 'history' | 'realtime';

/** 趋势时间窗选项（10M/30M/1H/4H/8H/72H） */
const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '10M', value: 'last_10_minutes' },
  { label: '30M', value: 'last_30_minutes' },
  { label: '1H', value: 'last_1_hour' },
  { label: '4H', value: 'last_4_hours' },
  { label: '8H', value: 'last_8_hours' },
  { label: '72H', value: 'last_72_hours' },
];

const viewModeOptions = [
  { label: '历史', value: 'history' },
  { label: '实时', value: 'realtime' },
];

/** 时间窗毫秒数（实时模式裁剪窗口用） */
const WINDOW_MS: Record<LoopApi.TrendWindow, number> = {
  last_10_minutes: 10 * 60 * 1000,
  last_30_minutes: 30 * 60 * 1000,
  last_1_hour: 3600 * 1000,
  last_2_hours: 2 * 3600 * 1000,
  last_4_hours: 4 * 3600 * 1000,
  last_8_hours: 8 * 3600 * 1000,
  last_24_hours: 24 * 3600 * 1000,
  last_72_hours: 72 * 3600 * 1000,
};

/** 实时模式最大保留点数（防止长时间挂窗内存/渲染膨胀） */
const LIVE_MAX_POINTS = 3600;
/** 自定义范围上限（对齐平台性能边界 30 天） */
const MAX_CUSTOM_DAYS = 30;

const viewMode = ref<ViewMode>('history');
const trendLoading = ref(false);
const trendDetail = ref<LoopApi.MonitorDetail | null>(null);
const trendWindow = ref<LoopApi.TrendWindow>('last_4_hours');
/** 自定义起止时间（null = 按时间窗档位，结束于当前时间） */
const customRange = ref<[Dayjs, Dayjs] | null>(null);
const showLegend = ref(true);
/** 实时追加渲染标记：true 时 WaveformChart 保留当前缩放状态 */
const appendMode = ref(false);
const waveformChartRef = ref<InstanceType<typeof WaveformChart>>();
const trendMaximized = ref(false);

const trendChartHeight = computed(() =>
  trendMaximized.value ? 'calc(100vh - 220px)' : '400px',
);

const customRangeValid = computed(() => {
  if (!customRange.value) return true;
  const [s, e] = customRange.value;
  return Boolean(s && e && e.isAfter(s) && e.diff(s, 'day') <= MAX_CUSTOM_DAYS);
});

/** 控制方式标签色（对齐 UI/UX §7.3 MODE 语义） */
function modeColor(modeLabel: null | string | undefined): string {
  if (modeLabel === 'Auto') return 'green';
  if (modeLabel === 'Manual') return 'red';
  if (modeLabel === 'Cascade') return 'blue';
  return 'default';
}

// ===== 实时推送（每秒向趋势图追加一个点） =====
const realtime = useLoopRealtime();
let liveUnsub: (() => void) | null = null;
let liveTimer: null | ReturnType<typeof setInterval> = null;
/** 各通道最近值（跨消息保持，SP/MODE 变化稀疏时维持阶梯延续） */
const lastLive: {
  mode: null | number;
  op: null | number;
  pv: null | number;
  pvQuality: LoopApi.Quality;
  sp: null | number;
} = { mode: null, op: null, pv: null, pvQuality: 'GOOD', sp: null };
/** 自上个追加点以来是否收到过本回路消息（无数据时追加断点而非假平线） */
let liveDirty = false;

const realtimeStatusColor = computed(() => {
  if (realtime.connectionStatus.value === 'online') return 'green';
  if (realtime.connectionStatus.value === 'reconnecting') return 'orange';
  return 'red';
});
const realtimeStatusText = computed(() => {
  if (realtime.connectionStatus.value === 'online') return '实时推送中';
  if (realtime.connectionStatus.value === 'reconnecting') return '重连中';
  return '离线';
});

/** 从种子历史尾部初始化各通道最近值，保证实时追加与历史曲线衔接 */
function seedLastLive() {
  const t = trendDetail.value?.trend;
  if (!t) return;
  const findLast = (arr: (null | number)[]): null | number => {
    for (let i = arr.length - 1; i >= 0; i--) {
      if (arr[i] != null) return arr[i]!;
    }
    return null;
  };
  lastLive.pv = findLast(t.pv);
  lastLive.sp = findLast(t.sp);
  lastLive.op = findLast(t.op);
  lastLive.mode = findLast(t.mode);
  for (let i = t.pvQuality.length - 1; i >= 0; i--) {
    if (t.pvQuality[i]) {
      lastLive.pvQuality = t.pvQuality[i]!;
      break;
    }
  }
}

/** WS 消息处理：匹配本回路 tagCode，更新各通道最近值 */
function handleRealtimeMessage(msg: RealtimeMessage) {
  const parsed = parseTagCode(msg.tagCode);
  if (!parsed || parsed.tagName !== props.tagName) return;
  const numValue = Number.parseFloat(msg.value);
  if (Number.isNaN(numValue)) return;
  switch (parsed.role) {
    case 'MODE': {
      lastLive.mode = numValue;
      break;
    }
    case 'OP': {
      lastLive.op = numValue;
      break;
    }
    case 'PV': {
      lastLive.pv = numValue;
      lastLive.pvQuality = mapQualityToLabel(msg.quality);
      break;
    }
    case 'SP': {
      lastLive.sp = numValue;
      break;
    }
    default: {
      return;
    }
  }
  liveDirty = true;
}

/** 每秒追加一个实时点到趋势尾部，并按时间窗/容量上限裁剪头部 */
function appendLivePoint() {
  const trend = trendDetail.value?.trend;
  if (!trend) return;
  appendMode.value = true;
  const ts = Date.now();
  trend.timestamps.push(ts);
  if (liveDirty) {
    trend.pv.push(lastLive.pv);
    trend.sp.push(lastLive.sp);
    trend.op.push(lastLive.op);
    trend.mode.push(lastLive.mode);
    trend.pvQuality.push(lastLive.pvQuality);
    liveDirty = false;
    // 同步当前值快照（null 表示该通道尚无数据，不覆盖 REST 权威值）
    const cv = trendDetail.value?.currentValues;
    if (cv) {
      if (lastLive.pv !== null) cv.pv = lastLive.pv;
      if (lastLive.sp !== null) cv.sp = lastLive.sp;
      if (lastLive.op !== null) cv.op = lastLive.op;
      if (lastLive.mode !== null) {
        cv.mode = lastLive.mode;
        // 与 use-loop-realtime 一致的安全默认映射，自定义映射以 REST 为权威
        if (lastLive.mode === 0) cv.modeLabel = 'Manual';
        else if (lastLive.mode >= 1) cv.modeLabel = 'Auto';
      }
      cv.readAt = new Date(ts).toISOString();
    }
  } else {
    // 无新数据：追加断点（不伪造平线）
    trend.pv.push(null);
    trend.sp.push(null);
    trend.op.push(null);
    trend.mode.push(null);
    trend.pvQuality.push(null);
  }
  // 按时间窗裁剪 + 容量上限裁剪
  const minTs = ts - (WINDOW_MS[trendWindow.value] ?? WINDOW_MS.last_4_hours);
  while (
    trend.timestamps.length > 0 &&
    (trend.timestamps[0]! < minTs || trend.timestamps.length > LIVE_MAX_POINTS)
  ) {
    trend.timestamps.shift();
    trend.pv.shift();
    trend.sp.shift();
    trend.op.shift();
    trend.mode.shift();
    trend.pvQuality.shift();
  }
}

function stopRealtime() {
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
  if (liveUnsub) {
    liveUnsub();
    liveUnsub = null;
  }
}

async function startRealtime() {
  stopRealtime();
  await loadTrendDetail();
  seedLastLive();
  liveDirty = false;
  realtime.start();
  liveUnsub = realtime.onMessage(handleRealtimeMessage);
  liveTimer = setInterval(appendLivePoint, 1000);
}

/** 加载趋势详情（历史模式：档位或自定义范围；实时模式：作为种子数据） */
async function loadTrendDetail() {
  if (!props.loopId) return;
  trendLoading.value = true;
  appendMode.value = false;
  try {
    const range =
      viewMode.value === 'history' &&
      customRange.value &&
      customRangeValid.value
        ? {
            tsStart: customRange.value[0].toISOString(),
            tsEnd: customRange.value[1].toISOString(),
          }
        : undefined;
    trendDetail.value = await getLoopMonitorDetailApi(
      props.loopId,
      trendWindow.value,
      range,
    );
    // WaveformChart 内置 watch(trend) 自动渲染，DOM 更新后 resize 修正尺寸
    await nextTick();
    waveformChartRef.value?.resize();
  } catch {
    // 错误已由拦截器处理
  } finally {
    trendLoading.value = false;
  }
}

/** 切换时间窗档位：清空自定义范围；实时模式下重载种子 */
function handleTrendWindowChange() {
  customRange.value = null;
  if (viewMode.value === 'realtime') {
    void startRealtime();
  } else {
    void loadTrendDetail();
  }
}

/** 自定义起止时间变更（antd 与 dayjs 双版本类型声明冲突，运行时同一实例） */
function onCustomRangeChange(val: unknown): void {
  customRange.value = val as [Dayjs, Dayjs] | null;
  if (customRangeValid.value) void loadTrendDetail();
}

/** 历史/实时模式切换 */
watch(viewMode, (mode) => {
  if (!props.open || !props.loopId) return;
  if (mode === 'realtime') {
    void startRealtime();
  } else {
    stopRealtime();
    void loadTrendDetail();
  }
});

/** 最大化/还原时重置图表尺寸 */
function handleTrendMaximizeChange(maximized: boolean) {
  trendMaximized.value = maximized;
  nextTick(() => {
    setTimeout(() => waveformChartRef.value?.resize(), 100);
  });
}

/** 打开时加载；打开状态下切换回路时重载；关闭时停止实时推送 */
watch(
  () => [props.open, props.loopId] as const,
  ([open, loopId]) => {
    if (open && loopId) {
      trendDetail.value = null;
      customRange.value = null;
      if (viewMode.value === 'realtime') {
        void startRealtime();
      } else {
        void loadTrendDetail();
      }
    } else if (!open) {
      stopRealtime();
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  stopRealtime();
});

function handleOpenChange(val: boolean) {
  emit('update:open', val);
}
</script>

<template>
  <ClpmModal
    :open="open"
    :title="`趋势 - ${tagName || ''}`"
    width="1100px"
    :footer="null"
    destroy-on-close
    @update:open="handleOpenChange"
    @maximize-change="handleTrendMaximizeChange"
  >
    <Spin :spinning="trendLoading">
      <div class="space-y-3">
        <!-- 模式 + 时间范围 + 图例 + 当前 MODE -->
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-wrap items-center gap-3">
            <RadioGroup
              v-model:value="viewMode"
              :options="viewModeOptions"
              option-type="button"
              button-style="solid"
              size="small"
            />
            <div class="flex items-center gap-2">
              <span class="text-sm text-gray-500">时间范围：</span>
              <RadioGroup
                v-model:value="trendWindow"
                :options="trendWindowOptions"
                option-type="button"
                button-style="solid"
                size="small"
                @change="handleTrendWindowChange"
              />
            </div>
            <template v-if="viewMode === 'history'">
              <RangePicker
                :value="customRange as any"
                :allow-clear="true"
                :disabled-date="(d: Dayjs) => d.isAfter(dayjs(), 'day')"
                format="MM-DD HH:mm"
                :placeholder="['开始时间', '结束时间']"
                :show-time="{ format: 'HH:mm' }"
                size="small"
                @change="onCustomRangeChange"
              />
              <span v-if="!customRangeValid" class="text-xs text-red-500">
                需起&lt;止且跨度 ≤30 天
              </span>
            </template>
          </div>
          <div class="flex items-center gap-3">
            <Tag v-if="viewMode === 'realtime'" :color="realtimeStatusColor">
              {{ realtimeStatusText }}
            </Tag>
            <div class="flex items-center gap-1">
              <span class="text-sm text-gray-500">图例</span>
              <Switch v-model:checked="showLegend" size="small" />
            </div>
            <div class="flex items-center gap-2">
              <span class="text-sm text-gray-500">当前控制方式：</span>
              <Tag
                v-if="trendDetail?.currentValues?.modeLabel"
                :color="modeColor(trendDetail.currentValues.modeLabel)"
              >
                {{ trendDetail.currentValues.modeLabel }}
              </Tag>
              <span v-else class="text-gray-400">—</span>
            </div>
          </div>
        </div>

        <!-- 当前值快照 -->
        <div
          v-if="trendDetail"
          class="flex flex-wrap items-center gap-4 rounded border p-3"
        >
          <div>
            <span class="text-xs text-gray-400">PV</span>
            <span
              v-if="trendDetail.currentValues.pv != null"
              class="ml-2 flex items-baseline gap-1"
            >
              <ClpmNumeric
                :value="trendDetail.currentValues.pv"
                :precision="2"
                mono
                size="sm"
                :weight="600"
              />
              <span class="text-xs text-gray-500">{{
                trendDetail.currentValues.unit
              }}</span>
            </span>
            <span v-else class="ml-2 text-gray-400">—</span>
          </div>
          <div>
            <span class="text-xs text-gray-400">SP</span>
            <span
              v-if="trendDetail.currentValues.sp != null"
              class="ml-2 flex items-baseline gap-1"
            >
              <ClpmNumeric
                :value="trendDetail.currentValues.sp"
                :precision="2"
                mono
                size="sm"
                :weight="600"
              />
              <span class="text-xs text-gray-500">{{
                trendDetail.currentValues.unit
              }}</span>
            </span>
            <span v-else class="ml-2 text-gray-400">—</span>
          </div>
          <div>
            <span class="text-xs text-gray-400">OP</span>
            <span
              v-if="trendDetail.currentValues.op != null"
              class="ml-2 flex items-baseline gap-0.5"
            >
              <ClpmNumeric
                :value="trendDetail.currentValues.op"
                :precision="2"
                mono
                size="sm"
                :weight="600"
              />
              <span class="text-xs text-gray-500">%</span>
            </span>
            <span v-else class="ml-2 text-gray-400">—</span>
          </div>
          <div>
            <span class="text-xs text-gray-400">读取时间</span>
            <span class="ml-2 text-sm">
              {{ formatTime(trendDetail.currentValues.readAt) }}
            </span>
          </div>
        </div>

        <!-- 趋势图（WaveformChart：PV/SP 主轴 + OP 副轴 + MODE 阶梯轴，X/Y 双轴缩放） -->
        <div v-if="trendDetail">
          <WaveformChart
            ref="waveformChartRef"
            :trend="trendDetail.trend"
            :height="trendChartHeight"
            :show-legend="showLegend"
            :preserve-zoom="appendMode"
          />
        </div>
        <div v-else class="py-12 text-center text-gray-400">暂无趋势数据</div>
      </div>
    </Spin>
  </ClpmModal>
</template>
