<script lang="ts" setup>
/**
 * S4-DIAG-008 诊断列表页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 顶部 KpiStrip：待处理 / 处理中 / 近 7 天新增
 * - Partial 警告横幅：INCONCLUSIVE 回路提示
 * - 筛选栏（装置/诊断标签/处理状态/可信度等级/时间窗）
 * - 表格展示诊断列表（回路位号/装置/评分/诊断标签/置信度/处理状态/诊断时间/操作）
 * - 诊断标签使用 Tag 组件，按颜色区分
 * - 置信度使用进度条显示
 * - 点击行跳转诊断详情页 /diagnosis/detail/:loopId
 * - 分页
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  message,
  Progress,
  Select,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisListApi } from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmDataCanvas, ClpmKpiStrip, ClpmPageToolbar, ClpmToolbarButton } from '#/components/clpm';
import type { KpiStripItem } from '#/components/clpm';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { $t } from '#/locales';
import { flattenNodes } from '#/utils/plant-node';

import Tracker from './tracker.vue';

defineOptions({ name: 'DiagnosisList' });

const { themeColors } = useClpmTheme();

/** 可信度等级（对齐 ConfidenceEvaluator A/B/C/D/E） */
type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E';

const router = useRouter();

const loading = ref(false);
const diagnosisList = ref<DiagnosisApi.DiagnosisListItem[]>([]);
const total = ref(0);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

/** 异常跟踪抽屉状态（FDS §5.4：从诊断列表页右侧滑出） */
const trackerDrawerVisible = ref(false);
const trackerLoopId = ref('');

const query = reactive({
  plantNodeId: undefined as string | undefined,
  diagnosisLabel: undefined as DiagnosisLabel | undefined,
  actionStatus: undefined as DiagnosisApi.ActionStatus | undefined,
  confidenceLevel: undefined as ConfidenceLevel | undefined,
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

/** 可信度等级选项（对齐 ConfidenceEvaluator A/B/C/D/E，valid_rate 阈值 95/80/60/20%） */
const confidenceLevelOptions: { label: string; value: ConfidenceLevel }[] = [
  { label: 'A级（≥95%）', value: 'A' },
  { label: 'B级（80~95%）', value: 'B' },
  { label: 'C级（60~80%）', value: 'C' },
  { label: 'D级（20~60%）', value: 'D' },
  { label: 'E级（<20%，不确定）', value: 'E' },
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
    title: '综合评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 100,
    align: 'right',
  },
  {
    title: '诊断标签',
    dataIndex: 'diagnosisLabel',
    key: 'diagnosisLabel',
    width: 130,
  },
  {
    title: '置信度',
    dataIndex: 'confidence',
    key: 'confidence',
    width: 160,
  },
  {
    title: '融合置信度',
    dataIndex: 'fusedConfidence',
    key: 'fusedConfidence',
    width: 110,
    align: 'right',
  },
  {
    title: '处理状态',
    dataIndex: 'actionStatus',
    key: 'actionStatus',
    width: 100,
  },
  {
    title: '诊断时间',
    dataIndex: 'diagnosedAt',
    key: 'diagnosedAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 180, fixed: 'right' },
];

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载诊断列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getDiagnosisListApi({
      plantNodeId: query.plantNodeId,
      diagnosisLabel: query.diagnosisLabel,
      actionStatus: query.actionStatus,
      timeWindow: query.timeWindow,
      page: query.page,
      pageSize: query.pageSize,
    });
    diagnosisList.value = data.items || [];
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

/** 跳转诊断详情 */
function handleViewDetail(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

/** 打开异常跟踪抽屉（FDS §5.4） */
function handleOpenTracker(loopId: string) {
  trackerLoopId.value = loopId;
  trackerDrawerVisible.value = true;
}

/** 工具栏：刷新 */
function handleRefresh() {
  loadList();
}

/** 工具栏：导出 */
function handleExport() {
  message.info('导出功能开发中');
}

/** 工具栏：批量处理 */
function handleBatchProcess() {
  message.info('批量处理功能开发中');
}

/**
 * 根据诊断 confidence（0~1）推导可信度等级
 * 对齐 ConfidenceEvaluator A/B/C/D/E 阈值（95/80/60/20%）
 *
 * 注：DiagnosisListItem 暂无 good_value_rate 字段，使用 confidence 作为代理
 */
function deriveConfidenceLevel(confidence: number): ConfidenceLevel | '—' {
  const rate = confidence * 100;
  if (rate >= 95) return 'A';
  if (rate >= 80) return 'B';
  if (rate >= 60) return 'C';
  if (rate >= 20) return 'D';
  if (rate > 0) return 'E';
  return '—';
}

/** KpiStrip 摘要指标：待处理 / 处理中 / 近 7 天新增 */
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const pendingCount = diagnosisList.value.filter(
    (item) => item.actionStatus === 'PENDING',
  ).length;
  const inProgressCount = diagnosisList.value.filter(
    (item) => item.actionStatus === 'IN_PROGRESS',
  ).length;
  const sevenDaysAgo = dayjs().subtract(7, 'day');
  const recentNewCount = diagnosisList.value.filter((item) =>
    item.diagnosedAt ? dayjs(item.diagnosedAt).isAfter(sevenDaysAgo) : false,
  ).length;

  return [
    {
      key: 'pending',
      label: '待处理',
      value: pendingCount,
      unit: '条',
      status: 'warning',
    },
    {
      key: 'in_progress',
      label: '处理中',
      value: inProgressCount,
      unit: '条',
      status: 'primary',
    },
    {
      key: 'recent_new',
      label: '近 7 天新增',
      value: recentNewCount,
      unit: '条',
      status: 'neutral',
    },
  ];
});

/** INCONCLUSIVE 回路数（可信度等级为 E，即 valid_rate < 20%） */
const inconclusiveCount = computed(
  () =>
    diagnosisList.value.filter(
      (item) => deriveConfidenceLevel(item.confidence) === 'E',
    ).length,
);

/** 是否显示 INCONCLUSIVE 警告横幅 */
const showInconclusiveAlert = computed(() => inconclusiveCount.value > 0);

/**
 * 按可信度等级前端过滤（后端暂不支持该筛选条件）
 * 注意：仅过滤当前页数据，分页总数仍为 API 返回值
 */
const filteredDiagnosisList = computed(() => {
  if (!query.confidenceLevel) return diagnosisList.value;
  return diagnosisList.value.filter(
    (item) => deriveConfidenceLevel(item.confidence) === query.confidenceLevel,
  );
});

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

/** 置信度颜色 */
function confidenceColor(val: number): string {
  if (val >= 0.8) return themeColors.value.SUCCESS;
  if (val >= 0.5) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

function labelName(label: DiagnosisLabel): string {
  return getDiagnosisLabelName(label);
}

function statusName(status: DiagnosisApi.ActionStatus): string {
  return statusOptions.find((o) => o.value === status)?.label || status;
}

/** Progress 格式化函数（兼容 number | undefined） */
function formatPercent(p: number | undefined): string {
  if (p === undefined || p === null || Number.isNaN(p)) return '—';
  return `${p?.toFixed(2) ?? '0.00'}%`;
}

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :title="$t('diagnosis.list.title')"
      subtitle="按诊断标签、状态和时间窗查看异常对象，并快速进入详情或异常跟踪。"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loading"
          @click="handleRefresh"
        />
        <ClpmToolbarButton icon="export" label="导出" @click="handleExport" />
        <ClpmToolbarButton
          icon="ant-design:thunderbolt-outlined"
          label="批量处理"
          variant="primary"
          @click="handleBatchProcess"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 顶部 KpiStrip：待处理 / 处理中 / 近 7 天新增 -->
    <ClpmKpiStrip class="mt-4" :items="kpiStripItems" :loading="loading" />

    <!-- Partial 警告横幅：INCONCLUSIVE 回路提示 -->
    <Alert
      v-if="showInconclusiveAlert"
      class="mt-3"
      type="warning"
      show-icon
      :message="`当前有 ${inconclusiveCount} 个回路评估结果为不确定（INCONCLUSIVE），建议检查数据质量后重新评估`"
    />

    <ClpmDataCanvas class="mt-3" title="诊断列表" :loading="loading">
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.plantNodeId"
          :placeholder="$t('diagnosis.list.plantNodePlaceholder')"
          style="width: 220px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.diagnosisLabel"
          :placeholder="$t('diagnosis.list.labelPlaceholder')"
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
          v-model:value="query.confidenceLevel"
          placeholder="可信度等级"
          style="width: 180px"
          allow-clear
          :options="confidenceLevelOptions"
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
        :data-source="filteredDiagnosisList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: DiagnosisApi.DiagnosisListItem) => record.loopId"
        :scroll="{ x: 1370 }"
        size="middle"
        :custom-row="
          (record: DiagnosisApi.DiagnosisListItem) => ({
            onClick: () => handleViewDetail(record.loopId),
            style: { cursor: 'pointer' },
          })
        "
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'compositeScore'">
            <span class="font-medium text-blue-600">
              {{ Number(record.compositeScore).toFixed(2) }}
            </span>
          </template>
          <template v-else-if="column.key === 'diagnosisLabel'">
            <Tag
              :color="labelColorMap[record.diagnosisLabel as DiagnosisLabel]"
            >
              {{ record.labelName || labelName(record.diagnosisLabel) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'confidence'">
            <Progress
              :percent="Number((record.confidence * 100).toFixed(2))"
              :stroke-color="confidenceColor(record.confidence)"
              :show-info="true"
              size="small"
              :format="formatPercent"
            />
          </template>
          <template v-else-if="column.key === 'fusedConfidence'">
            <span :style="{ color: confidenceColor(record.fusedConfidence) }">
              {{ Number(record.fusedConfidence).toFixed(2) }}
            </span>
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
          <template v-else-if="column.key === 'diagnosedAt'">
            {{ formatTime(record.diagnosedAt) }}
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
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 异常跟踪抽屉（FDS §5.4：从右侧滑出） -->
    <Tracker
      v-if="trackerDrawerVisible"
      :drawer-mode="true"
      :loop-id="trackerLoopId"
      @close="trackerDrawerVisible = false"
    />
  </Page>
</template>
