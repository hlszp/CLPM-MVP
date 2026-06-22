<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'TuningAlgorithm' });
</script>

<template>
  <Page title="整定算法">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          整定算法 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          PID 整定算法：Ziegler-Nichols / Cohen-Coon / IMC / Lambda 等方法。
        </p>
      </div>
    </div>
  </Page>
</template>
