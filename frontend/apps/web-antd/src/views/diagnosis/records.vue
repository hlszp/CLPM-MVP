<script setup lang="ts">
/**
 * 诊断记录 —— 历史列表 + 筛选 + 导出 + 抽屉详情。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 */
import type { Dayjs } from 'dayjs';

import type { DiagnosisApi } from '#/api/diagnosis';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Card,
  DatePicker,
  Drawer,
  message,
  Select,
  Table,
} from 'ant-design-vue';

import {
  exportDiagnosisRunsApi,
  getDiagnosisRunDetailApi,
  getDiagnosisRunsApi,
} from '#/api/diagnosis';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';

import DiagnosisResultPanel from './components/diagnosis-result-panel.vue';
import {
  CATEGORY_META,
  CATEGORY_OPTIONS,
  REVIEW_STATUS_COLOR,
  REVIEW_STATUS_TEXT,
  RUN_STATUS_TEXT,
  SEVERITY_TEXT,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
} from './constants';

const { RangePicker } = DatePicker;

const loading = ref(false);
const items = ref<DiagnosisApi.RunListItem[]>([]);
const total = ref(0);
const exporting = ref(false);

const query = reactive({
  page: 1,
  pageSize: 20,
  category: undefined as DiagnosisApi.Category | undefined,
  severity: undefined as DiagnosisApi.Severity | undefined,
  status: undefined as DiagnosisApi.RunStatus | undefined,
  reviewStatus: undefined as DiagnosisApi.ReviewStatus | undefined,
  range: undefined as [Dayjs, Dayjs] | undefined,
});

async function load() {
  loading.value = true;
  try {
    const params: DiagnosisApi.RunQuery = {
      page: query.page,
      pageSize: query.pageSize,
      category: query.category,
      severity: query.severity,
      status: query.status,
      reviewStatus: query.reviewStatus,
    };
    if (query.range) {
      params.startTime = `${query.range[0].format('YYYY-MM-DD')}T00:00:00`;
      params.endTime = `${query.range[1].format('YYYY-MM-DD')}T23:59:59`;
    }
    const res = await getDiagnosisRunsApi(params);
    items.value = res.items;
    total.value = res.total;
  } finally {
    loading.value = false;
  }
}

function handleTableChange(pag: { current?: number; pageSize?: number }) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  load();
}

// ---- 抽屉详情 ----
const drawerOpen = ref(false);
const detail = ref<DiagnosisApi.RunDetail | null>(null);
const detailLoading = ref(false);

async function openDetail(record: DiagnosisApi.RunListItem) {
  drawerOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  try {
    detail.value = await getDiagnosisRunDetailApi(record.id);
  } finally {
    detailLoading.value = false;
  }
}

// ---- 导出 ----
async function handleExport() {
  exporting.value = true;
  try {
    const blob = await exportDiagnosisRunsApi({
      category: query.category,
      severity: query.severity,
      status: query.status,
    });
    const url = URL.createObjectURL(
      new Blob([blob as unknown as BlobPart], {
        type: 'text/csv;charset=utf-8',
      }),
    );
    const a = document.createElement('a');
    a.href = url;
    a.download = `diagnosis_runs_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    message.error('导出失败');
  } finally {
    exporting.value = false;
  }
}

const columns = [
  { dataIndex: 'createdAt', title: '时间', width: 150 },
  { dataIndex: 'loopTagName', title: '回路', width: 120 },
  { dataIndex: 'primaryCategoryLabel', title: '主分类', width: 150 },
  { dataIndex: 'secondaryCategories', title: '次分类', width: 150 },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 80 },
  { dataIndex: 'severity', title: '严重度', width: 76 },
  { dataIndex: 'timeWindowStart', title: '时间窗', width: 220 },
  { dataIndex: 'triggerType', title: '触发方式', width: 88 },
  { dataIndex: 'reviewResultLabels', title: '复核结论', width: 150 },
  { dataIndex: 'reviewStatus', title: '复核状态', width: 88 },
  { dataIndex: 'triggeredBy', title: '发起人', width: 100 },
  { dataIndex: 'status', title: '状态', width: 90 },
];

function fmtWindow(record: DiagnosisApi.RunListItem) {
  const s = record.timeWindowStart?.slice(5, 16).replace('T', ' ');
  const e = record.timeWindowEnd?.slice(5, 16).replace('T', ' ');
  return s && e ? `${s} ~ ${e}` : '—';
}

function catColor(record: DiagnosisApi.RunListItem) {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

function secondaryText(record: DiagnosisApi.RunListItem) {
  if (!record.secondaryCategories?.length) return '—';
  return record.secondaryCategories
    .map(
      (j) => j.categoryLabel ?? CATEGORY_META[j.category]?.label ?? j.category,
    )
    .join('、');
}

onMounted(load);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="历史诊断记录检索 · 按分类/严重度筛选 · 点击行查看完整结论"
      title="诊断记录"
    >
      <template #actions>
        <ClpmToolbarButton
          :loading="exporting"
          icon="ant-design:download-outlined"
          label="导出 CSV"
          @click="handleExport"
        />
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="load()"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 筛选行 -->
    <div class="mb-3 mt-2 flex flex-wrap items-center gap-3">
      <RangePicker
        v-model:value="query.range"
        style="width: 240px"
        @change="load()"
      />
      <Select
        v-model:value="query.category"
        :allow-clear="true"
        :options="CATEGORY_OPTIONS"
        placeholder="主分类"
        style="width: 160px"
        @change="load()"
      />
      <Select
        v-model:value="query.severity"
        :allow-clear="true"
        :options="[
          { label: '高', value: 'HIGH' },
          { label: '中', value: 'MEDIUM' },
          { label: '低', value: 'LOW' },
        ]"
        placeholder="严重度"
        style="width: 110px"
        @change="load()"
      />
      <Select
        v-model:value="query.status"
        :allow-clear="true"
        :options="[
          { label: '完成', value: 'SUCCESS' },
          { label: '部分完成', value: 'PARTIAL' },
          { label: '失败', value: 'FAILED' },
        ]"
        placeholder="状态"
        style="width: 120px"
        @change="load()"
      />
      <Select
        v-model:value="query.reviewStatus"
        :allow-clear="true"
        :options="[
          { label: '待复核', value: 'PENDING' },
          { label: '已复核', value: 'REVIEWED' },
        ]"
        placeholder="复核状态"
        style="width: 110px"
        @change="load()"
      />
    </div>

    <Card :body-style="{ padding: '0' }" size="small">
      <ClpmDataCanvas
        :empty="!loading && items.length === 0"
        empty-text="暂无诊断记录"
      >
        <Table
          :columns="columns"
          :custom-row="
            (record: DiagnosisApi.RunListItem) => ({
              onClick: () => openDetail(record),
              style: { cursor: 'pointer' },
            })
          "
          :data-source="items"
          :pagination="{
            current: query.page,
            pageSize: query.pageSize,
            showSizeChanger: true,
            pageSizeOptions: ['10', '20', '50'],
            showTotal: (t: number) => `共 ${t} 条`,
            total,
          }"
          :loading="loading"
          row-key="id"
          size="small"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'createdAt'">
              {{ record.createdAt?.slice(0, 19).replace('T', ' ') }}
            </template>
            <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
              <span
                v-if="record.primaryCategory"
                :style="{ color: catColor(record as DiagnosisApi.RunListItem) }"
                class="font-medium"
              >
                {{ record.primaryCategoryLabel }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'secondaryCategories'">
              {{ secondaryText(record as DiagnosisApi.RunListItem) }}
            </template>
            <template v-else-if="column.dataIndex === 'primaryConfidence'">
              {{
                record.primaryConfidence == null
                  ? '—'
                  : `${Math.round(record.primaryConfidence * 100)}%`
              }}
            </template>
            <template v-else-if="column.dataIndex === 'severity'">
              {{
                record.severity
                  ? (SEVERITY_TEXT[record.severity] ?? record.severity)
                  : '—'
              }}
            </template>
            <template v-else-if="column.dataIndex === 'timeWindowStart'">
              {{ fmtWindow(record as DiagnosisApi.RunListItem) }}
            </template>
            <template v-else-if="column.dataIndex === 'triggerType'">
              <span
                v-if="record.triggerType"
                :style="{ color: TRIGGER_TYPE_COLOR[record.triggerType] }"
              >
                {{
                  record.triggerTypeLabel ??
                  TRIGGER_TYPE_TEXT[record.triggerType] ??
                  record.triggerType
                }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'reviewResultLabels'">
              <span v-if="record.reviewResultLabels?.length" class="text-xs">
                {{ record.reviewResultLabels.join('、') }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'reviewStatus'">
              <span
                v-if="record.reviewStatus"
                :style="{ color: REVIEW_STATUS_COLOR[record.reviewStatus] }"
              >
                {{
                  REVIEW_STATUS_TEXT[record.reviewStatus] ?? record.reviewStatus
                }}
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'status'">
              {{ RUN_STATUS_TEXT[record.status] ?? record.status }}
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </Card>

    <Drawer
      v-model:open="drawerOpen"
      title="诊断结论"
      width="720"
      :destroy-on-close="true"
    >
      <ClpmDataCanvas
        :empty="!detail"
        :loading="detailLoading"
        empty-text="无详情"
      >
        <DiagnosisResultPanel v-if="detail" :detail="detail" />
      </ClpmDataCanvas>
    </Drawer>
  </Page>
</template>
