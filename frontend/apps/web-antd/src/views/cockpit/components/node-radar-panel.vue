<script lang="ts" setup>
import type { GradeInfo, SixDimValues } from '../loops-shared';
import type { RankSelection } from '../utils/score-rank';

/**
 * 驾驶舱总览 · 六维绩效雷达（2026-10-10 修订：替换原处置待办 5 态胶囊）
 *
 * - 六维口径与回路页右侧雷达同源（SIX_DIMS：自控率/平稳率/准确率/快速率/
 *   好值率/有效率，0~100），数值=选中装置/单元参评回路的评估均值
 *   （节点快照 kpi_node_snapshot_hourly，evaluated_loops 加权口径）
 * - 数据：/performance/nodes/{nodeId}/snapshot（排名区选中节点 plant_node.id）
 * - 未选中 → 空态提示（不造数）；面板高度与同行绩效趋势一致（栅格行高）
 * - 中心=综合评分 + 五档等级（复用 cockpit-radar）
 */
import type { MetricApi } from '#/api/metric';

import { computed, ref, watch } from 'vue';

import { getNodeSnapshotApi } from '#/api/metric';

import { resolveGrade, sixDimsFromNodeSnapshot } from '../loops-shared';
import CockpitRadar from './cockpit-radar.vue';

const props = defineProps<{
  /** 排名区选中节点（null=未选中，显示空态提示） */
  node?: null | RankSelection;
}>();

const dims = ref<null | SixDimValues>(null);
const score = ref<null | number>(null);
const loopCount = ref<null | number>(null);
const loading = ref(false);

async function load() {
  if (!props.node) {
    dims.value = null;
    score.value = null;
    loopCount.value = null;
    return;
  }
  loading.value = true;
  try {
    const snap: MetricApi.NodeSnapshotItem | null = await getNodeSnapshotApi(
      props.node.nodeId,
    );
    dims.value = sixDimsFromNodeSnapshot(snap);
    score.value = snap?.score ?? null;
    loopCount.value = snap?.loopCount ?? null;
  } catch {
    dims.value = null;
    score.value = null;
    loopCount.value = null;
  } finally {
    loading.value = false;
  }
}

watch(() => props.node?.nodeId ?? null, load, { immediate: true });

/** C5 混合刷新：由父级触发重拉（未选中时为空操作） */
defineExpose({ reload: load });

const grade = computed<GradeInfo | null>(() => resolveGrade(score.value));

const typeZh = computed(() =>
  props.node?.type === 'UNIT' ? '单元' : '装置',
);
</script>

<template>
  <div class="cockpit-panel radar">
    <div class="cockpit-panel__hd">
      六维绩效
      <span class="sub">
        <template v-if="node">{{ node.name }} · 参评回路均值</template>
        <template v-else>点击左侧排名选择装置/单元</template>
      </span>
    </div>
    <div class="radar__bd">
      <div v-if="loading" class="radar__state">加载中…</div>
      <div v-else-if="!node" class="radar__state">
        从「综合评分」排名区点击装置或单元行<br />查看其六维绩效雷达
      </div>
      <template v-else>
        <CockpitRadar :dims="dims" :grade="grade" :score="score" />
        <div v-if="loopCount !== null" class="radar__ft">
          {{ typeZh }} · 参评回路 <b>{{ loopCount }}</b>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.radar__bd {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 4px 8px 8px;
}

.radar__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  line-height: 1.8;
  color: var(--ck-text-3);
  text-align: center;
}

.radar__ft {
  position: absolute;
  right: 12px;
  bottom: 8px;
  font-size: 10px;
  color: var(--ck-text-3);
}

.radar__ft b {
  color: var(--ck-text-2);
  font-variant-numeric: tabular-nums;
}
</style>
