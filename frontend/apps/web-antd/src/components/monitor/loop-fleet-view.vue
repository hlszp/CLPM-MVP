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

import { Button, message, Switch, Table, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopMonitorListApi } from '#/api/loop';
import {
  ClpmFitnessBadge,
  ClpmNumeric,
} from '#/components/clpm';
import DayDeltaBadge from '#/components/loop/day-delta-badge.vue';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import {
  LOOP_TYPE_LABEL_MAP,
  MODE_LABEL_MAP,
  useLoopPalettes,
} from '#/composables/use-loop-palettes';
import { bindLoopInterest, useLoopRealtime } from '#/composables/use-loop-realtime';
import { useMonitorContext } from '#/composables/use-monitor-context';
import { useTableDensity } from '#/composables/use-table-density';
import { GRADE_THRESHOLDS } from '#/constants/clpm-ui';

defineOptions({ name: 'LoopFleetView' });

withDefaults(
  defineProps<{
    /** 是否显示自动刷新开关 */
    showAutoRefresh?: boolean;
    /** 是否显示表格工具条（标题 + 导出 + WS 开关） */
    showToolbar?: boolean;
  }>(),
  {
    showAutoRefresh: true,
    showToolbar: true,
  },
);

const emit = defineEmits<{
  (e: 'loopClick', loopId: string, record: LoopApi.MonitorListItem): void;
  /** 操作列"趋势"点击：弹出该回路趋势图 */
  (e: 'trendClick', record: LoopApi.MonitorListItem): void;
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

// ===== 表格列定义 =====
// 顺序：位号 / 描述 / 装置·单元 / 回路类型 / 量程 / 模式 / SP / PV / OP / 回路等级 / 性能评分 / 适用性 / 操作
// 量程列合并"量程+单位"（形如 0-100℃）；可信度不在列表展示（详情抽屉保留）
// 位号/评分列支持服务端排序（sorter 在 visibleColumns 中注入受控 sortOrder）
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
  { title: '量程', key: 'range', width: 110, align: 'center' },
  { title: '模式', key: 'mode', width: 110, align: 'center' },
  { title: '设定值 SP', key: 'sp', width: 90, align: 'center' },
  { title: '测量值 PV', key: 'pv', width: 90, align: 'center' },
  { title: '输出值 OP(%)', key: 'op', width: 110, align: 'center' },
  { title: '性能等级', key: 'grade', width: 80, align: 'center' },
  {
    title: '性能评分',
    dataIndex: 'score',
    key: 'score',
    width: 95,
    align: 'center',
  },
  // P2 IA优化：适用性等级列
  { title: '适用性', key: 'fitnessLevel', width: 95, align: 'center' },
  { title: '操作', key: 'action', width: 120, align: 'center', fixed: 'right' },
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
  return columns.map((c: any, i: number) => ({
    key: getColumnKey(c),
    label: String(c.title ?? ''),
    visible: true,
    order: i,
  }));
}

const columnConfigs = ref<ColumnConfig[]>(
  preferences.value.columns && preferences.value.columns.length > 0
    ? preferences.value.columns
    : buildDefaultColumnConfigs(),
);

/** 服务端排序状态（默认：评分升序，最差在前，对齐 C1-1 增量巡检口径） */
const sortState = reactive<{
  field: 'score' | 'tagName';
  order: 'ascend' | 'descend';
}>({ field: 'score', order: 'ascend' });

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
    })
    .map((c: any) => {
      // 位号/评分列：注入受控服务端排序（升/降两档切换）
      const key = getColumnKey(c);
      if (key === 'tagName' || key === 'score') {
        return {
          ...c,
          sorter: true,
          sortDirections: ['ascend', 'descend'],
          sortOrder:
            sortState.field === (key === 'tagName' ? 'tagName' : 'score')
              ? sortState.order
              : false,
        };
      }
      return c;
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

// 声明当前页回路的实时兴趣集合（服务端订阅过滤）。
// 修复 2026-09-07：本组件此前只消费消息不声明兴趣，而 layouts/basic.vue
// 在路由切换时清空兴趣——切到回路监视页后订阅恒为空集，服务端过滤掉
// 全部推送，实时值长期冻结（仅刷新页面的 REST 首屏会更新一次）。
// 列表加载/翻页/筛选变化时 watch 自动重订当前页回路。
bindLoopInterest(() => monitorList.value.map((l) => l.tagName));

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

// ===== 回路等级配置（对齐 use-score-color GB/T 44693.2-2024 §6.3 默认阈值）=====
// 五档：优秀(≥90) / 良好(≥80) / 合格(≥60) / 警告(≥40) / 不合格(<40)
// tagColor 使用 Ant Design Tag 预设色：绿→蓝→金→橙→红 形成视觉渐变
type GradeKey = 'excellent' | 'fair' | 'good' | 'poor' | 'warning';

// 0929 口径收敛：档位定义唯一源在 constants/clpm-ui（GRADE_THRESHOLDS）
const GRADE_TAG_COLOR: Record<number, string> = {
  1: 'green',
  2: 'blue',
  3: 'gold',
  4: 'orange',
  5: 'red',
};
const GRADE_CONFIG: ReadonlyArray<{
  key: GradeKey;
  label: string;
  minScore: number;
  tagColor: string;
}> = GRADE_THRESHOLDS.map((t) => ({
  key: t.name.toLowerCase() as GradeKey,
  label: t.label ?? t.name,
  minScore: t.minScore,
  tagColor: GRADE_TAG_COLOR[t.level] ?? 'gray',
}));

/** 性能等级 → 评分区间请求参数（回路监视页改版 P1-1；从 GRADE_THRESHOLDS
 * 单源派生半开区间 [min, max)，0/100 端点等价不设限；INCONCLUSIVE=无评分） */
const GRADE_QUERY_MAP: Record<
  string,
  { maxScore?: number; minScore?: number } | { unscored: true }
> = Object.fromEntries([
  ...GRADE_THRESHOLDS.map((t) => [
    t.name,
    {
      minScore: t.minScore > 0 ? t.minScore : undefined,
      maxScore: t.maxScore < 100 ? t.maxScore : undefined,
    },
  ]),
  ['INCONCLUSIVE', { unscored: true }],
]);

// ===== 数据加载 =====
async function loadList() {
  loading.value = true;
  errorMessage.value = null;
  try {
    const gradeQuery = monitorCtx.grade.value
      ? GRADE_QUERY_MAP[monitorCtx.grade.value]
      : undefined;
    const data = await getLoopMonitorListApi({
      plantNodeId: monitorCtx.plantNodeId.value ?? undefined,
      loopType: (monitorCtx.loopType.value as LoopApi.LoopType) || undefined,
      keyword: monitorCtx.keyword.value || undefined,
      controlMode:
        (monitorCtx.controlMode.value as LoopApi.MonitorQueryParams['controlMode']) ??
        undefined,
      ...gradeQuery,
      sortBy: sortState.field,
      sortOrder: sortState.order === 'ascend' ? 'asc' : 'desc',
      page: query.page,
      pageSize: query.pageSize,
    });
    // 默认（评分升序）时前端再按评分升序兜底（与服务端 C1-1 口径一致的双保险）；
    // 用户切换排序后以服务端返回顺序为准
    monitorList.value =
      sortState.field === 'score' && sortState.order === 'ascend'
        ? data.items.toSorted((a, b) => (a.score ?? 999) - (b.score ?? 999))
        : data.items;
    total.value = data.total;
  } catch (error: any) {
    errorMessage.value = error?.message ?? '加载失败';
    monitorList.value = [];
    total.value = 0;
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
  }
}

function handleTableChange(
  pagination: TablePaginationConfig,
  _filters: any,
  sorter: any,
) {
  // 排序切换（升/降两档；取消排序回到默认评分升序）
  const s = Array.isArray(sorter) ? sorter[0] : sorter;
  const key = s?.columnKey ?? s?.column?.key;
  if (s?.order && (key === 'tagName' || key === 'score')) {
    sortState.field = key;
    sortState.order = s.order;
  } else {
    sortState.field = 'score';
    sortState.order = 'ascend';
  }
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
    '模式',
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
  emit('loopClick', record.loopId, record);
}

/** 操作列"趋势"：弹出该回路趋势图弹窗 */
function handleTrendClick(record: LoopApi.MonitorListItem) {
  emit('trendClick', record);
}

// ===== 工具函数 =====
function modeColor(modeLabel: null | string | undefined): string {
  return modeLabelColor(modeLabel);
}

function modeText(record: LoopApi.MonitorListItem): string {
  return record.currentValues?.modeLabel || '—';
}

/** 量程格式化：min-max + 单位（形如 0-100℃），任一端缺失返回 null */
function formatRange(record: LoopApi.MonitorListItem): null | string {
  const min = record.pvRange?.min;
  const max = record.pvRange?.max;
  if (min == null || max == null) return null;
  return `${min}-${max}${record.pvUnit ?? ''}`;
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
  if (autoRefresh.value) {
    start();
  }
});

onBeforeUnmount(() => {
  stop();
  stopFallback();
});

// MW-P4-03：监听共享上下文筛选变化 → 重新加载列表
// 装置/类型/等级/关键词/只看关注项均从 URL 读取，变化时重置到第 1 页
watch(
  () => [
    monitorCtx.plantNodeId.value,
    monitorCtx.loopType.value,
    monitorCtx.grade.value,
    monitorCtx.keyword.value,
    monitorCtx.controlMode.value,
    monitorCtx.attentionOnly.value,
  ],
  () => {
    query.page = 1;
    loadList();
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
    <div v-if="showToolbar" class="loop-fleet-view__toolbar">
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
            class="cursor-pointer text-blue-600 hover:underline"
            role="button"
            tabindex="0"
            @click="handleRowClick(record as LoopApi.MonitorListItem)"
            @keydown.enter="handleRowClick(record as LoopApi.MonitorListItem)"
          >
            {{ (record as LoopApi.MonitorListItem).tagName }}
          </a>
        </template>
        <template v-else-if="column.key === 'action'">
          <div class="flex items-center justify-center gap-3">
            <a
              class="cursor-pointer text-blue-600 hover:underline"
              role="button"
              tabindex="0"
              @click="handleTrendClick(record as LoopApi.MonitorListItem)"
              @keydown.enter="handleTrendClick(record as LoopApi.MonitorListItem)"
            >
              趋势
            </a>
            <a
              class="cursor-pointer text-blue-600 hover:underline"
              role="button"
              tabindex="0"
              @click="handleRowClick(record as LoopApi.MonitorListItem)"
              @keydown.enter="handleRowClick(record as LoopApi.MonitorListItem)"
            >
              详情
            </a>
          </div>
        </template>
        <template v-else-if="column.key === 'range'">
          <span
            v-if="formatRange(record as LoopApi.MonitorListItem)"
            class="font-mono text-xs text-slate-600"
          >
            {{ formatRange(record as LoopApi.MonitorListItem) }}
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
        <template v-else-if="column.key === 'fitnessLevel'">
          <ClpmFitnessBadge
            :level="(record as LoopApi.MonitorListItem).fitnessLevel"
            :tags="(record as LoopApi.MonitorListItem).fitnessTags"
            size="sm"
          />
        </template>
        <template v-else-if="column.key === 'sp'">
          <ClpmNumeric
            v-if="(record as LoopApi.MonitorListItem).currentValues?.sp != null"
            :value="(record as LoopApi.MonitorListItem).currentValues?.sp"
            :precision="2"
            mono
            size="sm"
            :weight="400"
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
            :weight="400"
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
            :weight="400"
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
              :weight="400"
            />
            <DayDeltaBadge
              :delta="(record as LoopApi.MonitorListItem).scoreDelta"
              :trend="(record as LoopApi.MonitorListItem).dayTrend"
            />
          </span>
          <span v-else class="text-gray-400">—</span>
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

.loop-fleet-view__table {
  flex: 1;
  min-height: 0;
}

/* 列表字体全部取消加粗：覆盖共享组件（DayDeltaBadge 等）的 medium/semibold 字重 */
.loop-fleet-view__table :deep(td .font-medium),
.loop-fleet-view__table :deep(td .font-semibold),
.loop-fleet-view__table :deep(td .font-bold) {
  font-weight: 400;
}

.loop-fleet-view__footer {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
  padding: 4px 8px;
}
</style>
