<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'TuningModel' });
</script>

<template>
  <Page title="模型辨识">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          模型辨识 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          回路模型辨识：FOPDT / SOPDT 等模型拟合，辨识结果可视化。
        </p>
      </div>
    </div>
  </Page>
</template>
