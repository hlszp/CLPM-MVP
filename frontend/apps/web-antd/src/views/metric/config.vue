<script lang="ts" setup>
import { defineAsyncComponent } from 'vue';

defineOptions({ name: 'MetricConfig' });

const DefinitionTab = defineAsyncComponent(() => import('./config-definition.vue'));
</script>

<template>
  <DefinitionTab />
</template>
