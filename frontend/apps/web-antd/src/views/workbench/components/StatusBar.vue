<script setup lang="ts">
/**
 * 工作台底栏（方案 §5.1 F-GL-03：28px）
 *
 * 刷新时间 / 评估周期 / 数据流时延 / 插件在线数
 * - 刷新时间：store.lastRefreshAt
 * - 评估周期：workbench_precalc 5min（Celery beat 固定周期）
 * - 时延：M1 桩，M2 接 SignalR Hub 实测时延
 * - 插件在线：store.plugins 中 CORE+ENABLED 计数
 */
import { computed } from 'vue';

import { useWorkbenchStore } from '#/store/workbench';

const store = useWorkbenchStore();

const refreshTime = computed(() => {
  if (!store.lastRefreshAt) return '—';
  return new Date(store.lastRefreshAt).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
});

const onlinePluginCount = computed(
  () =>
    store.plugins.filter(
      (p) => p.status === 'CORE' || p.status === 'ENABLED',
    ).length,
);
const maintenancePluginCount = computed(
  () => store.plugins.filter((p) => p.status === 'MAINTENANCE').length,
);
const uninstalledPluginCount = computed(
  () => store.plugins.filter((p) => p.status === 'UNINSTALLED').length,
);
</script>

<template>
  <footer
    class="flex h-7 flex-none items-center gap-4 border-t border-[#E4E7ED] bg-white px-4 text-[11px] text-gray-500"
  >
    <span>刷新时间：<span class="text-gray-700">{{ refreshTime }}</span></span>
    <span class="text-gray-300">|</span>
    <span>评估周期：<span class="text-gray-700">5min</span></span>
    <span class="text-gray-300">|</span>
    <span>数据流时延：<span class="text-gray-700">—</span></span>
    <!-- A2 状态色图例 + 计数（全局参考，与 tabbar 状态点/系统健康带同色系） -->
    <span class="ml-auto flex items-center gap-2">
      <span class="flex items-center gap-0.5" title="在线（CORE/ENABLED）">
        <span
          class="inline-block h-1.5 w-1.5 rounded-full"
          style="background: #52c41a"
        ></span>在线 {{ onlinePluginCount }}
      </span>
      <span class="flex items-center gap-0.5" title="维护中（MAINTENANCE）">
        <span
          class="inline-block h-1.5 w-1.5 rounded-full"
          style="background: #fa8c16"
        ></span>维护 {{ maintenancePluginCount }}
      </span>
      <span class="flex items-center gap-0.5" title="未安装（UNINSTALLED）">
        <span
          class="inline-block h-1.5 w-1.5 rounded-full"
          style="background: #bfbfbf"
        ></span>未装 {{ uninstalledPluginCount }}
      </span>
    </span>
  </footer>
</template>
