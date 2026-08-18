<script lang="ts" setup>
/**
 * LoopFleetView — 批量回路表格视图（MW-P4-01 / MW-P4-03）
 *
 * 从旧 monitor.vue 抽取的列表、统计、列设置、密度、导出。
 * 页面壳、路由和全局工具栏不进入组件——由父页面提供。
 * 实时逻辑改用 useLoopRealtime（MW-P1-04）。
 *
 * MW-P4-03：筛选条件（装置/类型/关键词/只看关注项）统一从 useMonitorContext
 * 读取，不再维护内部筛选状态，与左侧导航共享同一 URL 真相源。
 * 筛选功能通过左侧装置树+回路列表区域实现，本面板不再重复显示。
 *
 * 对齐整改方案 §8 Phase 4。
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { LoopApi } from '#/api/loop';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from 'vue';

import { Button, Card, message, Switch, Table, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopMonitorListApi, getLoopTypeStatsApi } from '#/api/loop';
import { ClpmNumeric } from '#/components/clpm';
import DayDeltaBadge from '#/components/loop/day-delta-badge.vue';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import {
  LOOP_TYPE_LABEL_MAP,
  MODE_LABEL_MAP,
  useLoopPalettes,
} from '#/composables/use-loop-palettes';
import { useLoopRealtime } from '#/composables/use-loop-realtime';
import { useMonitorContext } from '#/composables/use-monitor-context';
import { useTableDensity } from '#/composables/use-table-density';

defineOptions({ name: 'LoopFleetView' });

withDefaults(
  defineProps<{
    /** 是否显示自动刷新开关 */
    showAutoRefresh?: boolean;
    /** 是否显示统计卡片区域 */
    showStats?: boolean;
  }>(),
  {
    showStats: true,
    showAutoRefresh: true,
  },
);

const emit = defineEmits<{
  (e: 'loopClick', loopId: string): void;
}>();

const { modeLabelColor } = useLoopPalettes();

// ===== 共享监控上下文（MW-P4-03）=====
// 筛选条件统一从 URL 读取，与 workspace 模式共享同一真相源
const monitorCtx = useMonitorContext();

// ===== 用户偏好 =====
const { preferences, updateColumns } = usePagePreference('loop-monitor');

// ===== 分页状态（仅分页为组件内部状态，筛选来自 monitorCtx）=====
const query = reactive({
  page: 1,
  pageSize: 20,
});

// ===== 数据状态 =====
const loading = ref(false);
const errorMessage = ref<null | string>(null);
const monitorList = ref<LoopApi.MonitorListItem[]>([]);
const total = ref(0);
const typeStats = ref<Record<string, number>>({});
/** 控制方式统计（后端基于 Redis 实时 MODE 值全量聚合） */
const modeStats = ref<Record<string, number>>({});
/** E-1 服务端聚合统计（全量不分页，五档计数/平均分/WORSENED/MODE分布） */
const aggregate = ref<LoopApi.MonitorAggregate | null>(null);

// ===== 表格列定义（对齐 02 回路列表标杆 v1.4：12 默认列 + 组态字段收起）=====
// 顺序：位号 / 描述 / 装置·单元 / 回路类型 / 回路等级 / 性能评分 / SP / PV / OP / MODE / 可信度 / 操作
// 量程/单位为组态字段，默认收起（buildDefaultColumnConfigs 中设 visible=false）
const columns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 150,
    align: 'left',
  },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    width: 180,
    ellipsis: true,
    align: 'left',
  },
  {
    title: '装置·单元',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 120,
    align: 'center',
  },
  {
    title: '回路类型',
    dataIndex: 'loopType',
    key: 'loopType',
    width: 100,
    align: 'center',
  },
  { title: '回路等级', key: 'grade', width: 80, align: 'center' },
  {
    title: '性能评分',
    dataIndex: 'score',
    key: 'score',
    width: 110,
    align: 'right',
  },
  { title: '设定值 SP', key: 'sp', width: 90, align: 'right' },
  { title: '测量值 PV', key: 'pv', width: 90, align: 'right' },
  { title: '输出值 OP(%)', key: 'op', width: 90, align: 'right' },
  { title: 'MODE', key: 'mode', width: 110, align: 'center' },
  { title: '可信度', key: 'dataHealth', width: 100, align: 'center' },
  { title: '测量量程', key: 'pvRange', width: 100, align: 'center' },
  { title: '单位', key: 'pvUnit', width: 55, align: 'center' },
];

function getColumnKey(col: any): string {
  if (col.key) return String(col.key);
  if (col.dataIndex) {
    return Array.isArray(col.dataIndex)
      ? String(col.dataIndex[0])
      : String(col.dataIndex);
  }
  return '';
}

function buildDefaultColumnConfigs(): ColumnConfig[] {
  // 组态字段（量程/单位）默认收起，进列配置可手动开启
  const hiddenKeys = new Set(['pvRange', 'pvUnit']);
  return columns.map((c: any, i: number) => ({
    key: getColumnKey(c),
    label: String(c.title ?? ''),
    visible: !hiddenKeys.has(getColumnKey(c)),
    order: i,
  }));
}

const columnConfigs = ref<ColumnConfig[]>(
  preferences.value.columns && preferences.value.columns.length > 0
    ? preferences.value.columns
    : buildDefaultColumnConfigs(),
);

const visibleColumns = computed<TableColumnsType>(() => {
  const configMap = new Map(
    columnConfigs.value.map((c, i) => [
      c.key,
      { visible: c.visible, order: i },
    ]),
  );
  return columns
    .filter((c: any) => {
      const cfg = configMap.get(getColumnKey(c));
      return cfg ? cfg.visible : true;
    })
    .toSorted((a: any, b: any) => {
      const aOrder = configMap.get(getColumnKey(a))?.order ?? 99;
      const bOrder = configMap.get(getColumnKey(b))?.order ?? 99;
      return aOrder - bOrder;
    });
});

function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
  updateColumns(cols);
}

// ===== 密度 =====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('loop-monitor');

// ===== 实时数据（MW-P1-04 useLoopRealtime）=====
const {
  applyMessage,
  connectionStatus: wsConnectionStatus,
  lastMessageAt,
  onMessage,
  start,
  startFallback,
  stop,
  stopFallback,
} = useLoopRealtime();

const autoRefresh = ref(true);
const isFallbackPolling = computed(
  () => wsConnectionStatus.value === 'offline',
);

// ===== 自动刷新状态 =====
const lastRefreshAt = ref<Date | null>(null);
const lastRefreshText = computed(() => {
  if (!lastRefreshAt.value) return '';
  const diff = dayjs().diff(lastRefreshAt.value, 'second');
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  return dayjs(lastRefreshAt.value).format('HH:mm:ss');
});

// ===== 统计卡片 =====
const totalLoops = computed(() =>
  Object.values(typeStats.value).reduce((sum, count) => sum + count, 0),
);

/** 当前回路类型（从 monitorCtx 读取，用于统计卡片高亮） */
const currentLoopType = computed(() => monitorCtx.loopType.value ?? '');

// ===== 回路等级配置（对齐 use-score-color GB/T 44693.2-2024 §6.3 默认阈值）=====
// 五档：优秀(≥90) / 良好(≥80) / 合格(≥60) / 警告(≥40) / 不合格(<40)
// tagColor 使用 Ant Design Tag 预设色：绿→蓝→金→橙→红 形成视觉渐变
type GradeKey = 'excellent' | 'fair' | 'good' | 'poor' | 'warning';

interface GradeStats {
  excellent: number;
  good: number;
  fair: number;
  warning: number;
  poor: number;
  none: number;
}

const GRADE_CONFIG: ReadonlyArray<{
  key: GradeKey;
  label: string;
  minScore: number;
  tagColor: string;
}> = [
  { key: 'excellent', label: '优秀', minScore: 90, tagColor: 'green' },
  { key: 'good', label: '良好', minScore: 80, tagColor: 'blue' },
  { key: 'fair', label: '合格', minScore: 60, tagColor: 'gold' },
  { key: 'warning', label: '警告', minScore: 40, tagColor: 'orange' },
  { key: 'poor', label: '不合格', minScore: 0, tagColor: 'red' },
];

/** E-1：优先使用服务端全量聚合（gradeCounts），降级为当前页前端计算 */
const gradeStats = computed<GradeStats>(() => {
  const stats: GradeStats = {
    excellent: 0,
    good: 0,
    fair: 0,
    warning: 0,
    poor: 0,
    none: 0,
  };
  // 优先服务端聚合
  const ag = aggregate.value;
  if (ag?.gradeCounts) {
    stats.excellent = ag.gradeCounts.EXCELLENT ?? 0;
    stats.good = ag.gradeCounts.GOOD ?? 0;
    stats.fair = ag.gradeCounts.FAIR ?? 0;
    stats.warning = ag.gradeCounts.WARNING ?? 0;
    stats.poor = ag.gradeCounts.POOR ?? 0;
    stats.none = ag.gradeCounts.INCONCLUSIVE ?? 0;
    return stats;
  }
  // 降级：当前页前端计算
  for (const item of monitorList.value) {
    const score = item.score;
    if (score == null || Number.isNaN(score)) {
      stats.none++;
      continue;
    }
    for (const cfg of GRADE_CONFIG) {
      if (score >= cfg.minScore) {
        stats[cfg.key]++;
        break;
      }
    }
  }
  return stats;
});

/** 综合性能（简单平均，筛选联动）：优先服务端聚合 */
const avgScore = computed<null | number>(() => {
  return aggregate.value?.avgScore ?? null;
});

/** 较昨日恶化数：优先服务端聚合 */
const worsenedCount = computed<number>(() => {
  return aggregate.value?.worsenedCount ?? 0;
});

// ===== 实时自控率 =====
// E-1 优先服务端 aggregate.autoControlRate（全量 MODE 分布聚合），降级为 modeStats 计算
const autoControlRate = computed(() => {
  if (aggregate.value?.autoControlRate != null) {
    return aggregate.value.autoControlRate;
  }
  const s = modeStats.value;
  const auto = (s['1'] ?? 0) + (s['2'] ?? 0) + (s['3'] ?? 0) + (s['4'] ?? 0);
  const denom = (s['0'] ?? 0) + auto + (s.unknown ?? 0);
  if (denom === 0) return 0;
  return Number(((auto / denom) * 100).toFixed(1));
});

const autoControlRateText = computed(
  () => `${autoControlRate.value.toFixed(1)}%`,
);

const autoControlRateColorClass = computed(() => {
  const rate = autoControlRate.value;
  if (rate >= 90) return 'text-emerald-600';
  if (rate >= 80) return 'text-blue-600';
  if (rate >= 60) return 'text-amber-600';
  return 'text-rose-600';
});

function handleTypeCardClick(type: string) {
  // MW-P4-03：类型筛选写入共享上下文（URL），不再维护内部状态
  if (type === 'ALL') {
    monitorCtx.update({ loopType: null, loopId: null });
  } else {
    monitorCtx.update({
      loopType: currentLoopType.value === type ? null : type,
      loopId: null,
    });
  }
  query.page = 1;
}

// ===== 数据加载 =====
async function loadList() {
  loading.value = true;
  errorMessage.value = null;
  try {
    const data = await getLoopMonitorListApi({
      plantNodeId: monitorCtx.plantNodeId.value ?? undefined,
      loopType: (monitorCtx.loopType.value as LoopApi.LoopType) || undefined,
      keyword: monitorCtx.keyword.value || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    // 标杆 v1.4：默认按评分升序（最差在前）；服务端 C1-1 已排序，前端作为双保险
    monitorList.value = data.items.toSorted((a, b) => {
      const sa = a.score ?? 999;
      const sb = b.score ?? 999;
      return sa - sb;
    });
    total.value = data.total;
    aggregate.value = data.aggregate ?? null;
  } catch (error: any) {
    errorMessage.value = error?.message ?? '加载失败';
    monitorList.value = [];
    total.value = 0;
    aggregate.value = null;
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
  }
}

async function loadLoopTypeStats() {
  try {
    const data = await getLoopTypeStatsApi(
      monitorCtx.plantNodeId.value ?? undefined,
    );
    const payload = data as any;
    typeStats.value = payload.loopTypeStats || payload;
    modeStats.value = payload.controlModeStats || {};
  } catch {
    // 错误已由拦截器处理
  }
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize ?? query.pageSize;
  loadList();
}

// ===== 导出 CSV =====
function exportCsv() {
  if (monitorList.value.length === 0) {
    message.warning('当前无可导出的数据');
    return;
  }
  const header = [
    '回路位号',
    '描述',
    '装置·单元',
    '回路类型',
    'SP',
    'PV',
    'OP',
    'MODE',
    '性能评分',
  ];
  const rows = monitorList.value.map((m) => [
    m.tagName ?? '',
    m.description ?? '',
    m.unitName ?? '',
    m.loopType ?? '',
    m.currentValues?.sp == null ? '' : m.currentValues.sp.toFixed(2),
    m.currentValues?.pv == null ? '' : m.currentValues.pv.toFixed(2),
    m.currentValues?.op == null ? '' : m.currentValues.op.toFixed(2),
    m.currentValues?.mode == null
      ? ''
      : (MODE_LABEL_MAP[String(m.currentValues.mode)] ??
        String(m.currentValues.mode)),
    m.score == null ? '' : Number(m.score).toFixed(2),
  ]);
  const csv = [header, ...rows]
    .map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `loop-monitor-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  message.success(`已导出 ${monitorList.value.length} 条回路`);
}

// ===== 行点击 → 切换到该回路详情 =====
function handleRowClick(record: LoopApi.MonitorListItem) {
  emit('loopClick', record.loopId);
}

// ===== 工具函数 =====
function modeColor(modeLabel: null | string | undefined): string {
  return modeLabelColor(modeLabel);
}

function modeText(record: LoopApi.MonitorListItem): string {
  return record.currentValues?.modeLabel || '—';
}

// ===== 回路等级（对齐 GRADE_CONFIG / useScoreColor GB/T 44693.2-2024 §6.3 默认阈值）=====
// 空评分返回中性灰，严禁映射为红色（"数据不足"不是"不合格"）
function getGradeTag(score: null | number | undefined): {
  color: string;
  label: string;
} {
  if (score == null || Number.isNaN(score))
    return { color: 'default', label: '—' };
  for (const cfg of GRADE_CONFIG) {
    if (score >= cfg.minScore) return { color: cfg.tagColor, label: cfg.label };
  }
  return { color: 'default', label: '—' };
}

// ===== 实时更新 =====
onMessage((msg) => {
  applyMessage(msg, monitorList.value as any[]);
  lastRefreshAt.value = new Date();
});

// ===== 自动刷新：WS 在线时仅靠 WS 推送更新 7 个实时值（PV/SP/OP/MODE/P/I/D），
// ===== 其余数据（统计/KPI 计算值等）只在手动刷新页面时更新；
// ===== WS 断连时回退到 30s 轮询，保证数据不会长时间停滞。
watch(wsConnectionStatus, (status) => {
  stopFallback();
  if (status === 'online') {
    loadList();
  } else {
    startFallback(async () => {
      await loadList();
    }, 30_000);
  }
});

// ===== 生命周期 =====
onMounted(() => {
  loadList();
  loadLoopTypeStats();
  if (autoRefresh.value) {
    start();
  }
});

onBeforeUnmount(() => {
  stop();
  stopFallback();
});

// MW-P4-03：监听共享上下文筛选变化 → 重新加载列表和统计
// 装置/类型/关键词/只看关注项均从 URL 读取，变化时重置到第 1 页
watch(
  () => [
    monitorCtx.plantNodeId.value,
    monitorCtx.loopType.value,
    monitorCtx.keyword.value,
    monitorCtx.attentionOnly.value,
  ],
  () => {
    query.page = 1;
    loadList();
    loadLoopTypeStats();
  },
);

defineExpose({
  refresh: loadList,
  exportCsv,
  cycleDensity,
  handleUpdateColumns,
  columnConfigs,
  densityLabel,
  lastRefreshText,
  wsConnectionStatus,
  lastMessageAt,
});
</script>

<template>
  <div class="loop-fleet-view">
    <!-- 表格工具条：左侧标题 + 右侧操作（导出/密度/自动刷新） -->
    <div class="loop-fleet-view__toolbar">
      <div class="loop-fleet-view__title">
        <span class="text-[15px] font-semibold text-gray-800">回路清单</span>
      </div>
      <div class="!ml-auto flex items-center gap-2 text-sm text-gray-500">
        <Button size="small" @click="exportCsv">导出</Button>
        <template v-if="showAutoRefresh">
          <Switch
            :checked="autoRefresh"
            @change="
              (val: any) => {
                autoRefresh = !!val;
                val ? start() : stop();
              }
            "
          />
          <span v-if="autoRefresh" class="text-xs text-gray-400">
            {{ isFallbackPolling ? 'WS 断连，轮询刷新中' : 'WS 实时推送' }}
          </span>
        </template>
      </div>
    </div>

    <!-- 统计卡片区域 -->
    <div v-if="showStats" class="loop-fleet-view__stats">
      <Card :body-style="{ padding: '8px 16px' }" class="h-auto">
        <div class="flex flex-wrap items-center gap-3">
          <div
            class="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-1.5 transition-opacity hover:opacity-80"
            :class="{ 'bg-gray-100': !currentLoopType }"
            role="button"
            tabindex="0"
            @click="handleTypeCardClick('ALL')"
            @keydown.enter="handleTypeCardClick('ALL')"
          >
            <span class="text-sm font-medium text-gray-600">全部</span>
            <span class="text-sm font-bold text-gray-800">{{
              totalLoops
            }}</span>
          </div>
          <div
            v-for="(count, key) in typeStats"
            v-show="count > 0"
            :key="key"
            class="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-1.5 transition-opacity hover:opacity-80"
            :class="{ 'bg-blue-50': currentLoopType === key }"
            role="button"
            tabindex="0"
            @click="handleTypeCardClick(key)"
            @keydown.enter="handleTypeCardClick(key)"
          >
            <Tag class="m-0">
              {{ LOOP_TYPE_LABEL_MAP[key ?? 'OTHER'] ?? '其他' }}
            </Tag>
            <span class="text-sm font-bold">{{ count }}</span>
          </div>

          <!-- 分隔符：类型统计 ↔ 等级统计 -->
          <div class="mx-1 h-5 w-px bg-gray-200"></div>

          <!-- 回路等级统计（基于当前页数据，五档颜色区分） -->
          <div
            v-for="cfg in GRADE_CONFIG"
            v-show="gradeStats[cfg.key] > 0"
            :key="`grade-${cfg.key}`"
            class="flex items-center gap-1.5 rounded-lg px-3 py-1.5"
          >
            <Tag :color="cfg.tagColor" class="m-0">{{ cfg.label }}</Tag>
            <span class="text-sm font-bold text-gray-800">{{
              gradeStats[cfg.key]
            }}</span>
          </div>
          <!-- 无评分回路（数据不足，中性灰，不计入任何等级） -->
          <div
            v-if="gradeStats.none > 0"
            class="flex items-center gap-1.5 rounded-lg px-3 py-1.5"
          >
            <Tag color="default" class="m-0">无评分</Tag>
            <span class="text-sm font-bold text-gray-400">{{
              gradeStats.none
            }}</span>
          </div>

          <!-- 分隔符：等级统计 ↔ 综合统计 -->
          <div class="mx-1 h-5 w-px bg-gray-200"></div>

          <!-- 综合性能（简单平均，E-1 服务端聚合，筛选联动） -->
          <div
            v-if="avgScore != null"
            class="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5"
          >
            <span class="text-sm font-medium text-gray-600">综合性能</span>
            <span class="text-sm font-bold text-gray-800">{{
              avgScore.toFixed(1)
            }}</span>
            <Tooltip title="筛选集合评分简单平均（非加权）" placement="bottom">
              <span class="cursor-help text-[10px] text-gray-400"
                >简单平均</span
              >
            </Tooltip>
          </div>

          <!-- 较昨日恶化（E-1 服务端聚合，scoreDelta ≤ -2） -->
          <div
            v-if="worsenedCount > 0"
            class="flex items-center gap-1.5 rounded-lg bg-rose-50 px-3 py-1.5"
          >
            <span class="text-sm font-medium text-rose-600">较昨日恶化</span>
            <span class="text-sm font-bold text-rose-700">{{
              worsenedCount
            }}</span>
          </div>

          <!-- 分隔符：综合统计 ↔ 实时自控率 -->
          <div class="mx-1 h-5 w-px bg-gray-200"></div>

          <!-- 实时自控率（E-1 优先服务端聚合，降级 modeStats） -->
          <div
            class="flex items-center gap-2 rounded-lg bg-gray-50 px-3 py-1.5"
          >
            <span class="text-sm font-medium text-gray-600">实时自控率</span>
            <span
              class="text-sm font-bold"
              :class="autoControlRateColorClass"
              >{{ autoControlRateText }}</span
            >
            <Tooltip
              title="实时口径（MODE 分布），与 KPI 有效自控率（快照口径）不同"
              placement="bottom"
            >
              <span class="cursor-help text-[10px] text-gray-400">实时</span>
            </Tooltip>
          </div>
        </div>
      </Card>
    </div>

    <!-- 表格 -->
    <Table
      :columns="visibleColumns"
      :data-source="monitorList"
      :loading="loading"
      :pagination="{
        current: query.page,
        pageSize: query.pageSize,
        total,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      :size="tableSize"
      :scroll="{ x: 1500 }"
      row-key="loopId"
      class="loop-fleet-view__table"
      :row-class-name="
        (record: any) =>
          record.loopId === $attrs['data-selected-loop-id']
            ? 'row-selected'
            : ''
      "
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'tagName'">
          <a
            class="cursor-pointer font-medium text-blue-600 hover:underline"
            role="button"
            tabindex="0"
            @click="handleRowClick(record as LoopApi.MonitorListItem)"
            @keydown.enter="handleRowClick(record as LoopApi.MonitorListItem)"
          >
            {{ (record as LoopApi.MonitorListItem).tagName }}
          </a>
        </template>
        <template v-else-if="column.key === 'pvRange'">
          <span
            v-if="
              (record as LoopApi.MonitorListItem).pvRange?.min != null ||
              (record as LoopApi.MonitorListItem).pvRange?.max != null
            "
            class="font-mono text-xs text-slate-600"
          >
            {{ (record as LoopApi.MonitorListItem).pvRange?.min ?? '—' }}
            ~
            {{ (record as LoopApi.MonitorListItem).pvRange?.max ?? '—' }}
          </span>
          <span v-else class="text-slate-300">—</span>
        </template>
        <template v-else-if="column.key === 'pvUnit'">
          <span
            v-if="(record as LoopApi.MonitorListItem).pvUnit"
            class="text-xs text-slate-600"
          >
            {{ (record as LoopApi.MonitorListItem).pvUnit }}
          </span>
          <span v-else class="text-slate-300">—</span>
        </template>
        <template v-else-if="column.key === 'loopType'">
          <Tag class="m-0">
            {{
              LOOP_TYPE_LABEL_MAP[
                (record as LoopApi.MonitorListItem).loopType ?? 'OTHER'
              ] ?? '其他'
            }}
          </Tag>
        </template>
        <template v-else-if="column.key === 'grade'">
          <Tag
            :color="
              getGradeTag((record as LoopApi.MonitorListItem).score).color
            "
            class="m-0"
          >
            {{ getGradeTag((record as LoopApi.MonitorListItem).score).label }}
          </Tag>
        </template>
        <template v-else-if="column.key === 'sp'">
          <ClpmNumeric
            v-if="(record as LoopApi.MonitorListItem).currentValues?.sp != null"
            :value="(record as LoopApi.MonitorListItem).currentValues?.sp"
            :precision="2"
            mono
            size="sm"
          />
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'pv'">
          <ClpmNumeric
            v-if="(record as LoopApi.MonitorListItem).currentValues?.pv != null"
            :value="(record as LoopApi.MonitorListItem).currentValues?.pv"
            :precision="2"
            mono
            size="sm"
          />
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'op'">
          <ClpmNumeric
            v-if="(record as LoopApi.MonitorListItem).currentValues?.op != null"
            :value="(record as LoopApi.MonitorListItem).currentValues?.op"
            :precision="2"
            mono
            size="sm"
          />
          <span v-else class="text-gray-400">—</span>
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
            class="inline-flex items-center gap-1"
          >
            <span
              class="h-2 w-2 rounded-full"
              :class="
                (record as LoopApi.MonitorListItem).score >= 80
                  ? 'bg-emerald-500'
                  : (record as LoopApi.MonitorListItem).score >= 60
                    ? 'bg-amber-500'
                    : 'bg-rose-500'
              "
            ></span>
            <ClpmNumeric
              :value="(record as LoopApi.MonitorListItem).score"
              :precision="2"
              mono
              size="sm"
            />
            <DayDeltaBadge
              :delta="(record as LoopApi.MonitorListItem).scoreDelta"
              :trend="(record as LoopApi.MonitorListItem).dayTrend"
            />
          </span>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'dataHealth'">
          <span class="text-xs text-gray-500">
            {{ (record as LoopApi.MonitorListItem).confidenceLevel ?? '—' }}
          </span>
        </template>
      </template>

      <!-- 空态 -->
      <template #emptyText>
        <div class="py-8 text-center text-gray-400">
          <p>暂无回路数据</p>
          <p v-if="errorMessage" class="text-red-400">{{ errorMessage }}</p>
        </div>
      </template>
    </Table>

    <!-- 状态栏 -->
    <div class="loop-fleet-view__footer">
      <span v-if="lastRefreshText" class="text-xs text-gray-400">
        最近刷新：{{ lastRefreshText }}
      </span>
      <span
        v-if="wsConnectionStatus === 'online'"
        class="text-xs text-emerald-500"
      >
        ● 实时连接
      </span>
      <span
        v-else-if="wsConnectionStatus === 'reconnecting'"
        class="text-xs text-amber-500"
      >
        ● 重连中
      </span>
      <span v-else class="text-xs text-gray-400"> ● 离线（轮询中） </span>
    </div>
  </div>
</template>

<style scoped>
.loop-fleet-view {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.loop-fleet-view__toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 4px 8px;
}

.loop-fleet-view__stats {
  flex-shrink: 0;
}

.loop-fleet-view__table {
  flex: 1;
  min-height: 0;
}

.loop-fleet-view__footer {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
  padding: 4px 8px;
}
</style>
