<script lang="ts" setup>
/**
 * S4-DIAG-010 Action Tracker 页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 表格展示跟踪记录列表（回路位号/诊断标签/状态/创建时间/更新时间/操作）
 * - 状态标签颜色：PENDING(default)/IN_PROGRESS(processing)/IMPLEMENTED(success)/IGNORED(warning)
 * - 顶部 KpiStrip：待处理 / 处理中 / 已实施 / 已忽略 各状态计数
 * - 状态机可视化：待处理 → 处理中 → 已实施 / 已忽略
 * - 状态更新下拉菜单（仅 IC_ENGINEER 可操作），Modal 含"变更说明"审计字段
 * - "A/B 对比"按钮打开抽屉展示处置前后 KPI 对比图表
 * - "导出 PDF"按钮后端同步生成诊断建议书，前端 Blob 直接下载（FDS §5.4.4）
 * - 筛选栏（状态/标签/时间）
 * - 抽屉模式与独立页模式统一使用 CLPM 组件
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { KpiStripItem } from '#/components/clpm';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Drawer,
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
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useIndustrialStatus } from '#/composables/use-industrial-status';
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

const { getStatusMeta } = useIndustrialStatus();
const { themeColors } = useClpmTheme();

/**
 * 动态容器：抽屉模式用 Drawer，独立页模式用 Page
 * 通过 v-bind="containerProps" 和 v-on="containerListeners" 处理两模式 props/事件差异，
 * 消除抽屉/独立页约 500 行模板重复。
 */
const containerComponent = computed(() => (props.drawerMode ? Drawer : Page));
const containerProps = computed(() =>
  props.drawerMode
    ? { open: true, title: '异常跟踪', width: '80%', placement: 'right' }
    : {},
);
const containerListeners = computed(() =>
  props.drawerMode ? { close: () => emit('close') } : {},
);

const loading = ref(false);
const trackerList = ref<DiagnosisApi.TrackerItem[]>([]);
const total = ref(0);
/** 全量聚合统计（后端 SQL group-by，不受分页影响） */
const aggregates = ref<DiagnosisApi.DiagnosisAggregates | null>(null);

const query = reactive({
  diagnosisLabel: undefined as DiagnosisLabel | undefined,
  actionStatus: undefined as DiagnosisApi.ActionStatus | undefined,
  loopId: props.loopId || undefined,
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

/** 处理状态颜色映射（已迁移至 useIndustrialStatus，保留 statusOptions 用于下拉） */
// const statusColorMap 已废弃，改用 useIndustrialStatus().getStatusMeta(status)

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

/** KpiStrip 摘要指标：各状态计数（后端聚合口径，不受分页影响） */
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const counts = aggregates.value?.statusCounts ?? {};
  return [
    {
      key: 'pending',
      label: '待处理',
      value: counts.PENDING ?? 0,
      unit: '条',
      status: 'warning',
    },
    {
      key: 'in_progress',
      label: '处理中',
      value: counts.IN_PROGRESS ?? 0,
      unit: '条',
      status: 'primary',
    },
    {
      key: 'implemented',
      label: '已实施',
      value: counts.IMPLEMENTED ?? 0,
      unit: '条',
      status: 'success',
    },
    {
      key: 'ignored',
      label: '已忽略',
      value: counts.IGNORED ?? 0,
      unit: '条',
      status: 'neutral',
    },
  ];
});

// 状态更新 Modal
const statusModalVisible = ref(false);
const statusModalLoading = ref(false);
const editingItem = ref<DiagnosisApi.TrackerItem | null>(null);
const statusForm = reactive({
  status: 'PENDING' as DiagnosisApi.ActionStatus,
  comment: '',
  changeRemark: '',
});

// A/B 对比抽屉
const abCompareVisible = ref(false);
const abCompareLoopId = ref('');
const abCompareImplementedAt = ref('');

/** 正在导出 PDF 的回路 ID（空串表示无导出中任务，防重复点击） */
const exportingLoopId = ref('');

/** 加载列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getTrackerListApi({
      diagnosisLabel: query.diagnosisLabel,
      actionStatus: query.actionStatus,
      loopId: query.loopId,
      timeWindow: query.timeWindow,
      page: query.page,
      pageSize: query.pageSize,
    });
    trackerList.value = data.items || [];
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

/** 提交状态更新（含变更说明审计字段） */
async function handleSubmitStatus() {
  if (!editingItem.value) return;
  if (!statusForm.changeRemark.trim()) {
    message.warning('请填写变更说明');
    return;
  }
  statusModalLoading.value = true;
  try {
    await updateTrackerStatusApi(editingItem.value.loopId, {
      status: statusForm.status,
      comment: statusForm.comment,
      changeRemark: statusForm.changeRemark,
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
  statusForm.changeRemark = '';
  statusModalVisible.value = true;
}

/** 生成导出 PDF 文件名：CLPM-诊断建议书-[位号]-[日期].pdf */
function buildExportFileName(tagName: string): string {
  const d = new Date();
  const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return `CLPM-诊断建议书-${tagName}-${date}.pdf`;
}

/** 导出 PDF（FDS §5.4.4：后端同步生成，前端 Blob 直接下载） */
async function handleExportPdf(record: DiagnosisApi.TrackerItem) {
  // 同一行正在导出时，避免重复提交
  if (exportingLoopId.value === record.loopId) {
    return;
  }
  exportingLoopId.value = record.loopId;
  try {
    const blob = await exportDiagnosisPdfApi(record.loopId);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = buildExportFileName(record.tagName);
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('诊断建议书已导出');
  } catch {
    // 错误已由拦截器处理
  } finally {
    exportingLoopId.value = '';
  }
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

/** 工具栏：刷新 */
function handleRefresh() {
  loadList();
}

// P2 #37 UX13: 导出/批量处理功能开发中，按钮改为 disabled + tooltip
// （原 message.info 让用户困惑，现在按钮灰显并悬浮显示原因）

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    // 强制北京时间（UTC+8）
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
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
</script>

<template>
  <!--
    抽屉模式 / 独立页模式统一容器：
    - 抽屉模式（drawerMode=true）：从诊断列表页/详情页右侧滑出，用 Drawer
    - 独立页模式（drawerMode=false）：直接路由访问 /diagnosis/tracker，用 Page
    通过 containerComponent/containerProps/containerListeners 消除两模式约 500 行模板重复
  -->
  <component
    :is="containerComponent"
    v-bind="containerProps"
    v-on="containerListeners"
  >
    <ClpmPageToolbar
      :compact="drawerMode"
      :title="drawerMode ? undefined : '异常跟踪'"
      :subtitle="
        drawerMode
          ? '状态、标签、时间窗统一筛选'
          : '状态、标签、时间窗统一筛选，跟踪异常处置闭环'
      "
    >
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
          disabled
          disabled-reason="导出功能开发中，待后端接口支持"
        />
        <ClpmToolbarButton
          icon="ant-design:thunderbolt-outlined"
          label="批量处理"
          variant="primary"
          disabled
          disabled-reason="批量处理功能开发中，待后端接口支持"
        />
      </template>
    </ClpmPageToolbar>

    <!-- KpiStrip：各状态计数 -->
    <ClpmKpiStrip
      :class="drawerMode ? 'mt-3' : 'mt-4'"
      :items="kpiStripItems"
      :loading="loading"
    />

    <!-- 状态机可视化：待处理 → 处理中 → 已实施 / 已忽略 -->
    <div class="status-flow-bar mt-3">
      <span class="status-flow-bar__label">状态流转</span>
      <Tag color="default">待处理</Tag>
      <span class="status-flow-bar__arrow">→</span>
      <Tag color="processing">处理中</Tag>
      <span class="status-flow-bar__arrow">→</span>
      <Tag color="success">已实施</Tag>
      <span class="status-flow-bar__alt">/</span>
      <Tag color="warning">已忽略</Tag>
    </div>

    <ClpmDataCanvas class="mt-3" title="异常跟踪列表" :loading="loading">
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
            <span
              class="clpm-num font-medium"
              :style="{ color: themeColors.INFO }"
            >
              {{ Number(record.compositeScore).toFixed(2) }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidence'">
            <span class="clpm-num">{{
              Number(record.confidence).toFixed(2)
            }}</span>
          </template>
          <template v-else-if="column.key === 'actionStatus'">
            <Tag
              :color="getStatusMeta(record.actionStatus as string).color"
              :style="{
                background: getStatusMeta(record.actionStatus as string)
                  .bgColor,
                borderColor: getStatusMeta(record.actionStatus as string)
                  .borderColor,
              }"
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
                  @click="
                    handleOpenAbCompare(record as DiagnosisApi.TrackerItem)
                  "
                >
                  A/B 对比
                </Button>
                <Button
                  v-permission="['IC_ENGINEER', 'PE_ENGINEER', 'EXPERT']"
                  type="link"
                  size="small"
                  :loading="exportingLoopId === record.loopId"
                  :disabled="
                    exportingLoopId !== '' && exportingLoopId !== record.loopId
                  "
                  @click="handleExportPdf(record as DiagnosisApi.TrackerItem)"
                >
                  导出 PDF
                </Button>
              </div>
            </div>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 状态更新 Modal（含变更说明审计字段） -->
    <Modal
      v-model:open="statusModalVisible"
      title="更新处理状态"
      :confirm-loading="statusModalLoading"
      width="520px"
      @ok="handleSubmitStatus"
    >
      <Form :model="statusForm" layout="vertical" class="pt-4">
        <FormItem label="回路位号">
          <span class="font-medium">{{ editingItem?.tagName }}</span>
        </FormItem>
        <FormItem label="处理状态" required>
          <Select v-model:value="statusForm.status" :options="statusOptions" />
        </FormItem>
        <FormItem label="变更说明" required>
          <Input.TextArea
            v-model:value="statusForm.changeRemark"
            placeholder="请说明本次状态变更的原因或依据，例如：经现场确认阀门存在粘滞，已安排检修"
            :rows="3"
            :maxlength="500"
            show-count
          />
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
  </component>
</template>

<style scoped>
/* 状态机可视化条 */
.status-flow-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.status-flow-bar__label {
  margin-right: 4px;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.status-flow-bar__arrow {
  font-size: 14px;
  font-weight: 700;
  color: hsl(var(--muted-foreground));
}

.status-flow-bar__alt {
  margin: 0 2px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}
</style>
