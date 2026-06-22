<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'LoopMonitor' });
</script>

<template>
  <Page title="回路监控">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          回路监控 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          以列表/卡片双视图展示所有控制回路的运行态（位号/PV/SP/OP/MODE/评分/状态）。
        </p>
      </div>
    </div>
  </Page>
</template>
