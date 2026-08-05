<script lang="ts" setup>
/**
 * 诊断记录页（原诊断列表改造，仅显示已归档数据）
 *
 * 对齐 PRD §4.4 + 实现契约 v2.0
 * - Tab「归档记录」：顶部 KpiStrip（后端聚合口径）+ 筛选栏 + 已归档记录表格
 * - Tab「诊断标签」：diagnosis_tag 标签面板（FE-14，筛选 + 处理/抑制）
 * - 点击行跳转诊断详情页 /diagnosis/detail/:loopId
 * - 分页
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { PlantNodeApi } from '#/api/plant-node';
import type { KpiStripItem } from '#/components/clpm';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import {
  Button,
  message,
  Modal,
  Select,
  Table,
  TabPane,
  Tabs,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  deleteDiagnosisTaskApi,
  exportDiagnosisStatisticsApi,
  getDiagnosisRecordsApi,
} from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmColumnSettings,
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import TagPanel from '#/components/diagnosis/tag-panel.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { runWithConcurrency } from '#/utils/concurrency';
import { formatTime } from '#/utils/format';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'DiagnosisRecords' });

const { themeColors } = useClpmTheme();

const router = useRouter();

const loading = ref(false);
const recordList = ref<DiagnosisApi.TaskItem[]>([]);
const total = ref(0);
/** 全量聚合统计（后端 SQL group-by，不受分页影响） */
const aggregates = ref<DiagnosisApi.DiagnosisAggregates | null>(null);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);
const selectedRowKeys = ref<string[]>([]);
const batchDeleteLoading = ref(false);
/** 工具栏 CSV 统计导出 loading（防重复点击） */
const exportingCsv = ref(false);

/** 行选择配置 */
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys.map(String);
  },
}));

/** 当前激活 Tab：归档记录 / 诊断标签（A11） */
const activeTab = ref('records');

const query = reactive({
  plantNodeId: undefined as string | undefined,
  diagnosisLabel: undefined as DiagnosisLabel | undefined,
  timeWindow: 'last_7_days' as DiagnosisApi.TimeWindow,
  page: 1,
  pageSize: 20,
});

/** 8 类诊断标签选项 */
const labelOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 装置/单元下拉选项（computed：避免模板内 plantNodes.map 每次重渲染重复计算） */
const plantNodeOptions = computed(() =>
  plantNodes.value.map((n) => ({ label: n.name, value: n.id })),
);

/** 标签颜色映射 */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

/** 时间窗选项（对齐后端 _build_time_window_condition 支持的值） */
const timeWindowOptions: { label: string; value: DiagnosisApi.TimeWindow }[] = [
  { label: '近 24 小时', value: 'last_24_hours' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 150 },
  {
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 160,
    ellipsis: true,
  },
  {
    title: '综合评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 100,
    align: 'right',
  },
  {
    title: '诊断标签',
    dataIndex: 'labels',
    key: 'labels',
    width: 200,
  },
  {
    title: '触发方式',
    dataIndex: 'triggerType',
    key: 'triggerType',
    width: 90,
    align: 'center',
  },
  {
    title: '诊断时间',
    dataIndex: 'triggeredAt',
    key: 'triggeredAt',
    width: 170,
  },
  {
    title: '归档时间',
    dataIndex: 'archivedAt',
    key: 'archivedAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
];

// ===== P2-04：表格列配置（显示/隐藏 + 排序，localStorage 持久化）=====
const { preferences: columnPrefs, updateColumns: persistColumns } =
  usePagePreference('diagnosis-records');

function getColumnKey(col: TableColumnsType[number]): string {
  const c = col as any;
  if (c.key) return String(c.key);
  if (c.dataIndex) {
    return Array.isArray(c.dataIndex)
      ? String(c.dataIndex[0])
      : String(c.dataIndex);
  }
  return '';
}

function buildDefaultColumnConfigs(): ColumnConfig[] {
  return columns.map((c, i) => ({
    key: getColumnKey(c),
    label: String(c.title ?? ''),
    visible: true,
    order: i,
  }));
}

const columnConfigs = ref<ColumnConfig[]>(
  columnPrefs.value.columns && columnPrefs.value.columns.length > 0
    ? columnPrefs.value.columns
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
    .filter((c) => {
      const cfg = configMap.get(getColumnKey(c));
      return cfg ? cfg.visible : true;
    })
    .toSorted((a, b) => {
      const aOrder = configMap.get(getColumnKey(a))?.order ?? 99;
      const bOrder = configMap.get(getColumnKey(b))?.order ?? 99;
      return aOrder - bOrder;
    });
});

function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
  persistColumns(cols);
}

function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
  persistColumns(columnConfigs.value);
}

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载诊断记录列表（已归档） */
async function loadList() {
  loading.value = true;
  try {
    const data = await getDiagnosisRecordsApi({
      plantNodeId: query.plantNodeId,
      diagnosisLabel: query.diagnosisLabel,
      timeWindow: query.timeWindow,
      page: query.page,
      pageSize: query.pageSize,
    });
    recordList.value = data.items || [];
    total.value = data.total || 0;
    aggregates.value = data.aggregates ?? null;
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
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

/** 跳转诊断详情 */
function handleViewDetail(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

/** 跳转异常跟踪页（F13：统一独立页入口，替代原抽屉模式） */
function handleOpenTracker(loopId: string) {
  router.push({ path: '/diagnosis/tracker', query: { loopId } });
}

/** 工具栏：刷新 */
function handleRefresh() {
  loadList();
}

/**
 * 将 timeWindow 枚举转为 [startDate, endDate] ISO 字符串（对齐后端 _build_time_window_condition 口径）。
 * last_24_hours → 24h / last_7_days → 7d / last_30_days → 30d
 */
function timeWindowToRange(timeWindow: DiagnosisApi.TimeWindow): {
  endDate: string;
  startDate: string;
} {
  const now = dayjs();
  const map: Record<DiagnosisApi.TimeWindow, number> = {
    last_24_hours: 24,
    last_7_days: 7 * 24,
    last_30_days: 30 * 24,
  };
  const hours = map[timeWindow] ?? 24 * 7;
  return {
    startDate: now.subtract(hours, 'hour').toISOString(),
    endDate: now.toISOString(),
  };
}

/** 生成 CSV 导出文件名：CLPM-诊断统计_[start]_[end].csv */
function buildCsvFileName(startDate: string, endDate: string): string {
  const s = startDate.slice(0, 10);
  const e = endDate.slice(0, 10);
  return `CLPM-诊断统计_${s}_${e}.csv`;
}

/**
 * 工具栏 CSV 统计导出（SVC-13：GET /diagnosis/statistics/export）。
 *
 * 按当前 timeWindow 推导时间范围，并透传 plantNodeId 筛选，
 * 导出诊断标签统计 CSV（UTF-8 with BOM）。
 */
async function handleExportCsv() {
  if (exportingCsv.value) return;
  exportingCsv.value = true;
  const { startDate, endDate } = timeWindowToRange(query.timeWindow);
  const params = {
    startDate,
    endDate,
    plantNodeId: query.plantNodeId,
  };
  try {
    const blob = await exportDiagnosisStatisticsApi(params);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = buildCsvFileName(startDate, endDate);
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('诊断统计 CSV 已导出');
  } catch {
    // 错误已由拦截器处理
  } finally {
    exportingCsv.value = false;
  }
}

/** 行级删除 */
function handleDelete(record: DiagnosisApi.TaskItem) {
  Modal.confirm({
    title: '确认删除',
    content: `确认删除回路 ${record.tagName} 的诊断记录？`,
    okType: 'danger',
    onOk: async () => {
      await deleteDiagnosisTaskApi(record.taskId);
      message.success('记录已删除');
      selectedRowKeys.value = selectedRowKeys.value.filter(
        (k) => k !== record.taskId,
      );
      await loadList();
    },
  });
}

/** 批量删除 */
async function handleBatchDelete() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先选择要删除的记录');
    return;
  }
  const count = selectedRowKeys.value.length;
  Modal.confirm({
    title: '确认批量删除',
    content: `确认删除 ${count} 条诊断记录？`,
    okType: 'danger',
    onOk: async () => {
      batchDeleteLoading.value = true;
      try {
        // allSettled 语义 + 并发限制：单项失败不中断其余删除，避免打满后端连接
        const { fulfilled, rejected } = await runWithConcurrency(
          selectedRowKeys.value,
          (id) => deleteDiagnosisTaskApi(id),
        );
        if (rejected === 0) {
          message.success(`已删除 ${fulfilled} 条记录`);
        } else {
          message.warning(
            `已删除 ${fulfilled} 条记录，${rejected} 条失败（错误已记录）`,
          );
        }
        selectedRowKeys.value = [];
        await loadList();
      } catch {
        // 错误已由拦截器处理
      } finally {
        batchDeleteLoading.value = false;
      }
    },
  });
}

/** KpiStrip 摘要指标：已归档总数 / 近 7 天归档 / 振荡类 / 阀门粘滞类（后端聚合口径） */
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const agg = aggregates.value;
  const totalCount = agg?.total ?? total.value;
  const recentCount = agg?.recent7Days ?? 0;
  const oscillationCount = agg?.labelCounts?.OSCILLATION ?? 0;
  const stictionCount = agg?.labelCounts?.VALVE_STICTION ?? 0;

  return [
    {
      key: 'total',
      label: '已归档总数',
      value: totalCount,
      unit: '条',
      status: 'neutral',
    },
    {
      key: 'recent',
      label: '近 7 天归档',
      value: recentCount,
      unit: '条',
      status: 'primary',
    },
    {
      key: 'oscillation',
      label: '振荡类',
      value: oscillationCount,
      unit: '条',
      status: 'warning',
    },
    {
      key: 'stiction',
      label: '阀门粘滞类',
      value: stictionCount,
      unit: '条',
      status: 'danger',
    },
  ];
});

/**
 * 相对时间格式化（如"2小时前"/"3天前"）
 */
function formatRelativeTime(t: null | string | undefined): string {
  if (!t) return '';
  try {
    const target = dayjs(t);
    const now = dayjs();
    const diffSec = now.diff(target, 'second');
    if (diffSec < 60) return '刚刚';
    const diffMin = now.diff(target, 'minute');
    if (diffMin < 60) return `${diffMin}分钟前`;
    const diffHour = now.diff(target, 'hour');
    if (diffHour < 24) return `${diffHour}小时前`;
    const diffDay = now.diff(target, 'day');
    if (diffDay < 30) return `${diffDay}天前`;
    const diffMonth = now.diff(target, 'month');
    if (diffMonth < 12) return `${diffMonth}个月前`;
    return `${now.diff(target, 'year')}年前`;
  } catch {
    return '';
  }
}

/** 触发方式中文 */
function triggerTypeName(t: string): string {
  return t === 'auto' ? '自动' : '手动';
}

/** 获取记录的诊断标签列表（多 Tag 展示） */
function getRecordTags(record: DiagnosisApi.TaskItem): {
  label: string;
  name: string;
}[] {
  if (!record.labels || record.labels.length === 0) return [];
  return record.labels.map((l) => ({
    label: l.label,
    name: labelName(l.label),
  }));
}

function labelName(label: string): string {
  return getDiagnosisLabelName(label as DiagnosisLabel);
}

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="诊断记录"
      subtitle="查看已归档的诊断任务记录，按装置、标签和时间窗筛选。"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loading"
          @click="handleRefresh"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出"
          :loading="exportingCsv"
          @click="handleExportCsv"
        />
        <ClpmColumnSettings
          :columns="columnConfigs"
          @update:columns="handleUpdateColumns"
          @reset="handleResetColumns"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 归档记录 / 诊断标签 Tab（A11：标签面板接入真实 diagnosis_tag 数据） -->
    <Tabs v-model:active-key="activeTab" class="mt-4">
      <TabPane key="records" tab="归档记录">
        <!-- 顶部 KpiStrip -->
        <ClpmKpiStrip :items="kpiStripItems" :loading="loading" />

        <ClpmDataCanvas class="mt-3" title="诊断记录" :loading="loading">
          <!-- 筛选栏 -->
          <div class="mb-4 flex flex-wrap items-center gap-3">
            <Select
              v-model:value="query.plantNodeId"
              placeholder="装置/单元筛选"
              style="width: 220px"
              allow-clear
              :options="plantNodeOptions"
              @change="handleSearch"
            />
            <Select
              v-model:value="query.diagnosisLabel"
              placeholder="诊断标签"
              style="width: 160px"
              allow-clear
              :options="labelOptions"
              @change="handleSearch"
            />
            <Select
              v-model:value="query.timeWindow"
              style="width: 140px"
              :options="timeWindowOptions"
              @change="handleSearch"
            />
            <Button type="primary" :loading="loading" @click="handleSearch">
              查询
            </Button>
            <Button
              danger
              :disabled="selectedRowKeys.length === 0"
              :loading="batchDeleteLoading"
              @click="handleBatchDelete"
            >
              <template #icon>
                <IconifyIcon icon="ant-design:delete-outlined" />
              </template>
              批量删除{{
                selectedRowKeys.length > 0
                  ? `（${selectedRowKeys.length}）`
                  : ''
              }}
            </Button>
          </div>

          <Table
            :columns="visibleColumns"
            :data-source="recordList"
            :loading="loading"
            :pagination="{
              current: query.page,
              pageSize: query.pageSize,
              total,
              showSizeChanger: true,
              showTotal: (t: number) => `共 ${t} 条`,
            }"
            :row-key="(record: DiagnosisApi.TaskItem) => record.taskId"
            :row-selection="rowSelection"
            :scroll="{ x: 1320 }"
            size="middle"
            :custom-row="
              (record: DiagnosisApi.TaskItem) => ({
                onClick: () => handleViewDetail(record.loopId),
                style: { cursor: 'pointer' },
              })
            "
            @change="handleTableChange"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'compositeScore'">
                <span
                  class="clpm-num font-medium"
                  :style="{ color: themeColors.INFO }"
                >
                  {{
                    record.compositeScore === null
                      ? '—'
                      : Number(record.compositeScore).toFixed(2)
                  }}
                </span>
              </template>
              <template v-else-if="column.key === 'labels'">
                <!-- 多 Tag 展示 -->
                <div class="flex flex-wrap gap-1">
                  <Tag
                    v-for="tag in getRecordTags(
                      record as DiagnosisApi.TaskItem,
                    )"
                    :key="tag.label"
                    :color="
                      labelColorMap[tag.label as DiagnosisLabel] || 'default'
                    "
                  >
                    {{ tag.name }}
                  </Tag>
                  <span
                    v-if="
                      getRecordTags(record as DiagnosisApi.TaskItem).length ===
                      0
                    "
                    class="text-xs"
                    :style="{ color: themeColors.NEUTRAL }"
                  >
                    —
                  </span>
                </div>
              </template>
              <template v-else-if="column.key === 'triggerType'">
                {{ triggerTypeName(record.triggerType) }}
              </template>
              <template v-else-if="column.key === 'triggeredAt'">
                <div class="flex flex-col leading-tight">
                  <span class="clpm-num">{{
                    formatTime(record.triggeredAt)
                  }}</span>
                  <span
                    v-if="formatRelativeTime(record.triggeredAt)"
                    class="text-xs text-muted-foreground"
                  >
                    {{ formatRelativeTime(record.triggeredAt) }}
                  </span>
                </div>
              </template>
              <template v-else-if="column.key === 'archivedAt'">
                <!-- 归档时间列：显示真实归档时间（自动归档/手动归档） -->
                <div class="flex flex-col leading-tight">
                  <span class="clpm-num">{{
                    formatTime(record.archivedAt)
                  }}</span>
                  <span
                    v-if="formatRelativeTime(record.archivedAt)"
                    class="text-xs text-muted-foreground"
                  >
                    {{ formatRelativeTime(record.archivedAt) }}
                  </span>
                </div>
              </template>
              <template v-else-if="column.key === 'action'">
                <Button
                  type="link"
                  size="small"
                  @click.stop="handleViewDetail(record.loopId)"
                >
                  查看详情
                </Button>
                <Button
                  type="link"
                  size="small"
                  @click.stop="handleOpenTracker(record.loopId)"
                >
                  异常跟踪
                </Button>
                <Button
                  type="link"
                  size="small"
                  danger
                  @click.stop="handleDelete(record as DiagnosisApi.TaskItem)"
                >
                  删除
                </Button>
              </template>
            </template>
          </Table>
        </ClpmDataCanvas>
      </TabPane>

      <TabPane key="tags" tab="诊断标签">
        <TagPanel />
      </TabPane>
    </Tabs>
    <!-- F13：Tracker 抽屉已移除，统一跳转 /diagnosis/tracker?loopId=xxx -->
  </Page>
</template>
