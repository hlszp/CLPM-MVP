<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'LoopTagMapping' });
</script>

<template>
  <Page title="Tag 关联管理">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          Tag 关联管理 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          为回路关联 7 个 OPC tag：PV / SP / OP / MODE / PID_P / PID_I / PID_D。
        </p>
      </div>
    </div>
  </Page>
</template>
