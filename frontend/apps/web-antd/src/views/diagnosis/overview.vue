<script setup lang="ts">
/**
 * 诊断概览 —— 每回路最新一条诊断结论（2026-10-01 UX 重构：原诊断工作台
 * "未勾选回路时显示的最新诊断概览"独立成页）。
 *
 * 布局：左脊柱装置树（范围切换）+ 主区（诊断健康度覆盖面板 + 概览表）。
 * 行操作：证据 / 复核 / 历史 / 诊断（诊断=跳工作台并预选该回路——
 * 发起职责收敛到工作台，本页只读概览 + 复核闭环）。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Select, Spin, Table, Tree } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisRunsLatestApi } from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';

import DiagnosisCoveragePanel from './components/coverage-panel.vue';
import DiagnosisDetailModal from './components/diagnosis-detail-modal.vue';
import DiagnosisEvidenceDrawer from './components/evidence-drawer.vue';
import DiagnosisLoopArchiveDrawer from './components/loop-archive-drawer.vue';
import DiagnosisReviewDrawer from './components/review-drawer.vue';
import {
  CATEGORY_META,
  CATEGORY_OPTIONS,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  REVIEW_STATUS_COLOR,
  REVIEW_STATUS_TEXT,
  SCORE_GRADES,
  scoreGrade,
  SEVERITY_TEXT,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
} from './constants';

const router = useRouter();

// ===== 左脊柱：装置树 =====
/** ant Tree 节点约定为 {key, title}（TreeSelect 才是 {value, label}） */
interface PlantTreeNode {
  children?: PlantTreeNode[];
  key: string;
  title: string;
}

const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
const plantTreeExpandedKeys = ref<string[]>([]);
const plantTreeSelectedKeys = ref<string[]>([]);
const selectedPlantNodeId = ref<string | undefined>(undefined);

function buildTreeNodes(nodes: PlantNodeApi.PlantNode[]): PlantTreeNode[] {
  return nodes.map((n) => ({
    key: n.id,
    title: n.name,
    children: n.children?.length ? buildTreeNodes(n.children) : undefined,
  }));
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildTreeNodes(tree);
    plantTreeExpandedKeys.value = tree.map((n) => n.id);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

/** 装置节点选中：重拉该范围概览 */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  loadLatestOverview();
}

// ===== 最新诊断概览（每回路最新一条 + 未诊断回路） =====
const latestItems = ref<DiagnosisApi.LatestRunItem[]>([]);
const latestLoading = ref(false);

/** 后端时间为 naive UTC ISO（无 Z 后缀），补 Z 后按本地时区展示 */
function fmtUtc(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso)
    ? naiveIso
    : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

async function loadLatestOverview(): Promise<void> {
  latestLoading.value = true;
  try {
    const res = await getDiagnosisRunsLatestApi(selectedPlantNodeId.value);
    latestItems.value = res.items;
  } catch {
    latestItems.value = [];
  } finally {
    latestLoading.value = false;
  }
}

// ===== 概览筛选（诊断状态 + 等级/评分/结论/严重度） =====
type LatestFilter = 'all' | 'diagnosed' | 'undiagnosed';
const latestFilter = ref<LatestFilter>('all');
const filterImportance = ref<number | undefined>();
const filterScoreGrade = ref<string | undefined>();
const filterCategory = ref<DiagnosisApi.Category | undefined>();
const filterSeverity = ref<DiagnosisApi.Severity | undefined>();

const filteredLatestItems = computed(() => {
  let list = latestItems.value;
  if (latestFilter.value === 'diagnosed') list = list.filter((i) => i.runId);
  if (latestFilter.value === 'undiagnosed') list = list.filter((i) => !i.runId);
  if (filterImportance.value != null)
    list = list.filter((i) => i.importanceLevel === filterImportance.value);
  if (filterScoreGrade.value) {
    const grade = SCORE_GRADES.find((g) => g.key === filterScoreGrade.value);
    if (grade) {
      const next = SCORE_GRADES.find((g) => g.min < grade.min);
      list = list.filter((i) => {
        if (i.latestScore == null) return false;
        return (
          i.latestScore >= grade.min && (next ? i.latestScore < next.min : true)
        );
      });
    }
  }
  if (filterCategory.value)
    list = list.filter((i) => i.primaryCategory === filterCategory.value);
  if (filterSeverity.value)
    list = list.filter((i) => i.severity === filterSeverity.value);
  return list;
});

/** 未诊断回路数（一回路一条） */
const undiagnosedCount = computed(
  () => latestItems.value.filter((item) => !item.runId).length,
);

/** 概览覆盖回路数（用于标题"N 个回路"） */
const overviewLoopCount = computed(() => latestItems.value.length);

const latestColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 116 },
  { dataIndex: 'loopDescription', title: '名称', width: 126, ellipsis: true },
  { dataIndex: 'importanceLevel', title: '等级', width: 54 },
  { dataIndex: 'latestScore', title: '性能评分', width: 70 },
  { key: 'scoreGrade', title: '性能等级', width: 66 },
  {
    dataIndex: 'primaryCategoryLabel',
    title: '诊断结论',
    width: 140,
    ellipsis: true,
  },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 62 },
  { dataIndex: 'severity', title: '严重度', width: 54 },
  { dataIndex: 'triggerType', title: '触发方式', width: 80 },
  { dataIndex: 'runCount', title: '诊断次序', width: 68 },
  {
    dataIndex: 'reviewResultLabels',
    title: '复核结论',
    width: 130,
    ellipsis: true,
  },
  { dataIndex: 'reviewStatus', title: '状态', width: 66 },
  { dataIndex: 'lastDiagnosedAt', title: '诊断时间', width: 96 },
  { key: 'action', title: '操作', width: 156, fixed: 'right' as const },
];

function latestCatColor(record: DiagnosisApi.LatestRunItem): string {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}

function openLatestDetail(record: DiagnosisApi.LatestRunItem): void {
  // 概览行点击 → 诊断详情弹窗（基本信息 + KPI + 结论 + 证据）
  detailItem.value = record;
  detailModalOpen.value = true;
}

// ===== 诊断详情弹窗（行点击弹出） =====
const detailModalOpen = ref(false);
const detailItem = ref<DiagnosisApi.LatestRunItem | null>(null);

/** 行内"诊断"→ 跳工作台预选该回路（发起职责收敛到工作台，2026-10-01） */
function gotoWorkbench(loopId: string): void {
  router.push({ path: '/diagnosis/workbench', query: { loopId } });
}

// ===== 概览行操作：证据 / 复核 / 历史 =====
const evidenceOpen = ref(false);
const evidenceRunId = ref<null | string>(null);
const reviewOpen = ref(false);
const reviewItem = ref<DiagnosisApi.LatestRunItem | null>(null);
const historyOpen = ref(false);
const historyItem = ref<DiagnosisApi.LatestRunItem | null>(null);

function openEvidence(record: DiagnosisApi.LatestRunItem): void {
  if (!record.runId) return;
  evidenceRunId.value = record.runId;
  evidenceOpen.value = true;
}

function openReview(record: DiagnosisApi.LatestRunItem): void {
  if (!record.runId) return;
  reviewItem.value = record;
  reviewOpen.value = true;
}

function openHistory(record: DiagnosisApi.LatestRunItem): void {
  historyItem.value = record;
  historyOpen.value = true;
}

/** 档案抽屉：run 色块/列表行点击 → 复用诊断详情弹窗打开该次 run */
function openArchiveRun(item: DiagnosisApi.LatestRunItem): void {
  detailItem.value = item;
  detailModalOpen.value = true;
}

/** 档案抽屉空态引导 → 跳工作台发起该回路诊断 */
function onArchiveTriggerDiagnosis(loopId: string): void {
  historyOpen.value = false;
  gotoWorkbench(loopId);
}

/** 复核完成 → 刷新概览（复核状态/结论即时回显） */
function onReviewDone(): void {
  loadLatestOverview();
}

onMounted(() => {
  loadPlantTree();
  loadLatestOverview();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="latestLoading"
      subtitle="每回路最新一条诊断结论：症状证据 → 原因分类 → 处置建议（表分页渲染）"
      title="诊断概览"
    >
      <template #actions>
        <ClpmToolbarButton
          :loading="latestLoading"
          icon="ant-design:sync-outlined"
          label="刷新概览"
          @click="loadLatestOverview()"
        />
        <ClpmToolbarButton
          icon="ant-design:fund-projection-screen-outlined"
          label="诊断记录"
          @click="router.push({ path: '/diagnosis/records' })"
        />
      </template>
    </ClpmPageToolbar>

    <div class="diag-ov-layout">
      <!-- 左脊柱：装置树（范围切换） -->
      <aside class="diag-ov-sidebar">
        <div class="diag-ov-sidebar__section-title">
          <span>装置</span>
          <button
            v-if="plantTreeSelectedKeys.length > 0"
            class="diag-ov-sidebar__clear"
            @click="handlePlantTreeSelect([])"
          >
            清除
          </button>
        </div>
        <Spin :spinning="plantTreeLoading" size="small">
          <Tree
            v-if="plantTreeData.length > 0"
            v-model:expanded-keys="plantTreeExpandedKeys"
            v-model:selected-keys="plantTreeSelectedKeys"
            :block-node="true"
            :show-line="false"
            :tree-data="plantTreeData as any"
            class="diag-ov-plant-tree"
            @select="handlePlantTreeSelect"
          />
          <div v-else class="diag-ov-sidebar__empty">暂无装置数据</div>
        </Spin>
      </aside>

      <!-- 主区：覆盖面板 + 概览表 -->
      <div class="diag-ov-main">
        <DiagnosisCoveragePanel class="mb-3" />
        <Card size="small">
          <template #title>
            最新诊断概览
            <span class="text-xs font-normal text-neutral-400">
              {{ selectedPlantNodeId ? '当前装置范围' : '全厂' }} ·
              {{ overviewLoopCount }} 个回路（一回路一条最新结论）
            </span>
          </template>
          <!-- 筛选：诊断状态标签 + 等级/评分/结论/严重度下拉 -->
          <div class="diag-ov-latest-filter">
            <button
              v-for="f in [
                { key: 'all', label: '全部' },
                { key: 'diagnosed', label: '已诊断' },
                { key: 'undiagnosed', label: '未诊断' },
              ]"
              :key="f.key"
              class="diag-ov-latest-filter__btn"
              :class="{
                'diag-ov-latest-filter__btn--active': latestFilter === f.key,
              }"
              @click="latestFilter = f.key as LatestFilter"
            >
              {{ f.label }}
              <span
                v-if="f.key === 'undiagnosed' && undiagnosedCount > 0"
                class="diag-ov-latest-filter__count"
              >
                {{ undiagnosedCount }}
              </span>
            </button>
            <Select
              v-model:value="filterImportance"
              :allow-clear="true"
              :options="[
                { label: '1级（关键）', value: 1 },
                { label: '2级（重要）', value: 2 },
                { label: '3级（一般）', value: 3 },
              ]"
              placeholder="回路等级"
              size="small"
              style="width: 118px"
            />
            <Select
              v-model:value="filterScoreGrade"
              :allow-clear="true"
              :options="
                SCORE_GRADES.map((g) => ({ label: g.label, value: g.key }))
              "
              placeholder="性能评分"
              size="small"
              style="width: 100px"
            />
            <Select
              v-model:value="filterCategory"
              :allow-clear="true"
              :options="CATEGORY_OPTIONS"
              placeholder="诊断结论"
              size="small"
              style="width: 140px"
            />
            <Select
              v-model:value="filterSeverity"
              :allow-clear="true"
              :options="[
                { label: '高', value: 'HIGH' },
                { label: '中', value: 'MEDIUM' },
                { label: '低', value: 'LOW' },
              ]"
              placeholder="严重度"
              size="small"
              style="width: 92px"
            />
          </div>
          <Table
            class="diag-ov-latest-table"
            :columns="latestColumns"
            :custom-row="
              (record: DiagnosisApi.LatestRunItem) => ({
                style: record.runId ? 'cursor: pointer' : '',
                onClick: () => openLatestDetail(record),
              })
            "
            :data-source="filteredLatestItems"
            :loading="latestLoading"
            :row-key="(record: DiagnosisApi.LatestRunItem) => record.loopId"
            :pagination="{
              pageSize: 50,
              pageSizeOptions: ['20', '50', '100'],
              showSizeChanger: true,
              showTotal: (t: number) => `共 ${t} 条`,
              size: 'small',
              showLessItems: true,
            }"
            :scroll="{ x: 1330 }"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'loopTagName'">
                {{ record.loopTagName }}
              </template>
              <template v-else-if="column.dataIndex === 'loopDescription'">
                <span class="text-neutral-500">{{
                  record.loopDescription || '—'
                }}</span>
              </template>
              <template v-else-if="column.dataIndex === 'importanceLevel'">
                <span
                  v-if="record.importanceLevel"
                  :style="{
                    color: IMPORTANCE_LEVEL_COLOR[record.importanceLevel],
                  }"
                >
                  {{
                    IMPORTANCE_LEVEL_TEXT[record.importanceLevel] ??
                    record.importanceLevel
                  }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'latestScore'">
                <span
                  v-if="record.latestScore != null"
                  class="font-medium tabular-nums"
                >
                  {{ record.latestScore.toFixed(1) }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'scoreGrade'">
                <span
                  v-if="record.latestScore != null"
                  :style="{ color: scoreGrade(record.latestScore)?.color }"
                >
                  {{ scoreGrade(record.latestScore)?.label }}
                </span>
                <span v-else class="text-neutral-400">—</span>
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
              <template v-else-if="column.dataIndex === 'runCount'">
                <span v-if="record.runId && record.runCount">
                  第 {{ record.runCount }} 次
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'primaryCategoryLabel'">
                <span
                  v-if="record.primaryCategoryLabel"
                  :style="{
                    color: latestCatColor(record as DiagnosisApi.LatestRunItem),
                  }"
                  class="font-medium"
                >
                  {{ record.primaryCategoryLabel }}
                </span>
                <span v-else class="text-neutral-400">—</span>
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
                    REVIEW_STATUS_TEXT[record.reviewStatus] ??
                    record.reviewStatus
                  }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.dataIndex === 'lastDiagnosedAt'">
                <span v-if="record.runId">{{
                  fmtUtc(record.lastDiagnosedAt)
                }}</span>
                <span v-else class="text-neutral-400">未诊断</span>
              </template>
              <template v-else-if="column.key === 'action'">
                <div class="flex gap-1" @click.stop>
                  <Button
                    size="small"
                    type="link"
                    :disabled="!record.runId"
                    @click.stop="
                      openEvidence(record as DiagnosisApi.LatestRunItem)
                    "
                  >
                    证据
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    :disabled="!record.runId"
                    @click.stop="
                      openReview(record as DiagnosisApi.LatestRunItem)
                    "
                  >
                    复核
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    @click.stop="
                      openHistory(record as DiagnosisApi.LatestRunItem)
                    "
                  >
                    历史
                  </Button>
                  <Button
                    size="small"
                    type="link"
                    @click.stop="gotoWorkbench(record.loopId)"
                  >
                    诊断
                  </Button>
                </div>
              </template>
            </template>
          </Table>
        </Card>
      </div>
    </div>

    <!-- 概览行操作弹层：证据 / 复核 / 历史 + 行点击诊断详情 -->
    <DiagnosisEvidenceDrawer
      v-model:open="evidenceOpen"
      :run-id="evidenceRunId"
    />
    <DiagnosisReviewDrawer
      v-model:open="reviewOpen"
      :item="reviewItem"
      @done="onReviewDone"
    />
    <DiagnosisLoopArchiveDrawer
      v-model:open="historyOpen"
      :loop-id="historyItem?.loopId ?? null"
      :loop-tag-name="historyItem?.loopTagName"
      @open-run="openArchiveRun"
      @trigger-diagnosis="onArchiveTriggerDiagnosis"
    />
    <DiagnosisDetailModal
      v-model:open="detailModalOpen"
      :item="detailItem"
      @reviewed="onReviewDone"
    />
  </Page>
</template>

<style scoped>
/* 概览页布局：左脊柱装置树 + 主区（类工作台布局） */
.diag-ov-layout {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

.diag-ov-sidebar {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: 208px;
  padding: 8px;
  overflow: auto;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-ov-sidebar__section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px 6px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-ov-sidebar__clear {
  padding: 0 4px;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.diag-ov-sidebar__empty {
  padding: 12px 8px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.diag-ov-plant-tree {
  font-size: 12px;
}

.diag-ov-main {
  flex: 1;
  min-width: 0;
}

/* 概览表：紧凑字体 + 单行不换行 */
.diag-ov-latest-table :deep(.ant-table-cell) {
  font-size: 12px;
  white-space: nowrap;
}

.diag-ov-latest-table :deep(.ant-table-cell .ant-btn-link) {
  padding: 0 2px;
  font-size: 12px;
}

/* 概览筛选标签 */
.diag-ov-latest-filter {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 0 0 8px;
}

.diag-ov-latest-filter__btn {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 3px 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  transition: all 0.15s;
}

.diag-ov-latest-filter__btn:hover {
  color: hsl(var(--foreground));
  background: hsl(var(--accent));
}

.diag-ov-latest-filter__btn--active {
  font-weight: 500;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary) / 20%);
}

.diag-ov-latest-filter__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  font-size: 10px;
  font-weight: 600;
  color: #fff;
  background: hsl(var(--primary));
  border-radius: 8px;
}
</style>
