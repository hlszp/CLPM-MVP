<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'DiagnosisTracker' });
</script>

<template>
  <Page title="异常跟踪">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          异常跟踪 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          异常跟踪状态管理：PENDING / IN_PROGRESS / RESOLVED / IGNORED
          状态流转。
        </p>
      </div>
    </div>
  </Page>
</template>
