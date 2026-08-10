/** * 工作台推荐下一步（MW-P3-07） * * 页面只保留一个 primary 主动作 + 原因 +
前置条件。 * 主动作执行前沿用现有可信度与危险确认门禁。 * 对齐整改方案 §7.3
推荐下一步规则。 */
<script lang="ts" setup>
import type { MonitorApi } from '#/api/monitor';

import { computed } from 'vue';

import { Button, Tag } from 'ant-design-vue';

defineOptions({ name: 'WorkbenchNextAction' });

const props = defineProps<{
  nextAction: MonitorApi.NextAction;
}>();

const emit = defineEmits<{
  (e: 'action', actionType: MonitorApi.NextActionType): void;
}>();

const isPassive = computed(
  () => props.nextAction.actionType === 'CONTINUE_MONITORING',
);

function handleAction() {
  if (!props.nextAction.enabled) return;
  emit('action', props.nextAction.actionType);
}
</script>

<template>
  <div class="next-action" role="region" aria-label="推荐下一步">
    <div class="next-action__left">
      <span class="next-action__icon">
        <Tag
          :color="isPassive ? 'default' : 'processing'"
          class="!m-0 !text-[10px]"
        >
          推荐
        </Tag>
      </span>
      <div class="next-action__text">
        <div class="next-action__label">
          {{ nextAction.label }}
        </div>
        <div class="next-action__reason">{{ nextAction.reason }}</div>
      </div>
    </div>
    <div class="next-action__right">
      <Button
        :type="isPassive ? 'default' : 'primary'"
        size="small"
        :disabled="!nextAction.enabled"
        :title="nextAction.disabledReason ?? undefined"
        @click="handleAction"
      >
        {{ nextAction.label }}
      </Button>
      <span
        v-if="!nextAction.enabled && nextAction.disabledReason"
        class="next-action__disabled"
      >
        {{ nextAction.disabledReason }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.next-action {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 4px 8px;
  background: hsl(var(--accent) / 5%);
  border: 1px solid hsl(var(--accent) / 20%);
  border-radius: 6px;
}

.next-action__left {
  display: flex;
  flex: 1;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.next-action__icon {
  flex-shrink: 0;
}

.next-action__text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.next-action__label {
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--foreground) / 90%);
  white-space: nowrap;
}

.next-action__reason {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
  white-space: nowrap;
}

.next-action__right {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  align-items: center;
}

.next-action__disabled {
  font-size: 11px;
  color: hsl(var(--status-warn) / 80%);
  white-space: nowrap;
}
</style>
