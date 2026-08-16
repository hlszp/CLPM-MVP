<script setup lang="ts">
/**
 * 诊断工作台 —— 发起与结果一体（单页两段式）。
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 上段：回路多选（URL ?loopId= 带入）+ 时间窗 + 算子组 + 发起（细粒度进度）
 * 下段：结果列表（多回路）→ 选中行展开 DiagnosisResultPanel
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Progress,
  Segmented,
  Select,
  Table,
  message,
} from 'ant-design-vue';

import { getLoopListApi } from '#/api/loop';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { CATEGORY_META, SEVERITY_TEXT } from './constants';
import DiagnosisResultPanel from './components/diagnosis-result-panel.vue';
import { useDiagnosisRunner } from './composables/use-diagnosis-runner';

const route = useRoute();
const router = useRouter();

// ---- 回路选项 ----
const loopOptions = ref<Array<{ label: string; value: string }>>([]);
const loopLoading = ref(false);
const selectedLoopIds = ref<string[]>([]);

async function loadLoopOptions(keyword = '') {
  loopLoading.value = true;
  try {
    // 空 keyword 不传参（后端对空字符串 keyword 返回 422）
    const params = keyword
      ? { page: 1, pageSize: 200, keyword }
      : { page: 1, pageSize: 200 };
    const res = await getLoopListApi(params);
    loopOptions.value = res.items.map((l: LoopApi.LoopListItem) => ({
      label: l.tagName,
      value: l.loopId,
    }));
  } catch {
    loopOptions.value = [];
  } finally {
    loopLoading.value = false;
  }
}

// ---- 发起配置 ----
const timeWindow = ref<'24h' | '30d' | '7d'>('7d');
const operatorGroup = ref<'fast' | 'full'>('full');
const timeWindowMap = { '24h': 'last_24h', '30d': 'last_30d', '7d': 'last_7d' } as const;

const canTrigger = computed(() => selectedLoopIds.value.length > 0 && !runner.running.value);

// ---- 任务执行（细粒度进度 + 完成后拉结果） ----
const selectedRunId = ref('');
const selectedDetail = ref<null | DiagnosisApi.RunDetail>(null);
const detailLoading = ref(false);

async function loadDetail(runId: string) {
  selectedRunId.value = runId;
  detailLoading.value = true;
  selectedDetail.value = null;
  try {
    const { getDiagnosisRunDetailApi } = await import('#/api/diagnosis');
    selectedDetail.value = await getDiagnosisRunDetailApi(runId);
  } finally {
    detailLoading.value = false;
  }
}

const runner = useDiagnosisRunner({
  onFinished(items) {
    if (items.length > 0) {
      loadDetail(items[0]!.id);
      message.success(`诊断完成：${items.length} 个回路`);
    } else {
      message.warning('诊断完成但未产生结果记录');
    }
  },
});

async function handleTrigger() {
  try {
    await runner.trigger({
      loopIds: selectedLoopIds.value,
      timeWindow: { preset: timeWindowMap[timeWindow.value] },
      operatorGroup: operatorGroup.value,
    });
    message.info('诊断任务已提交');
  } catch (error) {
    message.error(`发起诊断失败：${(error as Error).message}`);
  }
}

// ---- 结果列表（多回路批量） ----
const resultColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 130 },
  { dataIndex: 'status', title: '状态', width: 100 },
  { dataIndex: 'primaryCategoryLabel', title: '主分类', width: 160 },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 90 },
  { dataIndex: 'severity', title: '严重度', width: 80 },
];

function confOf(record: DiagnosisApi.RunListItem) {
  return record.primaryConfidence == null
    ? '—'
    : `${Math.round(record.primaryConfidence * 100)}%`;
}

function catColor(record: DiagnosisApi.RunListItem) {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

// ---- URL 上下文（回路工作台跳入）----
const fromWorkbench = computed(() => route.query.from === 'workbench');

function goBackToWorkbench() {
  const loopId = selectedLoopIds.value[0];
  router.push({
    path: '/monitor/loop-workbench',
    query: loopId ? { loopId } : undefined,
  });
}

onMounted(() => {
  loadLoopOptions();
  const q = route.query.loopId;
  if (typeof q === 'string' && q) {
    selectedLoopIds.value = [q];
  }
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="runner.running.value"
      subtitle="回路性能问题定性归因：症状证据 → 原因分类 → 处置建议"
      title="诊断工作台"
    >
      <template #context>
        <button
          v-if="fromWorkbench"
          class="flex items-center gap-1 rounded border border-transparent px-2 py-0.5 text-xs text-blue-600 hover:border-blue-200 hover:bg-blue-50"
          @click="goBackToWorkbench"
        >
          <span>←</span><span>回路工作台</span>
        </button>
      </template>
      <template #actions>
        <ClpmToolbarButton
          :loading="runner.running.value"
          icon="ant-design:sync-outlined"
          label="刷新回路"
          @click="loadLoopOptions()"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 发起区 -->
    <Card class="mb-4" size="small">
      <div class="flex flex-wrap items-center gap-3">
        <Select
          v-model:value="selectedLoopIds"
          :loading="loopLoading"
          :max-tag-count="6"
          :options="loopOptions"
          mode="multiple"
          option-filter-prop="label"
          placeholder="选择回路（可多选，支持搜索）"
          show-search
          style="min-width: 320px"
        />
        <Segmented
          v-model:value="timeWindow"
          :options="[
            { label: '24 小时', value: '24h' },
            { label: '7 天', value: '7d' },
            { label: '30 天', value: '30d' },
          ]"
        />
        <Segmented
          v-model:value="operatorGroup"
          :options="[
            { label: '全量算子', value: 'full' },
            { label: '快速', value: 'fast' },
          ]"
        />
        <Button
          :disabled="!canTrigger"
          :loading="runner.running.value"
          type="primary"
          @click="handleTrigger"
        >
          发起诊断
        </Button>
        <span
          v-if="selectedLoopIds.length === 0"
          class="text-xs text-neutral-400"
        >
          先选择回路
        </span>
      </div>
      <div v-if="runner.running.value || runner.progress.value > 0" class="mt-3">
        <Progress
          :percent="Math.round(runner.progress.value * 100)"
          :status="runner.errorMessage.value ? 'exception' : 'active'"
          size="small"
        />
        <div class="mt-1 text-xs text-neutral-500">
          {{ runner.stage.value || '等待执行' }}
        </div>
      </div>
      <div
        v-if="runner.errorMessage.value"
        class="mt-2 text-xs text-red-500"
      >
        {{ runner.errorMessage.value }}
      </div>
    </Card>

    <!-- 结果区 -->
    <ClpmDataCanvas :empty="runner.resultItems.value.length === 0" empty-text="发起诊断后在此查看结果">
      <div class="space-y-4">
        <Card size="small" title="诊断结果">
          <Table
            :columns="resultColumns"
            :custom-row="
              (record: DiagnosisApi.RunListItem) => ({
                onClick: () => loadDetail(record.id),
              })
            "
            :data-source="runner.resultItems.value"
            :pagination="false"
            :row-class-name="
              (record: DiagnosisApi.RunListItem) =>
                record.id === selectedRunId ? 'diag-row-selected' : ''
            "
            row-key="id"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'status'">
                {{ record.status === 'SUCCESS' ? '完成' : record.status === 'PARTIAL' ? '部分完成' : record.status }}
              </template>
              <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
                <span
                  v-if="record.primaryCategoryLabel"
                  :style="{ color: catColor(record as DiagnosisApi.RunListItem) }"
                  class="font-medium"
                >
                  {{ record.primaryCategoryLabel }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'primaryConfidence'">
                {{ confOf(record as DiagnosisApi.RunListItem) }}
              </template>
              <template v-else-if="column.dataIndex === 'severity'">
                {{ record.severity ? (SEVERITY_TEXT[record.severity] ?? record.severity) : '—' }}
              </template>
            </template>
          </Table>
        </Card>

        <Card size="small" title="结论详情">
          <ClpmDataCanvas
            :empty="!selectedDetail"
            :loading="detailLoading"
            empty-text="在上方结果列表中点击回路查看完整结论"
          >
            <DiagnosisResultPanel v-if="selectedDetail" :detail="selectedDetail" />
          </ClpmDataCanvas>
        </Card>
      </div>
    </ClpmDataCanvas>
  </Page>
</template>

<style scoped>
:deep(.diag-row-selected) {
  td {
    border-top: 1px solid hsl(var(--primary) / 30%);
    border-bottom: 1px solid hsl(var(--primary) / 30%);
  }

  td:first-child {
    border-left: 1px solid hsl(var(--primary) / 30%);
  }

  td:last-child {
    border-right: 1px solid hsl(var(--primary) / 30%);
  }
}
</style>
