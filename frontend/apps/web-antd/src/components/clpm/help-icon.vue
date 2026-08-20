<script setup lang="ts">
/**
 * ClpmHelpIcon — 帮助符号（?）
 *
 * 页面/Tab 内联帮助入口：问号图标，hover 提示"查看帮助"，点击弹出
 * showPageHelp 汇总说明弹窗。用于替代页面内大段说明文字块（信息密度
 * 治理：说明收拢进帮助，正文只留数据）。
 */
import { IconifyIcon } from '@vben/icons';

import { showPageHelp } from '#/composables/use-page-toolbar';

interface Props {
  /** 帮助标题（弹窗标题） */
  title: string;
  /** 汇总说明正文 */
  content: string;
  /** 图标大小，默认 14 */
  size?: number;
}

const props = withDefaults(defineProps<Props>(), {
  size: 14,
});

function openHelp() {
  showPageHelp({ title: props.title, content: props.content });
}
</script>

<template>
  <span
    class="clpm-help-icon"
    role="button"
    tabindex="0"
    title="查看帮助"
    @click="openHelp"
    @keydown.enter="openHelp"
  >
    <IconifyIcon icon="ant-design:question-circle-outlined" :size="size" />
  </span>
</template>

<style scoped>
.clpm-help-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: 4px;
  vertical-align: middle;
  color: hsl(var(--foreground) / 35%);
  cursor: help;
  transition: color 0.15s;
}

.clpm-help-icon:hover {
  color: hsl(var(--status-info));
}
</style>
