<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { AlertApi } from '#/api/alert';

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
  getAlertEventsApi,
  getAlertBadgeApi,
  markFalsePositiveApi,
  resetAlertBadgeApi,
  resolveEventApi,
} from '#/api/alert';
import { formatTime } from '#/utils/format';
import { SEVERITY_LABEL } from '#/constants/clpm-ui';

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
      value != null ? Number(value).toFixed(3) : '-',
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

async function handleArchive(record: AlertApi.EventItem) {
  Modal.confirm({
    title: '确认归档',
    content: '归档后事件将不可再操作，确认归档？',
    onOk: async () => {
      try {
        await archiveEventApi(record.eventId);
        message.success('事件已归档');
        await loadEvents();
      } catch {
        message.error('归档失败');
      }
    },
  });
}

async function handleResetBadge() {
  try {
    await resetAlertBadgeApi();
    badgeCount.value = 0;
  } catch {
    // 静默
  }
}

onMounted(() => {
  loadEvents();
  loadBadge();
});
</script>

<template>
  <Page :title="'预警事件'">
    <template #extra>
      <Badge :count="badgeCount" :offset="[-4, 4]">
        <Button size="small" @click="handleResetBadge">已读</Button>
      </Badge>
    </template>

    <!-- 筛选栏 -->
    <Form layout="inline" class="mb-4">
      <FormItem label="状态">
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
      <FormItem label="严重度">
        <Select
          v-model:value="query.severity"
          allow-clear
          placeholder="全部"
          style="width: 120px"
          :options="
            (['CRITICAL', 'ERROR', 'WARN', 'INFO'] as AlertApi.Severity[]).map(
              (v) => ({ value: v, label: `${severityLabel[v]}（${v}）` }),
            )
          "
        />
      </FormItem>
      <FormItem label="回路ID">
        <Input
          v-model:value="query.loopId"
          allow-clear
          placeholder="回路 ID"
          style="width: 200px"
        />
      </FormItem>
      <FormItem>
        <Space>
          <Button type="primary" @click="handleSearch">查询</Button>
          <Button @click="handleReset">重置</Button>
        </Space>
      </FormItem>
    </Form>

    <!-- 事件表格 -->
    <Table
      :columns="columns"
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
      :ok-text="`确认处置`"
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
  </Page>
</template>
