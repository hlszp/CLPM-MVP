<script lang="ts" setup>
import { Page } from '@vben/common-ui';

defineOptions({ name: 'DiagnosisWaveform' });
</script>

<template>
  <Page title="波形查看">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          波形查看 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          PV/SP/OP 时序趋势波形（PV 线根据质量码断线），支持时间范围选择与缩放。
        </p>
      </div>
    </div>
  </Page>
</template>
