<script lang="ts" setup>
/**
 * LoopFleetView — 批量回路表格视图（MW-P4-01 / MW-P4-03）
 *
 * 从旧 monitor.vue 抽取的列表、统计、列设置、密度、导出。
 * 页面壳、路由和全局工具栏不进入组件——由父页面提供。
 * 实时逻辑改用 useLoopRealtime（MW-P1-04）。
 *
 * MW-P4-03：筛选条件（装置/类型/关键词/只看关注项）统一从 useMonitorContext
 * 读取，不再维护内部筛选状态，与 workspace 模式共享同一 URL 真相源。
 * 保存视图、搜索框由父页面的 MonitorContextToolbar 统一提供。
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

import { getDiagnosisListApi } from '#/api/diagnosis';
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
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';

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

// ===== 诊断标签 map =====
const diagLabelMap = ref<
  Record<string, { color: string; label: string; labelCode: string }>
>({});

// ===== 表格列定义（与旧 monitor.vue 一致）=====
const columns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 150,
    align: 'left',
  },
  {
    title: '名称',
    dataIndex: 'description',
    key: 'description',
    width: 180,
    ellipsis: true,
    align: 'left',
  },
  {
    title: '所属单元',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 120,
    align: 'center',
  },
  { title: '测量量程', key: 'pvRange', width: 100, align: 'center' },
  { title: '单位', key: 'pvUnit', width: 55, align: 'center' },
  {
    title: '类型',
    dataIndex: 'loopType',
    key: 'loopType',
    width: 100,
    align: 'center',
  },
  { title: '设定值 SP', key: 'sp', width: 90, align: 'right' },
  { title: '测量值 PV', key: 'pv', width: 90, align: 'right' },
  { title: '输出值 OP(%)', key: 'op', width: 90, align: 'right' },
  { title: '控制方式', key: 'mode', width: 110, align: 'center' },
  {
    title: '性能指数',
    dataIndex: 'score',
    key: 'score',
    width: 85,
    align: 'right',
  },
  { title: '诊断标签', key: 'diagLabel', width: 110, align: 'center' },
  { title: '数据健康度', key: 'dataHealth', width: 130, align: 'center' },
  { title: '操作', key: 'action', width: 120, fixed: 'right', align: 'center' },
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
    monitorList.value = data.items;
    total.value = data.total;
    loadDiagLabels(data.items.map((it) => it.loopId));
  } catch (error: any) {
    errorMessage.value = error?.message ?? '加载失败';
    monitorList.value = [];
    total.value = 0;
    diagLabelMap.value = {};
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
    typeStats.value =
      (data as any).loopTypeStats || (data as Record<string, number>);
  } catch {
    // 错误已由拦截器处理
  }
}

async function loadDiagLabels(loopIds: string[]) {
  if (loopIds.length === 0) {
    diagLabelMap.value = {};
    return;
  }
  try {
    const data = await getDiagnosisListApi({ loopIds, page: 1, pageSize: 100 });
    const map: Record<
      string,
      { color: string; label: string; labelCode: string }
    > = {};
    for (const item of data.items ?? []) {
      const labelName =
        item.labelName || getDiagnosisLabelName(item.diagnosisLabel as any);
      const color =
        (DIAGNOSIS_LABEL_COLOR_MAP as Record<string, string>)[
          item.diagnosisLabel as string
        ] ?? 'default';
      map[item.loopId] = {
        color,
        label: labelName,
        labelCode: item.diagnosisLabel,
      };
    }
    diagLabelMap.value = map;
  } catch {
    diagLabelMap.value = {};
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
    '名称',
    '所属单元',
    '类型',
    'SP',
    'PV',
    'OP',
    '控制方式',
    '性能指数',
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

// ===== 行点击 → 切换到 workspace =====
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

// ===== 实时更新 =====
onMessage((msg) => {
  applyMessage(msg, monitorList.value as any[]);
  lastRefreshAt.value = new Date();
});

// ===== 自动刷新：WS 在线不轮询，断连 30s 轮询 =====
watch(wsConnectionStatus, (status) => {
  if (status === 'online') {
    stopFallback();
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
    <!-- 表格工具条（导出/密度/自动刷新；筛选由父页面 MonitorContextToolbar 统一提供） -->
    <div class="loop-fleet-view__toolbar">
      <div class="!ml-auto flex items-center gap-2 text-sm text-gray-500">
        <Button size="small" @click="exportCsv">导出</Button>
        <Button size="small" @click="cycleDensity">
          密度：{{ densityLabel }}
        </Button>
        <template v-if="showAutoRefresh">
          <span>自动刷新（30s）</span>
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
      :scroll="{ x: 1400 }"
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
            :weight="600"
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
              :precision="1"
              mono
              size="sm"
              :weight="600"
            />
            <DayDeltaBadge
              :delta="(record as LoopApi.MonitorListItem).scoreDelta"
              :trend="(record as LoopApi.MonitorListItem).dayTrend"
            />
          </span>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'diagLabel'">
          <Tag
            v-if="diagLabelMap[(record as LoopApi.MonitorListItem).loopId]"
            :color="
              diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!.color
            "
            class="m-0"
          >
            {{
              diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!.label
            }}
          </Tag>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'dataHealth'">
          <span class="text-xs text-gray-500">
            {{ (record as LoopApi.MonitorListItem).confidenceLevel ?? '—' }}
          </span>
        </template>
        <template v-else-if="column.key === 'action'">
          <Button
            type="link"
            size="small"
            @click="handleRowClick(record as LoopApi.MonitorListItem)"
          >
            进入工作台
          </Button>
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
