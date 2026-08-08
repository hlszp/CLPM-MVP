<script lang="ts" setup>
/**
 * LoopTrendModal 回路趋势弹窗（共享组件）
 *
 * 从回路实时监控页趋势弹窗提炼（monitor.vue），供回路工作台等页面复用：
 * - 时间窗切换（1h/2h/4h/8h/24h/72h）
 * - 当前值快照（PV/SP/OP + 读取时间）+ 当前控制方式
 * - 波形复用 WaveformChart（PV/SP 主轴 + OP 副轴 + MODE 背景带）
 *
 * 用法：
 * ```vue
 * <LoopTrendModal v-model:open="open" :loop-id="loopId" :tag-name="tagName" />
 * ```
 */
import type { LoopApi } from '#/api/loop';

import { computed, nextTick, ref, watch } from 'vue';

import { RadioGroup, Spin, Tag } from 'ant-design-vue';

import { getLoopMonitorDetailApi } from '#/api/loop';
import { ClpmModal, ClpmNumeric } from '#/components/clpm';
import { formatTime } from '#/utils/format';

import WaveformChart from './waveform-chart.vue';

defineOptions({ name: 'LoopTrendModal' });

const props = withDefaults(defineProps<Props>(), {
  loopId: null,
  tagName: '',
});

const emit = defineEmits<{
  'update:open': [value: boolean];
}>();

interface Props {
  /** 是否打开（v-model:open） */
  open: boolean;
  /** 回路 ID */
  loopId?: null | string;
  /** 回路位号（标题显示） */
  tagName?: string;
}

/** 趋势时间窗选项（与 monitor.vue 一致） */
const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '1h', value: 'last_1_hour' },
  { label: '2h', value: 'last_2_hours' },
  { label: '4h', value: 'last_4_hours' },
  { label: '8h', value: 'last_8_hours' },
  { label: '24h', value: 'last_24_hours' },
  { label: '72h', value: 'last_72_hours' },
];

const trendLoading = ref(false);
const trendDetail = ref<LoopApi.MonitorDetail | null>(null);
const trendWindow = ref<LoopApi.TrendWindow>('last_4_hours');
const waveformChartRef = ref<InstanceType<typeof WaveformChart>>();
const trendMaximized = ref(false);

const trendChartHeight = computed(() =>
  trendMaximized.value ? 'calc(100vh - 220px)' : '400px',
);

/** 控制方式标签色（对齐 UI/UX §7.3 MODE 语义） */
function modeColor(modeLabel: null | string | undefined): string {
  if (modeLabel === 'Auto') return 'green';
  if (modeLabel === 'Manual') return 'red';
  if (modeLabel === 'Cascade') return 'blue';
  return 'default';
}

/** 加载趋势详情 */
async function loadTrendDetail() {
  if (!props.loopId) return;
  trendLoading.value = true;
  try {
    trendDetail.value = await getLoopMonitorDetailApi(
      props.loopId,
      trendWindow.value,
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

function handleTrendWindowChange() {
  loadTrendDetail();
}

/** 最大化/还原时重置图表尺寸 */
function handleTrendMaximizeChange(maximized: boolean) {
  trendMaximized.value = maximized;
  nextTick(() => {
    setTimeout(() => waveformChartRef.value?.resize(), 100);
  });
}

/** 打开时加载；打开状态下切换回路时重载 */
watch(
  () => [props.open, props.loopId] as const,
  ([open, loopId]) => {
    if (open && loopId) {
      trendDetail.value = null;
      loadTrendDetail();
    }
  },
  { immediate: true },
);

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
        <!-- 时间范围 + 当前 MODE -->
        <div class="flex flex-wrap items-center justify-between gap-3">
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

        <!-- 趋势图（WaveformChart：PV/SP 主轴 + OP 副轴） -->
        <div v-if="trendDetail">
          <WaveformChart
            ref="waveformChartRef"
            :trend="trendDetail.trend"
            :height="trendChartHeight"
          />
        </div>
        <div v-else class="py-12 text-center text-gray-400">暂无趋势数据</div>
      </div>
    </Spin>
  </ClpmModal>
</template>
