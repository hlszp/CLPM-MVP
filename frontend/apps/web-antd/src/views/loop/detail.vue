<script lang="ts" setup>
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

defineOptions({ name: 'LoopDetail' });

const route = useRoute();
const loopId = route.params.id as string;
</script>

<template>
  <Page :title="`回路详情 - ${loopId}`">
    <div class="flex h-full items-center justify-center">
      <div class="text-center">
        <h2 class="text-2xl font-bold text-muted-foreground">
          回路详情 - 开发中
        </h2>
        <p class="mt-2 text-sm text-muted-foreground">
          回路 ID: {{ loopId }} | 基础信息、Tag 关联、运行态参数、PV/OP/SP
          时序趋势波形、近期 KPI 摘要。
        </p>
      </div>
    </div>
  </Page>
</template>
