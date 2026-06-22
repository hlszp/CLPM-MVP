<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'SystemPermissions' });
</script>

<template>
  <Page title="权限矩阵">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          权限矩阵 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          角色权限矩阵配置：5 类角色 × 模块操作权限的矩阵视图。
        </p>
      </div>
    </div>
  </Page>
</template>
