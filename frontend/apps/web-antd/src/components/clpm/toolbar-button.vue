<script lang="ts" setup>
import type { ButtonProps } from 'ant-design-vue';

import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Button, Tooltip } from 'ant-design-vue';

import { TOOLBAR_DEFAULT_VARIANT, TOOLBAR_ICON_MAP } from './toolbar-config';
import type { ToolbarAction, ToolbarVariant } from './toolbar-config';

defineOptions({ name: 'ClpmToolbarButton' });

interface Props {
  /** 按钮文字 */
  label?: string;
  /**
   * 按钮功能名
   * - 传 ToolbarAction 枚举值时，自动映射图标和默认变体
   * - 传 string 时视为自定义 Iconify 图标名，需同时指定 variant
   */
  icon?: ToolbarAction | string;
  /** 功能色变体，未指定时根据 icon 推导 */
  variant?: ToolbarVariant;
  /** 激活态（如自动刷新开启时），仅 default/dashed 变体生效，激活后变 primary 填充 */
  active?: boolean;
  /** 加载态 */
  loading?: boolean;
  /** 禁用态 */
  disabled?: boolean;
  /** 禁用原因，用于 Tooltip 显示 */
  disabledReason?: string;
  /** 仅图标模式，必须配合 label 或 tooltip 使用 */
  iconOnly?: boolean;
  /** 自定义 tooltip 文案，默认用 label 或 disabledReason */
  tooltip?: string;
  /** 按钮尺寸，默认 small（对齐 PageToolbar 紧凑布局） */
  size?: ButtonProps['size'];
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  icon: undefined,
  variant: undefined,
  active: false,
  loading: false,
  disabled: false,
  disabledReason: '',
  iconOnly: false,
  tooltip: '',
  size: 'small',
});

const emit = defineEmits<{
  click: [event: MouseEvent];
}>();

/** 解析图标名：枚举值走映射表，其他字符串直接当 iconify 名 */
const iconName = computed(() => {
  if (!props.icon) return '';
  return TOOLBAR_ICON_MAP[props.icon as ToolbarAction] ?? props.icon;
});

/** 解析变体：显式指定 > 根据 icon 默认推导 > default */
const resolvedVariant = computed<ToolbarVariant>(() => {
  if (props.variant) return props.variant;
  if (props.icon && props.icon in TOOLBAR_DEFAULT_VARIANT) {
    return TOOLBAR_DEFAULT_VARIANT[props.icon as ToolbarAction];
  }
  return 'default';
});

/** Ant Design Button 的 type 属性 */
const buttonType = computed(() => {
  if (resolvedVariant.value === 'danger') return 'default';
  if (resolvedVariant.value === 'export') return 'default';
  if (resolvedVariant.value === 'primary') return 'primary';
  if (resolvedVariant.value === 'link') return 'link';
  if (resolvedVariant.value === 'dashed') return 'dashed';
  return 'default';
});

/** 是否 danger（红色） */
const isDanger = computed(() => resolvedVariant.value === 'danger');

/** 是否激活态填充 */
const isActive = computed(() => props.active && !props.disabled);

/** 实际禁用态：disabled 或 loading */
const isDisabled = computed(() => props.disabled || props.loading);

/** Tooltip 文案 */
const tooltipText = computed(() => {
  if (props.tooltip) return props.tooltip;
  if (props.disabled && props.disabledReason) return props.disabledReason;
  if (props.iconOnly) return props.label;
  return '';
});

/** 是否需要 Tooltip */
const needTooltip = computed(() => Boolean(tooltipText.value));

/** 点击事件透传 */
function handleClick(event: MouseEvent) {
  if (isDisabled.value) return;
  emit('click', event);
}

/**
 * 导出变体的样式类
 * antd Button 无内置 export 语义色，通过 CSS 覆盖为绿色
 */
const exportClass = 'clpm-toolbar-btn--export';
/** 激活态样式类 */
const activeClass = 'clpm-toolbar-btn--active';
</script>

<template>
  <Tooltip v-if="needTooltip" :title="tooltipText">
    <Button
      :class="[
        'clpm-toolbar-btn',
        resolvedVariant === 'export' ? exportClass : '',
        isActive ? activeClass : '',
      ]"
      :danger="isDanger"
      :disabled="isDisabled"
      :loading="loading"
      :size="size"
      :type="buttonType"
      @click="handleClick"
    >
      <IconifyIcon
        v-if="iconName"
        :icon="iconName"
        class="clpm-toolbar-btn__icon"
      />
      <span v-if="!iconOnly && label" class="clpm-toolbar-btn__label">{{
        label
      }}</span>
    </Button>
  </Tooltip>
  <Button
    v-else
    :class="[
      'clpm-toolbar-btn',
      resolvedVariant === 'export' ? exportClass : '',
      isActive ? activeClass : '',
    ]"
    :danger="isDanger"
    :disabled="isDisabled"
    :loading="loading"
    :size="size"
    :type="buttonType"
    @click="handleClick"
  >
    <IconifyIcon
      v-if="iconName"
      :icon="iconName"
      class="clpm-toolbar-btn__icon"
    />
    <span v-if="!iconOnly && label" class="clpm-toolbar-btn__label">{{
      label
    }}</span>
  </Button>
</template>

<style scoped>
.clpm-toolbar-btn {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.clpm-toolbar-btn__icon {
  font-size: 14px;
}

.clpm-toolbar-btn__label {
  font-size: 13px;
  line-height: 1;
}

/* 导出变体：绿色调（对齐 THEME_COLORS.SUCCESS） */
.clpm-toolbar-btn--export:not(:disabled):not(.ant-btn-primary) {
  color: hsl(var(--success) / 85%);
  border-color: hsl(var(--success));
}

.clpm-toolbar-btn--export:not(:disabled):not(.ant-btn-primary):hover {
  color: hsl(var(--success));
  background: hsl(var(--success) / 12%);
  border-color: hsl(var(--success) / 75%);
}

.clpm-toolbar-btn--export:not(:disabled):not(.ant-btn-primary):active {
  background: hsl(var(--success) / 18%);
  border-color: hsl(var(--success) / 85%);
}

/* 激活态：主色填充（用于自动刷新开启、视图切换激活等） */
.clpm-toolbar-btn--active:not(:disabled) {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.clpm-toolbar-btn--active:not(:disabled):hover {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary) / 90%);
  border-color: hsl(var(--primary) / 90%);
}
</style>
