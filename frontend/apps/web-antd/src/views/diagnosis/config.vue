<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'DiagnosisConfig' });
</script>

<template>
  <Page title="诊断配置">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          诊断配置 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          诊断指标配置：管理诊断规则、阈值、启用状态。
        </p>
      </div>
    </div>
  </Page>
</template>
