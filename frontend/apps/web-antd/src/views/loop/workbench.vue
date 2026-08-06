<script lang="ts" setup>
/**
 * 回路工作台（IA 重构 Phase B·§4.1）
 *
 * 双轴导航 · 实体轴：单回路 360° 一站式处置
 * master-detail 布局：左侧回路列表 + 右侧 6 Tab 工作台
 *
 * 6 Tab：概览 / 评估 / 诊断 / 整定 / 效果对比 / 处置时间线
 * 硬性规则：每个 Tab 最多"摘要 + 1 主图 + 跳转入口"，禁止内嵌完整职能表格
 *
 * 路由：
 * - /loop/workbench         → 本页（回路菜单主页）
 * - /loop/workbench?loopId= → 预选回路
 * - /loop/detail/:id        → redirect 到 /loop/workbench?loopId=:id
 *
 * 后端零改动：全部前端组合现有 API
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { KpiSnapshotItem, LoopConfidenceLatestItem } from '#/api/metric';
import type { LoopApi } from '#/api/loop';

import { computed, defineAsyncComponent, onMounted, provide, ref, watch } from 'vue';
import type { Component } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Empty, Input, Spin, TabPane, Tabs } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisDetailApi } from '#/api/diagnosis';
import {
  getLoopConfidenceLatestApi,
  getLoopSnapshotsApi,
} from '#/api/metric';
import { getLoopMonitorListApi } from '#/api/loop';
import {
  ClpmAiDrawer,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';

defineOptions({ name: 'LoopWorkbench' });

// ===== Tab 组件懒加载（首次切换时才请求 JS chunk） =====
// defineAsyncComponent 返回值即组件定义，直接持有即可
const OverviewTab: Component = defineAsyncComponent(
  () => import('./tabs/overview-tab.vue'),
);
const AssessmentTab: Component = defineAsyncComponent(
  () => import('./tabs/assessment-tab.vue'),
);
const DiagnosisTab: Component = defineAsyncComponent(
  () => import('./tabs/diagnosis-tab.vue'),
);
const TuningTab: Component = defineAsyncComponent(
  () => import('./tabs/tuning-tab.vue'),
);
const ComparisonTab: Component = defineAsyncComponent(
  () => import('./tabs/comparison-tab.vue'),
);
const TimelineTab: Component = defineAsyncComponent(
  () => import('./tabs/timeline-tab.vue'),
);

const route = useRoute();
const router = useRouter();

// ===== 左侧回路列表 =====
const loopList = ref<LoopApi.MonitorListItem[]>([]);
const loopListLoading = ref(false);
const loopListError = ref('');
const searchKeyword = ref('');

// ===== 右侧工作台状态 =====
const selectedLoopId = ref<null | string>(null);
const selectedLoop = computed(() =>
  loopList.value.find((l) => l.loopId === selectedLoopId.value),
);
const activeTab = ref('overview');

// 已渲染过的 Tab 集合（切换后才渲染，避免 6 Tab 同时请求）
const loadedTabs = ref<Set<string>>(new Set(['overview']));

// ===== 诊断数据共享（概览 / 诊断 Tab 复用，避免重复 API 调用） =====
// 通过 provide/inject 将诊断详情加载提升到父级：选中回路即加载一次，
// 概览 Tab 的"诊断标签"摘要与诊断 Tab 的完整诊断共用同一份数据。
const diagnosisDetail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const diagnosisLoading = ref(false);

async function loadDiagnosis(loopId: string): Promise<void> {
  diagnosisLoading.value = true;
  try {
    diagnosisDetail.value = await getDiagnosisDetailApi(loopId).catch(
      () => null,
    );
  } finally {
    diagnosisLoading.value = false;
  }
}

provide('diagnosisDetail', diagnosisDetail);
provide('diagnosisLoading', diagnosisLoading);
provide('loadDiagnosis', loadDiagnosis);

// ===== 评估数据共享（评估 Tab 用，提升到父级避免子组件重复请求） =====
// 评估快照：getLoopConfidenceLatestApi → 最近一次评估记录（12 子指标 + 评分 + 可信度）
// 评分趋势：getLoopSnapshotsApi → 近 7 天历史快照（综合评分 + 各 KPI 时间序列）
const assessmentDetail = ref<LoopConfidenceLatestItem | null>(null);
const assessmentLoading = ref(false);
const scoreHistory = ref<KpiSnapshotItem[]>([]);

/** 拉取近 7 天评分趋势快照（分页，pageSize 上限 100） */
async function loadScoreHistory(loopId: string): Promise<KpiSnapshotItem[]> {
  const endTime = dayjs();
  const startTime = endTime.subtract(7, 'day');
  const allItems: KpiSnapshotItem[] = [];
  let page = 1;
  const pageLimit = 100;
  let total = 0;
  do {
    const res = await getLoopSnapshotsApi({
      loopId,
      startTime: startTime.toISOString(),
      endTime: endTime.toISOString(),
      latestOnly: false,
      page,
      pageSize: pageLimit,
    }).catch(() => ({ items: [], total: 0 }));
    allItems.push(...(res.items || []));
    total = res.total ?? 0;
    page += 1;
  } while ((page - 1) * pageLimit < total);
  return allItems.toSorted((a, b) => {
    const aTs = a.tsStart || '';
    const bTs = b.tsStart || '';
    return aTs.localeCompare(bTs);
  });
}

async function loadAssessment(loopId: string): Promise<void> {
  assessmentLoading.value = true;
  try {
    const [latest, snapshots] = await Promise.all([
      getLoopConfidenceLatestApi(loopId).catch(() => null),
      loadScoreHistory(loopId),
    ]);
    assessmentDetail.value = latest;
    scoreHistory.value = snapshots;
  } finally {
    assessmentLoading.value = false;
  }
}

provide('assessmentDetail', assessmentDetail);
provide('assessmentLoading', assessmentLoading);
provide('scoreHistory', scoreHistory);
provide('loadAssessment', loadAssessment);

// ===== AI 洞察两级门禁（Phase A 已建） =====
// scene=performance 需要 loopId 上下文：未选回路时灰显（disabled-context）
const { gateStatus, gateTooltip, init: initAiGate } = useAiInsightGate();
initAiGate();
const aiDrawerOpen = ref(false);
const aiGateStatus = computed(() => gateStatus(selectedLoopId.value, true));
const aiGateTooltip = computed(() => gateTooltip(aiGateStatus.value));

/** 6 Tab 定义 */
const tabPanes = [
  { key: 'overview', label: '概览' },
  { key: 'assessment', label: '评估' },
  { key: 'diagnosis', label: '诊断' },
  { key: 'tuning', label: '整定' },
  { key: 'comparison', label: '效果对比' },
  { key: 'timeline', label: '处置时间线' },
] as const;

/** Tab key → 组件映射（供 template <component :is> 使用） */
function tabComponent(key: string): Component {
  switch (key) {
    case 'overview': {
      return OverviewTab;
    }
    case 'assessment': {
      return AssessmentTab;
    }
    case 'diagnosis': {
      return DiagnosisTab;
    }
    case 'tuning': {
      return TuningTab;
    }
    case 'comparison': {
      return ComparisonTab;
    }
    case 'timeline': {
      return TimelineTab;
    }
    default: {
      return OverviewTab;
    }
  }
}

// ===== 数据加载 =====

/** 加载回路列表 */
async function loadLoopList(): Promise<void> {
  loopListLoading.value = true;
  loopListError.value = '';
  try {
    const res = await getLoopMonitorListApi({
      page: 1,
      // 后端 pageSize 上限 100，超出触发 ERR_VALIDATION
      pageSize: 100,
      keyword: searchKeyword.value || undefined,
    });
    loopList.value = res.items;
    // 若 URL 带 loopId 且列表中有匹配项，选中它；否则选第一项
    const queryLoopId = route.query.loopId as string | undefined;
    const matched =
      queryLoopId && loopList.value.some((l) => l.loopId === queryLoopId)
        ? queryLoopId
        : (loopList.value[0]?.loopId ?? null);
    if (matched !== selectedLoopId.value) {
      selectLoop(matched);
    } else if (matched === null) {
      selectedLoopId.value = null;
    }
  } catch (error: any) {
    // 拦截器已 toast；此处记录用于左侧列表内联错误占位（避免误显示"暂无回路"）
    loopListError.value = error?.message ?? '加载回路列表失败';
    loopList.value = [];
  } finally {
    loopListLoading.value = false;
  }
}

/** 选中回路：更新状态 + 同步 URL query */
function selectLoop(loopId: null | string): void {
  selectedLoopId.value = loopId;
  if (loopId) {
    router.replace({ query: { ...route.query, loopId } });
    loadedTabs.value = new Set([activeTab.value]);
  }
}

/** Tab 切换：标记已加载 */
function handleTabChange(key: number | string): void {
  activeTab.value = String(key);
  loadedTabs.value.add(String(key));
}

/** 搜索防抖 */
let searchTimer: null | ReturnType<typeof setTimeout> = null;
function handleSearchInput(): void {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadLoopList(), 300);
}

// ===== 生命周期 =====

onMounted(() => {
  const queryLoopId = route.query.loopId as string | undefined;
  if (queryLoopId) {
    selectedLoopId.value = queryLoopId;
  }
  loadLoopList();
});

// 监听 URL loopId 变化（如从 /loop/detail/:id redirect 过来）
watch(
  () => route.query.loopId,
  (newLoopId) => {
    if (newLoopId && newLoopId !== selectedLoopId.value) {
      selectedLoopId.value = newLoopId as string;
      loadedTabs.value = new Set([activeTab.value]);
    }
  },
);

// 选中回路变化时加载诊断 + 评估数据（概览 / 诊断 / 评估 Tab 共用，避免子组件重复请求）
watch(
  selectedLoopId,
  (newId) => {
    if (newId) {
      loadDiagnosis(newId);
      loadAssessment(newId);
    } else {
      diagnosisDetail.value = null;
      assessmentDetail.value = null;
      scoreHistory.value = [];
    }
  },
  { immediate: true },
);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="回路工作台"
      subtitle="单回路 360° 一站式处置"
      :loading="loopListLoading"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ai"
          icon-only
          label="AI 洞察"
          :disabled="aiGateStatus !== 'active'"
          :disabled-reason="aiGateTooltip"
          :tooltip="aiGateTooltip"
          @click="aiDrawerOpen = true"
        />
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loopListLoading"
          @click="loadLoopList"
        />
      </template>
    </ClpmPageToolbar>

    <div class="flex h-[calc(100vh-140px)] gap-3">
      <!-- ===== 左侧：回路列表 ===== -->
      <div
        class="flex w-60 shrink-0 flex-col overflow-hidden rounded-lg border bg-white"
      >
        <div class="border-b p-2">
          <Input
            v-model:value="searchKeyword"
            placeholder="搜索回路位号..."
            allow-clear
            size="small"
            @input="handleSearchInput"
            @press-enter="loadLoopList"
          />
        </div>
        <Spin :spinning="loopListLoading" size="small">
          <div class="max-h-[calc(100vh-210px)] overflow-y-auto">
            <div
              v-for="item in loopList"
              :key="item.loopId"
              class="cursor-pointer border-b px-3 py-2 transition-colors last:border-b-0 hover:bg-blue-50"
              :class="{
                'border-l-[3px] border-l-blue-500 bg-blue-50':
                  item.loopId === selectedLoopId,
              }"
              @click="selectLoop(item.loopId)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="truncate text-sm font-medium">{{
                  item.tagName
                }}</span>
                <span
                  v-if="item.confidenceLevel"
                  class="shrink-0 text-xs font-semibold"
                  :class="{
                    'text-green-600': ['A', 'B'].includes(item.confidenceLevel),
                    'text-orange-500': item.confidenceLevel === 'C',
                    'text-red-500': ['D', 'E'].includes(item.confidenceLevel),
                  }"
                >
                  {{ item.confidenceLevel }}
                </span>
              </div>
              <div class="mt-0.5 flex items-center justify-between gap-2">
                <span class="truncate text-xs text-gray-400">{{
                  item.description || '—'
                }}</span>
                <span class="shrink-0 text-xs text-gray-400"
                  >评分 {{ item.score ?? '—' }}</span
                >
              </div>
            </div>
            <div
              v-if="!loopListLoading && loopListError"
              class="flex flex-col items-center gap-2 py-8 text-center text-xs text-red-500"
            >
              <span>{{ loopListError }}</span>
              <Button size="small" @click="loadLoopList">重试</Button>
            </div>
            <Empty
              v-else-if="!loopListLoading && loopList.length === 0"
              description="暂无回路"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="py-8"
            />
          </div>
        </Spin>
      </div>

      <!-- ===== 右侧：6 Tab 工作台 ===== -->
      <div
        class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border bg-white"
      >
        <!-- 选中回路摘要头 -->
        <div
          v-if="selectedLoop"
          class="flex items-center gap-4 border-b px-4 py-2 text-sm"
        >
          <span class="text-base font-semibold">{{
            selectedLoop.tagName
          }}</span>
          <span class="text-gray-400">{{ selectedLoop.description }}</span>
          <span class="ml-auto text-xs text-gray-400"
            >类型 {{ selectedLoop.loopType || '—' }}</span
          >
        </div>

        <Tabs
          v-model:active-key="activeTab"
          class="loop-workbench-tabs flex-1 min-h-0 px-4"
          size="small"
          @change="handleTabChange"
        >
          <TabPane v-for="tab in tabPanes" :key="tab.key" :tab="tab.label">
            <!-- 仅在选中回路 + Tab 已激活过时渲染（懒加载） -->
            <template v-if="selectedLoopId && loadedTabs.has(tab.key)">
              <component
                :is="tabComponent(tab.key)"
                :loop-id="selectedLoopId"
              />
            </template>
            <Empty
              v-else-if="!selectedLoopId"
              description="请从左侧选择回路"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="py-12"
            />
          </TabPane>
        </Tabs>
      </div>
    </div>

    <!-- ===== AI 洞察右抽屉 ===== -->
    <ClpmAiDrawer
      v-model:open="aiDrawerOpen"
      scene="performance"
      :loop-id="selectedLoopId"
    />
  </Page>
</template>

<style scoped>
/* 6 Tab 内容（概览等）较长，在固定高度工作台内启用 Tab 内容区垂直滚动 */
.loop-workbench-tabs {
  height: 100%;
}

.loop-workbench-tabs :deep(.ant-tabs-content-holder) {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
</style>
