<script lang="ts" setup>
/**
 * 报告类页面统一错误态（2026-09-24 新增）
 *
 * 背景：报告模块 6 个页面的 load() 普遍是空 catch，接口失败后只把 data 置空，
 * 页面渲染成"没有数据"的空图表 —— 管理员无法区分"确实没数据"与"服务挂了"，
 * 运维侧也没有任何信号。本组件提供常驻、可重试的错误提示。
 */
defineOptions({ name: 'ClpmLoadErrorAlert' });

withDefaults(
  defineProps<{
    /** 是否处于加载失败态 */
    error?: boolean;
    /** 错误文案（默认通用文案，不暴露实现细节） */
    errorText?: string;
  }>(),
  {
    error: false,
    errorText: '数据加载失败，请稍后重试；若持续失败请联系系统管理员',
  },
);

const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <Alert
    v-if="error"
    class="mb-3"
    show-icon
    type="error"
    :message="errorText"
  >
    <template #action>
      <Button size="small" type="link" @click="emit('retry')">重新加载</Button>
    </template>
  </Alert>
</template>
