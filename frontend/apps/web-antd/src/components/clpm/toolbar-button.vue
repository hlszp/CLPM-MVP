<script lang="ts" setup>
import type { ButtonProps } from 'ant-design-vue';

import type { CSSProperties } from 'vue';
import type { ToolbarAction, ToolbarVariant } from './toolbar-config';

import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Button, Tooltip } from 'ant-design-vue';

import {
  TOOLBAR_DEFAULT_VARIANT,
  TOOLBAR_ICON_COLOR,
  TOOLBAR_ICON_MAP,
} from './toolbar-config';

defineOptions({ name: 'ClpmToolbarButton' });

const props = withDefaults(defineProps<Props>(), {
  label: '',
  icon: undefined,
  variant: undefined,
  active: false,
  loading: false,
  disabled: false,
  disabledReason: '',
  // P0：默认仅图标模式（对齐 ZL 工业设计规范"统一工具符 bar"约定，
  // 所有页面的工具栏按钮默认只显示图标，hover 显示 label tooltip）。
  // 需要展示"图标+文字"的少数场景显式传 :icon-only="false"。
  iconOnly: true,
  tooltip: '',
  size: 'small',
});

const emit = defineEmits<{
  click: [event: MouseEvent];
}>();

interface Props {
  /** 按钮文字 */
  label?: string;
  /**
   * 按钮功能名
   * - 传 ToolbarAction 枚举值时，自动映射图标和默认变体
   * - 传 string 时视为自定义 Iconify 图标名，需同时指定 variant
   */
  icon?: string | ToolbarAction;
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
  /** 仅图标模式（默认 true），必须配合 label 或 tooltip 使用；需展示文字时传 :icon-only="false" */
  iconOnly?: boolean;
  /** 自定义 tooltip 文案，默认用 label 或 disabledReason */
  tooltip?: string;
  /** 按钮尺寸，默认 small（对齐 PageToolbar 紧凑布局） */
  size?: ButtonProps['size'];
}

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
  if (resolvedVariant.value === 'primary') return 'primary';
  if (resolvedVariant.value === 'link') return 'link';
  if (resolvedVariant.value === 'dashed') return 'dashed';
  // #6: 标准工具（有语义色）与 export 变体统一用 text 类型——无线框、仅图标色，
  // 对齐 Google Material Design icon-button 风格
  if (resolvedVariant.value === 'export') return 'text';
  if (props.icon && props.icon in TOOLBAR_ICON_COLOR) return 'text';
  return 'default';
});

/** 是否 danger（红色） */
const isDanger = computed(() => resolvedVariant.value === 'danger');

/** 是否激活态填充 */
const isActive = computed(() => props.active && !props.disabled);

/** 实际禁用态：disabled 或 loading */
const isDisabled = computed(() => props.disabled || props.loading);

/**
 * 图标语义色（UI/UX v6.1 统一工具栏）
 * - 仅「标准工具」有语义色（TOOLBAR_ICON_COLOR）
 * - 启用态 + 中性变体（default/dashed/link）：图标套语义色
 * - 填充变体（primary/danger/export）：图标跟随前景色（白），不着色
 * - 禁用态：统一降饱和灰（见 CSS .clpm-toolbar-btn__icon--disabled）
 */
const iconColor = computed(() => {
  if (!props.icon) return '';
  return TOOLBAR_ICON_COLOR[props.icon as ToolbarAction] ?? '';
});

const iconStyle = computed<CSSProperties>(() => {
  if (isDisabled.value) return {}; // 禁用态由 CSS 强制灰
  const v = resolvedVariant.value;
  // 仅中性变体着色，填充变体保持前景色
  if (v === 'default' || v === 'dashed' || v === 'link') {
    const c = iconColor.value;
    return c ? { color: c } : {};
  }
  return {};
});

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
  <!--
    Tooltip 分支用 <span> 包裹作为单元素根节点。
    原因：antd Tooltip 内部 <Trigger> 渲染为 Fragment 根，若 ClpmToolbarButton
    的根直接是 <Tooltip>，则外部指令（如 v-permission）会沿组件链向下传递
    直到 Trigger 的 Fragment 根，触发 Vue 警告
    "Runtime directive used on component with non-element root node"。
    用原生 <span> 作为根节点让指令落在元素上，避免警告并保证指令生效。
  -->
  <span v-if="needTooltip" class="clpm-toolbar-btn-host">
    <Tooltip :title="tooltipText">
      <Button
        class="clpm-toolbar-btn"
        :class="[
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
          :class="{ 'clpm-toolbar-btn__icon--disabled': isDisabled }"
          :style="iconStyle"
        />
        <span v-if="!iconOnly && label" class="clpm-toolbar-btn__label">{{
          label
        }}</span>
      </Button>
    </Tooltip>
  </span>
  <Button
    v-else
    class="clpm-toolbar-btn"
    :class="[
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
/*
  Tooltip 分支的宿主 <span>：作为单元素根节点承接外部指令（v-permission 等）。
  inline-flex 让 <span> 紧贴内部 Button 尺寸，保持工具栏原有布局
  （此前 Button 直接作为 flex item，现由 host 承担该角色，视觉无差异）。
*/
.clpm-toolbar-btn-host {
  display: inline-flex;
  align-items: center;
}

.clpm-toolbar-btn {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

/* #6: text 类型标准工具——无线框，hover/active 用圆角底色反馈 */
.clpm-toolbar-btn.ant-btn-text {
  padding: 4px 8px;
  border: none !important;
  box-shadow: none !important;
}

.clpm-toolbar-btn.ant-btn-text:not(:disabled):hover {
  background: hsl(var(--muted) / 60%);
}

.clpm-toolbar-btn__icon {
  font-size: 16px;
}

/* 禁用态图标：统一降饱和灰（Poka-Yoke 灰而不藏，对齐 §5.1.2） */
.clpm-toolbar-btn__icon--disabled {
  color: hsl(var(--muted-foreground)) !important;
  opacity: 0.4;
}

.clpm-toolbar-btn__label {
  font-size: 13px;
  line-height: 1;
}

/* 导出变体：绿色调（对齐 THEME_COLORS.SUCCESS） */
.clpm-toolbar-btn--export:not(:disabled):not(.ant-btn-primary) {
  color: hsl(var(--success) / 85%);
}

.clpm-toolbar-btn--export:not(:disabled):not(.ant-btn-primary):hover {
  color: hsl(var(--success));
  background: hsl(var(--success) / 12%);
}

/* 激活态：主色圆角底色（用于筛选展开、自动刷新开启、视图切换激活等） */
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
