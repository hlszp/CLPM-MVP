<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'MetricStatistics' });
</script>

<template>
  <Page title="统计报表">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          统计报表 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          KPI
          趋势对比折线图、装置评分排名柱状图、差等生分布饼图，支持时间范围/装置/单元/指标筛选与导出。
        </p>
      </div>
    </div>
  </Page>
</template>
