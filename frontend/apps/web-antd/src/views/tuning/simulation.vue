<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'TuningSimulation' });
</script>

<template>
  <Page title="闭环仿真">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          闭环仿真 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          闭环仿真：整定前后阶跃响应对比、扰动抑制对比。
        </p>
      </div>
    </div>
  </Page>
</template>
