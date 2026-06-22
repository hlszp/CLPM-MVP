<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'TuningWorkbench' });
</script>

<template>
  <Page title="整定工作台">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          整定工作台 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          整定全流程工作台：模型辨识 → 整定算法 → 闭环仿真 → 效果统计。
        </p>
      </div>
    </div>
  </Page>
</template>
