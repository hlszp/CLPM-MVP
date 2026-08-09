<script lang="ts" setup>
/**
 * 关注队列页面（整改方案 §8.1 / MW-P2-06~09）
 *
 * 统一聚合五类关注来源：ALERT / DEGRADATION / DATA_QUALITY / TRACKER / VERIFICATION
 * 服务端按角色生成 primaryAction/actions，前端直接使用不做权限推断。
 *
 * 深链接：?eventId= 自动打开目标详情；?source=ALERT 按 ALERT 筛选。
 * 动作复用：确认/处置/误报/归档 调用现有 alert API，不另建状态机。
 * Sponsor 只读：服务端不返回 OPEN_WORKBENCH/写动作，前端仅渲染返回的 actions。
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { MonitorApi } from '#/api/monitor';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, h, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Drawer,
  FormItem,
  Input,
  message,
  Modal,
  Space,
  Table,
  Tag,
  Textarea,
  Tooltip,
} from 'ant-design-vue';

import {
  acknowledgeEventApi,
  markFalsePositiveApi,
  resolveEventApi,
} from '#/api/alert';
import { getAttentionListApi } from '#/api/monitor';
import {
  ClpmEmptyState,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'MonitorAttention' });

const route = useRoute();
const router = useRouter();

// ===== 语义映射（不暴露裸英文枚举）=====
const PRIORITY_LABEL: Record<MonitorApi.AttentionPriority, string> = {
  URGENT: '紧急',
  HIGH: '高',
  MEDIUM: '中',
  LOW: '低',
};

const PRIORITY_COLOR: Record<MonitorApi.AttentionPriority, string> = {
  URGENT: 'red',
  HIGH: 'volcano',
  MEDIUM: 'orange',
  LOW: 'blue',
};

const SOURCE_LABEL: Record<MonitorApi.AttentionSource, string> = {
  ALERT: '活跃预警',
  DEGRADATION: '评分恶化',
  DATA_QUALITY: '数据质量',
  TRACKER: '待处置工单',
  VERIFICATION: '验证超期',
};

const SOURCE_COLOR: Record<MonitorApi.AttentionSource, string> = {
  ALERT: 'red',
  DEGRADATION: 'orange',
  DATA_QUALITY: 'purple',
  TRACKER: 'cyan',
  VERIFICATION: 'magenta',
};

const STATUS_LABEL: Record<MonitorApi.AttentionStatus, string> = {
  OPEN: '待处理',
  ACKNOWLEDGED: '已确认',
  SUPPRESSED: '已抑制',
  IN_PROGRESS: '处理中',
  VERIFYING: '验证中',
};

const STATUS_COLOR: Record<MonitorApi.AttentionStatus, string> = {
  OPEN: 'red',
  ACKNOWLEDGED: 'orange',
  SUPPRESSED: 'default',
  IN_PROGRESS: 'processing',
  VERIFYING: 'warning',
};

const PRIORITY_ORDER: MonitorApi.AttentionPriority[] = [
  'URGENT',
  'HIGH',
  'MEDIUM',
  'LOW',
];
const SOURCE_ORDER: MonitorApi.AttentionSource[] = [
  'ALERT',
  'DEGRADATION',
  'DATA_QUALITY',
  'TRACKER',
  'VERIFICATION',
];
/** 来源合法值集合（用于深链接参数校验） */
const SOURCE_SET = new Set<string>(SOURCE_ORDER);

// ===== 表格密度三档（持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('monitor-attention');

// ===== 列表状态 =====
const loading = ref(false);
const attentionList = ref<MonitorApi.AttentionItem[]>([]);
const total = ref(0);
const aggregates = ref<MonitorApi.AttentionAggregates>({
  byPriority: {},
  bySource: {},
  byStatus: {},
});
const lastRefresh = ref('');

const query = reactive({
  plantNodeId: undefined as string | undefined,
  source: [] as MonitorApi.AttentionSource[],
  priority: [] as MonitorApi.AttentionPriority[],
  status: [] as MonitorApi.AttentionStatus[],
  keyword: '',
  page: 1,
  pageSize: 20,
});

// 筛选区折叠态（工具栏「筛选」工具切换）
const filterVisible = ref(true);

// ===== 详情抽屉 =====
const detailVisible = ref(false);
const currentItem = ref<MonitorApi.AttentionItem | null>(null);

// ===== 处置弹窗 =====
const resolveVisible = ref(false);
const resolveNote = ref('');
const resolvingAttentionId = ref('');
const resolvingEventId = ref('');
const resolveLoading = ref(false);

// ===== 行内动作防重复提交 =====
const actingAttentionId = ref('');

// ===== 列定义 =====
const columns: TableColumnsType = [
  {
    title: '优先级',
    dataIndex: 'priority',
    key: 'priority',
    width: 80,
    fixed: 'left',
    customRender: ({ value }) =>
      h(
        Tag,
        { color: PRIORITY_COLOR[value as MonitorApi.AttentionPriority] },
        () => PRIORITY_LABEL[value as MonitorApi.AttentionPriority] ?? value,
      ),
  },
  {
    title: '来源',
    dataIndex: 'source',
    key: 'source',
    width: 110,
    customRender: ({ value }) =>
      h(
        Tag,
        { color: SOURCE_COLOR[value as MonitorApi.AttentionSource] },
        () => SOURCE_LABEL[value as MonitorApi.AttentionSource] ?? value,
      ),
  },
  {
    title: '回路',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 140,
    ellipsis: true,
  },
  {
    title: '摘要',
    dataIndex: 'summary',
    key: 'summary',
    ellipsis: true,
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 90,
    customRender: ({ value }) =>
      h(
        Tag,
        { color: STATUS_COLOR[value as MonitorApi.AttentionStatus] },
        () => STATUS_LABEL[value as MonitorApi.AttentionStatus] ?? value,
      ),
  },
  {
    title: '发生时间',
    dataIndex: 'occurredAt',
    key: 'occurredAt',
    width: 170,
    customRender: ({ value }) => formatTime(value),
  },
  {
    title: '排序原因',
    dataIndex: 'rankReasons',
    key: 'rankReasons',
    width: 200,
    ellipsis: true,
    customRender: ({ value }) => (value as string[])?.join('；') ?? '-',
  },
  {
    title: '操作',
    key: 'actions',
    width: 160,
    fixed: 'right',
  },
];

// ===== 列设置（排除「操作」列，其始终可见不可隐藏） =====
function buildDefaultColumnConfigs(): ColumnConfig[] {
  return columns
    .filter((c: any) => c.key !== 'actions')
    .map((c: any, i: number) => ({
      key: String(c.key),
      label: String(c.title ?? ''),
      visible: true,
      order: i,
    }));
}
const columnConfigs = ref<ColumnConfig[]>(buildDefaultColumnConfigs());
const visibleColumns = computed<TableColumnsType>(() =>
  columns.filter((c: any) => {
    if (c.key === 'actions') return true;
    const cfg = columnConfigs.value.find((cc) => cc.key === c.key);
    return cfg ? cfg.visible : true;
  }),
);
function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
}
function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
}

// ===== 数据加载 =====
async function loadData() {
  loading.value = true;
  try {
    const res = await getAttentionListApi({
      plantNodeId: query.plantNodeId || undefined,
      source: query.source.length > 0 ? query.source : undefined,
      priority: query.priority.length > 0 ? query.priority : undefined,
      status: query.status.length > 0 ? query.status : undefined,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    attentionList.value = res.items;
    total.value = res.total;
    aggregates.value = res.aggregates;
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  } catch (error: any) {
    message.error(error?.message ?? '加载关注队列失败');
    attentionList.value = [];
  } finally {
    loading.value = false;
  }
}

// ===== 筛选切换 =====
function handleFilterChange() {
  query.page = 1;
  loadData();
}

function toggleArrayFilter<T>(arr: T[], val: T): T[] {
  return arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];
}

function toggleSource(s: MonitorApi.AttentionSource) {
  query.source = toggleArrayFilter(query.source, s);
  handleFilterChange();
}

function togglePriority(p: MonitorApi.AttentionPriority) {
  query.priority = toggleArrayFilter(query.priority, p);
  handleFilterChange();
}

function handleKeywordSearch() {
  query.page = 1;
  loadData();
}

function handleResetFilters() {
  query.source = [];
  query.priority = [];
  query.status = [];
  query.keyword = '';
  query.page = 1;
  loadData();
}

// ===== 分页 =====
function handlePageChange(pag: TablePaginationConfig) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  loadData();
}

// ===== 详情抽屉 =====
function openDetail(item: MonitorApi.AttentionItem) {
  currentItem.value = item;
  detailVisible.value = true;
}

// ===== 动作执行 =====
async function executeAction(
  item: MonitorApi.AttentionItem,
  action: MonitorApi.AttentionAction,
) {
  if (!action.enabled) {
    if (action.disabledReason) {
      message.warning(action.disabledReason);
    }
    return;
  }

  // 跳转类动作
  if (
    action.target &&
    (action.type === 'OPEN_WORKBENCH' ||
      action.type === 'VIEW_ALERT_HISTORY' ||
      action.type === 'BACK_TO_OVERVIEW' ||
      action.type === 'VIEW_DETAIL')
  ) {
    router.push({
      path: action.target.route,
      query: action.target.query,
    });
    return;
  }

  // ALERT 来源写操作——复用现有 alert API
  if (item.source === 'ALERT' && item.eventId) {
    if (actingAttentionId.value) return;
    actingAttentionId.value = item.attentionId;
    try {
      switch (action.type) {
        case 'ACKNOWLEDGE': {
          await acknowledgeEventApi(item.eventId);
          message.success('事件已确认');
          break;
        }
        case 'MARK_FALSE_POSITIVE': {
          await markFalsePositiveApi(item.eventId, true);
          message.success('已标记误报');
          break;
        }
        default: {
          break;
        }
      }
      await loadData();
      if (currentItem.value?.attentionId === item.attentionId) {
        detailVisible.value = false;
      }
    } catch (error: any) {
      message.error(error?.message ?? '操作失败');
    } finally {
      actingAttentionId.value = '';
    }
  }
}

// 处置动作：打开弹窗收集处置说明（与预警事件页口径一致）
function openResolveModal(item: MonitorApi.AttentionItem) {
  if (!item.eventId) return;
  resolvingAttentionId.value = item.attentionId;
  resolvingEventId.value = item.eventId;
  resolveNote.value = '';
  resolveVisible.value = true;
}

async function handleResolveSubmit() {
  if (!resolveNote.value.trim()) {
    message.warning('请填写处置说明');
    return;
  }
  if (resolveLoading.value) return;
  resolveLoading.value = true;
  try {
    await resolveEventApi(resolvingEventId.value, resolveNote.value);
    message.success('事件已处置');
    resolveVisible.value = false;
    await loadData();
    if (currentItem.value?.attentionId === resolvingAttentionId.value) {
      detailVisible.value = false;
    }
  } catch (error: any) {
    message.error(error?.message ?? '处置失败');
  } finally {
    resolveLoading.value = false;
  }
}

// 归档动作由「预警记录」页承载（VIEW_ALERT_HISTORY 跳转），关注队列不直接归档。

// ===== 导出当前筛选结果为 CSV（客户端生成，无需后端接口）=====
function exportAttentionCsv() {
  if (attentionList.value.length === 0) {
    message.warning('当前无可导出的数据');
    return;
  }
  const header = [
    '优先级',
    '来源',
    '回路',
    '摘要',
    '状态',
    '发生时间',
    '排序原因',
  ];
  const rows = attentionList.value.map((i) => [
    PRIORITY_LABEL[i.priority] ?? i.priority,
    SOURCE_LABEL[i.source] ?? i.source,
    i.tagName,
    i.summary,
    STATUS_LABEL[i.status] ?? i.status,
    formatTime(i.occurredAt),
    (i.rankReasons ?? []).join('；'),
  ]);
  const csv = [header, ...rows]
    .map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `attention-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  message.success(`已导出 ${attentionList.value.length} 条关注项`);
}

function handleHelp() {
  showPageHelp({
    title: '关注队列 帮助',
    content:
      '关注队列统一聚合五类当前行动项：活跃预警、评分恶化、数据质量异常、待处置工单、验证超期。按优先级和来源筛选；点击「详情」查看完整信息；对预警事件可执行确认/处置/误报/归档。仅承载当前行动项，历史记录请查看「预警记录」。',
  });
}

function toggleFilter() {
  filterVisible.value = !filterVisible.value;
}

// 跳转预警记录（历史/审计/导出入口）
function goToAlertHistory() {
  router.push({
    path: '/monitor/alerts',
    query: query.source.includes('ALERT')
      ? { loopId: query.plantNodeId || undefined }
      : {},
  });
}

// ===== 统一工具栏（标准 5 工具：刷新/筛选/导出/列设置/帮助）=====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadData, loading: loading.value },
  filter: { onClick: () => toggleFilter(), active: filterVisible.value },
  export: {
    onClick: exportAttentionCsv,
    permission: ['ADMIN', 'IC_ENGINEER'],
    disabledReason: '仅工程师/管理员可导出',
  },
  setting: {},
  help: { onClick: handleHelp },
}));

// ===== 深链接：?eventId= 自动打开目标详情；?source= 初始筛选 =====
function applyUrlContext() {
  const sourceParam = route.query.source as string | undefined;
  const eventIdParam = route.query.eventId as string | undefined;
  if (sourceParam && SOURCE_SET.has(sourceParam)) {
    query.source = [sourceParam as MonitorApi.AttentionSource];
  }
  return eventIdParam;
}

// 深链接 eventId：数据加载后在列表中查找并打开详情
function tryOpenDetailByEventId(eventId: string) {
  const target = attentionList.value.find((i) => i.eventId === eventId);
  if (target) {
    openDetail(target);
  }
}

// ===== 生命周期 =====
onMounted(async () => {
  const eventId = applyUrlContext();
  await loadData();
  if (eventId) {
    tryOpenDetailByEventId(eventId);
  }
});

// URL query 变化时重新加载（浏览器前进/后退/铃铛深链接）
watch(
  () => route.query,
  (q) => {
    const newSource = q.source as string | undefined;
    const newEventId = q.eventId as string | undefined;
    if (
      newSource &&
      SOURCE_SET.has(newSource) &&
      !query.source.includes(newSource as MonitorApi.AttentionSource)
    ) {
      query.source = [newSource as MonitorApi.AttentionSource];
      query.page = 1;
      loadData().then(() => {
        if (newEventId) tryOpenDetailByEventId(newEventId);
      });
    } else if (newEventId) {
      tryOpenDetailByEventId(newEventId);
    }
  },
);
</script>

<template>
  <Page>
    <!-- 统一工具栏（吸顶 · 右对齐 · 彩色语义图标） -->
    <ClpmPageToolbar
      title="关注队列"
      subtitle="当前行动项——预警/恶化/质量/工单/验证五合一"
      :loading="loading"
      :last-refresh="lastRefresh"
    >
      <template #actions>
        <ClpmStandardActions
          :items="toolbarItems"
          :column-configs="columnConfigs"
          @update:columns="handleUpdateColumns"
          @reset-columns="handleResetColumns"
        />
        <!-- 密度三档切换 -->
        <ClpmToolbarButton
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 筛选区（工具栏「筛选」工具可折叠） -->
    <div
      class="clpm-filter-bar"
      :class="{ 'clpm-filter-bar--collapsed': !filterVisible }"
    >
      <!-- 优先级标签筛选 -->
      <div class="flex flex-wrap items-center gap-1.5">
        <span class="text-xs text-gray-500">优先级</span>
        <Tag
          v-for="p in PRIORITY_ORDER"
          :key="p"
          :color="query.priority.includes(p) ? PRIORITY_COLOR[p] : 'default'"
          class="cursor-pointer"
          @click="togglePriority(p)"
        >
          {{ PRIORITY_LABEL[p] }}
          <span v-if="aggregates.byPriority[p]" class="ml-1 opacity-70">
            {{ aggregates.byPriority[p] }}
          </span>
        </Tag>
      </div>

      <!-- 来源标签筛选 -->
      <div class="flex flex-wrap items-center gap-1.5">
        <span class="text-xs text-gray-500">来源</span>
        <Tag
          v-for="s in SOURCE_ORDER"
          :key="s"
          :color="query.source.includes(s) ? SOURCE_COLOR[s] : 'default'"
          class="cursor-pointer"
          @click="toggleSource(s)"
        >
          {{ SOURCE_LABEL[s] }}
          <span v-if="aggregates.bySource[s]" class="ml-1 opacity-70">
            {{ aggregates.bySource[s] }}
          </span>
        </Tag>
      </div>

      <FormItem label="关键词" class="!mb-0">
        <Input
          v-model:value="query.keyword"
          allow-clear
          placeholder="位号 / 标题"
          style="width: 200px"
          @press-enter="handleKeywordSearch"
        />
      </FormItem>

      <Space class="!ml-auto">
        <Button type="primary" @click="handleKeywordSearch">查询</Button>
        <Button @click="handleResetFilters">重置</Button>
        <Button type="link" size="small" @click="goToAlertHistory">
          查看预警记录
        </Button>
      </Space>
    </div>

    <!-- 关注队列表格 -->
    <Table
      :columns="visibleColumns"
      :data-source="attentionList"
      :loading="loading"
      :pagination="{
        current: query.page,
        pageSize: query.pageSize,
        total,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      :size="tableSize"
      :scroll="{ x: 1200 }"
      row-key="attentionId"
      @change="handlePageChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'actions'">
          <Space :size="4">
            <Button
              type="link"
              size="small"
              @click="openDetail(record as MonitorApi.AttentionItem)"
            >
              详情
            </Button>
            <Button
              v-if="
                record.primaryAction &&
                record.primaryAction.type !== 'VIEW_DETAIL' &&
                record.primaryAction.type !== 'OPEN_WORKBENCH'
              "
              type="link"
              size="small"
              :disabled="!record.primaryAction.enabled || !!actingAttentionId"
              @click="
                executeAction(
                  record as MonitorApi.AttentionItem,
                  record.primaryAction,
                )
              "
            >
              {{ record.primaryAction.label }}
            </Button>
            <Button
              v-if="
                record.source === 'ALERT' &&
                record.eventId &&
                record.actions?.some(
                  (a: MonitorApi.AttentionAction) =>
                    a.type === 'OPEN_WORKBENCH',
                )
              "
              type="link"
              size="small"
              @click="
                executeAction(
                  record as MonitorApi.AttentionItem,
                  (record.actions as MonitorApi.AttentionAction[]).find(
                    (a) => a.type === 'OPEN_WORKBENCH',
                  )!,
                )
              "
            >
              进入工作台
            </Button>
          </Space>
        </template>
      </template>

      <template #emptyText>
        <ClpmEmptyState
          scene="tracker"
          title="暂无关注项"
          description="当前没有需要处理的预警、评分恶化、数据质量异常或开放工单。"
          :actions="[
            {
              label: '查看预警记录',
              icon: 'lucide:history',
              onClick: goToAlertHistory,
            },
          ]"
        />
      </template>
    </Table>

    <!-- 详情抽屉 -->
    <Drawer
      v-model:open="detailVisible"
      title="关注项详情"
      width="540"
      :destroy-on-close="true"
    >
      <template v-if="currentItem">
        <Descriptions :column="1" bordered size="small">
          <DescriptionsItem label="优先级">
            <Tag :color="PRIORITY_COLOR[currentItem.priority]">
              {{ PRIORITY_LABEL[currentItem.priority] }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="来源">
            <Tag :color="SOURCE_COLOR[currentItem.source]">
              {{ SOURCE_LABEL[currentItem.source] }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="状态">
            <Tag :color="STATUS_COLOR[currentItem.status]">
              {{ STATUS_LABEL[currentItem.status] }}
            </Tag>
            <span class="ml-2 text-xs text-gray-400">
              来源状态：{{ currentItem.sourceStatus }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="回路">
            {{ currentItem.tagName }}
          </DescriptionsItem>
          <DescriptionsItem label="标题">
            {{ currentItem.title }}
          </DescriptionsItem>
          <DescriptionsItem label="摘要">
            {{ currentItem.summary }}
          </DescriptionsItem>
          <DescriptionsItem label="排序原因">
            <div v-for="reason in currentItem.rankReasons" :key="reason">
              · {{ reason }}
            </div>
          </DescriptionsItem>
          <DescriptionsItem label="发生时间">
            {{ formatTime(currentItem.occurredAt) }}
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.updatedAt" label="更新时间">
            {{ formatTime(currentItem.updatedAt) }}
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.score !== undefined" label="评分">
            {{ currentItem.score }}
            <span v-if="currentItem.scoreDelta !== undefined" class="ml-2">
              <Tag :color="currentItem.scoreDelta <= -5 ? 'red' : 'orange'">
                {{ currentItem.scoreDelta > 0 ? '+' : ''
                }}{{ currentItem.scoreDelta }}
              </Tag>
            </span>
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.confidenceLevel" label="可信度">
            {{ currentItem.confidenceLevel }}
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.sourceSeverity" label="严重度">
            {{ currentItem.sourceSeverity }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 动作区：服务端按角色生成，Sponsor 仅 VIEW_DETAIL/BACK_TO_OVERVIEW -->
        <div class="mt-4">
          <div class="mb-2 text-sm font-medium text-gray-700">可用操作</div>
          <Space wrap>
            <Button
              v-for="action in currentItem.actions"
              :key="action.type"
              :type="
                action.type === currentItem.primaryAction.type
                  ? 'primary'
                  : 'default'
              "
              :disabled="!action.enabled || !!actingAttentionId"
              :loading="actingAttentionId === currentItem.attentionId"
              size="small"
              @click="
                action.type === 'RESOLVE'
                  ? openResolveModal(currentItem)
                  : executeAction(currentItem, action)
              "
            >
              {{ action.label }}
            </Button>
          </Space>
          <div
            v-for="action in currentItem.actions.filter(
              (a) => !a.enabled && a.disabledReason,
            )"
            :key="`reason-${action.type}`"
            class="mt-2 text-xs text-gray-400"
          >
            {{ action.label }}：{{ action.disabledReason }}
          </div>
        </div>

        <!-- 安全边界提示（不使用 Emoji，用图标+文字） -->
        <div
          class="mt-4 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-700"
        >
          <Tooltip>
            <template #title>
              平台只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕。
            </template>
            <span class="inline-flex items-center gap-1">
              <IconifyIcon icon="lucide:shield-alert" :size="13" />
              平台安全边界：只读建议、人工实施、需留痕
            </span>
          </Tooltip>
        </div>
      </template>
    </Drawer>

    <!-- 处置弹窗 -->
    <Modal
      v-model:open="resolveVisible"
      title="处置预警事件"
      :confirm-loading="resolveLoading"
      ok-text="提交处置"
      cancel-text="取消"
      @ok="handleResolveSubmit"
    >
      <div class="py-2">
        <p class="mb-2 text-sm text-gray-600">
          请填写处置说明（必填），提交后事件状态将变为「已处置」。
        </p>
        <Textarea
          v-model:value="resolveNote"
          :rows="4"
          placeholder="例如：检查发现阀门存在粘滞，已联系仪表人员检修并调整 PID 参数..."
          :maxlength="500"
          show-count
        />
      </div>
    </Modal>
  </Page>
</template>

<style scoped>
.clpm-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: hsl(var(--card) / 60%);
  border-radius: 6px;
}

.clpm-filter-bar--collapsed {
  display: none;
}
</style>
