<script lang="ts" setup>
/**
 * S5-SYS-007 自动报表管理页
 *
 * 对齐 IDS v3.2 §2.6 + PRD §4.6 + UI/UX v4.1 §6.6.3
 * - 表格展示报表配置列表（名称/周期/接收人/状态）
 * - 新增/编辑配置弹窗
 * - "立即生成"按钮触发异步任务
 * - 任务进度查询（轮询 taskId）
 * - 仅 ADMIN 可见（路由与后端 reports.py 全端点均已收紧 ADMIN）
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { SystemApi } from '#/api/system';

import { onMounted, onUnmounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Progress,
  Select,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  createReportConfigApi,
  generateReportApi,
  getReportConfigListApi,
  getReportTaskStatusApi,
  updateReportConfigApi,
} from '#/api/system';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { usePolling } from '#/composables/use-polling';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'SystemReports' });

const loading = ref(false);
const reportList = ref<SystemApi.ReportConfig[]>([]);

/** 报表周期选项 */
const periodOptions = [
  { label: '班报', value: 'SHIFT' },
  { label: '日报', value: 'DAILY' },
  { label: '周报', value: 'WEEKLY' },
  { label: '月报', value: 'MONTHLY' },
];

const columns: TableColumnsType = [
  {
    title: '报表名称',
    dataIndex: 'name',
    key: 'name',
    width: 200,
  },
  {
    title: '报表周期',
    dataIndex: 'reportPeriod',
    key: 'reportPeriod',
    width: 120,
  },
  {
    title: '收件人',
    dataIndex: 'recipients',
    key: 'recipients',
    width: 240,
    ellipsis: true,
  },
  {
    title: '状态',
    dataIndex: 'isEnabled',
    key: 'isEnabled',
    width: 90,
    align: 'center',
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 170,
  },
  {
    title: '生成进度',
    key: 'task_progress',
    width: 180,
  },
  { title: '操作', key: 'action', width: 200, fixed: 'right' },
];

// 新增/编辑 Modal
const modalVisible = ref(false);
const modalLoading = ref(false);
const editingReport = ref<null | SystemApi.ReportConfig>(null);
const formRef = ref();
const formState = reactive({
  name: '',
  reportPeriod: 'DAILY',
  recipients_text: '',
  isEnabled: true,
});

// 任务进度跟踪
interface TaskProgress {
  taskId: string;
  configId: string;
  status: SystemApi.ReportTaskStatus;
  progress: number;
  message: string;
}

const taskProgressMap = ref<Map<string, TaskProgress>>(new Map());

/** 轮询一次全部进行中的生成任务（单任务失败跳过本次，不中断其余任务） */
async function pollTaskProgress() {
  if (taskProgressMap.value.size === 0) {
    stopPolling();
    return;
  }
  const entries = [...taskProgressMap.value.entries()];
  for (const [configId, progress] of entries) {
    try {
      const taskResult = await getReportTaskStatusApi(progress.taskId);
      taskProgressMap.value.set(configId, {
        ...progress,
        status: taskResult.status,
        progress: taskResult.progress ?? 0,
        message: taskResult.message || '',
      });
      if (taskResult.status === 'COMPLETED' || taskResult.status === 'FAILED') {
        if (taskResult.status === 'COMPLETED') {
          message.success(`报表「${recordName(configId)}」生成完成`);
        } else {
          message.error(`报表「${recordName(configId)}」生成失败`);
        }
        taskProgressMap.value.delete(configId);
        await loadList();
      }
    } catch {
      // 轮询失败，跳过本次
    }
  }
  if (taskProgressMap.value.size === 0) {
    stopPolling();
  }
}

/** 任务进度轮询（3s，usePolling 防堆积 + 页面隐藏自动暂停） */
const { start: startPolling, stop: stopPolling } = usePolling(
  pollTaskProgress,
  { interval: 3000 },
);

/** 加载报表配置列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getReportConfigListApi();
    reportList.value = data || [];
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 打开新增弹窗 */
function handleOpenAdd() {
  editingReport.value = null;
  formState.name = '';
  formState.reportPeriod = 'DAILY';
  formState.recipients_text = '';
  formState.isEnabled = true;
  modalVisible.value = true;
}

/** 打开编辑弹窗 */
function handleOpenEdit(record: SystemApi.ReportConfig) {
  editingReport.value = record;
  formState.name = record.name;
  formState.reportPeriod = record.reportPeriod;
  formState.recipients_text = record.recipients.join('\n');
  formState.isEnabled = record.isEnabled;
  modalVisible.value = true;
}

/** 提交新增/编辑 */
function handleSubmit() {
  formRef.value?.validate().then(async () => {
    const recipients = formState.recipients_text
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    if (recipients.length === 0) {
      message.warning('至少需要一个收件人');
      return;
    }
    modalLoading.value = true;
    try {
      if (editingReport.value) {
        await updateReportConfigApi(editingReport.value.id, {
          name: formState.name,
          reportPeriod: formState.reportPeriod,
          recipients,
          isEnabled: formState.isEnabled,
        });
        message.success('报表配置更新成功');
      } else {
        await createReportConfigApi({
          name: formState.name,
          reportPeriod: formState.reportPeriod,
          recipients,
          isEnabled: formState.isEnabled,
        });
        message.success('报表配置创建成功');
      }
      modalVisible.value = false;
      await loadList();
    } catch {
      // 错误已由拦截器处理
    } finally {
      modalLoading.value = false;
    }
  });
}

/** 切换启用状态 */
async function handleToggleEnabled(record: SystemApi.ReportConfig) {
  try {
    await updateReportConfigApi(record.id, { isEnabled: !record.isEnabled });
    message.success(`报表已${record.isEnabled ? '停用' : '启用'}`);
    await loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 立即生成报表 */
async function handleGenerate(record: SystemApi.ReportConfig) {
  try {
    const result = await generateReportApi(record.id);
    message.success(`生成任务已提交，任务 ID：${result.taskId}`);
    taskProgressMap.value.set(record.id, {
      taskId: result.taskId,
      configId: record.id,
      status: 'PROCESSING',
      progress: 0,
      message: '任务已提交，等待执行...',
    });
    startPolling();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 根据 configId 获取报表名称 */
function recordName(configId: string): string {
  const report = reportList.value.find((r) => r.id === configId);
  return report?.name || configId;
}

/** 获取任务进度 */
function getTaskProgress(configId: string): TaskProgress | undefined {
  return taskProgressMap.value.get(configId);
}

/** 任务状态颜色 */
function taskStatusColor(status: SystemApi.ReportTaskStatus): string {
  const map: Record<SystemApi.ReportTaskStatus, string> = {
    PROCESSING: 'blue',
    COMPLETED: 'green',
    FAILED: 'red',
  };
  return map[status] || 'default';
}

/** 任务状态标签 */
function taskStatusLabel(status: SystemApi.ReportTaskStatus): string {
  const map: Record<SystemApi.ReportTaskStatus, string> = {
    PROCESSING: '生成中',
    COMPLETED: '完成',
    FAILED: '失败',
  };
  return map[status] || status;
}

function periodLabel(period: string): string {
  return periodOptions.find((t) => t.value === period)?.label || period;
}

onMounted(() => {
  loadList();
});

onUnmounted(() => {
  stopPolling();
});

/** 工具栏刷新：重新加载报表配置列表 */
function handleRefresh() {
  loadList();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '自动报表管理 帮助',
    content:
      '自动报表管理页：配置班报 / 日报 / 周报 / 月报，支持手动触发生成并跟踪执行进度。可新增/编辑报表配置（名称、周期、收件人列表、启用状态），「立即生成」异步触发报表生成任务并轮询进度。仅 ADMIN 可访问。刷新按钮重新拉取报表配置列表。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="自动报表管理"
      subtitle="配置日报/周报/月报，手动触发生成并跟踪执行进度。"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <ClpmDataCanvas class="mt-4" title="报表配置列表" :loading="loading">
      <div class="mb-4 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          管理班报/日报/周报/月报配置 · 支持手动触发生成与进度查询
        </p>
        <Button type="primary" @click="handleOpenAdd">新增报表</Button>
      </div>

      <Table
        :columns="columns"
        :data-source="reportList"
        :loading="loading"
        :pagination="false"
        :row-key="(record: SystemApi.ReportConfig) => record.id"
        :scroll="{ x: 1400 }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'recipients'">
            <span class="text-xs font-mono">
              {{ (record.recipients || []).join(', ') }}
            </span>
            <Tag class="ml-1" size="small">
              {{ (record.recipients || []).length }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'reportPeriod'">
            <Tag color="blue">{{ periodLabel(record.reportPeriod) }}</Tag>
          </template>
          <template v-else-if="column.key === 'isEnabled'">
            <Tag :color="record.isEnabled ? 'green' : 'default'">
              {{ record.isEnabled ? '启用' : '停用' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'updatedAt'">
            {{ formatTime(record.updatedAt) }}
          </template>
          <template v-else-if="column.key === 'task_progress'">
            <div v-if="getTaskProgress(record.id)">
              <Tag :color="taskStatusColor(getTaskProgress(record.id)!.status)">
                {{ taskStatusLabel(getTaskProgress(record.id)!.status) }}
              </Tag>
              <Progress
                v-if="getTaskProgress(record.id)!.status === 'PROCESSING'"
                :percent="getTaskProgress(record.id)!.progress"
                size="small"
              />
            </div>
            <span v-else class="text-gray-300">—</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                type="link"
                size="small"
                @click="handleOpenEdit(record as SystemApi.ReportConfig)"
              >
                编辑
              </Button>
              <Button
                type="link"
                size="small"
                :disabled="!record.isEnabled || !!getTaskProgress(record.id)"
                @click="handleGenerate(record as SystemApi.ReportConfig)"
              >
                立即生成
              </Button>
              <Button
                type="link"
                size="small"
                @click="handleToggleEnabled(record as SystemApi.ReportConfig)"
              >
                {{ record.isEnabled ? '停用' : '启用' }}
              </Button>
            </div>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 新增/编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="editingReport ? `编辑报表 - ${editingReport.name}` : '新增报表'"
      :confirm-loading="modalLoading"
      width="600px"
      @ok="handleSubmit"
    >
      <Form ref="formRef" :model="formState" layout="vertical" class="pt-4">
        <FormItem
          name="name"
          label="报表名称"
          :rules="[{ required: true, message: '请输入报表名称' }]"
        >
          <Input
            v-model:value="formState.name"
            placeholder="如：加氢联合车间日报"
          />
        </FormItem>

        <FormItem
          name="reportPeriod"
          label="报表周期"
          :rules="[{ required: true, message: '请选择报表周期' }]"
        >
          <Select
            v-model:value="formState.reportPeriod"
            :options="periodOptions"
            placeholder="选择报表周期"
          />
        </FormItem>

        <FormItem
          name="recipients_text"
          label="收件人列表"
          :rules="[{ required: true, message: '请输入至少一个收件人' }]"
        >
          <Input.TextArea
            v-model:value="formState.recipients_text"
            placeholder="每行一个邮箱地址"
            :rows="4"
          />
        </FormItem>

        <FormItem name="isEnabled" label="启用状态">
          <Switch v-model:checked="formState.isEnabled" />
        </FormItem>
      </Form>
    </Modal>
  </Page>
</template>
