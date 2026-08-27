<script setup lang="ts">
/**
 * 模块维护横幅（方案 §5.1 F-GL-05：banner）
 *
 * 从 store 读取所有 MAINTENANCE 态模块，在顶栏下方显示维护提示横幅。
 * 多模块并行维护时依次展示进度。
 */
import { computed } from 'vue';

import { useWorkbenchStore } from '#/store/workbench';

const store = useWorkbenchStore();

const maintenancePlugins = computed(() =>
  store.plugins.filter((p) => p.status === 'MAINTENANCE'),
);
</script>

<template>
  <div
    v-if="maintenancePlugins.length > 0"
    class="flex flex-none items-center gap-3 border-b border-orange-200 bg-orange-50 px-4 py-1 text-xs text-orange-700"
  >
    <span class="flex-none">⚙</span>
    <span
      v-for="p in maintenancePlugins"
      :key="p.module_key"
      class="truncate"
    >
      {{ p.display_name
      }}{{
        p.maintenance_window?.progress_pct != null
          ? ` 升级中 ${p.maintenance_window.progress_pct}%`
          : ' 维护中'
      }}
    </span>
  </div>
</template>
