<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'DiagnosisAbCompare' });
</script>

<template>
  <Page title="A/B 对比">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          A/B 对比 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          回路 A/B 时段对比分析：整定前后波形对比、KPI 对比、改善度评估。
        </p>
      </div>
    </div>
  </Page>
</template>
