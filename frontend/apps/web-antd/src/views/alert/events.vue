<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { AlertApi } from '#/api/alert';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, h, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Badge,
  Button,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Select,
  Space,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  acknowledgeEventApi,
  archiveEventApi,
  getAlertBadgeApi,
  getAlertEventsApi,
  markFalsePositiveApi,
  resetAlertBadgeApi,
  resolveEventApi,
} from '#/api/alert';
import {
  ClpmDangerConfirmModal,
  ClpmEmptyState, ClpmPageToolbar, ClpmStandardActions 
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { SEVERITY_LABEL } from '#/constants/clpm-ui';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'AlertEvents' });

// 严重度中文标签（对齐 clpm-ui.ts 统一映射，与诊断/跟踪模块共用）
const severityLabel = SEVERITY_LABEL;

const userStore = useUserStore();
const canEdit = computed(() =>
  ['ADMIN', 'IC_ENGINEER'].includes(userStore.userInfo?.roles?.[0] ?? ''),
);
const canArchive = computed(() => userStore.userInfo?.roles?.[0] === 'ADMIN');

// 列表状态
const loading = ref(false);
const eventList = ref<AlertApi.EventItem[]>([]);
const total = ref(0);
const query = reactive({
  status: undefined as AlertApi.EventStatus | undefined,
  severity: undefined as AlertApi.Severity | undefined,
  loopId: '',
  page: 1,
  pageSize: 20,
});

// 徽章
const badgeCount = ref(0);

// 筛选区折叠态（工具栏「筛选」工具切换）
const filterVisible = ref(true);

// 最近刷新时间（工具栏状态反馈区）
const lastRefresh = ref('');

// 详情抽屉
const detailVisible = ref(false);
const currentEvent = ref<AlertApi.EventItem | null>(null);

// 处置弹窗
const resolveVisible = ref(false);
const resolveNote = ref('');
const resolvingEventId = ref('');

// 严重度颜色映射
const severityColor: Record<AlertApi.Severity, string> = {
  CRITICAL: 'red',
  ERROR: 'volcano',
  WARN: 'orange',
  INFO: 'blue',
};

const statusColor: Record<AlertApi.EventStatus, string> = {
  ACTIVE: 'red',
  ACKNOWLEDGED: 'orange',
  RESOLVED: 'green',
  SUPPRESSED: 'default',
  ARCHIVED: 'default',
};

const statusLabel: Record<AlertApi.EventStatus, string> = {
  ACTIVE: '待确认',
  ACKNOWLEDGED: '已确认',
  RESOLVED: '已处置',
  SUPPRESSED: '已抑制',
  ARCHIVED: '已归档',
};

const columns: TableColumnsType = [
  {
    title: '回路',
    dataIndex: 'loopName',
    key: 'loopName',
    width: 140,
    ellipsis: true,
    customRender: ({ record }) => record.loopName || record.loopId.slice(0, 8),
  },
  {
    title: '规则',
    dataIndex: 'ruleCode',
    key: 'ruleCode',
    width: 160,
    ellipsis: true,
  },
  {
    title: '严重度',
    dataIndex: 'severity',
    key: 'severity',
    width: 90,
    customRender: ({ value }) =>
      h(
        Tag,
        { color: severityColor[value as AlertApi.Severity] },
        () => severityLabel[value as AlertApi.Severity] ?? value,
      ),
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 90,
    customRender: ({ value }) =>
      h(
        Tag,
        { color: statusColor[value as AlertApi.EventStatus] },
        () => statusLabel[value as AlertApi.EventStatus] ?? value,
      ),
  },
  {
    title: '触发值',
    dataIndex: 'triggeredValue',
    key: 'triggeredValue',
    width: 100,
    customRender: ({ value }) =>
      value == null ? '-' : Number(value).toFixed(3),
  },
  {
    title: '触发时间',
    dataIndex: 'triggeredAt',
    key: 'triggeredAt',
    width: 170,
    customRender: ({ value }) => formatTime(value),
  },
  {
    title: '重复次数',
    dataIndex: 'triggerCount',
    key: 'triggerCount',
    width: 90,
  },
  { title: '操作', key: 'action', width: 200, fixed: 'right' },
];

// ===== 列设置（列配置：排除「操作」列，其始终可见不可隐藏） =====
function buildDefaultColumnConfigs(): ColumnConfig[] {
  return columns
    .filter((c: any) => c.key !== 'action')
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
    if (c.key === 'action') return true;
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

async function loadEvents() {
  loading.value = true;
  try {
    const params: AlertApi.EventListParams = {
      status: query.status,
      severity: query.severity,
      loopId: query.loopId || undefined,
      limit: query.pageSize,
      offset: (query.page - 1) * query.pageSize,
    };
    const res = await getAlertEventsApi(params);
    eventList.value = res.items;
    total.value = res.total;
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  } catch {
    message.error('加载预警事件失败');
  } finally {
    loading.value = false;
  }
}

async function loadBadge() {
  try {
    const res = await getAlertBadgeApi();
    badgeCount.value = res.count;
  } catch {
    // 静默失败
  }
}

function handleSearch() {
  query.page = 1;
  loadEvents();
}

function handleReset() {
  query.status = undefined;
  query.severity = undefined;
  query.loopId = '';
  query.page = 1;
  loadEvents();
}

function handlePageChange(pag: TablePaginationConfig) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  loadEvents();
}

function showDetail(record: AlertApi.EventItem) {
  currentEvent.value = record;
  detailVisible.value = true;
}

async function handleAcknowledge(record: AlertApi.EventItem) {
  try {
    await acknowledgeEventApi(record.eventId);
    message.success('事件已确认');
    await loadEvents();
  } catch {
    message.error('确认失败');
  }
}

function openResolveModal(record: AlertApi.EventItem) {
  resolvingEventId.value = record.eventId;
  resolveNote.value = '';
  resolveVisible.value = true;
}

async function handleResolve() {
  if (!resolveNote.value.trim()) {
    message.warning('请填写处置说明');
    return;
  }
  try {
    await resolveEventApi(resolvingEventId.value, resolveNote.value);
    message.success('事件已处置');
    resolveVisible.value = false;
    await loadEvents();
  } catch {
    message.error('处置失败');
  }
}

async function handleMarkFalsePositive(record: AlertApi.EventItem) {
  try {
    await markFalsePositiveApi(record.eventId, true);
    message.success('已标记为误报');
    await loadEvents();
  } catch {
    message.error('标记失败');
  }
}

/** 归档事件：危险确认弹窗（归档后不可再操作、无恢复入口，按不可逆处理） */
const archiveOpen = ref(false);
const archiveTarget = ref<AlertApi.EventItem | null>(null);
const archiveLoading = ref(false);

function handleArchive(record: AlertApi.EventItem) {
  archiveTarget.value = record;
  archiveOpen.value = true;
}

async function handleArchiveConfirm() {
  if (!archiveTarget.value) return;
  archiveLoading.value = true;
  try {
    await archiveEventApi(archiveTarget.value.eventId);
    message.success('事件已归档');
    archiveOpen.value = false;
    await loadEvents();
  } catch {
    message.error('归档失败');
  } finally {
    archiveLoading.value = false;
  }
}

async function handleResetBadge() {
  try {
    await resetAlertBadgeApi();
    badgeCount.value = 0;
  } catch {
    // 静默
  }
}

/** 导出当前筛选结果为 CSV（客户端生成，无需后端接口） */
function exportEventsCsv() {
  if (eventList.value.length === 0) {
    message.warning('当前无可导出的数据');
    return;
  }
  const header = [
    '回路',
    '规则代码',
    '严重度',
    '状态',
    '触发值',
    '触发时间',
    '重复次数',
  ];
  const rows = eventList.value.map((e) => [
    e.loopName || e.loopId,
    e.ruleCode,
    severityLabel[e.severity] ?? e.severity,
    statusLabel[e.status] ?? e.status,
    e.triggeredValue == null ? '' : Number(e.triggeredValue).toFixed(4),
    formatTime(e.triggeredAt),
    String(e.triggerCount ?? ''),
  ]);
  const csv = [header, ...rows]
    .map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `alert-events-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  message.success(`已导出 ${eventList.value.length} 条事件`);
}

function handleHelp() {
  showPageHelp({
    title: '预警事件 帮助',
    content:
      '预警事件由规则引擎实时求值产生。可按状态、严重度、回路筛选；对待确认事件执行「确认/处置/误报」操作，已处置事件可归档。点击「导出」可将当前筛选结果保存为 CSV。',
  });
}

// ===== 统一工具栏（标准 5 工具：刷新/筛选/导出/列设置/帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadEvents, loading: loading.value },
  filter: { onClick: () => toggleFilter(), active: filterVisible.value },
  export: {
    onClick: exportEventsCsv,
    permission: ['ADMIN', 'IC_ENGINEER'],
    disabledReason: '仅工程师/管理员可导出',
  },
  setting: {},
  help: { onClick: handleHelp },
}));

function toggleFilter() {
  filterVisible.value = !filterVisible.value;
}

onMounted(() => {
  loadEvents();
  loadBadge();
});
</script>

<template>
  <Page>
    <!-- 统一工具栏（吸顶 · 右对齐 · 彩色语义图标） -->
    <ClpmPageToolbar
      title="预警事件"
      subtitle="规则引擎实时求值产生的事件流"
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
      </template>
    </ClpmPageToolbar>

    <!-- 筛选区（工具栏「筛选」工具可折叠） -->
    <div
      class="clpm-filter-bar"
      :class="{ 'clpm-filter-bar--collapsed': !filterVisible }"
    >
      <FormItem label="状态" class="!mb-0">
        <Select
          v-model:value="query.status"
          allow-clear
          placeholder="全部状态"
          style="width: 140px"
          :options="
            Object.entries(statusLabel).map(([value, label]) => ({
              value,
              label,
            }))
          "
        />
      </FormItem>
      <FormItem label="严重度" class="!mb-0">
        <Select
          v-model:value="query.severity"
          allow-clear
          placeholder="全部"
          style="width: 140px"
          :options="
            (['CRITICAL', 'ERROR', 'WARN', 'INFO'] as AlertApi.Severity[]).map(
              (v) => ({ value: v, label: `${severityLabel[v]}（${v}）` }),
            )
          "
        />
      </FormItem>
      <FormItem label="回路ID" class="!mb-0">
        <Input
          v-model:value="query.loopId"
          allow-clear
          placeholder="回路 ID"
          style="width: 200px"
          @press-enter="handleSearch"
        />
      </FormItem>
      <Space class="!ml-auto">
        <Button type="primary" @click="handleSearch">查询</Button>
        <Button @click="handleReset">重置</Button>
      </Space>
      <Badge :count="badgeCount" :offset="[-4, 4]">
        <Button size="small" @click="handleResetBadge">标记已读</Button>
      </Badge>
    </div>

    <!-- 事件表格 -->
    <Table
      :columns="visibleColumns"
      :data-source="eventList"
      :loading="loading"
      :pagination="{
        current: query.page,
        pageSize: query.pageSize,
        total,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      :scroll="{ x: 1200 }"
      row-key="eventId"
      size="small"
      @change="handlePageChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'action'">
          <Space :size="4">
            <Button
              type="link"
              size="small"
              @click="showDetail(record as AlertApi.EventItem)"
            >
              详情
            </Button>
            <Button
              v-if="canEdit && record.status === 'ACTIVE'"
              type="link"
              size="small"
              @click="handleAcknowledge(record as AlertApi.EventItem)"
            >
              确认
            </Button>
            <Button
              v-if="
                canEdit &&
                ['ACTIVE', 'ACKNOWLEDGED', 'SUPPRESSED'].includes(record.status)
              "
              type="link"
              size="small"
              @click="openResolveModal(record as AlertApi.EventItem)"
            >
              处置
            </Button>
            <Button
              v-if="canEdit && !record.isFalsePositive"
              type="link"
              size="small"
              @click="handleMarkFalsePositive(record as AlertApi.EventItem)"
            >
              误报
            </Button>
            <Button
              v-if="canArchive && record.status === 'RESOLVED'"
              type="link"
              size="small"
              @click="handleArchive(record as AlertApi.EventItem)"
            >
              归档
            </Button>
          </Space>
        </template>
      </template>
      <template #emptyText>
        <ClpmEmptyState
          title="暂无预警事件"
          description="当前筛选条件（状态/严重度/回路）下无预警事件；规则引擎巡检产生的事件会实时出现在这里。"
        />
      </template>
    </Table>

    <!-- 详情抽屉 -->
    <Drawer
      v-model:open="detailVisible"
      title="预警事件详情"
      placement="right"
      :width="640"
    >
      <Descriptions v-if="currentEvent" :column="1" bordered size="small">
        <DescriptionsItem label="事件ID">{{
          currentEvent.eventId
        }}</DescriptionsItem>
        <DescriptionsItem label="回路">{{
          currentEvent.loopName || currentEvent.loopId
        }}</DescriptionsItem>
        <DescriptionsItem label="规则代码">{{
          currentEvent.ruleCode
        }}</DescriptionsItem>
        <DescriptionsItem label="规则版本">{{
          currentEvent.ruleVersion
        }}</DescriptionsItem>
        <DescriptionsItem label="严重度">
          <Tag :color="severityColor[currentEvent.severity]">{{
            severityLabel[currentEvent.severity] ?? currentEvent.severity
          }}</Tag>
        </DescriptionsItem>
        <DescriptionsItem label="状态">
          <Tag :color="statusColor[currentEvent.status]">{{
            statusLabel[currentEvent.status]
          }}</Tag>
        </DescriptionsItem>
        <DescriptionsItem label="触发值">{{
          currentEvent.triggeredValue != null
            ? Number(currentEvent.triggeredValue).toFixed(4)
            : '-'
        }}</DescriptionsItem>
        <DescriptionsItem label="可信度等级">{{
          currentEvent.confidenceLevel || '-'
        }}</DescriptionsItem>
        <DescriptionsItem label="重复触发次数">{{
          currentEvent.triggerCount
        }}</DescriptionsItem>
        <DescriptionsItem label="触发时间">{{
          formatTime(currentEvent.triggeredAt)
        }}</DescriptionsItem>
        <DescriptionsItem label="确认人">{{
          currentEvent.acknowledgedBy || '-'
        }}</DescriptionsItem>
        <DescriptionsItem label="处置人">{{
          currentEvent.resolvedBy || '-'
        }}</DescriptionsItem>
        <DescriptionsItem label="处置说明">{{
          currentEvent.resolutionNote || '-'
        }}</DescriptionsItem>
        <DescriptionsItem label="误报标记">
          <Tag v-if="currentEvent.isFalsePositive" color="red">误报</Tag>
          <span v-else>-</span>
        </DescriptionsItem>
        <DescriptionsItem label="关联工单">{{
          currentEvent.trackerId || '-'
        }}</DescriptionsItem>
        <DescriptionsItem label="触发条件快照">
          <pre class="max-h-48 overflow-auto rounded bg-gray-50 p-2 text-xs">{{
            JSON.stringify(currentEvent.triggerConditionSnapshot, null, 2)
          }}</pre>
        </DescriptionsItem>
        <DescriptionsItem label="规则DSL快照">
          <pre class="max-h-64 overflow-auto rounded bg-gray-50 p-2 text-xs">{{
            JSON.stringify(currentEvent.ruleDslSnapshot, null, 2)
          }}</pre>
        </DescriptionsItem>
      </Descriptions>
    </Drawer>

    <!-- 处置弹窗 -->
    <Modal
      v-model:open="resolveVisible"
      title="处置预警事件"
      ok-text="确认处置"
      cancel-text="取消"
      @ok="handleResolve"
    >
      <Form layout="vertical">
        <FormItem label="处置说明" required>
          <Input.TextArea
            v-model:value="resolveNote"
            :rows="4"
            placeholder="请填写处置说明（必填）"
            :maxlength="500"
            show-count
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- 归档事件：危险确认弹窗（归档后不可再操作，按不可逆处理） -->
    <ClpmDangerConfirmModal
      v-model:open="archiveOpen"
      title="归档预警事件"
      action="归档"
      :target="archiveTarget?.ruleCode ?? ''"
      impact-scope="归档后事件将从事件列表移除且不可再操作"
      rollback-tip="此操作不可逆，归档后无法恢复"
      require-confirm-code
      confirm-code-placeholder="请输入规则代码以确认"
      :loading="archiveLoading"
      @confirm="handleArchiveConfirm"
    />
  </Page>
</template>
