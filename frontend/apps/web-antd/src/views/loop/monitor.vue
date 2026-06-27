<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { EchartsUIType } from '@vben/plugins/echarts';

/**
 * S2-LOOP-011 回路监控列表页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.15
 * - 沿用回路台账列表风格（筛选区 + Table + 分页）
 * - 筛选：装置/单元层级路径 + 回路类型 + 关键字
 * - Table 列：回路编号 / 名称 / 类型 / SP / PV / OP / MODE / 性能指数 / 操作
 * - 操作列：趋势 / 性能 / 详情
 * - 趋势 Modal：复用 WaveformChart 组件（与回路详情页风格统一）
 * - 性能 Modal：ECharts 仪表盘 + 6 大 KPI 卡片（含权重）
 * - 30 秒自动刷新（Switch 开关 + 倒计时）
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  RadioGroup,
  Select,
  Spin,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  getLoopDetailApi,
  getLoopMonitorDetailApi,
  getLoopMonitorListApi,
} from '#/api/loop';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopMonitor' });

const router = useRouter();

// ===== 常量 =====

/** 回路类型映射（label + color） */
const LOOP_TYPE_MAP: Record<string, { color: string; label: string }> = {
  TEMPERATURE: { label: '温度', color: 'red' },
  PRESSURE: { label: '压力', color: 'blue' },
  LEVEL: { label: '液位', color: 'green' },
  FLOW: { label: '流量', color: 'cyan' },
  ANALYSIS: { label: '分析', color: 'purple' },
  SPEED: { label: '速度', color: 'orange' },
  OTHER: { label: '其他', color: 'default' },
};

const loopTypeOptions = [
  { label: '全部', value: undefined },
  ...Object.entries(LOOP_TYPE_MAP).map(([value, { label }]) => ({
    label,
    value,
  })),
];

/** 趋势时间窗选项 */
const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '1h', value: 'last_1_hour' },
  { label: '2h', value: 'last_2_hours' },
  { label: '4h', value: 'last_4_hours' },
  { label: '8h', value: 'last_8_hours' },
  { label: '24h', value: 'last_24_hours' },
  { label: '72h', value: 'last_72_hours' },
];

/** KPI 状态映射 */
const kpiStatusMap: Record<string, { color: string; label: string }> = {
  SUCCESS: { color: 'green', label: '良好' },
  INCONCLUSIVE: { color: 'default', label: '未确定' },
  PARTIAL: { color: 'orange', label: '部分' },
};

/** 性能 Modal 中 KPI 结果是否为 INCONCLUSIVE */
const isPerfInconclusive = computed(
  () => perfDetail.value?.kpiSummary.status === 'INCONCLUSIVE',
);

/** 6 大 KPI 配置（含权重 key） */
const kpiItems: {
  desc: string;
  key: keyof LoopApi.KpiSummary;
  label: string;
  unit: string;
  weightKey?: keyof LoopApi.ScoreWeights;
}[] = [
  {
    desc: '自动模式率',
    key: 'auto_mode_rate',
    label: '自控率',
    unit: '%',
    weightKey: 'auto_mode_rate',
  },
  {
    desc: '有效自控率',
    key: 'effective_auto_rate',
    label: '有效自控率',
    unit: '%',
  },
  {
    desc: '稳定率',
    key: 'steady_rate',
    label: '平稳率',
    unit: '%',
    weightKey: 'steady_rate',
  },
  {
    desc: '准确度',
    key: 'accuracy_rate',
    label: '准确率',
    unit: '%',
    weightKey: 'accuracy_rate',
  },
  {
    desc: '快速率',
    key: 'fast_response_rate',
    label: '快速率',
    unit: '%',
    weightKey: 'fast_response_rate',
  },
  {
    desc: '振荡率',
    key: 'oscillation_rate',
    label: '振荡率',
    unit: '%',
    weightKey: 'oscillation_rate',
  },
  {
    desc: '饱和率',
    key: 'saturation_rate',
    label: '饱和率',
    unit: '%',
    weightKey: 'saturation_rate',
  },
  {
    desc: '优良值率',
    key: 'good_value_rate',
    label: '好值率',
    unit: '%',
  },
];

// ===== 列表状态 =====

const loading = ref(false);
const monitorList = ref<LoopApi.MonitorListItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  loopType: undefined as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 100,
});

const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

/** 工厂节点层级选项（显示完整路径：工厂A / 装置B / 单元C） */
const plantNodeOptions = computed(() => {
  const nodeMap = new Map<string, PlantNodeApi.PlantNode>();
  for (const node of plantNodes.value) {
    nodeMap.set(node.id, node);
  }
  return plantNodes.value.map((node) => {
    const path: string[] = [];
    let current: PlantNodeApi.PlantNode | undefined = node;
    while (current) {
      path.unshift(current.name);
      current = current.parentId ? nodeMap.get(current.parentId) : undefined;
    }
    return {
      label: path.join(' / '),
      value: node.id,
    };
  });
});

const columns: TableColumnsType = [
  { title: '回路编号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '名称',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 100 },
  { title: '设定值 SP', key: 'sp', width: 120 },
  { title: '测量值 PV', key: 'pv', width: 120 },
  { title: '输出值 OP', key: 'op', width: 120 },
  { title: '控制方式', key: 'mode', width: 110 },
  { title: '性能指数', dataIndex: 'score', key: 'score', width: 100 },
  { title: '操作', key: 'action', width: 200, fixed: 'right' },
];

// ===== 自动刷新 =====

const autoRefresh = ref(true);
const refreshInterval = 30; // seconds
const countdown = ref(refreshInterval);
let refreshTimer: null | ReturnType<typeof setInterval> = null;
let countdownTimer: null | ReturnType<typeof setInterval> = null;

// ===== 趋势 Modal =====

const trendModalVisible = ref(false);
const trendLoading = ref(false);
const trendDetail = ref<LoopApi.MonitorDetail | null>(null);
const trendWindow = ref<LoopApi.TrendWindow>('last_4_hours');
const waveformChartRef = ref<InstanceType<typeof WaveformChart>>();
const trendFullscreen = ref(false);

const trendModalWidth = computed(() =>
  trendFullscreen.value ? '100vw' : '1100px',
);
const trendChartHeight = computed(() =>
  trendFullscreen.value ? 'calc(100vh - 220px)' : '400px',
);
const trendBodyStyle = computed(() =>
  trendFullscreen.value
    ? { height: 'calc(100vh - 55px)', overflow: 'auto', padding: '16px' }
    : { maxHeight: 'calc(100vh - 120px)', overflow: 'auto' },
);

function toggleTrendFullscreen() {
  trendFullscreen.value = !trendFullscreen.value;
  nextTick(() => {
    setTimeout(() => waveformChartRef.value?.resize(), 100);
  });
}

// ===== 性能 Modal =====

const perfModalVisible = ref(false);
const perfLoading = ref(false);
const perfDetail = ref<LoopApi.MonitorDetail | null>(null);
const perfWindow = ref<LoopApi.TrendWindow>('last_24_hours');
const loopDetailForWeights = ref<LoopApi.LoopDetail | null>(null);
const gaugeChartRef = ref<EchartsUIType>();
const { renderEcharts: renderGaugeChart } = useEcharts(gaugeChartRef);

// ===== 当前操作的回路 =====

const currentRecord = ref<LoopApi.MonitorListItem | null>(null);

// ===== 工具函数 =====

/** MODE 颜色映射：Auto=绿 / Manual=橙 / Cascade=蓝 */
function modeColor(modeLabel: string): string {
  if (modeLabel === 'Auto') return 'green';
  if (modeLabel === 'Manual') return 'orange';
  if (modeLabel === 'Cascade') return 'blue';
  return 'default';
}

/** MODE 中文标签映射：0=Manual, 1=Auto, 2=Cascade */
function modeText(record: LoopApi.MonitorListItem): string {
  const label = record.currentValues?.modeLabel;
  if (label) return label;
  const mode = record.currentValues?.mode;
  if (mode === 0) return 'Manual';
  if (mode === 1) return 'Auto';
  if (mode === 2) return 'Cascade';
  return '—';
}

/** OP 值格式化，带 % 后缀 */
function formatOp(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${val.toFixed(2)}%`;
}

/** 数值 + 单位格式化 */
function formatValueWithUnit(
  val: null | number | undefined,
  unit?: string,
  digits = 2,
): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  const formatted = val.toFixed(digits);
  return unit ? `${formatted} ${unit}` : formatted;
}

function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

// ===== 数据加载 =====

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载监控列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getLoopMonitorListApi({
      plantNodeId: query.plantNodeId,
      loopType: query.loopType as LoopApi.LoopType | undefined,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    monitorList.value = data.items;
    total.value = data.total;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 100;
  loadList();
}

// ===== 趋势 Modal =====

/** 打开趋势 Modal */
async function openTrend(record: LoopApi.MonitorListItem) {
  currentRecord.value = record;
  trendModalVisible.value = true;
  trendWindow.value = 'last_4_hours';
  trendDetail.value = null;
  await loadTrendDetail();
}

/** 加载趋势详情 */
async function loadTrendDetail() {
  if (!currentRecord.value) return;
  trendLoading.value = true;
  try {
    trendDetail.value = await getLoopMonitorDetailApi(
      currentRecord.value.loopId,
      trendWindow.value,
    );
    // WaveformChart 组件内置 watch(trend) 自动渲染，只需在 DOM 更新后触发 resize 修正尺寸
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

// ===== 性能 Modal =====

/** 打开性能 Modal */
async function openPerformance(record: LoopApi.MonitorListItem) {
  currentRecord.value = record;
  perfModalVisible.value = true;
  perfWindow.value = 'last_24_hours';
  perfDetail.value = null;
  loopDetailForWeights.value = null;
  await loadPerfDetail();
}

/** 加载性能详情 */
async function loadPerfDetail() {
  if (!currentRecord.value) return;
  perfLoading.value = true;
  try {
    const [detail, loopDetail] = await Promise.all([
      getLoopMonitorDetailApi(currentRecord.value.loopId, perfWindow.value),
      getLoopDetailApi(currentRecord.value.loopId),
    ]);
    perfDetail.value = detail;
    loopDetailForWeights.value = loopDetail;
    await nextTick();
    renderGauge();
  } catch {
    // 错误已由拦截器处理
  } finally {
    perfLoading.value = false;
  }
}

/** 渲染仪表盘 */
function renderGauge() {
  const score = perfDetail.value?.kpiSummary.composite_score;
  if (score === null || score === undefined) return;

  renderGaugeChart({
    series: [
      {
        axisLine: {
          lineStyle: {
            color: [
              [0.6, '#ff4d4f'],
              [0.8, '#faad14'],
              [1, '#52c41a'],
            ],
            width: 18,
          },
        },
        axisTick: { show: false },
        data: [{ name: '综合性能指数', value: score }],
        detail: {
          fontSize: 28,
          formatter: '{value}',
          offsetCenter: [0, '50%'],
        },
        max: 100,
        min: 0,
        pointer: { itemStyle: { color: 'auto' } },
        progress: { show: true, width: 18 },
        splitLine: { length: 18 },
        title: { fontSize: 14, offsetCenter: [0, '80%'] },
        type: 'gauge',
      },
    ],
  });
}

function handlePerfWindowChange() {
  loadPerfDetail();
}

// ===== 详情跳转 =====

function viewDetail(record: LoopApi.MonitorListItem) {
  router.push(`/loop/detail/${record.loopId}`);
}

// ===== 自动刷新 =====

function startAutoRefresh() {
  stopAutoRefresh();
  if (autoRefresh.value) {
    countdown.value = refreshInterval;
    refreshTimer = setInterval(() => {
      loadList();
      countdown.value = refreshInterval;
    }, refreshInterval * 1000);
    countdownTimer = setInterval(() => {
      if (countdown.value > 0) countdown.value -= 1;
    }, 1000);
  }
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function handleToggleAutoRefresh(val: any) {
  autoRefresh.value = !!val;
  if (autoRefresh.value) {
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
}

// ===== 生命周期 =====

onMounted(() => {
  loadPlantNodes();
  loadList();
  startAutoRefresh();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <Page title="回路监控">
    <Card>
      <!-- 筛选区 -->
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap items-center gap-3">
          <Select
            v-model:value="query.plantNodeId"
            placeholder="按装置/单元筛选"
            style="width: 260px"
            allow-clear
            show-search
            :options="plantNodeOptions"
            :filter-option="
              (input: string, option: any) => option.label.includes(input)
            "
            @change="handleSearch"
          />
          <Select
            v-model:value="query.loopType"
            placeholder="按回路类型筛选"
            style="width: 160px"
            allow-clear
            :options="loopTypeOptions"
            @change="handleSearch"
          />
          <Input
            v-model:value="query.keyword"
            placeholder="搜索位号/描述"
            allow-clear
            style="width: 240px"
            @press-enter="handleSearch"
          />
          <Button type="primary" @click="handleSearch">查询</Button>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">
            自动刷新（{{ refreshInterval }}s）
          </span>
          <Switch :checked="autoRefresh" @change="handleToggleAutoRefresh" />
          <span
            v-if="autoRefresh"
            class="text-xs text-gray-400"
            style="min-width: 56px"
          >
            {{ countdown }}s 后刷新
          </span>
          <Button size="small" :loading="loading" @click="loadList">
            手动刷新
          </Button>
        </div>
      </div>

      <Table
        :columns="columns"
        :data-source="monitorList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: ['20', '50', '100'],
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: LoopApi.MonitorListItem) => record.loopId"
        :scroll="{ x: 1200 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'loopType'">
            <Tag
              :color="
                LOOP_TYPE_MAP[
                  (record as LoopApi.MonitorListItem).loopType ?? 'OTHER'
                ]?.color ?? 'default'
              "
              class="m-0"
            >
              {{
                LOOP_TYPE_MAP[
                  (record as LoopApi.MonitorListItem).loopType ?? 'OTHER'
                ]?.label ?? '其他'
              }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'sp'">
            {{
              formatValueWithUnit(
                (record as LoopApi.MonitorListItem).currentValues?.sp,
                (record as LoopApi.MonitorListItem).currentValues?.unit,
              )
            }}
          </template>
          <template v-else-if="column.key === 'pv'">
            <span class="font-medium text-blue-600">
              {{
                formatValueWithUnit(
                  (record as LoopApi.MonitorListItem).currentValues?.pv,
                  (record as LoopApi.MonitorListItem).currentValues?.unit,
                )
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'op'">
            {{
              formatOp((record as LoopApi.MonitorListItem).currentValues?.op)
            }}
          </template>
          <template v-else-if="column.key === 'mode'">
            <Tag
              v-if="
                (record as LoopApi.MonitorListItem).currentValues?.modeLabel ||
                (record as LoopApi.MonitorListItem).currentValues?.mode != null
              "
              :color="
                modeColor(
                  (record as LoopApi.MonitorListItem).currentValues?.modeLabel,
                )
              "
            >
              {{ modeText(record as LoopApi.MonitorListItem) }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'score'">
            <span
              v-if="(record as LoopApi.MonitorListItem).score != null"
              class="font-medium"
            >
              {{ (record as LoopApi.MonitorListItem).score?.toFixed(1) ?? '—' }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                type="link"
                size="small"
                @click="openTrend(record as LoopApi.MonitorListItem)"
              >
                趋势
              </Button>
              <Button
                type="link"
                size="small"
                @click="openPerformance(record as LoopApi.MonitorListItem)"
              >
                性能
              </Button>
              <Button
                type="link"
                size="small"
                @click="viewDetail(record as LoopApi.MonitorListItem)"
              >
                详情
              </Button>
            </div>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 趋势 Modal -->
    <Modal
      v-model:open="trendModalVisible"
      :width="trendModalWidth"
      :body-style="trendBodyStyle"
      :footer="null"
      destroy-on-close
      :style="trendFullscreen ? { top: 0, paddingBottom: 0 } : {}"
      @cancel="trendFullscreen = false"
    >
      <template #title>
        <div class="flex items-center justify-between pr-8">
          <span>趋势 - {{ currentRecord?.tagName ?? '' }}</span>
          <Button type="text" size="small" @click="toggleTrendFullscreen">
            {{ trendFullscreen ? '退出全屏' : '全屏' }}
          </Button>
        </div>
      </template>
      <Spin :spinning="trendLoading">
        <div v-if="currentRecord" class="space-y-3">
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
              <span class="ml-2 font-medium text-blue-600">
                {{
                  formatValueWithUnit(
                    trendDetail.currentValues.pv,
                    trendDetail.currentValues.unit,
                  )
                }}
              </span>
            </div>
            <div>
              <span class="text-xs text-gray-400">SP</span>
              <span class="ml-2 font-medium">
                {{
                  formatValueWithUnit(
                    trendDetail.currentValues.sp,
                    trendDetail.currentValues.unit,
                  )
                }}
              </span>
            </div>
            <div>
              <span class="text-xs text-gray-400">OP</span>
              <span class="ml-2 font-medium">
                {{ formatOp(trendDetail.currentValues.op) }}
              </span>
            </div>
            <div>
              <span class="text-xs text-gray-400">读取时间</span>
              <span class="ml-2 text-sm">
                {{ formatTime(trendDetail.currentValues.readAt) }}
              </span>
            </div>
          </div>

          <!-- 趋势图（复用 WaveformChart 组件，与回路详情页风格统一） -->
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
    </Modal>

    <!-- 性能 Modal -->
    <Modal
      v-model:open="perfModalVisible"
      :title="`性能 - ${currentRecord?.tagName ?? ''}`"
      width="900px"
      :footer="null"
      destroy-on-close
    >
      <Spin :spinning="perfLoading">
        <div v-if="perfDetail" class="space-y-4">
          <!-- 时间范围 -->
          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-500">时间范围：</span>
            <RadioGroup
              v-model:value="perfWindow"
              :options="trendWindowOptions"
              option-type="button"
              button-style="solid"
              size="small"
              @change="handlePerfWindowChange"
            />
          </div>

          <!-- INCONCLUSIVE 警告 -->
          <Alert
            v-if="isPerfInconclusive"
            class="mb-4"
            type="warning"
            show-icon
            message="该回路本期评估数据不足，结果不确定"
            description="有效数据率低于 20%，KPI 数值仅供参考，不参与评级与排行。"
          />

          <!-- 综合评分 + 仪表盘 -->
          <div
            class="flex items-center gap-6 rounded border p-4"
            :class="{ 'opacity-60': isPerfInconclusive }"
          >
            <div style="width: 240px; height: 240px">
              <EchartsUI
                v-if="perfDetail.kpiSummary.composite_score != null"
                ref="gaugeChartRef"
                height="240px"
              />
              <div
                v-else
                class="flex h-full items-center justify-center text-gray-400"
              >
                暂无评分
              </div>
            </div>
            <div class="flex-1">
              <div class="text-sm text-gray-500">
                综合性能指数（composite_score）
              </div>
              <div
                class="mt-1 text-3xl font-bold"
                :class="
                  isPerfInconclusive
                    ? 'text-gray-400'
                    : {
                        'text-green-600':
                          (perfDetail.kpiSummary.composite_score ?? 0) >= 80,
                        'text-orange-500':
                          (perfDetail.kpiSummary.composite_score ?? 0) >= 60 &&
                          (perfDetail.kpiSummary.composite_score ?? 0) < 80,
                        'text-red-500':
                          (perfDetail.kpiSummary.composite_score ?? 0) < 60,
                      }
                "
              >
                {{ perfDetail.kpiSummary.composite_score?.toFixed(1) ?? '—' }}
              </div>
              <div class="mt-2 flex items-center gap-2">
                <span class="text-xs text-gray-400">KPI 状态：</span>
                <Tag :color="kpiStatusMap[perfDetail.kpiSummary.status]?.color">
                  {{
                    kpiStatusMap[perfDetail.kpiSummary.status]?.label ||
                    perfDetail.kpiSummary.status
                  }}
                </Tag>
              </div>
              <div class="mt-1 text-xs text-gray-400">
                算法版本：{{ perfDetail.kpiSummary.algorithm_version }}
              </div>
              <div class="text-xs text-gray-400">
                计算时间：{{ formatTime(perfDetail.kpiSummary.calculatedAt) }}
              </div>
            </div>
          </div>

          <!-- 6 大 KPI 卡片（含权重） -->
          <div
            class="grid grid-cols-2 gap-3 md:grid-cols-3"
            :class="{ 'opacity-60': isPerfInconclusive }"
          >
            <div
              v-for="item in kpiItems"
              :key="item.key"
              class="rounded border p-3"
            >
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium">{{ item.label }}</span>
                <span class="text-xs text-gray-400">
                  权重：{{
                    item.weightKey
                      ? (loopDetailForWeights?.basicInfo.scoreWeights?.[
                          item.weightKey
                        ] ?? '—')
                      : '—'
                  }}%
                </span>
              </div>
              <div class="mt-1 text-xl font-medium">
                {{
                  (perfDetail.kpiSummary[item.key] as null | number)?.toFixed(
                    1,
                  ) ?? '—'
                }}{{ item.unit }}
              </div>
              <div class="mt-1 text-xs text-gray-400">{{ item.desc }}</div>
            </div>
          </div>
        </div>
        <div v-else class="py-12 text-center text-gray-400">暂无性能数据</div>
      </Spin>
    </Modal>
  </Page>
</template>
