<script setup lang="ts">
/**
 * 批次诊断记录抽屉（诊断任务页"查看结果"，2026-10-05 用户裁决）
 *
 * 按任务 ID 拉取该批次产出的回路诊断记录（GET /diagnosis/runs?taskId=，
 * 后端原生支持），右侧抽屉内浏览，不跳转诊断记录页。
 * 列口径与诊断记录页前五列一致：回路 / 诊断时间 / 主分类 / 次分类 / 触发方式。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { ref, watch } from 'vue';

import { Drawer, Table, Tag } from 'ant-design-vue';

import { getDiagnosisRunsApi } from '#/api/diagnosis';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatLocalTime } from '#/utils/format';

import { CATEGORY_META, TRIGGER_TYPE_COLOR, TRIGGER_TYPE_TEXT } from '../constants';

defineOptions({ name: 'DiagnosisBatchRunsDrawer' });

const props = defineProps<{
  /** 批次任务 ID（diagnosis_run.task_id 软关联） */
  taskId?: string;
  /** 批次任务标题（抽屉副标题） */
  taskTitle?: string;
}>();

const open = defineModel<boolean>('open', { default: false });

const { themeColors } = useClpmTheme();

const loading = ref(false);
const loadError = ref(false);
const items = ref<DiagnosisApi.RunListItem[]>([]);
const total = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);

async function loadRuns() {
  if (!props.taskId) return;
  loading.value = true;
  loadError.value = false;
  try {
    const res = await getDiagnosisRunsApi({
      taskId: props.taskId,
      page: currentPage.value,
      pageSize: pageSize.value,
    });
    items.value = res.items ?? [];
    total.value = res.total ?? 0;
  } catch (error) {
    console.error('加载批次诊断记录失败:', error);
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

watch(
  [open, () => props.taskId],
  ([visible]) => {
    if (visible) {
      currentPage.value = 1;
      loadRuns();
    }
  },
  { immediate: true },
);

/** 主分类空值兜底：NULL=未见异常（门禁通过但无算子命中）；FAILED 行无结论产出 */
function primaryText(record: DiagnosisApi.RunListItem): string {
  if (record.primaryCategory)
    return record.primaryCategoryLabel ?? record.primaryCategory;
  return record.status === 'FAILED' ? '—' : '未见异常';
}

function primaryColor(record: DiagnosisApi.RunListItem): string {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

/** 次分类文本（多值顿号连接；空为常态——单候选命中/其余候选转待复核） */
function secondaryText(record: DiagnosisApi.RunListItem): string {
  const list = record.secondaryCategories ?? [];
  if (list.length === 0) return '';
  return list
    .map(
      (j) =>
        j.categoryLabel ?? CATEGORY_META[j.category]?.label ?? j.category,
    )
    .join('、');
}

function handleTableChange(p: { current?: number; pageSize?: number }) {
  if (p.current) currentPage.value = p.current;
  if (p.pageSize) pageSize.value = p.pageSize;
  loadRuns();
}

const columns = [
  { dataIndex: 'loopTagName', key: 'loopTagName', title: '回路', width: 130 },
  {
    key: 'createdAt',
    title: '诊断时间',
    width: 150,
    dataIndex: 'createdAt',
  },
  { key: 'primaryCategory', title: '主分类', width: 140, dataIndex: 'primaryCategory' },
  {
    key: 'secondaryCategories',
    title: '次分类',
    width: 160,
    dataIndex: 'secondaryCategories',
  },
  {
    key: 'triggerType',
    title: '触发方式',
    width: 90,
    dataIndex: 'triggerType',
  },
  { key: 'status', title: '状态', width: 80, dataIndex: 'status' },
];

// 暴露给单元测试的接口（逻辑层断言，避开 ant 组件 stub 不渲染插槽的问题）
defineExpose({ loadRuns, primaryText, secondaryText });
</script>

<template>
  <Drawer
    v-model:open="open"
    :title="`批次诊断记录 · ${props.taskTitle ?? props.taskId ?? ''}`"
    placement="right"
    width="min(1080px, 92vw)"
    destroy-on-close
  >
    <ClpmDataCanvas
      :loading="loading"
      :error="loadError"
      :empty="!loading && !loadError && items.length === 0"
      empty-reason="该批次暂无诊断记录（任务可能仍在执行或未产出记录）"
      @retry="loadRuns"
    >
      <Table
        :columns="columns"
        :data-source="items"
        :pagination="{
          current: currentPage,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        row-key="id"
        size="small"
        :scroll="{ x: 760 }"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'loopTagName'">
            <span class="font-mono">{{ record.loopTagName ?? '—' }}</span>
          </template>
          <template v-else-if="column.key === 'createdAt'">
            <span class="clpm-num">{{ formatLocalTime(record.createdAt) }}</span>
          </template>
          <template v-else-if="column.key === 'primaryCategory'">
            <Tag :color="primaryColor(record as DiagnosisApi.RunListItem)">
              {{ primaryText(record as DiagnosisApi.RunListItem) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'secondaryCategories'">
            <span
              v-if="secondaryText(record as DiagnosisApi.RunListItem)"
              class="text-xs"
            >
              {{ secondaryText(record as DiagnosisApi.RunListItem) }}
            </span>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="column.key === 'triggerType'">
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
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="column.key === 'status'">
            <span class="text-xs">{{ record.status }}</span>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>
  </Drawer>
</template>
