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

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, message, Select, Spin, Table, Tree } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisPrecheckApi } from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmFitnessRulesModal from '#/components/clpm/fitness-rules-modal.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { useLatestOverviewCache } from '#/composables/use-latest-overview-cache';

import DiagnosisDetailModal from './components/diagnosis-detail-modal.vue';
import DiagnosisEvidenceDrawer from './components/evidence-drawer.vue';
import DiagnosisLoopArchiveDrawer from './components/loop-archive-drawer.vue';
import DiagnosisPrecheckBadge from './components/precheck-badge.vue';
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
const fitnessRulesOpen = ref(false);

/** 左脊柱折叠（2026-10-01 用户口径：收起后主显示区占满；浮钮展开） */
const sidebarCollapsed = ref(false);

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

/** 收集全部有子节点的 key（默认展开到 UNIT 层，可手动折叠，2026-10-01 用户口径） */
function collectExpandableKeys(nodes: PlantTreeNode[], acc: string[] = []): string[] {
  for (const n of nodes) {
    if (n.children?.length) {
      acc.push(n.key);
      collectExpandableKeys(n.children, acc);
    }
  }
  return acc;
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildTreeNodes(tree);
    plantTreeExpandedKeys.value = collectExpandableKeys(plantTreeData.value);
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
// 1005 性能优化：模块级 60s 共享缓存（与整定总览复用一次 latest 大 JOIN），
// 且 latest 已自带 fitnessLevel（同表 LATERAL 扩列），免拉 monitor 13 页分页
const latestCache = useLatestOverviewCache();
const latestItems = latestCache.items;
const latestLoading = latestCache.loading;

/** 后端时间为 naive UTC ISO（无 Z 后缀），补 Z 后按本地时区展示 */
function fmtUtc(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso)
    ? naiveIso
    : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

async function loadLatestOverview(): Promise<void> {
  await latestCache.load(selectedPlantNodeId.value);
  // 预检徽标异步补（默认限量，见 loadPrecheck）；主表不等它
  void loadPrecheck();
}

// ===== 可诊断性承接（2026-10-04 D2 迁入；1005 改用 latest 自带 fitnessLevel） =====
/** 仅可诊断（隐藏 L0 数据严重不足；无适用性数据的回路不隐藏，与门禁同口径） */
const onlyDiagnosable = ref(false);

/** 预检徽标（16 号文 F5：后端单次上限 200）。
 *  1005 性能优化：默认只算前 PRECHECK_LIMIT 个回路（首屏分页+筛选够用），
 *  批间并行；1209 全量改为显式「加载全部预检」（loadAllPrecheck） */
const PRECHECK_BATCH = 200;
const PRECHECK_LIMIT = 400;
const precheckItems = ref(new Map<string, DiagnosisApi.PrecheckItem>());
const precheckAssessEnabled = ref(true);
const precheckAllLoaded = ref(false);
const precheckAllLoading = ref(false);
type BadgeFilter = 'all' | 'insufficient' | 'marginal' | 'sufficient' | 'unknown';
const badgeFilter = ref<BadgeFilter>('all');

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

async function fetchPrecheckBatched(ids: string[]): Promise<Map<string, DiagnosisApi.PrecheckItem>> {
  const next = new Map<string, DiagnosisApi.PrecheckItem>();
  const results = await Promise.all(
    chunk(ids, PRECHECK_BATCH).map((b) => getDiagnosisPrecheckApi(b)),
  );
  for (const res of results) {
    precheckAssessEnabled.value = res.assessEnabled;
    if (!res.assessEnabled) break;
    for (const item of res.items) next.set(item.loopId, item);
  }
  return next;
}

async function loadPrecheck(): Promise<void> {
  const ids = latestItems.value
    .map((l) => l.loopId)
    .filter((id) => !!id)
    .slice(0, PRECHECK_LIMIT);
  if (ids.length === 0) {
    precheckItems.value = new Map();
    precheckAllLoaded.value = true;
    return;
  }
  try {
    precheckItems.value = await fetchPrecheckBatched(ids);
    precheckAllLoaded.value = latestItems.value.length <= PRECHECK_LIMIT;
  } catch {
    // 预检失败降级：不显示徽标（不影响概览主数据）
    precheckItems.value = new Map();
  }
}

// 诚实化：按预检档筛选必须基于全量数据（限量数据上筛选=静默截断），自动补全
watch(badgeFilter, (v) => {
  if (v !== 'all' && !precheckAllLoaded.value) void loadAllPrecheck();
});

async function loadAllPrecheck(): Promise<void> {
  if (precheckAllLoaded.value || precheckAllLoading.value) return;
  precheckAllLoading.value = true;
  try {
    const all = latestItems.value.map((l) => l.loopId).filter((id) => !!id);
    const rest = all.slice(PRECHECK_LIMIT);
    const loaded = await fetchPrecheckBatched(rest);
    precheckItems.value = new Map([...precheckItems.value, ...loaded]);
    precheckAllLoaded.value = true;
  } catch {
    message.warning('预检全量加载失败，可重试');
  } finally {
    precheckAllLoading.value = false;
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
  // 仅可诊断：隐藏 L0（数据严重不足；无适用性数据不隐藏）
  if (onlyDiagnosable.value) {
    list = list.filter((i) => i.fitnessLevel !== 'L0');
  }
  // 预检徽标档筛选（评估禁用时徽标列隐藏，筛选同步失效）
  if (badgeFilter.value !== 'all' && precheckAssessEnabled.value) {
    list = list.filter((i) => {
      const lv = i.loopId
        ? (precheckItems.value.get(i.loopId)?.level ?? 'unknown')
        : 'unknown';
      return lv === badgeFilter.value;
    });
  }
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

const latestColumns = computed(() => {
  const cols: Array<Record<string, unknown>> = [
    { dataIndex: 'loopTagName', title: '回路', width: 116 },
    { dataIndex: 'loopDescription', title: '名称', width: 126, ellipsis: true },
    { dataIndex: 'importanceLevel', title: '等级', width: 54 },
  ];
  // 预检徽标列（评估禁用能力时整列隐藏，§5.4 隐藏而非置灰/误报）
  if (precheckAssessEnabled.value) {
    cols.push({ key: 'precheck', title: '预检', width: 62 });
  }
  cols.push(
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
  );
  return cols;
});

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

/** 行内"诊断"→ 跳回路工作台诊断剖面预选该回路（发起职责收敛，2026-10-01；D2 并入 2026-10-04） */
function gotoWorkbench(loopId: string): void {
  router.push({
    path: '/loop/workbench360',
    query: { loopId, section: 'diagnosis' },
  });
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
        <ClpmToolbarButton
          icon="ant-design:question-circle-outlined"
          label="三性说明"
          @click="fitnessRulesOpen = true"
        />
      </template>
    </ClpmPageToolbar>

    <div class="diag-ov-layout">
      <!-- 左脊柱折叠态：浮起小按钮展开 -->
      <button
        v-if="sidebarCollapsed"
        class="diag-ov-sidebar-expand"
        title="展开装置树"
        @click="sidebarCollapsed = false"
      >
        <span class="i-lucide:panel-right"></span>
      </button>
      <!-- 左脊柱：装置树（范围切换；可折叠收起扩大主区） -->
      <aside v-if="!sidebarCollapsed" class="diag-ov-sidebar">
        <div class="diag-ov-sidebar__section-title">
          <span>装置</span>
          <span class="flex items-center gap-1">
            <button
              v-if="plantTreeSelectedKeys.length > 0"
              class="diag-ov-sidebar__clear"
              @click="handlePlantTreeSelect([])"
            >
              清除
            </button>
            <button
              class="diag-ov-sidebar__clear"
              title="收起装置树"
              @click="sidebarCollapsed = true"
            >
              收起
            </button>
          </span>
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
            <!-- 仅可诊断（2026-10-04 D2 自诊断工作台迁入：隐藏 L0 数据严重不足） -->
            <button
              class="diag-ov-latest-filter__btn"
              :class="{
                'diag-ov-latest-filter__btn--active': onlyDiagnosable,
              }"
              title="隐藏不具备诊断条件的回路（仅 L0 数据严重不足；无适用性数据的回路不隐藏）"
              @click="onlyDiagnosable = !onlyDiagnosable"
            >
              仅可诊断
            </button>
            <!-- 预检档位筛选（评估禁用时徽标列隐藏，筛选同隐藏）；
                 1005：预检默认限量加载，全量需显式触发（档位筛选会自动补全） -->
            <span
              v-if="precheckAssessEnabled && !precheckAllLoaded"
              class="text-xs text-neutral-400"
            >
              预检已载 {{ precheckItems.size }}/{{ overviewLoopCount }}
            </span>
            <Button
              v-if="precheckAssessEnabled && !precheckAllLoaded"
              size="small"
              :loading="precheckAllLoading"
              @click="loadAllPrecheck()"
            >
              加载全部预检
            </Button>
            <Select
              v-if="precheckAssessEnabled"
              v-model:value="badgeFilter"
              :options="[
                { label: '预检全部', value: 'all' },
                { label: '数据充足', value: 'sufficient' },
                { label: '疑似不足', value: 'marginal' },
                { label: '数据不足', value: 'insufficient' },
                { label: '预检未知', value: 'unknown' },
              ]"
              size="small"
              style="width: 110px"
            />
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
            :scroll="{ x: 1392 }"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'loopTagName'">
                {{ record.loopTagName }}
              </template>
              <template v-else-if="column.dataIndex === 'loopDescription'">
                <span class="text-neutral-500">{{
                  record.loopDescription || record.unitName || '—'
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
              <template v-else-if="column.key === 'precheck'">
                <DiagnosisPrecheckBadge
                  :item="
                    record.loopId
                      ? precheckItems.get(record.loopId)
                      : undefined
                  "
                />
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
    <ClpmFitnessRulesModal v-model:open="fitnessRulesOpen" />
  </Page>
</template>

<style scoped>
/* 概览页布局：左脊柱装置树 + 主区（类工作台布局） */
.diag-ov-layout {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

.diag-ov-sidebar-expand {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 44px;
  margin-top: 4px;
  font-size: 14px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-ov-sidebar-expand:hover {
  color: hsl(var(--primary));
}

.diag-ov-sidebar {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
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
