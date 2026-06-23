<script lang="ts" setup>
/**
 * S4-DIAG-008 诊断列表页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 筛选栏（装置选择/诊断标签选择/处理状态选择/时间窗选择）
 * - 表格展示诊断列表（回路位号/装置/评分/诊断标签/置信度/处理状态/诊断时间/操作）
 * - 诊断标签使用 Tag 组件，按颜色区分
 * - 置信度使用进度条显示
 * - 点击行跳转诊断详情页 /diagnosis/detail/:loopId
 * - 分页
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Progress, Select, Table, Tag } from 'ant-design-vue';

import { getDiagnosisListApi } from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { $t } from '#/locales';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'DiagnosisList' });

const router = useRouter();

const loading = ref(false);
const diagnosisList = ref<DiagnosisApi.DiagnosisListItem[]>([]);
const total = ref(0);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

const query = reactive({
  plantNodeId: undefined as string | undefined,
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
  { title: '操作', key: 'action', width: 110, fixed: 'right' },
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
  if (val >= 0.8) return '#52c41a';
  if (val >= 0.5) return '#faad14';
  return '#ff4d4f';
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
  <Page :title="$t('diagnosis.list.title')">
    <Card>
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
        :data-source="diagnosisList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: DiagnosisApi.DiagnosisListItem) => record.loopId"
        :scroll="{ x: 1300 }"
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
          </template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
