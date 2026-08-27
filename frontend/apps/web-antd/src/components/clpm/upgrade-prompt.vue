<script lang="ts" setup>
/**
 * ClpmUpgradePrompt - 克制版升级引导卡
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §4.9 模式 E
 * 用于统计报告-管理总览底部：虚线边框 + lock 图标 + 灰色文字，
 * 明确告知「下一阶段可获得什么能力」，不用实心按钮/色块/营销文案。
 *
 * P0 仅建组件 + 管理总览底部空容器；P1 实装模块启用引导。
 */
import { IconifyIcon } from '@vben/icons';

defineOptions({ name: 'ClpmUpgradePrompt' });

withDefaults(
  defineProps<{
    description?: string;
    /** 下一阶段标识，默认 S2 */
    stage?: string;
    title?: string;
  }>(),
  {
    title: '升级到下一阶段',
    description: '启用更多闭环模块后，此处将展示对应管理指标。',
    stage: 'S2',
  },
);
</script>

<template>
  <div class="clpm-upgrade-prompt">
    <IconifyIcon icon="lucide:lock" class="clpm-upgrade-prompt__icon" />
    <div class="clpm-upgrade-prompt__body">
      <div class="clpm-upgrade-prompt__title">
        {{ title }}
        <span class="clpm-upgrade-prompt__stage">{{ stage }}</span>
      </div>
      <div class="clpm-upgrade-prompt__desc">
        <slot>{{ description }}</slot>
      </div>
    </div>
  </div>
</template>

<style scoped>
.clpm-upgrade-prompt {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 16px 20px;
  color: hsl(var(--muted-foreground));
  border: 1px dashed hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-upgrade-prompt__icon {
  flex: 0 0 auto;
  margin-top: 2px;
  font-size: 18px;
  color: hsl(var(--muted-foreground) / 70%);
}

.clpm-upgrade-prompt__body {
  flex: 1 1 auto;
  min-width: 0;
}

.clpm-upgrade-prompt__title {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--foreground) / 80%);
}

.clpm-upgrade-prompt__stage {
  padding: 0 6px;
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
  color: hsl(var(--muted-foreground));
  border: 1px solid hsl(var(--border));
  border-radius: 3px;
}

.clpm-upgrade-prompt__desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 18px;
}
</style>
