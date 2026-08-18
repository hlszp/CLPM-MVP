<script lang="ts" setup>
/**
 * 整定工作台（整定模块主入口，09 设计方案 §6.2）
 *
 * 单页 4 锚点流程：① 过程辨识 → ② 整定矩阵 → ③ 仿真对比 → ④ 方案确认。
 * 顺序解锁（前序未完成后续置灰，Poka-Yoke）；跨锚点状态见
 * composables/use-tuning-workbench.ts。
 *
 * 入口上下文：?loopId=xx&from=diagnosis（诊断 TUNING 类建议「去整定」）。
 */
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Alert, Select } from 'ant-design-vue';

import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';

import ConfirmSection from './components/confirm-section.vue';
import IdentifySection from './components/identify-section.vue';
import MatrixSection from './components/matrix-section.vue';
import SimulateSection from './components/simulate-section.vue';
import { useTuningWorkbench } from './composables/use-tuning-workbench';

defineOptions({ name: 'TuningWorkbench' });

const route = useRoute();
const ctx = useTuningWorkbench();

const fromDiagnosis = ref(false);

const anchors = [
  { href: '#tuning-anchor-identify', label: '① 过程辨识' },
  { href: '#tuning-anchor-matrix', label: '② 整定矩阵' },
  { href: '#tuning-anchor-simulate', label: '③ 仿真对比' },
  { href: '#tuning-anchor-confirm', label: '④ 方案确认' },
];

function scrollTo(href: string) {
  document
    .querySelector(href)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

onMounted(async () => {
  await ctx.loadLoops();
  const loopId = route.query.loopId as string | undefined;
  if (loopId) {
    fromDiagnosis.value = route.query.from === 'diagnosis';
    ctx.selectLoop(loopId);
  }
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      subtitle="回路 PID 参数优化：辨识 → 整定矩阵 → 仿真对比 → 方案确认"
      title="整定工作台"
    >
      <template #actions>
        <Select
          :value="ctx.loopId.value || undefined"
          show-search
          :options="ctx.loopOptions.value"
          :loading="ctx.loopsLoading.value"
          :filter-option="false"
          placeholder="选择回路（输入位号搜索）"
          style="width: 280px"
          size="small"
          @search="ctx.loadLoops"
          @change="(v: any) => ctx.selectLoop(String(v))"
        />
      </template>
    </ClpmPageToolbar>

    <Alert
      v-if="fromDiagnosis"
      class="mb-2"
      type="info"
      message="来自诊断中心的整定请求：已预填回路，可直接发起过程辨识"
      show-icon
      closable
    />

    <!-- 锚点导航（吸顶） -->
    <div class="tuning-anchor-nav">
      <a
        v-for="a in anchors"
        :key="a.href"
        class="tuning-anchor-link"
        @click.prevent="scrollTo(a.href)"
      >
        {{ a.label }}
      </a>
    </div>

    <template v-if="ctx.loopId.value">
      <IdentifySection :ctx="ctx" />
      <MatrixSection :ctx="ctx" />
      <SimulateSection :ctx="ctx" />
      <ConfirmSection :ctx="ctx" />
    </template>
    <div v-else class="py-16 text-center text-sm text-neutral-400">
      请先在右上角选择回路
    </div>
  </Page>
</template>

<style scoped>
.tuning-anchor-nav {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  gap: 16px;
  padding: 6px 12px;
  margin-bottom: 8px;
  background: hsl(var(--background));
  border-bottom: 1px solid hsl(var(--border));
}

.tuning-anchor-link {
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
}

.tuning-anchor-link:hover {
  text-decoration: underline;
}

:deep(.tuning-section) {
  margin-bottom: 12px;
  scroll-margin-top: 48px;
}
</style>
