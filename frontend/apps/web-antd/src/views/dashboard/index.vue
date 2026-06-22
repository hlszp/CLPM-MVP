<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'DashboardPage' });
</script>

<template>
  <Page title="性能总览首页">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          性能总览首页 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          工作台（门户）：全角色日常作业入口，聚合性能评估、诊断中心、Action
          Tracker 多模块数据。
        </p>
      </div>
    </div>
  </Page>
</template>
