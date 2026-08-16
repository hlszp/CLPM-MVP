<script setup lang="ts">
/**
 * ClpmEvidenceCanvas
 * Phase 1-C 证据画布：统一证据区块容器
 *
 * 职责：
 * 1. 基于 OperationalContext 自动处理全局 loading/error/empty/stale/partial 状态
 * 2. 按 section 管理证据区块（assessment/diagnosis/tuning/tracker 等）
 * 3. 自动判断区块可用性（unavailableSections），不可用时显示占位而非崩溃
 * 4. 提供 named slot 由业务页面组装证据卡片
 *
 * 用法：
 * <ClpmEvidenceCanvas>
 *   <template #assessment>
 *     <AssessmentCard />
 *   </template>
 *   <template #diagnosis>
 *     <DiagnosisCard />
 *   </template>
 * </ClpmEvidenceCanvas>
 */
import { computed } from 'vue';
import { Alert, Spin } from 'ant-design-vue';
import { injectOperationalContext } from '#/composables/use-operational-context';
import ClpmEmptyState from './empty-state.vue';

defineOptions({ name: 'ClpmEvidenceCanvas' });

const props = withDefaults(
  defineProps<{
    /** 紧凑模式：减小 padding */
    compact?: boolean;
    /** 需要展示的区块列表（顺序即显示顺序） */
    sections?: string[];
  }>(),
  {
    compact: false,
    sections: () => ['dataHealth', 'scoreTrend', 'assessment', 'diagnosis', 'tuning', 'tracker'],
  },
);

const ctx = injectOperationalContext();

const isGlobalLoading = computed(() => ctx?.loading.value ?? false);
const isGlobalError = computed(() => ctx?.stateFace.value === 'error');
const isEmpty = computed(() => ctx?.stateFace.value === 'empty');
const isPartial = computed(() => ctx?.stateFace.value === 'partial');
const isStale = computed(() => ctx?.stateFace.value === 'stale');
const errorMessage = computed(() => ctx?.error.value?.message ?? '数据加载失败');
const unavailableList = computed(() => ctx?.unavailableSections.value ?? []);

function isSectionAvailable(section: string): boolean {
  return ctx?.isSectionAvailable(section) ?? true;
}

function reload() {
  ctx?.loadFromRoute();
}
</script>

<template>
  <div :class="['evidence-canvas', { 'evidence-canvas--compact': compact }]">
    <!-- 全局 stale 警告条 -->
    <Alert
      v-if="isStale"
      type="warning"
      show-icon
      class="evidence-canvas__stale-bar"
      message="数据可能已陈旧，请检查实时连接"
      banner
    />

    <!-- 全局 partial 警告条 -->
    <Alert
      v-else-if="isPartial && unavailableList.length > 0"
      type="warning"
      show-icon
      class="evidence-canvas__partial-bar"
      :message="`${unavailableList.length} 个数据区块加载失败，其他内容正常显示`"
      banner
      closable
    />

    <!-- loading 遮罩 -->
    <Spin
      v-if="isGlobalLoading"
      spinning
      tip="加载中..."
      class="evidence-canvas__spin"
    >
      <div class="evidence-canvas__loading-placeholder" />
    </Spin>

    <!-- error 状态 -->
    <template v-else-if="isGlobalError">
      <ClpmEmptyState
        title="数据加载失败"
        :description="errorMessage"
        icon="error"
      >
        <template #actions>
          <a-button size="small" type="primary" @click="reload">
            重新加载
          </a-button>
        </template>
      </ClpmEmptyState>
    </template>

    <!-- empty 状态 -->
    <template v-else-if="isEmpty">
      <ClpmEmptyState
        title="请选择回路"
        description="从回路列表或系统概览选择一个回路以查看详情"
        icon="empty"
      />
    </template>

    <!-- 正常内容：按 sections 顺序渲染 slot -->
    <template v-else>
      <div class="evidence-canvas__grid">
        <template v-for="section in sections" :key="section">
          <!-- 区块不可用：显示占位 -->
          <section
            v-if="!isSectionAvailable(section)"
            :class="['evidence-canvas__section', 'evidence-canvas__section--unavailable']"
          >
            <div class="evidence-canvas__section-unavailable">
              <span class="evidence-canvas__section-name">{{ section }}</span>
              <span class="evidence-canvas__section-hint">数据不可用</span>
            </div>
          </section>
          <!-- 区块可用：渲染 slot -->
          <section v-else :class="['evidence-canvas__section', `evidence-canvas__section--${section}`]">
            <slot :name="section" :ctx="ctx" />
          </section>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
.evidence-canvas {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: hsl(var(--background));
  border-radius: 8px;
}

.evidence-canvas--compact {
  gap: 8px;
  padding: 12px;
}

.evidence-canvas__stale-bar,
.evidence-canvas__partial-bar {
  border-radius: 6px;
}

.evidence-canvas__spin {
  width: 100%;
  min-height: 200px;
}

.evidence-canvas__loading-placeholder {
  min-height: 200px;
  background: hsl(var(--muted) / 30%);
  border-radius: 6px;
}

.evidence-canvas__grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.evidence-canvas__section {
  overflow: hidden;
  border-radius: 6px;
}

.evidence-canvas__section--unavailable {
  padding: 16px;
  background: hsl(var(--muted) / 20%);
  border: 1px dashed hsl(var(--border));
}

.evidence-canvas__section-unavailable {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: hsl(var(--foreground) / 40%);
}

.evidence-canvas__section-name {
  font-weight: 500;
}

.evidence-canvas__section-hint {
  padding: 1px 6px;
  background: hsl(var(--muted));
  border-radius: 4px;
}
</style>
