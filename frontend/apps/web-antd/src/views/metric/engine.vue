<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'MetricEngine' });
</script>

<template>
  <Page title="引擎规则">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          引擎规则 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          配置评估引擎的计算周期、数据拉取规则、调度参数。
        </p>
      </div>
    </div>
  </Page>
</template>
