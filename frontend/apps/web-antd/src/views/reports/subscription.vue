<script lang="ts" setup>
/**
 * S5-SYS-007 自动报表管理页
 *
 * 对齐 IDS v3.2 §2.6 + PRD §4.6 + UI/UX v4.1 §6.6.3
 * - 表格展示报表配置列表（名称/周期/接收人/状态）
 * - 新增/编辑配置弹窗（配置 CRUD 保留，语义为"预配置"）
 * - 仅 ADMIN 可见（路由与后端 reports.py 全端点均已收紧 ADMIN）
 *
 * 报告模块优化 P0-1（2026-08-28）：订阅页诚实化
 * - 自动生成为占位实现（极简 PDF、无真实文件落盘），Beat 调度已摘除
 *   （backend/app/tasks/report_generator.py），「立即生成」置灰 +
 *   「批量生成」入口移除 + 生成进度列隐藏；P3 做实后恢复
 *   （见 docs/设计文档/CLPM报告模块优化实施方案-2026-08-28.md §3.1，D1 已决）
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { SystemApi } from '#/api/system';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Badge,
  Button,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Select,
  Switch,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  createReportConfigApi,
  getReportConfigListApi,
  updateReportConfigApi,
} from '#/api/system';
import {
  ClpmDataCanvas,
  ClpmEmptyState,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'ReportsSubscription' });

const loading = ref(false);
const reportList = ref<SystemApi.ReportConfig[]>([]);

// ===== P2-07：批量操作（行多选 + 批量启用/停用；批量生成已随 P0-1 移除）=====
const selectedRowKeys = ref<string[]>([]);
const batchLoading = ref(false);

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys as string[];
  },
}));

/** 已选报表中可启用的（当前停用）数量 */
const selectedEnableCount = computed(
  () =>
    reportList.value.filter(
      (r) => selectedRowKeys.value.includes(r.id) && !r.isEnabled,
    ).length,
);
/** 已选报表中可停用的（当前启用）数量 */
const selectedDisableCount = computed(
  () =>
    reportList.value.filter(
      (r) => selectedRowKeys.value.includes(r.id) && r.isEnabled,
    ).length,
);

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

/** 整改 C2-5：从模板新建（空态引导：班报/日报/月报预置周期） */
function handleCreateFromTemplate(period: string, name: string) {
  editingReport.value = null;
  formState.name = name;
  formState.reportPeriod = period;
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

// ===== P2-07：批量启用/停用 =====

/** 批量启用报表配置 */
async function handleBatchEnable() {
  if (selectedEnableCount.value === 0) {
    message.warning('所选报表中没有可启用的停用报表');
    return;
  }
  const targets = reportList.value.filter(
    (r) => selectedRowKeys.value.includes(r.id) && !r.isEnabled,
  );
  batchLoading.value = true;
  try {
    const results = await Promise.allSettled(
      targets.map((r) => updateReportConfigApi(r.id, { isEnabled: true })),
    );
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;
    const failed = results.length - succeeded;
    if (succeeded > 0) message.success(`已启用 ${succeeded} 个报表配置`);
    if (failed > 0) message.warning(`${failed} 个报表启用失败`);
    selectedRowKeys.value = [];
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchLoading.value = false;
  }
}

/** 批量停用报表配置 */
async function handleBatchDisable() {
  if (selectedDisableCount.value === 0) {
    message.warning('所选报表中没有可停用的启用报表');
    return;
  }
  const targets = reportList.value.filter(
    (r) => selectedRowKeys.value.includes(r.id) && r.isEnabled,
  );
  batchLoading.value = true;
  try {
    const results = await Promise.allSettled(
      targets.map((r) => updateReportConfigApi(r.id, { isEnabled: false })),
    );
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;
    const failed = results.length - succeeded;
    if (succeeded > 0) message.success(`已停用 ${succeeded} 个报表配置`);
    if (failed > 0) message.warning(`${failed} 个报表停用失败`);
    selectedRowKeys.value = [];
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchLoading.value = false;
  }
}

function periodLabel(period: string): string {
  return periodOptions.find((t) => t.value === period)?.label || period;
}

onMounted(() => {
  loadList();
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
      '自动报表管理页：预配置班报 / 日报 / 周报 / 月报（名称、周期、收件人列表、启用状态），当前为预配置语义，配置数据不会删除。自动生成功能暂未开放（开放后按周期推送报表），「立即生成」按钮置灰；统计查询类报告请使用各报告页的导出功能。仅 ADMIN 可访问。刷新按钮重新拉取报表配置列表。',
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
      subtitle="预配置报表订阅（名称/周期/收件人），自动生成暂未开放，开放后按周期推送。"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <ClpmDataCanvas class="mt-4" title="报表配置列表" :loading="loading">
      <div class="mb-4 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          管理班报/日报/周报/月报配置 ·
          自动生成暂未开放，当前为预配置；统计报告请使用各报告页导出
        </p>
        <Button type="primary" @click="handleOpenAdd">新建报表</Button>
      </div>

      <!-- P2-07：批量操作工具栏（选中行时显示；批量生成已随 P0-1 移除） -->
      <div
        v-if="selectedRowKeys.length > 0"
        class="mb-3 flex items-center gap-3 rounded border border-blue-200 bg-blue-50 px-4 py-2"
      >
        <Badge :count="selectedRowKeys.length" :offset="[6, 0]" />
        <span class="text-sm text-blue-700">
          已选 {{ selectedRowKeys.length }} 个报表配置
        </span>
        <div class="flex-1"></div>
        <Tooltip
          :title="
            selectedEnableCount === 0 ? '所选报表中没有可启用的停用报表' : ''
          "
        >
          <Button
            size="small"
            type="primary"
            ghost
            :disabled="selectedEnableCount === 0"
            :loading="batchLoading"
            @click="handleBatchEnable"
          >
            批量启用
          </Button>
        </Tooltip>
        <Tooltip
          :title="
            selectedDisableCount === 0 ? '所选报表中没有可停用的启用报表' : ''
          "
        >
          <Button
            size="small"
            :disabled="selectedDisableCount === 0"
            :loading="batchLoading"
            @click="handleBatchDisable"
          >
            批量停用
          </Button>
        </Tooltip>
        <Button size="small" type="text" @click="selectedRowKeys = []">
          取消选择
        </Button>
      </div>

      <Table
        :columns="columns"
        :data-source="reportList"
        :loading="loading"
        :pagination="false"
        :row-key="(record: SystemApi.ReportConfig) => record.id"
        :row-selection="rowSelection"
        :scroll="{ x: 1200 }"
        size="middle"
      >
        <template #emptyText>
          <ClpmEmptyState
            title="尚未配置自动报表"
            description="从模板快速开始：班报（每班推送当班 KPI 摘要）、日报、月报（管理评审用）。"
            :actions="[
              {
                label: '新建班报',
                primary: true,
                onClick: () => handleCreateFromTemplate('SHIFT', '班报'),
              },
              {
                label: '新建日报',
                onClick: () => handleCreateFromTemplate('DAILY', '日报'),
              },
              {
                label: '新建月报',
                onClick: () => handleCreateFromTemplate('MONTHLY', '月报'),
              },
            ]"
          />
        </template>
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
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                type="link"
                size="small"
                @click="handleOpenEdit(record as SystemApi.ReportConfig)"
              >
                编辑
              </Button>
              <!-- P0-1 诚实化：自动生成暂未开放，按钮置灰 + 说明 -->
              <Tooltip title="自动生成暂未开放，敬请期待">
                <Button type="link" size="small" disabled> 立即生成 </Button>
              </Tooltip>
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
      :title="editingReport ? `编辑报表 - ${editingReport.name}` : '新建报表'"
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
