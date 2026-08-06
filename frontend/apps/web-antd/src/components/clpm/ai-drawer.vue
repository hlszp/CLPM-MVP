<script lang="ts" setup>
/**
 * ClpmAiDrawer — AI 洞察右抽屉（IA 重构 Phase A·§5.2.3）
 *
 * 右侧 overlay 抽屉，动画 ≤300ms（ease-out-quint），遮罩可关、Esc 可关。
 * 内部复用 ClpmAiInsight 渲染洞察正文（autoLoad，LLM 失败 fallback 模板）。
 *
 * 调用方：工具栏 AI 图标（已通过两级门禁）点击后 open=true。
 * 设计依据：IA 重构方案 §5.2；对齐 ZL 工业设计规范（Calm UI / Poka-Yoke）。
 */
import type { AiInsightApi } from '#/api/ai-insight';

import { ClpmAiInsight } from './index';

import { Drawer } from 'ant-design-vue';

defineOptions({ name: 'ClpmAiDrawer' });

interface Props {
  /** v-model:open */
  open: boolean;
  /** 场景：diagnosis/performance/tuning/workbench */
  scene: AiInsightApi.SceneId;
  /** 回路 ID（diagnosis/performance/tuning 场景需要） */
  loopId?: null | string;
  /** 整定任务 ID（tuning 场景需要） */
  taskId?: null | string;
  /** 抽屉标题，默认按 scene */
  title?: string;
}

const props = withDefaults(defineProps<Props>(), {
  loopId: null,
  taskId: null,
  title: '',
});

const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>();

const SCENE_TITLE: Record<AiInsightApi.SceneId, string> = {
  diagnosis: 'AI 诊断洞察',
  performance: 'AI 性能分析',
  tuning: 'AI 整定建议',
  workbench: 'AI 运维洞察',
};

function handleClose(): void {
  emit('update:open', false);
}
</script>

<template>
  <Drawer
    :body-style="{ padding: '16px' }"
    :mask="true"
    :mask-closable="true"
    :open="open"
    placement="right"
    :root-style="{ '--clpm-ai-drawer-transition': '300ms cubic-bezier(0.16,1,0.3,1)' }"
    :title="props.title || SCENE_TITLE[props.scene]"
    :width="480"
    @close="handleClose"
  >
    <ClpmAiInsight
      :auto-load="true"
      :hide-when-disabled="false"
      :loop-id="props.loopId"
      :scene="props.scene"
      :task-id="props.taskId"
      variant="tab"
    />
  </Drawer>
</template>

<style scoped>
:deep(.ant-drawer-content-wrapper) {
  transition: transform var(
    --clpm-ai-drawer-transition,
    300ms cubic-bezier(0.16, 1, 0.3, 1)
  );
}
</style>
