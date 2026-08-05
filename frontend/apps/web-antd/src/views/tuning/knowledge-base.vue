<script lang="ts">
import { h } from 'vue';
</script>

<script lang="ts" setup>
/**
 * 整定知识库页面（P3-01）
 *
 * 展示验证通过的整定案例快照，支持按控制类型/问题类型/算法/效果筛选。
 * 点击行打开详情抽屉，查看 PID 变化与 KPI 改善明细。
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { KnowledgeBaseApi } from '#/api/tuning';
import type { KpiStripItem } from '#/components/clpm';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import { Drawer, Select, Table, Tag } from 'ant-design-vue';

import { getKnowledgeBaseApi } from '#/api/tuning';
import {
  ClpmColumnSettings,
  ClpmConfidenceBadge,
  ClpmDataCanvas,
  ClpmEmptyState,
  ClpmKpiStrip,
  ClpmPageToolbar,
} from '#/components/clpm';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'TuningKnowledgeBase' });

const { preferences: columnPrefs, updateColumns: persistColumns } =
  usePagePreference('tuning-knowledge-base');

const loading = ref(false);
const recordList = ref<KnowledgeBaseApi.KnowledgeEntry[]>([]);
const total = ref(0);

/** 详情抽屉 */
const drawerVisible = ref(false);
const currentEntry = ref<KnowledgeBaseApi.KnowledgeEntry | null>(null);

const query = reactive({
  loopType: undefined as string | undefined,
  diagnosisLabel: undefined as string | undefined,
  algorithm: undefined as string | undefined,
  effectVerified: undefined as string | undefined,
  page: 1,
  pageSize: 20,
});

// ---------------------------------------------------------------------------
// 列设置
// ---------------------------------------------------------------------------

function buildDefaultColumnConfigs(): ColumnConfig[] {
  return [
    { key: 'tagName', label: '位号', visible: true },
    { key: 'loopType', label: '控制类型', visible: true },
    { key: 'diagnosisLabel', label: '问题类型', visible: true },
    { key: 'modelType', label: '模型/算法', visible: true },
    { key: 'confidenceLevel', label: '可信度', visible: true },
    { key: 'improved', label: '改善/恶化', visible: true },
    { key: 'effectVerified', label: '效果', visible: true },
    { key: 'matchSource', label: '关联方式', visible: false },
    { key: 'implementedAt', label: '实施时间', visible: true },
    { key: 'verifiedAt', label: '验证时间', visible: false },
  ];
}

const columnConfigs = ref<ColumnConfig[]>(
  columnPrefs.value.columns && columnPrefs.value.columns.length > 0
    ? columnPrefs.value.columns
    : buildDefaultColumnConfigs(),
);

function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
  persistColumns(cols);
}

function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
  persistColumns(columnConfigs.value);
}

// ---------------------------------------------------------------------------
// 数据
// ---------------------------------------------------------------------------

/** KPI 条 */
const kpiItems = computed<KpiStripItem[]>(() => {
  const items = recordList.value;
  const improved = items.filter((e) => e.effectVerified === true).length;
  const deteriorated = items.filter((e) => e.effectVerified === false).length;
  const totalImproved = items.reduce(
    (sum, e) => sum + (e.improvedCount ?? 0),
    0,
  );
  const avgImproved = items.length > 0 ? totalImproved / items.length : 0;
  return [
    { key: 'total', label: '总条目', value: total.value, status: 'neutral' },
    { key: 'improved', label: '改善案例', value: improved, status: 'success' },
    {
      key: 'deteriorated',
      label: '恶化案例',
      value: deteriorated,
      status: 'danger',
    },
    {
      key: 'avgImproved',
      label: '平均改善指标数',
      value: Number(avgImproved.toFixed(1)),
      status: 'warning',
    },
  ];
});

/** 控制类型选项 */
const loopTypeOptions = [
  { label: '流量', value: 'FLOW' },
  { label: '压力', value: 'PRESSURE' },
  { label: '温度', value: 'TEMPERATURE' },
  { label: '液位', value: 'LEVEL' },
  { label: '成分', value: 'COMPOSITION' },
];

/** 算法选项 */
const algorithmOptions = [
  { label: 'ARX', value: 'arx' },
  { label: 'ARMAX', value: 'armax' },
  { label: 'IV', value: 'iv' },
  { label: 'IMC', value: 'IMC' },
  { label: 'Lambda', value: 'LAMBDA' },
  { label: 'Z-N', value: 'ZN' },
  { label: 'SIMC', value: 'SIMC' },
];

/** 效果选项（Select 不支持 boolean value，用字符串代理） */
const effectOptions = [
  { label: '改善', value: 'true' },
  { label: '恶化', value: 'false' },
];

/** 获取列 key */
function getColumnKey(col: TableColumnsType[number]): string {
  const c = col as Record<string, unknown>;
  return (c.dataIndex as string) || (c.key as string) || '';
}

/** 表格全部列定义 */
const allColumns: TableColumnsType = [
  {
    title: '位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 120,
    fixed: 'left',
  },
  {
    title: '控制类型',
    dataIndex: 'loopType',
    key: 'loopType',
    width: 100,
    customRender: ({ text }) => {
      const opt = loopTypeOptions.find((o) => o.value === text);
      return opt ? opt.label : (text ?? '-');
    },
  },
  {
    title: '问题类型',
    dataIndex: 'diagnosisLabel',
    key: 'diagnosisLabel',
    width: 120,
    customRender: ({ text }) => {
      if (!text) return '-';
      const colorMap = DIAGNOSIS_LABEL_COLOR_MAP as Record<string, string>;
      const color = colorMap[text] || 'default';
      return h(Tag, { color }, () =>
        getDiagnosisLabelName(
          text as Parameters<typeof getDiagnosisLabelName>[0],
        ),
      );
    },
  },
  {
    title: '模型/算法',
    key: 'modelType',
    width: 140,
    customRender: ({ record }) => {
      const r = record as KnowledgeBaseApi.KnowledgeEntry;
      const parts: string[] = [];
      if (r.modelType) parts.push(r.modelType);
      if (r.algorithm) parts.push(r.algorithm);
      return parts.length > 0 ? parts.join(' / ') : '-';
    },
  },
  {
    title: '可信度',
    dataIndex: 'confidenceLevel',
    key: 'confidenceLevel',
    width: 80,
    customRender: ({ text }) => {
      if (!text) return '-';
      return h(ClpmConfidenceBadge, { level: text as never });
    },
  },
  {
    title: '改善/恶化',
    key: 'improved',
    width: 120,
    customRender: ({ record }) => {
      const r = record as KnowledgeBaseApi.KnowledgeEntry;
      const imp = r.improvedCount ?? 0;
      const det = r.deterioratedCount ?? 0;
      return h(
        'span',
        { class: imp > det ? 'text-green-600' : 'text-red-600' },
        `${imp} / ${det}`,
      );
    },
  },
  {
    title: '效果',
    dataIndex: 'effectVerified',
    key: 'effectVerified',
    width: 80,
    customRender: ({ text }) => {
      if (text === true) return h(Tag, { color: 'green' }, () => '改善');
      if (text === false) return h(Tag, { color: 'red' }, () => '恶化');
      return '-';
    },
  },
  {
    title: '关联方式',
    dataIndex: 'matchSource',
    key: 'matchSource',
    width: 100,
    customRender: ({ text }) => {
      const map: Record<string, string> = {
        exact: '精确匹配',
        time_window: '时间窗口',
        none: '无整定记录',
      };
      return map[text as string] ?? (text as string);
    },
  },
  {
    title: '实施时间',
    dataIndex: 'implementedAt',
    key: 'implementedAt',
    width: 160,
    customRender: ({ text }) => formatTime(text as string),
  },
  {
    title: '验证时间',
    dataIndex: 'verifiedAt',
    key: 'verifiedAt',
    width: 160,
    customRender: ({ text }) => formatTime(text as string),
  },
];

/** 可见列（根据列设置过滤+排序） */
const visibleColumns = computed<TableColumnsType>(() => {
  const configMap = new Map(
    columnConfigs.value.map((c, i) => [
      c.key,
      { visible: c.visible, order: i },
    ]),
  );
  return allColumns
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

/** 分页配置 */
const pagination = computed<TablePaginationConfig>(() => ({
  current: query.page,
  pageSize: query.pageSize,
  total: total.value,
  showSizeChanger: true,
  showTotal: (t) => `共 ${t} 条`,
}));

/** 加载数据 */
async function loadData() {
  loading.value = true;
  try {
    const resp = await getKnowledgeBaseApi({
      loopType: query.loopType,
      diagnosisLabel: query.diagnosisLabel,
      algorithm: query.algorithm,
      effectVerified:
        query.effectVerified === 'true'
          ? true
          : query.effectVerified === 'false'
            ? false
            : undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    recordList.value = resp.items;
    total.value = resp.total;
  } finally {
    loading.value = false;
  }
}

/** 搜索 */
function handleSearch() {
  query.page = 1;
  loadData();
}

/** 分页变更 */
function handlePageChange(pag: TablePaginationConfig) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  loadData();
}

/** 行点击 → 打开详情 */
function handleRowClick(record: KnowledgeBaseApi.KnowledgeEntry) {
  currentEntry.value = record;
  drawerVisible.value = true;
}

/** KPI 对比明细 */
const kpiComparisonList = computed(() => {
  const summary = currentEntry.value?.kpiSummary;
  if (!summary) return [];
  const comparison = (summary as Record<string, unknown>).kpiComparison;
  return Array.isArray(comparison)
    ? (comparison as Array<Record<string, unknown>>)
    : [];
});

/** 格式化数值 */
function fmtNum(val: unknown): string {
  if (val === undefined || val === null) return '-';
  const n = Number(val);
  return Number.isNaN(n) ? '-' : n.toFixed(2);
}

/** 关联方式显示名（提取自模板，避免内联对象字面量） */
function matchSourceLabel(src: string | null | undefined): string {
  if (!src) return '-';
  const map: Record<string, string> = {
    exact: '精确匹配',
    time_window: '时间窗口',
    none: '无整定记录',
  };
  return map[src] ?? src;
}

/** 诊断标签颜色（提取自模板，避免内联类型断言） */
function diagnosisLabelColor(label: string | null | undefined): string {
  if (!label) return 'default';
  const map = DIAGNOSIS_LABEL_COLOR_MAP as Record<string, string>;
  return map[label] || 'default';
}

onMounted(loadData);
</script>

<template>
  <Page :hide-footer="true">
    <ClpmPageToolbar
      title="整定知识库"
      description="验证通过的整定案例自动沉淀，支持按控制类型/问题类型查询相似案例"
    >
      <template #filters>
        <Select
          v-model:value="query.loopType"
          :options="loopTypeOptions"
          allow-clear
          placeholder="控制类型"
          style="width: 120px"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.diagnosisLabel"
          :options="DIAGNOSIS_LABEL_OPTIONS"
          allow-clear
          placeholder="问题类型"
          style="width: 140px"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.algorithm"
          :options="algorithmOptions"
          allow-clear
          placeholder="算法"
          style="width: 120px"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.effectVerified"
          :options="effectOptions"
          allow-clear
          placeholder="效果"
          style="width: 100px"
          @change="handleSearch"
        />
      </template>
      <template #extra>
        <ClpmColumnSettings
          :columns="columnConfigs"
          @update:columns="handleUpdateColumns"
          @reset="handleResetColumns"
        />
      </template>
    </ClpmPageToolbar>

    <ClpmKpiStrip :items="kpiItems" />

    <ClpmDataCanvas>
      <Table
        :columns="visibleColumns"
        :data-source="recordList"
        :loading="loading"
        :pagination="pagination"
        :scroll="{ x: 1100 }"
        row-key="id"
        size="small"
        @change="handlePageChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tagName'">
            <a
              class="text-primary cursor-pointer"
              @click="handleRowClick(record as KnowledgeBaseApi.KnowledgeEntry)"
            >
              {{ record.tagName }}
            </a>
          </template>
        </template>
      </Table>

      <ClpmEmptyState
        v-if="!loading && recordList.length === 0"
        title="暂无知识库条目"
        description="整定案例验证通过后将自动沉淀到知识库"
      />
    </ClpmDataCanvas>

    <!-- 详情抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      title="知识库条目详情"
      placement="right"
      :width="560"
    >
      <template v-if="currentEntry">
        <!-- 基本信息 -->
        <div class="mb-6">
          <h3 class="mb-3 text-base font-semibold">基本信息</h3>
          <div class="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span class="text-muted-foreground">位号：</span>
              {{ currentEntry.tagName }}
            </div>
            <div>
              <span class="text-muted-foreground">控制类型：</span>
              {{
                loopTypeOptions.find((o) => o.value === currentEntry!.loopType)
                  ?.label ??
                currentEntry.loopType ??
                '-'
              }}
            </div>
            <div>
              <span class="text-muted-foreground">问题类型：</span>
              <Tag
                v-if="currentEntry.diagnosisLabel"
                :color="diagnosisLabelColor(currentEntry.diagnosisLabel)"
              >
                {{
                  getDiagnosisLabelName(currentEntry.diagnosisLabel as never)
                }}
              </Tag>
              <span v-else>-</span>
            </div>
            <div>
              <span class="text-muted-foreground">严重度：</span>
              {{ currentEntry.severity ?? '-' }}
            </div>
            <div>
              <span class="text-muted-foreground">关联方式：</span>
              {{ matchSourceLabel(currentEntry.matchSource) }}
            </div>
            <div>
              <span class="text-muted-foreground">效果：</span>
              <Tag v-if="currentEntry.effectVerified === true" color="green">
                改善
              </Tag>
              <Tag
                v-else-if="currentEntry.effectVerified === false"
                color="red"
              >
                恶化
              </Tag>
              <span v-else>-</span>
            </div>
          </div>
        </div>

        <!-- 整定元数据 -->
        <div class="mb-6">
          <h3 class="mb-3 text-base font-semibold">整定元数据</h3>
          <div class="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span class="text-muted-foreground">模型类型：</span>
              {{ currentEntry.modelType ?? '-' }}
            </div>
            <div>
              <span class="text-muted-foreground">算法：</span>
              {{ currentEntry.algorithm ?? '-' }}
            </div>
            <div>
              <span class="text-muted-foreground">辨识方法：</span>
              {{ currentEntry.identifyMethod ?? '-' }}
            </div>
            <div>
              <span class="text-muted-foreground">可信度：</span>
              <ClpmConfidenceBadge
                v-if="currentEntry.confidenceLevel"
                :level="currentEntry.confidenceLevel as never"
              />
              <span v-else>-</span>
            </div>
          </div>
        </div>

        <!-- PID 变化 -->
        <div class="mb-6">
          <h3 class="mb-3 text-base font-semibold">PID 参数变化</h3>
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b">
                <th class="py-2 text-left">参数</th>
                <th class="py-2 text-right">变更前</th>
                <th class="py-2 text-right">变更后</th>
              </tr>
            </thead>
            <tbody>
              <tr class="border-b">
                <td class="py-2">P</td>
                <td class="py-2 text-right">
                  {{ currentEntry.pidBefore?.p ?? '-' }}
                </td>
                <td class="py-2 text-right font-semibold text-blue-600">
                  {{ currentEntry.pidAfter?.p ?? '-' }}
                </td>
              </tr>
              <tr class="border-b">
                <td class="py-2">I</td>
                <td class="py-2 text-right">
                  {{ currentEntry.pidBefore?.i ?? '-' }}
                </td>
                <td class="py-2 text-right font-semibold text-blue-600">
                  {{ currentEntry.pidAfter?.i ?? '-' }}
                </td>
              </tr>
              <tr class="border-b">
                <td class="py-2">D</td>
                <td class="py-2 text-right">
                  {{ currentEntry.pidBefore?.d ?? '-' }}
                </td>
                <td class="py-2 text-right font-semibold text-blue-600">
                  {{ currentEntry.pidAfter?.d ?? '-' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- KPI 改善明细 -->
        <div v-if="kpiComparisonList.length > 0" class="mb-6">
          <h3 class="mb-3 text-base font-semibold">
            KPI 改善明细（{{ currentEntry.improvedCount ?? 0 }} 改善 /
            {{ currentEntry.deterioratedCount ?? 0 }} 恶化）
          </h3>
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b">
                <th class="py-2 text-left">指标</th>
                <th class="py-2 text-right">变更前</th>
                <th class="py-2 text-right">变更后</th>
                <th class="py-2 text-right">变化</th>
                <th class="py-2 text-center">结果</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(item, idx) in kpiComparisonList"
                :key="idx"
                class="border-b"
              >
                <td class="py-2">{{ item.metricName ?? item.metricKey }}</td>
                <td class="py-2 text-right">{{ fmtNum(item.before) }}</td>
                <td class="py-2 text-right">{{ fmtNum(item.after) }}</td>
                <td class="py-2 text-right">{{ fmtNum(item.change) }}</td>
                <td class="py-2 text-center">
                  <IconifyIcon
                    v-if="item.improved === true"
                    class="text-green-600"
                    icon="lucide:check"
                  />
                  <IconifyIcon
                    v-else-if="item.improved === false"
                    class="text-red-600"
                    icon="lucide:x"
                  />
                  <span v-else class="text-muted-foreground">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 时间信息 -->
        <div class="text-sm text-muted-foreground">
          <div v-if="currentEntry.implementedAt">
            实施时间：{{ formatTime(currentEntry.implementedAt) }}
          </div>
          <div v-if="currentEntry.verifiedAt">
            验证时间：{{ formatTime(currentEntry.verifiedAt) }}
          </div>
          <div v-if="currentEntry.createdAt">
            入库时间：{{ formatTime(currentEntry.createdAt) }}
          </div>
        </div>
      </template>
    </Drawer>
  </Page>
</template>
