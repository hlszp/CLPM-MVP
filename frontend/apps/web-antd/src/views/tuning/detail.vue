<script lang="ts" setup>
/**
 * 整定任务详情单页（Phase D §4.4.2）
 *
 * 替代原 3 页向导（model→algorithm→simulation），整合为单页 + 4 锚点导航：
 * - 顶部常驻: 回路信息 | 辨识可信度 | 当前 PID | 工具栏
 * - 锚点导航: ①过程辨识 ②PID推荐 ③闭环仿真 ④方案确认
 * - 每步计算仍重，保留"步骤"语义（门禁约束）但取消整页路由跳转
 *
 * 安全边界: 第④步"方案确认"仅输出建议+证据+风险+回退+留痕，绝不直写 DCS。
 *
 * 状态管理: 复用 useTuningStore，activeAnchor 与 store.currentStep 双向同步。
 * 恢复策略: URL 带 taskId → 后端回显；否则 sessionStorage 恢复；皆无 → 新流程。
 */
import type { TuningApi } from '#/api/tuning';

import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import { Alert, message, Tag, Tooltip } from 'ant-design-vue';

import {
  ClpmLoopContextHeader,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTuningStore } from '#/store/tuning';

defineOptions({ name: 'TuningDetail' });

const { themeColors } = useClpmTheme();

const route = useRoute();
const store = useTuningStore();

// ===== 锚点定义 =====
interface AnchorDef {
  key: number;
  title: string;
  subtitle: string;
  icon: string;
}

const ANCHORS: AnchorDef[] = [
  {
    key: 0,
    title: '过程辨识',
    subtitle: '辨识过程对象 G(s)',
    icon: 'lucide:git-branch',
  },
  {
    key: 1,
    title: 'PID 推荐',
    subtitle: '计算推荐 PID 参数',
    icon: 'lucide:sliders',
  },
  {
    key: 2,
    title: '闭环仿真',
    subtitle: '对比响应性能',
    icon: 'lucide:activity',
  },
  {
    key: 3,
    title: '方案确认',
    subtitle: '建议+风险+留痕',
    icon: 'lucide:check-circle',
  },
];

const activeAnchor = ref(0);

// ===== 懒加载子组件 =====
const IdentifySection = defineAsyncComponent(() => import('./model.vue'));
const PidSection = defineAsyncComponent(() => import('./algorithm.vue'));
const SimulationSection = defineAsyncComponent(
  () => import('./simulation.vue'),
);
const ConfirmSection = defineAsyncComponent(
  () => import('./sections/confirm-section.vue'),
);

// ===== 步骤门禁 =====
const canAccessPid = computed(
  () => !!store.identifyResult || activeAnchor.value >= 1,
);
const canAccessSimulation = computed(
  () => store.pidCandidates.length > 0 || activeAnchor.value >= 2,
);
const canAccessConfirm = computed(
  () => !!store.simulationResult || activeAnchor.value >= 3,
);

/** 锚点是否禁用（与门禁一致，供模板 tabindex/role 使用） */
function isAnchorDisabled(anchor: number): boolean {
  return (
    (anchor === 1 && !canAccessPid.value) ||
    (anchor === 2 && !canAccessSimulation.value) ||
    (anchor === 3 && !canAccessConfirm.value)
  );
}

/** P2-19：disabled 锚点的门禁前置条件说明（供 Tooltip 展示） */
function anchorGateHint(anchor: number): string {
  if (anchor === 1 && !canAccessPid.value) return '请先完成「过程辨识」步骤';
  if (anchor === 2 && !canAccessSimulation.value)
    return '请先完成「PID 推荐」步骤，生成候选 PID 参数';
  if (anchor === 3 && !canAccessConfirm.value)
    return '请先完成「闭环仿真」步骤';
  return '';
}

/** 锚点切换（受门禁约束） */
function handleAnchorChange(anchor: number) {
  if (anchor === 1 && !canAccessPid.value) {
    message.warning('请先完成模型辨识');
    return;
  }
  if (anchor === 2 && !canAccessSimulation.value) {
    message.warning('请先完成整定算法，生成候选 PID');
    return;
  }
  if (anchor === 3 && !canAccessConfirm.value) {
    message.warning('请先完成闭环仿真');
    return;
  }
  activeAnchor.value = anchor;
  store.currentStep = anchor;
}

// ===== 返回路径 =====
const backTo = computed(() => {
  const returnTo = route.query.returnTo as string | undefined;
  return returnTo || '/tuning/workbench';
});
const backLabel = computed(() => {
  return route.query.returnTo ? '返回诊断' : '返回整定工作台';
});

// ===== 顶部摘要信息 =====
const identifyConfidence = computed(() => {
  const result = store.identifyResult;
  if (!result) return null;
  return {
    level: result.confidenceLevel ?? '—',
    score: result.fittingScore,
  };
});

const currentPid = computed<null | TuningApi.PidParamsWithLabel>(() => {
  if (store.pidCandidates.length === 0) return null;
  return store.pidCandidates[0] ?? null;
});

// ===== 恢复策略 =====
async function restoreState() {
  const taskId = route.query.taskId as string | undefined;
  const queryLoopId = route.query.loopId as string | undefined;
  if (taskId) {
    const ok = await store.restoreFromTask(taskId);
    if (!ok) {
      message.warning('任务回显失败，请重新选择任务或新建整定流程');
    }
  } else if (queryLoopId) {
    store.restoreFromSession();
    if (store.currentLoopId !== queryLoopId) {
      store.setCurrentLoop(queryLoopId);
    }
  } else {
    store.restoreFromSession();
  }
  activeAnchor.value = store.currentStep ?? 0;
}

onMounted(() => {
  restoreState();
});

/** 工具栏刷新态 */
const loading = ref(false);

/** 工具栏刷新：重新恢复整定流程状态 */
function handleRefresh() {
  loading.value = true;
  restoreState().finally(() => {
    loading.value = false;
  });
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '整定任务详情 帮助',
    content:
      '单页整定工作流，4 锚点导航：① 过程辨识（辨识 G(s)） → ② PID 推荐（计算候选参数） → ③ 闭环仿真（对比响应性能） → ④ 方案确认（建议+风险+留痕）。锚点切换受门禁约束，须完成前序步骤。平台不直接修改 DCS 参数，只输出建议与证据。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

// 同步 store.currentStep → activeAnchor
watch(
  () => store.currentStep,
  (step) => {
    if (step !== activeAnchor.value) {
      activeAnchor.value = step;
    }
  },
);
</script>

<template>
  <Page>
    <!-- 顶部常驻信息栏 -->
    <ClpmPageToolbar :loading="loading">
      <template #left>
        <ClpmLoopContextHeader
          :editable="activeAnchor === 0"
          :show-time-window="activeAnchor === 0"
          :back-to="backTo"
          :back-label="backLabel"
        />
      </template>
      <template #right>
        <!-- P3-34：徽章容器 flex-wrap 防窄屏折行错位 -->
        <div class="flex flex-wrap items-center gap-2">
          <!-- 辨识可信度徽章 -->
          <div
            v-if="identifyConfidence"
            class="flex items-center gap-2 rounded border px-3 py-1 text-sm"
            :style="{
              borderColor: themeColors.SUCCESS,
              background: `${themeColors.SUCCESS}10`,
            }"
          >
            <IconifyIcon
              icon="ant-design:safety-certificate-outlined"
              :size="16"
            />
            <span :style="{ color: themeColors.NEUTRAL }">可信度</span>
            <Tag color="success" class="!m-0">
              {{ identifyConfidence.level }}
            </Tag>
            <span
              v-if="identifyConfidence.score !== null"
              class="text-xs"
              :style="{ color: themeColors.NEUTRAL }"
            >
              拟合 {{ Number(identifyConfidence.score).toFixed(2) }}
            </span>
          </div>
          <!-- 当前 PID 徽章 -->
          <div
            v-if="currentPid"
            class="flex items-center gap-2 rounded border px-3 py-1 text-sm"
            :style="{
              borderColor: themeColors.INFO,
              background: `${themeColors.INFO}10`,
            }"
          >
            <IconifyIcon icon="ant-design:control-outlined" :size="16" />
            <span :style="{ color: themeColors.NEUTRAL }">推荐 PID</span>
            <span class="font-mono text-xs">
              P={{ currentPid.kp }} I={{ currentPid.ti }} D={{ currentPid.td }}
            </span>
          </div>
        </div>
      </template>
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- 锚点导航栏 -->
    <div class="anchor-nav sticky top-0 z-10 border-b bg-content px-4 py-2">
      <div class="flex items-center gap-2">
        <Tooltip
          v-for="anchor in ANCHORS"
          :key="anchor.key"
          :title="
            isAnchorDisabled(anchor.key)
              ? anchorGateHint(anchor.key)
              : undefined
          "
          :mouse-enter-delay="0.3"
        >
          <div
            class="anchor-item"
            :class="{
              'anchor-item--active': activeAnchor === anchor.key,
              'anchor-item--disabled': isAnchorDisabled(anchor.key),
            }"
            :role="isAnchorDisabled(anchor.key) ? undefined : 'button'"
            :tabindex="isAnchorDisabled(anchor.key) ? -1 : 0"
            :aria-pressed="activeAnchor === anchor.key"
            @click="handleAnchorChange(anchor.key)"
            @keydown.enter="handleAnchorChange(anchor.key)"
            @keydown.space.prevent="handleAnchorChange(anchor.key)"
          >
            <div class="anchor-index">{{ anchor.key + 1 }}</div>
            <div class="anchor-body">
              <div class="anchor-title">{{ anchor.title }}</div>
              <div
                class="anchor-subtitle"
                :style="{ color: themeColors.NEUTRAL }"
              >
                {{ anchor.subtitle }}
              </div>
            </div>
          </div>
        </Tooltip>
      </div>
    </div>

    <!-- 安全边界提示 -->
    <Alert
      v-if="activeAnchor === 3"
      type="info"
      show-icon
      class="mx-4 mt-3"
      message="只读建议 · 人工实施 · 需留痕"
      description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
    />

    <!-- 内容区：v-show 保持各组件状态 -->
    <div class="tuning-detail-content">
      <IdentifySection v-show="activeAnchor === 0" embedded />
      <PidSection v-show="activeAnchor === 1" embedded />
      <SimulationSection v-show="activeAnchor === 2" embedded />
      <ConfirmSection v-show="activeAnchor === 3" />
    </div>
  </Page>
</template>

<style scoped>
.anchor-nav {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
}

.anchor-item {
  display: flex;
  flex: 1;
  gap: 8px;
  align-items: center;
  min-width: 0;
  padding: 8px 12px;
  cursor: pointer;
  background: hsl(var(--muted) / 30%);
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
  transition:
    background-color 0.2s,
    border-color 0.2s;
}

.anchor-item:hover:not(.anchor-item--disabled) {
  background: hsl(var(--primary) / 6%);
  border-color: hsl(var(--primary) / 40%);
}

.anchor-item--active {
  position: relative;
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary));
}

/* P3-35：active 锚点增加左侧色条增强视觉定位 */
.anchor-item--active::before {
  position: absolute;
  top: 50%;
  left: 0;
  width: 3px;
  height: 60%;
  content: '';
  background: hsl(var(--primary));
  border-radius: 0 2px 2px 0;
  transform: translateY(-50%);
}

.anchor-item--disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.anchor-index {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
  border-radius: 50%;
}

.anchor-item--disabled .anchor-index {
  background: hsl(var(--muted-foreground));
}

.anchor-body {
  flex: 1;
  min-width: 0;
}

.anchor-title {
  font-size: 13px;
  font-weight: 600;
}

.anchor-subtitle {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  line-height: 1.4;
  white-space: nowrap;
}

.tuning-detail-content {
  flex: 1;
  min-height: 0;
}
</style>
