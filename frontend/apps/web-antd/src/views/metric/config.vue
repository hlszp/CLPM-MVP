<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'MetricConfig' });
</script>

<template>
  <Page title="指标配置">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          指标配置 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          管理 6 大核心
          KPI（好值率、自控率、平稳率、准确率、振荡率、饱和率）的计算公式、权重、阈值、启用状态。
        </p>
      </div>
    </div>
  </Page>
</template>
