<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'TuningStats' });
</script>

<template>
  <Page title="效果统计">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          效果统计 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          整定效果统计：整定前后 KPI 对比、改善度评估、历史整定记录。
        </p>
      </div>
    </div>
  </Page>
</template>
