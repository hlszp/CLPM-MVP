<script setup lang="ts">
/**
 * OperationalContextProvider
 * Phase 1 共享载体：跨组件层级注入 OperationalContext
 *
 * 用法：
 * <OperationalContextProvider>
 *   <ChildComponent /> <!-- 内部可 injectOperationalContext() -->
 * </OperationalContextProvider>
 */
import { provideOperationalContext } from '#/composables/use-operational-context';

provideOperationalContext();
</script>

<template>
  <slot />
</template>
