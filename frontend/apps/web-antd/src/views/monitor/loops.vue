<script lang="ts" setup>
/**
 * 回路列表独立页面（页型 B：对象表 / 面点分离）
 *
 * 路由：/monitor/loops（canonical）
 * 角色：全角色可见（ADMIN/IC_ENGINEER/PE_ENGINEER/SPONSOR/EXPERT）
 *
 * MVP v1（2026-08-16）：
 * - 复用 LoopFleetView 组件（统计卡/类型筛选/等级分布/自控率/表格/导出/WS实时）
 * - 关键词搜索（写入 URL keyword 参数，与工作台共享筛选上下文）
 * - 行点击/位号链接 → 跳转回路工作台，携带 from=/monitor/loops 及筛选上下文
 *
 * 后续按标杆设计迭代：
 * - R2.5 等级速览卡（服务端 aggregate E-1）
 * - 左脊柱装置树（从 workbench.vue 抽取为共享组件）
 * - 卡片视图（view=card 后端已支持，前端接通）
 * - 服务端排序（评分升序/日Δ E-2）
 * - 等级/状态筛选（E-3）
 */
import { ref, watch } from 'vue';

import { Input, Tooltip } from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';
import LoopFleetView from '#/components/monitor/loop-fleet-view.vue';
import { useMonitorContext } from '#/composables/use-monitor-context';

defineOptions({ name: 'MonitorLoops' });

const monitorCtx = useMonitorContext();

// ===== 关键词搜索（防抖 300ms）=====
const keyword = ref(monitorCtx.keyword.value);
let keywordTimer: ReturnType<typeof setTimeout> | null = null;

watch(keyword, (val) => {
  if (keywordTimer) clearTimeout(keywordTimer);
  keywordTimer = setTimeout(() => {
    monitorCtx.update({ keyword: val });
  }, 300);
});

// 从 URL 同步（浏览器前进/后退）
watch(
  () => monitorCtx.keyword.value,
  (val) => {
    if (val !== keyword.value) keyword.value = val;
  },
);

// ===== 行点击 → 下钻工作台 =====
function handleLoopClick(loopId: string) {
  monitorCtx.navigateWithMonitorContext('/monitor/loop-workbench', {
    loopId,
    from: '/monitor/loops',
  });
}
</script>

<template>
  <div class="monitor-loops-page flex h-full flex-col">
    <!-- R1 页头工具栏 -->
    <ClpmPageToolbar
      title="回路列表"
      subtitle="全厂回路绩效扫视，锁定例外后进入工作台处置"
    >
      <template #actions>
        <div class="flex items-center gap-2">
          <Input
            v-model:value="keyword"
            allow-clear
            placeholder="搜索位号、描述、装置"
            class="!w-64"
          >
            <template #prefix>
              <div class="i-lucide:search w-4 h-4 text-gray-400" />
            </template>
          </Input>
          <Tooltip title="支持位号、回路描述、装置名称模糊匹配">
            <span class="cursor-help text-xs text-gray-400">?</span>
          </Tooltip>
        </div>
      </template>
    </ClpmPageToolbar>

    <!-- R4 主画布：LoopFleetView 承载统计卡 + 表格 -->
    <div class="flex-1 overflow-auto p-4">
      <LoopFleetView
        :show-stats="true"
        :show-auto-refresh="true"
        @loop-click="handleLoopClick"
      />
    </div>
  </div>
</template>

<style scoped>
.monitor-loops-page {
  background: var(--clpm-bg-canvas, #f5f6f8);
}
</style>


