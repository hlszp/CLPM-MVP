<script lang="ts" setup>
/**
 * UI 状态覆盖组件（V62-P1-023）
 *
 * 统一处理 loading / empty / error / success 四种页面状态，
 * 替代各页面散落的 `v-if` + 纯文字提示。
 *
 * - loading：Spin 转圈 + 提示文字
 * - empty：ant-design-vue Empty 空状态
 * - error：错误图标 + 错误信息 + 重试按钮
 * - success：透传 slot 内容
 *
 * partial 状态（如辨识成功但可信度低）不由此组件处理，
 * 由页面内 Alert 组件展示业务警告。
 */
import { Button, Empty, Spin } from 'ant-design-vue';

import { IconifyIcon } from '@vben/icons';

defineOptions({ name: 'ClpmStateOverlay' });

type OverlayStatus = 'empty' | 'error' | 'loading' | 'success';

const props = withDefaults(
  defineProps<{
    /** 当前状态 */
    status: OverlayStatus;
    /** 空状态描述文字 */
    emptyDescription?: string;
    /** 错误标题 */
    errorMessage?: string;
    /** 错误详情（次要文字） */
    errorDetail?: string;
    /** 加载提示文字 */
    loadingTip?: string;
    /** 重试按钮文字 */
    retryText?: string;
    /** 是否显示重试按钮（默认显示） */
    retryable?: boolean;
  }>(),
  {
    emptyDescription: '暂无数据',
    errorMessage: '加载失败',
    errorDetail: '',
    loadingTip: '加载中…',
    retryText: '重试',
    retryable: true,
  },
);

const emit = defineEmits<{ retry: [] }>();

function handleRetry() {
  emit('retry');
}
</script>

<template>
  <!-- success：透传 slot 内容 -->
  <template v-if="status === 'success'">
    <slot />
  </template>

  <!-- 非 success：覆盖内容区 -->
  <div
    v-else
    class="clpm-state-overlay flex flex-col items-center justify-center gap-3 py-12"
  >
    <!-- loading -->
    <Spin v-if="status === 'loading'" :tip="loadingTip" size="large" />

    <!-- empty -->
    <Empty
      v-else-if="status === 'empty'"
      :description="emptyDescription"
      :image-style="{ height: '48px' }"
    />

    <!-- error -->
    <div
      v-else-if="status === 'error'"
      class="error-state flex flex-col items-center gap-2"
    >
      <IconifyIcon
        icon="lucide:alert-circle"
        class="text-3xl"
        style="color: hsl(var(--destructive))"
      />
      <div class="text-sm font-medium" style="color: hsl(var(--foreground))">
        {{ errorMessage }}
      </div>
      <div
        v-if="errorDetail"
        class="max-w-md text-center text-xs"
        style="color: hsl(var(--muted-foreground))"
      >
        {{ errorDetail }}
      </div>
      <Button v-if="retryable" size="small" class="mt-1" @click="handleRetry">
        {{ retryText }}
      </Button>
    </div>
  </div>
</template>
