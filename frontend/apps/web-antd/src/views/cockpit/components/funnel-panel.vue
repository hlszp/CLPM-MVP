<script lang="ts" setup>
/**
 * 驾驶舱总览 §5 闭环治理漏斗（方案 11 §5.2）
 *
 * 数据：GET /cockpit/overview → funnel
 * （发现异常 → 完成诊断 → 产出方案 → 处置闭环 + 积压条）。
 * 级间转化率 = 下一级 / 上一级；底部积压条 = backlog pending/inProgress/verifying。
 * 点阶段 → 抛 stage-click，父级开清单类弹窗（该级计数与口径说明，
 * 无后端阶段清单接口，不造数据）。
 */
import type { CockpitApi } from '#/api/cockpit';

import { computed, onMounted, ref, watch } from 'vue';

import { getCockpitOverviewApi } from '#/api/cockpit';
import { useCockpitStore } from '#/store/cockpit';

export type FunnelStageKey = 'closed' | 'diagnosed' | 'discovered' | 'tuned';

const emit = defineEmits<{
  stageClick: [stage: FunnelStageKey, count: number];
}>();

const cockpitStore = useCockpitStore();

const loading = ref(true);
const funnel = ref<CockpitApi.CockpitFunnel | null>(null);

async function load() {
  loading.value = true;
  try {
    const res = await getCockpitOverviewApi(cockpitStore.timeWindow);
    funnel.value = res?.funnel ?? null;
  } catch {
    funnel.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级（5min 定时/手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

const stages = computed(() => {
  const f = funnel.value;
  return [
    { count: f?.discovered ?? 0, key: 'discovered' as const, label: '发现异常' },
    { count: f?.diagnosed ?? 0, key: 'diagnosed' as const, label: '完成诊断' },
    { count: f?.tuned ?? 0, key: 'tuned' as const, label: '产出方案' },
    { count: f?.closed ?? 0, key: 'closed' as const, label: '处置闭环' },
  ];
});

const maxCount = computed(() =>
  Math.max(1, ...stages.value.map((s) => s.count)),
);

/** 级间转化率（下一级/上一级；上一级为 0 时显示 —） */
function rateBetween(idx: number): string {
  const cur = stages.value[idx];
  const prev = stages.value[idx - 1];
  if (!cur || !prev || prev.count <= 0) return '—';
  return `${Math.round((cur.count / prev.count) * 100)}%`;
}

const backlog = computed(() => {
  const b = funnel.value?.backlog;
  return {
    inProgress: b?.inProgress ?? 0,
    pending: b?.pending ?? 0,
    verifying: b?.verifying ?? 0,
  };
});

const backlogTotal = computed(
  () =>
    backlog.value.pending + backlog.value.inProgress + backlog.value.verifying,
);
</script>

<template>
  <div class="cockpit-panel funnel">
    <div class="cockpit-panel__hd">
      闭环治理漏斗
      <span class="sub">发现 → 诊断 → 整定 → 闭环</span>
    </div>
    <div class="funnel__bd">
      <div v-if="loading" class="funnel__state">加载中…</div>
      <template v-else>
        <div class="funnel__stages">
          <template v-for="(s, i) in stages" :key="s.key">
            <div
              class="funnel__stage"
              :class="`lv-${i}`"
              :style="{ width: `${28 + (s.count / maxCount) * 72}%` }"
              :title="`${s.label} ${s.count}`"
              @click="emit('stageClick', s.key, s.count)"
            >
              <span class="funnel__stage-label">{{ s.label }}</span>
              <span class="funnel__stage-count">{{ s.count }}</span>
            </div>
            <div v-if="i < stages.length - 1" class="funnel__rate">
              ↓ {{ rateBetween(i + 1) }}
            </div>
          </template>
        </div>

        <!-- 积压条 -->
        <div class="funnel__backlog">
          <div class="funnel__backlog-hd">
            处置积压
            <span class="funnel__backlog-total">{{ backlogTotal }}</span>
          </div>
          <div class="funnel__backlog-bar">
            <span
              class="seg pending"
              :style="{
                width: backlogTotal > 0 ? `${(backlog.pending / backlogTotal) * 100}%` : '0',
              }"
              :title="`待处理 ${backlog.pending}`"
            ></span>
            <span
              class="seg progress"
              :style="{
                width: backlogTotal > 0 ? `${(backlog.inProgress / backlogTotal) * 100}%` : '0',
              }"
              :title="`处理中 ${backlog.inProgress}`"
            ></span>
            <span
              class="seg verifying"
              :style="{
                width: backlogTotal > 0 ? `${(backlog.verifying / backlogTotal) * 100}%` : '0',
              }"
              :title="`验证中 ${backlog.verifying}`"
            ></span>
          </div>
          <div class="funnel__backlog-legend">
            <span><i class="dot pending"></i>待处理 {{ backlog.pending }}</span>
            <span><i class="dot progress"></i>处理中 {{ backlog.inProgress }}</span>
            <span><i class="dot verifying"></i>验证中 {{ backlog.verifying }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.funnel__bd {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  padding: 10px 14px;
  overflow: auto;
}

.funnel__state {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--ck-text-3);
}

.funnel__stages {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  justify-content: center;
  min-height: 0;
}

.funnel__stage {
  display: flex;
  flex: 1;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  min-width: 46%;
  max-height: 44px;
  padding: 0 14px;
  color: #fff;
  cursor: pointer;
  border-radius: 7px;
  transition: filter 0.15s;
}

.funnel__stage:hover {
  filter: brightness(1.12);
}

.funnel__stage.lv-0 {
  background: linear-gradient(90deg, #1d4ed8, #2563eb);
}

.funnel__stage.lv-1 {
  background: linear-gradient(90deg, #0369a1, #0ea5e9);
}

.funnel__stage.lv-2 {
  background: linear-gradient(90deg, #0f766e, #14b8a6);
}

.funnel__stage.lv-3 {
  background: linear-gradient(90deg, #15803d, #22c55e);
}

.funnel__stage-label {
  font-size: 12px;
  font-weight: 600;
}

.funnel__stage-count {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.funnel__rate {
  flex: none;
  height: 14px;
  font-size: 10px;
  line-height: 14px;
  color: var(--ck-text-3);
  text-align: center;
}

.funnel__backlog {
  flex: none;
  padding: 8px 10px;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.funnel__backlog-hd {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 11px;
  color: var(--ck-text-2);
}

.funnel__backlog-total {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.funnel__backlog-bar {
  display: flex;
  height: 8px;
  overflow: hidden;
  background: var(--ck-panel-3);
  border-radius: 4px;
}

.funnel__backlog-bar .seg {
  height: 100%;
  transition: width 0.3s;
}

.seg.pending,
.dot.pending {
  background: var(--ck-grade-fair);
}

.seg.progress,
.dot.progress {
  background: var(--ck-accent);
}

.seg.verifying,
.dot.verifying {
  background: var(--ck-grade-good);
}

.funnel__backlog-legend {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 10px;
  color: var(--ck-text-3);
}

.funnel__backlog-legend .dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
}
</style>
