<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'MetricRanking' });
</script>

<template>
  <Page title="低效排行">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          低效排行 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          多维筛选 +
          排行表格（排名/位号/单元/评分/平稳率/预诊/计算状态/处理状态）。
        </p>
      </div>
    </div>
  </Page>
</template>
