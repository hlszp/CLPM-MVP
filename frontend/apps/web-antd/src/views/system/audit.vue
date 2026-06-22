<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'SystemAudit' });
</script>

<template>
  <Page title="审计日志">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          审计日志 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          操作审计日志查询：用户、操作类型、时间范围、对象筛选。
        </p>
      </div>
    </div>
  </Page>
</template>
