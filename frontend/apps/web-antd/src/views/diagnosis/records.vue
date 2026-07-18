<script lang="ts" setup>
/**
 * 诊断记录页（原诊断列表改造，仅显示已归档数据）
 *
 * 对齐 PRD §4.4 + 实现契约 v2.0
 * - 顶部 KpiStrip：已归档总数 / 近 7 天归档 / 振荡类 / 阀门粘滞类
 * - 筛选栏（装置 / 诊断标签 / 时间窗）
 * - 表格展示已归档诊断记录（回路位号 / 装置 / 评分 / 诊断标签 / 置信度 / 归档时间 / 操作）
 * - 点击行跳转诊断详情页 /diagnosis/detail/:loopId
 * - 分页
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { PlantNodeApi } from '#/api/plant-node';
import type { KpiStripItem } from '#/components/clpm';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import { Button, message, Modal, Select, Table, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  deleteDiagnosisTaskApi,
  getDiagnosisRecordsApi,
} from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { flattenNodes } from '#/utils/plant-node';

import Tracker from './tracker.vue';

defineOptions({ name: 'DiagnosisRecords' });

const { themeColors } = useClpmTheme();

const router = useRouter();

const loading = ref(false);
const recordList = ref<DiagnosisApi.TaskItem[]>([]);
const total = ref(0);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);
const selectedRowKeys = ref<string[]>([]);
const batchDeleteLoading = ref(false);

/** 行选择配置 */
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys.map(String);
  },
}));

/** 异常跟踪抽屉状态（FDS §5.4：从诊断记录页右侧滑出） */
const trackerDrawerVisible = ref(false);
const trackerLoopId = ref('');

const query = reactive({
  plantNodeId: undefined as string | undefined,
  diagnosisLabel: undefined as DiagnosisLabel | undefined,
  timeWindow: 'last_7_days' as DiagnosisApi.TimeWindow,
  page: 1,
  pageSize: 20,
});

/** 8 类诊断标签选项 */
const labelOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 标签颜色映射 */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

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
    width: 160,
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
    dataIndex: 'labels',
    key: 'labels',
    width: 200,
  },
  {
    title: '触发方式',
    dataIndex: 'triggerType',
    key: 'triggerType',
    width: 90,
    align: 'center',
  },
  {
    title: '诊断时间',
    dataIndex: 'triggeredAt',
    key: 'triggeredAt',
    width: 170,
  },
  {
    title: '归档时间',
    dataIndex: 'completedAt',
    key: 'completedAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
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

/** 加载诊断记录列表（已归档） */
async function loadList() {
  loading.value = true;
  try {
    const data = await getDiagnosisRecordsApi({
      plantNodeId: query.plantNodeId,
      diagnosisLabel: query.diagnosisLabel,
      timeWindow: query.timeWindow,
      page: query.page,
      pageSize: query.pageSize,
    });
    recordList.value = data.items || [];
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

/** 行级删除 */
function handleDelete(record: DiagnosisApi.TaskItem) {
  Modal.confirm({
    title: '确认删除',
    content: `确认删除回路 ${record.tagName} 的诊断记录？`,
    okType: 'danger',
    onOk: async () => {
      await deleteDiagnosisTaskApi(record.taskId);
      message.success('记录已删除');
      selectedRowKeys.value = selectedRowKeys.value.filter(
        (k) => k !== record.taskId,
      );
      await loadList();
    },
  });
}

/** 批量删除 */
async function handleBatchDelete() {
  if (selectedRowKeys.value.length === 0) {
    message.warning('请先选择要删除的记录');
    return;
  }
  const count = selectedRowKeys.value.length;
  Modal.confirm({
    title: '确认批量删除',
    content: `确认删除 ${count} 条诊断记录？`,
    okType: 'danger',
    onOk: async () => {
      batchDeleteLoading.value = true;
      try {
        await Promise.all(
          selectedRowKeys.value.map((id) => deleteDiagnosisTaskApi(id)),
        );
        message.success(`已删除 ${count} 条记录`);
        selectedRowKeys.value = [];
        await loadList();
      } catch {
        // 错误已由拦截器处理
      } finally {
        batchDeleteLoading.value = false;
      }
    },
  });
}

/** KpiStrip 摘要指标：已归档总数 / 近 7 天归档 / 振荡类 / 阀门粘滞类 */
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const totalCount = recordList.value.length;
  const sevenDaysAgo = dayjs().subtract(7, 'day');
  const recentCount = recordList.value.filter((item) =>
    item.completedAt ? dayjs(item.completedAt).isAfter(sevenDaysAgo) : false,
  ).length;
  const oscillationCount = recordList.value.filter((item) =>
    item.labels?.some((l) => l.label === 'OSCILLATION'),
  ).length;
  const stictionCount = recordList.value.filter((item) =>
    item.labels?.some((l) => l.label === 'VALVE_STICTION'),
  ).length;

  return [
    {
      key: 'total',
      label: '已归档总数',
      value: totalCount,
      unit: '条',
      status: 'neutral',
    },
    {
      key: 'recent',
      label: '近 7 天归档',
      value: recentCount,
      unit: '条',
      status: 'primary',
    },
    {
      key: 'oscillation',
      label: '振荡类',
      value: oscillationCount,
      unit: '条',
      status: 'warning',
    },
    {
      key: 'stiction',
      label: '阀门粘滞类',
      value: stictionCount,
      unit: '条',
      status: 'danger',
    },
  ];
});

function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

/**
 * 相对时间格式化（如"2小时前"/"3天前"）
 */
function formatRelativeTime(t: null | string | undefined): string {
  if (!t) return '';
  try {
    const target = dayjs(t);
    const now = dayjs();
    const diffSec = now.diff(target, 'second');
    if (diffSec < 60) return '刚刚';
    const diffMin = now.diff(target, 'minute');
    if (diffMin < 60) return `${diffMin}分钟前`;
    const diffHour = now.diff(target, 'hour');
    if (diffHour < 24) return `${diffHour}小时前`;
    const diffDay = now.diff(target, 'day');
    if (diffDay < 30) return `${diffDay}天前`;
    const diffMonth = now.diff(target, 'month');
    if (diffMonth < 12) return `${diffMonth}个月前`;
    return `${now.diff(target, 'year')}年前`;
  } catch {
    return '';
  }
}

/** 触发方式中文 */
function triggerTypeName(t: string): string {
  return t === 'auto' ? '自动' : '手动';
}

/** 获取记录的诊断标签列表（多 Tag 展示） */
function getRecordTags(record: DiagnosisApi.TaskItem): {
  label: string;
  name: string;
}[] {
  if (!record.labels || record.labels.length === 0) return [];
  return record.labels.map((l) => ({
    label: l.label,
    name: labelName(l.label),
  }));
}

function labelName(label: string): string {
  return getDiagnosisLabelName(label as DiagnosisLabel);
}

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="诊断记录"
      subtitle="查看已归档的诊断任务记录，按装置、标签和时间窗筛选。"
    >
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
      </template>
    </ClpmPageToolbar>

    <!-- 顶部 KpiStrip -->
    <ClpmKpiStrip class="mt-4" :items="kpiStripItems" :loading="loading" />

    <ClpmDataCanvas class="mt-3" title="诊断记录" :loading="loading">
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.plantNodeId"
          placeholder="装置/单元筛选"
          style="width: 220px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.diagnosisLabel"
          placeholder="诊断标签"
          style="width: 160px"
          allow-clear
          :options="labelOptions"
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
        <Button
          danger
          :disabled="selectedRowKeys.length === 0"
          :loading="batchDeleteLoading"
          @click="handleBatchDelete"
        >
          <template #icon>
            <IconifyIcon icon="ant-design:delete-outlined" />
          </template>
          批量删除{{
            selectedRowKeys.length > 0 ? `（${selectedRowKeys.length}）` : ''
          }}
        </Button>
      </div>

      <Table
        :columns="columns"
        :data-source="recordList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: DiagnosisApi.TaskItem) => record.taskId"
        :row-selection="rowSelection"
        :scroll="{ x: 1320 }"
        size="middle"
        :custom-row="
          (record: DiagnosisApi.TaskItem) => ({
            onClick: () => handleViewDetail(record.loopId),
            style: { cursor: 'pointer' },
          })
        "
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'compositeScore'">
            <span
              class="clpm-num font-medium"
              :style="{ color: themeColors.INFO }"
            >
              {{
                record.compositeScore === null
                  ? '—'
                  : Number(record.compositeScore).toFixed(2)
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'labels'">
            <!-- 多 Tag 展示 -->
            <div class="flex flex-wrap gap-1">
              <Tag
                v-for="tag in getRecordTags(record as DiagnosisApi.TaskItem)"
                :key="tag.label"
                :color="labelColorMap[tag.label as DiagnosisLabel] || 'default'"
              >
                {{ tag.name }}
              </Tag>
              <span
                v-if="
                  getRecordTags(record as DiagnosisApi.TaskItem).length === 0
                "
                class="text-xs"
                :style="{ color: themeColors.NEUTRAL }"
              >
                —
              </span>
            </div>
          </template>
          <template v-else-if="column.key === 'triggerType'">
            {{ triggerTypeName(record.triggerType) }}
          </template>
          <template v-else-if="column.key === 'triggeredAt'">
            <div class="flex flex-col leading-tight">
              <span class="clpm-num">{{ formatTime(record.triggeredAt) }}</span>
              <span
                v-if="formatRelativeTime(record.triggeredAt)"
                class="text-xs text-muted-foreground"
              >
                {{ formatRelativeTime(record.triggeredAt) }}
              </span>
            </div>
          </template>
          <template v-else-if="column.key === 'completedAt'">
            <!-- 归档时间列：使用 completedAt 作为归档时间近似值 -->
            <div class="flex flex-col leading-tight">
              <span class="clpm-num">{{ formatTime(record.completedAt) }}</span>
              <span
                v-if="formatRelativeTime(record.completedAt)"
                class="text-xs text-muted-foreground"
              >
                {{ formatRelativeTime(record.completedAt) }}
              </span>
            </div>
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
            <Button
              type="link"
              size="small"
              danger
              @click.stop="handleDelete(record as DiagnosisApi.TaskItem)"
            >
              删除
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
