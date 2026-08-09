/**
 * LoopFleetView — 批量回路表格视图（MW-P4-01）
 *
 * 从旧 monitor.vue 抽取的列表、筛选、统计、列设置、密度、导出。
 * 页面壳、路由和全局工具栏不进入组件——由父页面提供。
 * 实时逻辑改用 useLoopRealtime（MW-P1-04）。
 *
 * 对齐整改方案 §8 Phase 4。
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from 'vue';

import { Button, Card, Input, message, Select, Switch, Table, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisListApi } from '#/api/diagnosis';
import {
  getLoopMonitorListApi,
  getLoopTypeStatsApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmNumeric,
  DayDeltaBadge,
} from '#/components/clpm';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import { useLoopRealtime } from '#/composables/use-loop-realtime';
import { useTableDensity } from '#/composables/use-table-density';
import {
  LOOP_TYPE_LABEL_MAP,
  MODE_LABEL_MAP,
  useLoopPalettes,
} from '#/composables/use-loop-palettes';
import { DIAGNOSIS_LABEL_COLOR_MAP, getDiagnosisLabelName } from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopFleetView' });

const props = withDefaults(
  defineProps<{
    /** 初始筛选（来自 useMonitorContext） */
    initialPlantNodeId?: string | undefined;
    initialLoopType?: string | undefined;
    initialKeyword?: string | undefined;
    /** 是否显示统计卡片区域 */
    showStats?: boolean;
    /** 是否显示自动刷新开关 */
    showAutoRefresh?: boolean;
  }>(),
  {
    initialPlantNodeId: undefined,
    initialLoopType: undefined,
    initialKeyword: undefined,
    showStats: true,
    showAutoRefresh: true,
  },
);

const emit = defineEmits<{
  (e: 'loopClick', loopId: string): void;
}>();

const { modeLabelColor } = useLoopPalettes();

// ===== 用户偏好 =====
const { preferences, updateColumns } = usePagePreference('loop-monitor');

// ===== 查询状态 =====
const query = reactive({
  plantNodeId: props.initialPlantNodeId as string | undefined,
  loopType: props.initialLoopType as string | undefined,
  keyword: props.initialKeyword ?? '',
  page: 1,
  pageSize: 20,
});

const loopTypeOptions = [
  { label: '全部', value: '' },
  ...Object.entries(LOOP_TYPE_LABEL_MAP).map(([value, label]) => ({
    label,
    value,
  })),
];

// ===== 数据状态 =====
const loading = ref(false);
const errorMessage = ref<string | null>(null);
const monitorList = ref<LoopApi.MonitorListItem[]>([]);
const total = ref(0);
const typeStats = ref<LoopApi.LoopTypeStat[]>([]);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

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
    return { label: path.join(' / '), value: node.id };
  });
});

// ===== 诊断标签 map =====
const diagLabelMap = ref<
  Record<string, { color: string; label: string; labelCode: string }>
>({});

// ===== 表格列定义（与旧 monitor.vue 一致）=====
const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 150, align: 'left' },
  { title: '名称', dataIndex: 'description', key: 'description', width: 180, ellipsis: true, align: 'left' },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 120, align: 'center' },
  { title: '测量量程', key: 'pvRange', width: 100, align: 'center' },
  { title: '单位', key: 'pvUnit', width: 55, align: 'center' },
  { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 100, align: 'center' },
  { title: '设定值 SP', key: 'sp', width: 90, align: 'right' },
  { title: '测量值 PV', key: 'pv', width: 90, align: 'right' },
  { title: '输出值 OP(%)', key: 'op', width: 90, align: 'right' },
  { title: '控制方式', key: 'mode', width: 110, align: 'center' },
  { title: '性能指数', dataIndex: 'score', key: 'score', width: 85, align: 'right' },
  { title: '诊断标签', key: 'diagLabel', width: 110, align: 'center' },
  { title: '数据健康度', key: 'dataHealth', width: 130, align: 'center' },
  { title: '操作', key: 'action', width: 120, fixed: 'right', align: 'center' },
];

function getColumnKey(col: any): string {
  if (col.key) return String(col.key);
  if (col.dataIndex) {
    return Array.isArray(col.dataIndex) ? String(col.dataIndex[0]) : String(col.dataIndex);
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
    columnConfigs.value.map((c, i) => [c.key, { visible: c.visible, order: i }]),
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
const { tableSize, densityLabel, cycleDensity } = useTableDensity('loop-monitor');

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
const isFallbackPolling = computed(() => wsConnectionStatus.value === 'offline');

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
  typeStats.value.reduce((sum, s) => sum + s.count, 0),
);

function handleTypeCardClick(type: string) {
  if (type === 'ALL') {
    query.loopType = '';
  } else {
    query.loopType = query.loopType === type ? '' : type;
  }
  query.page = 1;
  loadList();
}

// ===== 数据加载 =====
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

async function loadList() {
  loading.value = true;
  errorMessage.value = null;
  try {
    const data = await getLoopMonitorListApi({
      plantNodeId: query.plantNodeId,
      loopType: (query.loopType as LoopApi.LoopType) || undefined,
      keyword: query.keyword || undefined,
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
    const data = await getLoopTypeStatsApi({
      plantNodeId: query.plantNodeId,
    });
    typeStats.value = data;
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
    const map: Record<string, { color: string; label: string; labelCode: string }> = {};
    for (const item of data.items ?? []) {
      const labelName =
        item.labelName ||
        getDiagnosisLabelName(item.diagnosisLabel as any);
      const color =
        DIAGNOSIS_LABEL_COLOR_MAP[item.diagnosisLabel as any] ?? 'default';
      map[item.loopId] = { color, label: labelName, labelCode: item.diagnosisLabel };
    }
    diagLabelMap.value = map;
  } catch {
    diagLabelMap.value = {};
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
  loadLoopTypeStats();
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
  const header = ['回路位号', '名称', '所属单元', '类型', 'SP', 'PV', 'OP', '控制方式', '性能指数'];
  const rows = monitorList.value.map((m) => [
    m.tagName ?? '',
    m.description ?? '',
    m.unitName ?? '',
    m.loopType ?? '',
    m.currentValues?.sp == null ? '' : m.currentValues.sp.toFixed(2),
    m.currentValues?.pv == null ? '' : m.currentValues.pv.toFixed(2),
    m.currentValues?.op == null ? '' : m.currentValues.op.toFixed(2),
    m.currentValues?.mode == null ? '' : (MODE_LABEL_MAP[String(m.currentValues.mode)] ?? String(m.currentValues.mode)),
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
  loadPlantNodes();
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

// 父组件 initialQuery 变化时同步
watch(
  () => [props.initialPlantNodeId, props.initialLoopType, props.initialKeyword],
  ([nodeId, lType, kw]) => {
    if (query.plantNodeId !== nodeId) {
      query.plantNodeId = nodeId as string | undefined;
      query.page = 1;
      loadList();
      loadLoopTypeStats();
    }
    if (query.loopType !== lType) {
      query.loopType = lType as string | undefined;
      query.page = 1;
      loadList();
      loadLoopTypeStats();
    }
    if (query.keyword !== kw) {
      query.keyword = (kw as string) ?? '';
    }
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
    <!-- 筛选区 -->
    <div class="loop-fleet-view__filter">
      <Select
        v-model:value="query.plantNodeId"
        placeholder="按装置/单元筛选"
        style="width: 220px"
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
        style="width: 140px"
        allow-clear
        :options="loopTypeOptions"
        @change="handleSearch"
      />
      <Input
        v-model:value="query.keyword"
        placeholder="搜索位号/描述"
        allow-clear
        style="width: 200px"
        @press-enter="handleSearch"
      />
      <Button type="primary" @click="handleSearch">查询</Button>
      <div class="!ml-auto flex items-center gap-2 text-sm text-gray-500">
        <Button size="small" @click="exportCsv">导出</Button>
        <Button size="small" @click="cycleDensity">
          密度：{{ densityLabel }}
        </Button>
        <template v-if="showAutoRefresh">
          <span>自动刷新（30s）</span>
          <Switch
            :checked="autoRefresh"
            @change="(val: any) => { autoRefresh = !!val; val ? start() : stop(); }"
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
            :class="{ 'bg-gray-100': query.loopType === '' }"
            role="button"
            tabindex="0"
            @click="handleTypeCardClick('ALL')"
            @keydown.enter="handleTypeCardClick('ALL')"
          >
            <span class="text-sm font-medium text-gray-600">全部</span>
            <span class="text-sm font-bold text-gray-800">{{ totalLoops }}</span>
          </div>
          <div
            v-for="stat in typeStats"
            :key="stat.loopType"
            class="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-1.5 transition-opacity hover:opacity-80"
            :class="{ 'bg-blue-50': query.loopType === stat.loopType }"
            role="button"
            tabindex="0"
            @click="handleTypeCardClick(stat.loopType)"
            @keydown.enter="handleTypeCardClick(stat.loopType)"
          >
            <Tag class="m-0">
              {{ LOOP_TYPE_LABEL_MAP[stat.loopType ?? 'OTHER'] ?? '其他' }}
            </Tag>
            <span class="text-sm font-bold">{{ stat.count }}</span>
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
        total: total,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      :size="tableSize"
      :scroll="{ x: 1400 }"
      row-key="loopId"
      class="loop-fleet-view__table"
      :row-class-name="
        (record: any) =>
          record.loopId === $attrs['data-selected-loop-id'] ? 'row-selected' : ''
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
            :color="diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!.color"
            class="m-0"
          >
            {{ diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!.label }}
          </Tag>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'dataHealth'">
          <span class="text-xs text-gray-500">
            {{
              (record as LoopApi.MonitorListItem).confidenceLevel ?? '—'
            }}
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

.loop-fleet-view__filter {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
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
