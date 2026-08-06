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
import type { LoopApi } from '#/api/loop';

import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue';
import type { Component } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Empty, Input, Spin, TabPane, Tabs } from 'ant-design-vue';

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
const searchKeyword = ref('');

// ===== 右侧工作台状态 =====
const selectedLoopId = ref<null | string>(null);
const selectedLoop = computed(() =>
  loopList.value.find((l) => l.loopId === selectedLoopId.value),
);
const activeTab = ref('overview');

// 已渲染过的 Tab 集合（切换后才渲染，避免 6 Tab 同时请求）
const loadedTabs = ref<Set<string>>(new Set(['overview']));

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
  try {
    const res = await getLoopMonitorListApi({
      page: 1,
      pageSize: 200,
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
  } catch {
    // 错误已由拦截器处理
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
            <Empty
              v-if="!loopListLoading && loopList.length === 0"
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
          class="flex-1 overflow-hidden px-4"
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
