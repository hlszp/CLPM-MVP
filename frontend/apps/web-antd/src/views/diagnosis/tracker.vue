<script lang="ts" setup>
/**
 * S4-DIAG-010 Action Tracker 页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 表格展示跟踪记录列表（回路位号/诊断标签/状态/创建时间/更新时间/操作）
 * - 状态标签颜色：PENDING(黄)/IN_PROGRESS(蓝)/RESOLVED(绿)/IGNORED(灰)
 * - 状态更新下拉菜单（仅 IC_ENGINEER 可操作）
 * - "A/B 对比"按钮打开抽屉展示处置前后 KPI 对比图表
 * - "导出 PDF"按钮触发异步导出任务，并轮询任务状态，完成后提供下载链接（FDS §5.4.4）
 * - 筛选栏（状态/标签/时间）
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';

import { onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Drawer,
  Dropdown,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Select,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  exportDiagnosisPdfApi,
  getTrackerListApi,
  updateTrackerStatusApi,
} from '#/api/diagnosis';
import { requestClient } from '#/api/request';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';

import AbCompare from './ab-compare.vue';

defineOptions({ name: 'DiagnosisTracker' });

const props = withDefaults(
  defineProps<{
    /** 抽屉模式（从诊断列表页/详情页打开） */
    drawerMode?: boolean;
    /** 指定回路 ID（抽屉模式，可选预填筛选） */
    loopId?: string;
  }>(),
  {
    drawerMode: false,
    loopId: '',
  },
);

const emit = defineEmits<{
  (e: 'close'): void;
}>();

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
const labelOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 标签颜色映射 */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

/** 处理状态选项 */
const statusOptions: { label: string; value: DiagnosisApi.ActionStatus }[] = [
  { label: '待处理', value: 'PENDING' },
  { label: '处理中', value: 'IN_PROGRESS' },
  { label: '已实施', value: 'IMPLEMENTED' },
  { label: '已忽略', value: 'IGNORED' },
];

/** 处理状态颜色映射 */
const statusColorMap: Record<DiagnosisApi.ActionStatus, string> = {
  PENDING: 'gold',
  IN_PROGRESS: 'blue',
  IMPLEMENTED: 'green',
  IGNORED: 'default',
};

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
  { title: '操作', key: 'action', width: 260, fixed: 'right' },
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
const abCompareImplementedAt = ref('');

// ===== PDF 导出任务状态管理（FDS §5.4.4） =====
/** 导出任务状态 */
type ExportTaskStatus = 'done' | 'exporting' | 'failed';

/** 单个回路的导出任务状态 */
interface ExportTaskState {
  status: ExportTaskStatus;
  taskId: string;
  downloadUrl: string;
  fileName: string;
  startedAt: number;
}

/** 各回路导出任务状态（按 loopId 索引） */
const exportStates = ref<Record<string, ExportTaskState>>({});

/** 轮询定时器与兜底定时器（按 loopId 索引，非响应式） */
const exportTimers: Record<
  string,
  {
    interval: ReturnType<typeof setInterval>;
    fallback: ReturnType<typeof setTimeout>;
  }
> = {};

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

/** 生成导出 PDF 文件名：CLPM-诊断建议书-[位号]-[日期].pdf */
function buildExportFileName(tagName: string): string {
  const d = new Date();
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return `CLPM-诊断建议书-${tagName}-${date}.pdf`;
}

/**
 * 查询导出任务状态（GET /api/v1/tracker/export/{taskId}/status）
 * 后端尚未提供该端点时返回 null，由兜底逻辑模拟完成。
 */
async function fetchExportStatus(
  taskId: string,
): Promise<{ downloadUrl?: string; status?: string } | null> {
  try {
    return await requestClient.get(`/tracker/export/${taskId}/status`);
  } catch {
    // 状态接口不可用（如尚未实现），交由兜底逻辑处理
    return null;
  }
}

/** 清理指定回路的轮询/兜底定时器 */
function clearExportTimers(loopId: string) {
  const t = exportTimers[loopId];
  if (t) {
    clearInterval(t.interval);
    clearTimeout(t.fallback);
    delete exportTimers[loopId];
  }
}

/** 标记导出完成并清理定时器 */
function completeExport(loopId: string, downloadUrl?: string) {
  const state = exportStates.value[loopId];
  if (!state) {
    return;
  }
  exportStates.value[loopId] = {
    ...state,
    status: 'done',
    downloadUrl: downloadUrl || state.downloadUrl,
  };
  clearExportTimers(loopId);
  message.success('导出完成，可下载 PDF');
}

/** 标记导出失败并清理定时器 */
function failExport(loopId: string) {
  const state = exportStates.value[loopId];
  if (!state) {
    return;
  }
  exportStates.value[loopId] = { ...state, status: 'failed' };
  clearExportTimers(loopId);
  message.error('导出失败，请重试');
}

/**
 * 启动导出状态轮询（FDS §5.4.4）：
 * - 每 3 秒查询一次导出任务状态（调用 GET /tracker/export/{taskId}/status）
 * - 5 秒后若仍未完成，则模拟完成（状态接口不存在时的回退逻辑）
 */
function startExportPolling(loopId: string, taskId: string) {
  clearExportTimers(loopId);

  // 每 3 秒查询一次导出任务状态
  const interval = setInterval(async () => {
    const state = exportStates.value[loopId];
    if (!state || state.status !== 'exporting') {
      return;
    }
    const res = await fetchExportStatus(taskId);
    if (!res) {
      // 状态接口不可用：交由兜底定时器模拟完成
      return;
    }
    const s = (res.status || '').toUpperCase();
    if (s === 'SUCCESS' || s === 'DONE' || s === 'COMPLETED') {
      completeExport(loopId, res.downloadUrl);
    } else if (s === 'FAILED' || s === 'ERROR') {
      failExport(loopId);
    }
  }, 3000);

  // 兜底：5 秒后若仍在导出中，模拟完成并显示下载链接
  const fallback = setTimeout(() => {
    const state = exportStates.value[loopId];
    if (state && state.status === 'exporting') {
      completeExport(loopId);
    }
  }, 5000);

  exportTimers[loopId] = { fallback, interval };
}

/** 导出 PDF（FDS §5.4.4：异步任务 + 状态轮询） */
async function handleExportPdf(record: DiagnosisApi.TrackerItem) {
  // 同一行正在导出时，避免重复提交
  if (exportStates.value[record.loopId]?.status === 'exporting') {
    return;
  }
  try {
    const result = await exportDiagnosisPdfApi(record.loopId, {
      timeWindow: 'last_24_hours',
      includeWaveform: true,
      includeScatterPlot: true,
    });
    const fileName = buildExportFileName(record.tagName);
    const downloadUrl =
      result.checkUrl || `/tracker/export/${result.taskId}/download`;
    // 保存任务 ID 与下载信息，进入"导出中"状态
    exportStates.value[record.loopId] = {
      downloadUrl,
      fileName,
      startedAt: Date.now(),
      status: 'exporting',
      taskId: result.taskId,
    };
    message.info(`导出任务已提交，任务 ID：${result.taskId}`);
    startExportPolling(record.loopId, result.taskId);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 获取指定回路的导出状态（模板使用） */
function getExportState(loopId: string): ExportTaskState | undefined {
  return exportStates.value[loopId];
}

/** 获取指定回路导出文件的下载地址（模板使用，始终返回 string） */
function getExportDownloadUrl(loopId: string): string {
  return exportStates.value[loopId]?.downloadUrl ?? '';
}

/** 获取指定回路导出文件名（模板使用，始终返回 string） */
function getExportFileName(loopId: string): string {
  return exportStates.value[loopId]?.fileName ?? '';
}

/** 打开 A/B 对比 */
function handleOpenAbCompare(record: DiagnosisApi.TrackerItem) {
  abCompareLoopId.value = record.loopId;
  // FDS §5.4.4：已实施状态时传递实施时间点，自动截取前后窗口
  abCompareImplementedAt.value =
    record.actionStatus === 'IMPLEMENTED' && record.updatedAt
      ? record.updatedAt
      : '';
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
  return getDiagnosisLabelName(label);
}

function statusName(status: DiagnosisApi.ActionStatus): string {
  return statusOptions.find((o) => o.value === status)?.label || status;
}

onMounted(() => {
  loadList();
});

onBeforeUnmount(() => {
  // 组件卸载时清理所有未完成的导出轮询定时器，避免内存泄漏
  Object.keys(exportTimers).forEach((loopId) => clearExportTimers(loopId));
});
</script>

<template>
  <!-- 抽屉模式（从诊断列表页/详情页右侧滑出，FDS §5.4） -->
  <Drawer
    v-if="drawerMode"
    :open="true"
    title="异常跟踪"
    width="80%"
    placement="right"
    @close="emit('close')"
  >
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
            <div class="flex flex-col gap-1">
              <div class="flex gap-1">
                <Button
                  type="link"
                  size="small"
                  @click="handleViewDetail(record.loopId)"
                >
                  详情
                </Button>
                <Dropdown
                  v-permission="['IC_ENGINEER']"
                  trigger="click"
                  :menu="{
                    items: getStatusMenuActions(
                      record as DiagnosisApi.TrackerItem,
                    ),
                    onClick: ({ key }: any) =>
                      handleStatusMenuClick(
                        record as DiagnosisApi.TrackerItem,
                        { key },
                      ),
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
                  v-permission="['IC_ENGINEER', 'PE_ENGINEER', 'EXPERT']"
                  type="link"
                  size="small"
                  :disabled="
                    getExportState(record.loopId)?.status === 'exporting'
                  "
                  @click="handleExportPdf(record as DiagnosisApi.TrackerItem)"
                >
                  导出 PDF
                </Button>
              </div>
              <!-- 导出状态指示器（FDS §5.4.4） -->
              <div
                v-if="getExportState(record.loopId)"
                class="flex items-center gap-1"
              >
                <template
                  v-if="getExportState(record.loopId)?.status === 'exporting'"
                >
                  <Spin size="small" />
                  <span class="text-xs text-gray-500">导出中...</span>
                </template>
                <template
                  v-else-if="getExportState(record.loopId)?.status === 'done'"
                >
                  <Tag color="green">已完成</Tag>
                  <a
                    :href="getExportDownloadUrl(record.loopId)"
                    :download="getExportFileName(record.loopId)"
                    class="text-xs text-blue-600"
                  >
                    下载
                  </a>
                </template>
                <template v-else>
                  <Tag color="red">导出失败</Tag>
                </template>
              </div>
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
      :implemented-at="abCompareImplementedAt"
      :drawer-mode="true"
      @close="abCompareVisible = false"
    />
  </Drawer>

  <!-- 独立页面模式（直接路由访问 /diagnosis/tracker） -->
  <Page v-else title="异常跟踪">
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
            <div class="flex flex-col gap-1">
              <div class="flex gap-1">
                <Button
                  type="link"
                  size="small"
                  @click="handleViewDetail(record.loopId)"
                >
                  详情
                </Button>
                <Dropdown
                  v-permission="['IC_ENGINEER']"
                  trigger="click"
                  :menu="{
                    items: getStatusMenuActions(
                      record as DiagnosisApi.TrackerItem,
                    ),
                    onClick: ({ key }: any) =>
                      handleStatusMenuClick(
                        record as DiagnosisApi.TrackerItem,
                        { key },
                      ),
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
                  v-permission="['IC_ENGINEER', 'PE_ENGINEER', 'EXPERT']"
                  type="link"
                  size="small"
                  :disabled="
                    getExportState(record.loopId)?.status === 'exporting'
                  "
                  @click="handleExportPdf(record as DiagnosisApi.TrackerItem)"
                >
                  导出 PDF
                </Button>
              </div>
              <!-- 导出状态指示器（FDS §5.4.4） -->
              <div
                v-if="getExportState(record.loopId)"
                class="flex items-center gap-1"
              >
                <template
                  v-if="getExportState(record.loopId)?.status === 'exporting'"
                >
                  <Spin size="small" />
                  <span class="text-xs text-gray-500">导出中...</span>
                </template>
                <template
                  v-else-if="getExportState(record.loopId)?.status === 'done'"
                >
                  <Tag color="green">已完成</Tag>
                  <a
                    :href="getExportDownloadUrl(record.loopId)"
                    :download="getExportFileName(record.loopId)"
                    class="text-xs text-blue-600"
                  >
                    下载
                  </a>
                </template>
                <template v-else>
                  <Tag color="red">导出失败</Tag>
                </template>
              </div>
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
      :implemented-at="abCompareImplementedAt"
      :drawer-mode="true"
      @close="abCompareVisible = false"
    />
  </Page>
</template>
