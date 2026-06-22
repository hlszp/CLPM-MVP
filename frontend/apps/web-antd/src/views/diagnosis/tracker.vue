<script lang="ts" setup>
/**
 * S4-DIAG-010 Action Tracker 页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 表格展示跟踪记录列表（回路位号/诊断标签/状态/创建时间/更新时间/操作）
 * - 状态标签颜色：PENDING(黄)/IN_PROGRESS(蓝)/RESOLVED(绿)/IGNORED(灰)
 * - 状态更新下拉菜单（仅 IC_ENGINEER 可操作）
 * - "A/B 对比"按钮打开抽屉展示处置前后 KPI 对比图表
 * - "导出 PDF"按钮触发异步导出任务
 * - 筛选栏（状态/标签/时间）
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';

import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Dropdown,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Select,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  exportDiagnosisPdfApi,
  getTrackerListApi,
  updateTrackerStatusApi,
} from '#/api/diagnosis';

import AbCompare from './ab-compare.vue';

defineOptions({ name: 'DiagnosisTracker' });

const router = useRouter();

const loading = ref(false);
const trackerList = ref<DiagnosisApi.TrackerItem[]>([]);
const total = ref(0);

const query = reactive({
  diagnosisLabel: undefined as DiagnosisLabel | undefined,
  actionStatus: undefined as DiagnosisApi.ActionStatus | undefined,
  timeWindow: 'last_7_days' as DiagnosisApi.TimeWindow,
  page: 1,
  pageSize: 20,
});

/** 8 类诊断标签选项 */
const labelOptions: { label: string; value: DiagnosisLabel }[] = [
  { label: '振荡', value: 'OSCILLATION' },
  { label: '阀门粘滞', value: 'VALVE_STICTION' },
  { label: '参数过激', value: 'OVERAGGRESSIVE' },
  { label: '参数过保守', value: 'OVERCONSERVATIVE' },
  { label: '外扰频繁', value: 'EXTERNAL_DISTURBANCE' },
  { label: 'PV 质量异常', value: 'QUALITY_ABNORMAL' },
  { label: '输出饱和', value: 'OUTPUT_SATURATION' },
  { label: '人工复核', value: 'MANUAL_REVIEW' },
];

/** 标签颜色映射 */
const labelColorMap: Record<DiagnosisLabel, string> = {
  OSCILLATION: 'red',
  VALVE_STICTION: 'orange',
  OVERAGGRESSIVE: 'purple',
  OVERCONSERVATIVE: 'blue',
  EXTERNAL_DISTURBANCE: 'cyan',
  QUALITY_ABNORMAL: 'default',
  OUTPUT_SATURATION: 'gold',
  MANUAL_REVIEW: 'default',
};

/** 处理状态选项 */
const statusOptions: { label: string; value: DiagnosisApi.ActionStatus }[] = [
  { label: '待处理', value: 'PENDING' },
  { label: '处理中', value: 'IN_PROGRESS' },
  { label: '已解决', value: 'RESOLVED' },
  { label: '已忽略', value: 'IGNORED' },
];

/** 处理状态颜色映射 */
const statusColorMap: Record<DiagnosisApi.ActionStatus, string> = {
  PENDING: 'gold',
  IN_PROGRESS: 'blue',
  RESOLVED: 'green',
  IGNORED: 'default',
};

/** 时间窗选项 */
const timeWindowOptions: { label: string; value: DiagnosisApi.TimeWindow }[] = [
  { label: '今天', value: 'today' },
  { label: '昨天', value: 'yesterday' },
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
    width: 180,
    ellipsis: true,
  },
  {
    title: '诊断标签',
    dataIndex: 'diagnosisLabel',
    key: 'diagnosisLabel',
    width: 130,
  },
  {
    title: '综合评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 100,
    align: 'right',
  },
  {
    title: '置信度',
    dataIndex: 'confidence',
    key: 'confidence',
    width: 100,
    align: 'right',
  },
  {
    title: '处理状态',
    dataIndex: 'actionStatus',
    key: 'actionStatus',
    width: 110,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 170,
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
];

// 状态更新 Modal
const statusModalVisible = ref(false);
const statusModalLoading = ref(false);
const editingItem = ref<DiagnosisApi.TrackerItem | null>(null);
const statusForm = reactive({
  status: 'PENDING' as DiagnosisApi.ActionStatus,
  comment: '',
});

// A/B 对比抽屉
const abCompareVisible = ref(false);
const abCompareLoopId = ref('');

/** 加载列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getTrackerListApi({
      diagnosisLabel: query.diagnosisLabel,
      actionStatus: query.actionStatus,
      timeWindow: query.timeWindow,
      page: query.page,
      pageSize: query.pageSize,
    });
    trackerList.value = data.items || [];
    total.value = data.total || 0;
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

/** 提交状态更新 */
async function handleSubmitStatus() {
  if (!editingItem.value) return;
  statusModalLoading.value = true;
  try {
    await updateTrackerStatusApi(editingItem.value.loopId, {
      status: statusForm.status,
      comment: statusForm.comment,
    });
    message.success('状态更新成功');
    statusModalVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    statusModalLoading.value = false;
  }
}

/** 状态快捷下拉菜单 */
function getStatusMenuActions(record: DiagnosisApi.TrackerItem) {
  return statusOptions.map((s) => ({
    key: s.value,
    label: s.label,
    disabled: s.value === record.actionStatus,
  }));
}

function handleStatusMenuClick(
  record: DiagnosisApi.TrackerItem,
  { key }: { key: string },
) {
  editingItem.value = record;
  statusForm.status = key as DiagnosisApi.ActionStatus;
  statusForm.comment = record.comment || '';
  statusModalVisible.value = true;
}

/** 导出 PDF */
async function handleExportPdf(record: DiagnosisApi.TrackerItem) {
  try {
    const result = await exportDiagnosisPdfApi(record.loopId, {
      timeWindow: 'last_24_hours',
      includeWaveform: true,
      includeScatterPlot: true,
    });
    message.success(`导出任务已提交，任务 ID：${result.taskId}`);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 打开 A/B 对比 */
function handleOpenAbCompare(record: DiagnosisApi.TrackerItem) {
  abCompareLoopId.value = record.loopId;
  abCompareVisible.value = true;
}

/** 跳转诊断详情 */
function handleViewDetail(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

function labelName(label: DiagnosisLabel): string {
  return labelOptions.find((o) => o.value === label)?.label || label;
}

function statusName(status: DiagnosisApi.ActionStatus): string {
  return statusOptions.find((o) => o.value === status)?.label || status;
}

onMounted(() => {
  loadList();
});
</script>

<template>
  <Page title="异常跟踪">
    <Card>
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.diagnosisLabel"
          placeholder="诊断标签"
          style="width: 160px"
          allow-clear
          :options="labelOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.actionStatus"
          placeholder="处理状态"
          style="width: 140px"
          allow-clear
          :options="statusOptions"
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
      </div>

      <Table
        :columns="columns"
        :data-source="trackerList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: DiagnosisApi.TrackerItem) => record.loopId"
        :scroll="{ x: 1400 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'diagnosisLabel'">
            <Tag
              :color="labelColorMap[record.diagnosisLabel as DiagnosisLabel]"
            >
              {{ record.labelName || labelName(record.diagnosisLabel) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'compositeScore'">
            <span class="font-medium text-blue-600">
              {{ Number(record.compositeScore).toFixed(2) }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidence'">
            {{ Number(record.confidence).toFixed(2) }}
          </template>
          <template v-else-if="column.key === 'actionStatus'">
            <Tag
              :color="
                statusColorMap[record.actionStatus as DiagnosisApi.ActionStatus]
              "
            >
              {{ statusName(record.actionStatus as DiagnosisApi.ActionStatus) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatTime(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'updatedAt'">
            {{ formatTime(record.updatedAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                type="link"
                size="small"
                @click="handleViewDetail(record.loopId)"
              >
                详情
              </Button>
              <Dropdown
                v-permission="['IC_ENGINEER', 'ADMIN']"
                trigger="click"
                :menu="{
                  items: getStatusMenuActions(
                    record as DiagnosisApi.TrackerItem,
                  ),
                  onClick: ({ key }: any) =>
                    handleStatusMenuClick(record as DiagnosisApi.TrackerItem, {
                      key,
                    }),
                }"
              >
                <Button type="link" size="small">更新状态</Button>
              </Dropdown>
              <Button
                v-permission="['IC_ENGINEER', 'ADMIN', 'EXPERT']"
                type="link"
                size="small"
                @click="handleOpenAbCompare(record as DiagnosisApi.TrackerItem)"
              >
                A/B 对比
              </Button>
              <Button
                type="link"
                size="small"
                @click="handleExportPdf(record as DiagnosisApi.TrackerItem)"
              >
                导出 PDF
              </Button>
            </div>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 状态更新 Modal -->
    <Modal
      v-model:open="statusModalVisible"
      title="更新处理状态"
      :confirm-loading="statusModalLoading"
      width="480px"
      @ok="handleSubmitStatus"
    >
      <Form :model="statusForm" layout="vertical" class="pt-4">
        <FormItem label="回路位号">
          <span class="font-medium">{{ editingItem?.tagName }}</span>
        </FormItem>
        <FormItem label="处理状态" required>
          <Select v-model:value="statusForm.status" :options="statusOptions" />
        </FormItem>
        <FormItem label="处理备注">
          <Input.TextArea
            v-model:value="statusForm.comment"
            placeholder="例如：已联系设备部拆阀检查"
            :rows="3"
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- A/B 对比抽屉 -->
    <AbCompare
      v-if="abCompareVisible"
      :loop-id="abCompareLoopId"
      :drawer-mode="true"
      @close="abCompareVisible = false"
    />
  </Page>
</template>
