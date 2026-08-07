<script lang="ts" setup>
/**
 * 工具栏分组分隔符（UI/UX v6.1 统一工具栏 §5.1.1）
 *
 * 用于在 ClpmPageToolbar 的 #actions 槽中分隔不同工具组
 * （数据组 / 智能组 / 帮助组），提升视觉层次与功能辨识度。
 *
 * 用法：
 *   <template #actions>
 *     <ClpmToolbarButton icon="refresh" ... />
 *     <ClpmToolbarButton icon="export" ... />
 *     <ClpmToolbarDivider />
 *     <ClpmToolbarButton icon="ai" ... />
 *     <ClpmToolbarDivider />
 *     <ClpmToolbarButton icon="help" ... />
 *   </template>
 */
defineOptions({ name: 'ClpmToolbarDivider' });
</script>

<template>
  <span class="clpm-toolbar-divider" aria-hidden="true" />
</template>

<style scoped>
.clpm-toolbar-divider {
  display: inline-block;
  flex: 0 0 auto;
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: hsl(var(--border));
}
</style>
