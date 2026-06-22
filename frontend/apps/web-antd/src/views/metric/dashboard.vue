<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'MetricDashboard' });
</script>

<template>
  <Page title="性能看板">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          性能看板 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          展示 6 项 KPI 摘要卡片、全厂平稳率趋势、Partial 警告横幅。
        </p>
      </div>
    </div>
  </Page>
</template>
