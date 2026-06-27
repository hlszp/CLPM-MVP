<script lang="ts" setup>
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Dropdown, Menu, Tooltip } from 'ant-design-vue';

export interface SummaryAction {
  /** 是否危险操作（红色） */
  danger?: boolean;
  disabled?: boolean;
  /** 图标名（Iconify），如 'ant-design:edit-outlined' */
  icon?: string;
  key: string;
  label: string;
  type?: 'default' | 'link' | 'primary';
}

export interface SummaryItem {
  key: string;
  label: string;
  status?: 'danger' | 'neutral' | 'primary' | 'success' | 'warning';
  value: number | string;
}

defineOptions({ name: 'ClpmObjectSummaryBar' });

interface Props {
  actions?: SummaryAction[];
  /** 当操作数量超过此值时，多余操作收进"更多"下拉，0 表示不收起 */
  collapseAt?: number;
  items?: SummaryItem[];
  /** 加载态骨架屏 */
  loading?: boolean;
  /**
   * 主指标：突出展示，大号数值
   * 设置后在标题旁显示 24-28px 大号数值 + 标签
   */
  primaryItem?: SummaryItem | null;
  subtitle?: string;
  tags?: SummaryItem[];
  title: string;
}

const props = withDefaults(defineProps<Props>(), {
  actions: () => [],
  collapseAt: 3,
  items: () => [],
  loading: false,
  primaryItem: null,
  subtitle: '',
  tags: () => [],
});

const emit = defineEmits<{
  action: [key: string];
}>();

/** 外露操作（前 collapseAt 个） */
const visibleActions = computed(() => {
  if (props.collapseAt <= 0) return props.actions;
  return props.actions.slice(0, props.collapseAt);
});

/** 折叠操作（collapseAt 之后的） */
const collapsedActions = computed(() => {
  if (props.collapseAt <= 0) return [];
  return props.actions.slice(props.collapseAt);
});

/** 是否显示"更多"下拉 */
const hasMoreActions = computed(() => collapsedActions.value.length > 0);
</script>

<template>
  <section class="clpm-summary-bar" :class="{ 'is-loading': loading }">
    <!-- 骨架屏 -->
    <template v-if="loading">
      <div class="clpm-summary-bar__skeleton">
        <div class="clpm-summary-bar__skeleton-title"></div>
        <div class="clpm-summary-bar__skeleton-subtitle"></div>
      </div>
      <div class="clpm-summary-bar__skeleton-items">
        <div v-for="i in 4" :key="i" class="clpm-summary-bar__skeleton-item"></div>
      </div>
    </template>

    <!-- 实际内容 -->
    <template v-else>
      <div class="clpm-summary-bar__main">
        <div class="clpm-summary-bar__identity">
          <div class="clpm-summary-bar__title">{{ title }}</div>
          <div v-if="subtitle" class="clpm-summary-bar__subtitle">{{ subtitle }}</div>
        </div>

        <!-- 主指标突出展示 -->
        <div v-if="primaryItem" class="clpm-summary-bar__primary">
          <span class="clpm-summary-bar__primary-label">{{ primaryItem.label }}</span>
          <span
            class="clpm-summary-bar__primary-value"
            :class="`is-${primaryItem.status || 'neutral'}`"
          >
            {{ primaryItem.value }}
          </span>
        </div>

        <div v-if="tags.length" class="clpm-summary-bar__tags">
          <Tooltip v-for="tag in tags" :key="tag.key" :title="tag.label">
            <span
              class="clpm-summary-bar__tag"
              :class="`is-${tag.status || 'neutral'}`"
            >
              <span class="clpm-summary-bar__tag-label">{{ tag.label }}</span>
              <span class="clpm-summary-bar__tag-value">{{ tag.value }}</span>
            </span>
          </Tooltip>
        </div>
      </div>

      <div v-if="items.length" class="clpm-summary-bar__items">
        <div v-for="item in items" :key="item.key" class="clpm-summary-bar__item">
          <span class="clpm-summary-bar__item-label">{{ item.label }}</span>
          <span
            class="clpm-summary-bar__item-value"
            :class="`is-${item.status || 'neutral'}`"
          >
            {{ item.value }}
          </span>
        </div>
      </div>

      <div
        v-if="actions.length || $slots.actions || hasMoreActions"
        class="clpm-summary-bar__actions"
      >
        <slot name="actions">
          <!-- 外露操作 -->
          <button
            v-for="action in visibleActions"
            :key="action.key"
            class="clpm-summary-bar__action"
            :class="[`is-${action.type || 'default'}`, { 'is-danger': action.danger }]"
            :disabled="action.disabled"
            type="button"
            @click="emit('action', action.key)"
          >
            <IconifyIcon v-if="action.icon" :icon="action.icon" class="clpm-summary-bar__action-icon" />
            {{ action.label }}
          </button>

          <!-- 更多操作下拉 -->
          <Dropdown v-if="hasMoreActions" placement="bottomRight">
            <button class="clpm-summary-bar__action is-default" type="button">
              更多
              <IconifyIcon icon="ant-design:down-outlined" class="clpm-summary-bar__action-icon" />
            </button>
            <template #overlay>
              <Menu>
                <Menu.Item
                  v-for="action in collapsedActions"
                  :key="action.key"
                  :danger="action.danger"
                  :disabled="action.disabled"
                  @click="emit('action', action.key)"
                >
                  <IconifyIcon v-if="action.icon" :icon="action.icon" class="clpm-summary-bar__action-icon" />
                  <span>{{ action.label }}</span>
                </Menu.Item>
              </Menu>
            </template>
          </Dropdown>
        </slot>
      </div>
    </template>
  </section>
</template>

<style scoped>
.clpm-summary-bar {
  align-items: stretch;
  background: linear-gradient(180deg, hsl(var(--card)) 0%, hsl(var(--muted) / 35%) 100%);
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  display: flex;
  gap: 14px;
  padding: 10px 12px;
}

.clpm-summary-bar__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.clpm-summary-bar__identity {
  min-width: 0;
}

.clpm-summary-bar__title {
  color: hsl(var(--foreground));
  font-size: 16px;
  font-weight: 700;
  line-height: 22px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clpm-summary-bar__subtitle {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 主指标突出展示 */
.clpm-summary-bar__primary {
  align-items: baseline;
  display: flex;
  gap: 8px;
}

.clpm-summary-bar__primary-label {
  color: hsl(var(--muted-foreground));
  font-size: 13px;
}

.clpm-summary-bar__primary-value {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-feature-settings: 'tnum';
  font-size: 26px;
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  line-height: 32px;
}

.clpm-summary-bar__tags,
.clpm-summary-bar__items,
.clpm-summary-bar__actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.clpm-summary-bar__tag {
  align-items: center;
  border: 1px solid hsl(var(--border));
  border-radius: 999px;
  display: inline-flex;
  gap: 5px;
  min-height: 24px;
  padding: 2px 8px;
}

.clpm-summary-bar__tag-label,
.clpm-summary-bar__item-label {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
}

.clpm-summary-bar__tag-value,
.clpm-summary-bar__item-value {
  color: hsl(var(--foreground));
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-feature-settings: 'tnum';
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

.clpm-summary-bar__items {
  border-left: 1px solid hsl(var(--border));
  flex: 0 0 auto;
  padding-left: 14px;
}

.clpm-summary-bar__item {
  display: grid;
  gap: 2px;
  min-width: 72px;
}

.clpm-summary-bar__item-value {
  font-size: 15px;
}

.clpm-summary-bar__actions {
  border-left: 1px solid hsl(var(--border));
  flex: 0 0 auto;
  padding-left: 14px;
}

.clpm-summary-bar__action {
  align-items: center;
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  color: hsl(var(--foreground));
  cursor: pointer;
  display: inline-flex;
  font-size: 12px;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
}

.clpm-summary-bar__action:hover:not(:disabled) {
  border-color: hsl(var(--primary) / 50%);
  color: hsl(var(--primary));
}

.clpm-summary-bar__action.is-primary {
  background: hsl(var(--primary));
  border-color: hsl(var(--primary));
  color: hsl(var(--primary-foreground));
}

.clpm-summary-bar__action.is-primary:hover:not(:disabled) {
  background: hsl(var(--primary) / 90%);
  color: hsl(var(--primary-foreground));
}

.clpm-summary-bar__action.is-danger {
  border-color: hsl(var(--destructive) / 50%);
  color: hsl(var(--destructive));
}

.clpm-summary-bar__action.is-danger:hover:not(:disabled) {
  background: hsl(var(--destructive) / 8%);
  border-color: hsl(var(--destructive));
}

.clpm-summary-bar__action-icon {
  font-size: 13px;
}

.is-success { color: hsl(var(--success)); }
.is-warning { color: hsl(var(--warning)); }
.is-danger { color: hsl(var(--destructive)); }
.is-primary { color: hsl(var(--primary)); }
.is-neutral { color: hsl(var(--muted-foreground)); }

/* 骨架屏 */
.clpm-summary-bar.is-loading {
  pointer-events: none;
}

.clpm-summary-bar__skeleton,
.clpm-summary-bar__skeleton-items {
  animation: clpm-summary-skeleton 1.5s ease-in-out infinite;
  background: linear-gradient(
    90deg,
    hsl(var(--muted)) 25%,
    hsl(var(--accent)) 37%,
    hsl(var(--muted)) 63%
  );
  background-size: 400% 100%;
  border-radius: 2px;
}

.clpm-summary-bar__skeleton {
  flex: 1 1 auto;
}

.clpm-summary-bar__skeleton-title {
  height: 18px;
  margin-bottom: 8px;
  width: 50%;
}

.clpm-summary-bar__skeleton-subtitle {
  height: 14px;
  width: 30%;
}

.clpm-summary-bar__skeleton-items {
  display: flex;
  flex: 0 0 auto;
  gap: 12px;
  padding-left: 14px;
}

.clpm-summary-bar__skeleton-item {
  background: hsl(var(--card) / 50%);
  height: 40px;
  width: 80px;
}

@keyframes clpm-summary-skeleton {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}
</style>
