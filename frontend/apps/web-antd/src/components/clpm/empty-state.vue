<template>
  <Empty :description="null" :image-style="imageStyle" class="clpm-empty-state">
    <div class="clpm-empty-state__content">
      <IconifyIcon
        :icon="sceneConfig.icon"
        :size="iconSize"
        style="margin-bottom: 12px; color: hsl(var(--foreground) / 25%)"
      />
      <div class="clpm-empty-state__title">{{ title }}</div>
      <p v-if="description" class="clpm-empty-state__desc">{{ description }}</p>
      <div
        v-if="actions && actions.length > 0"
        class="clpm-empty-state__actions"
      >
        <Button
          v-for="(action, idx) in actions"
          :key="idx"
          :type="action.primary ? 'primary' : 'default'"
          size="small"
          @click="action.onClick?.()"
        >
          <IconifyIcon
            v-if="action.icon"
            :icon="action.icon"
            :size="14"
            style="margin-right: 4px"
          />
          {{ action.label }}
        </Button>
      </div>
    </div>
  </Empty>
</template>

<script setup lang="ts">
import { computed } from 'vue';

import { Button, Empty } from 'ant-design-vue';
import { IconifyIcon } from '@vben/icons';

interface EmptyAction {
  label: string;
  icon?: string;
  primary?: boolean;
  onClick?: () => void;
}

interface Props {
  /** 空状态标题 */
  title?: string;
  /** 描述文字 */
  description?: string;
  /** 预设场景：tracker(无异常) / data(无数据) / loop(无回路) / task(无任务) / custom */
  scene?: 'custom' | 'data' | 'loop' | 'task' | 'tracker';
  /** 操作按钮 */
  actions?: EmptyAction[];
  /** 图标大小 */
  iconSize?: number;
}

const props = withDefaults(defineProps<Props>(), {
  title: '',
  description: '',
  scene: 'custom',
  actions: () => [],
  iconSize: 48,
});

type SceneKey = 'custom' | 'data' | 'loop' | 'task' | 'tracker';

interface SceneConfig {
  title: string;
  description: string;
  icon: string;
}

const presetScenes: Record<SceneKey, SceneConfig> = {
  tracker: {
    title: '暂无待处理异常',
    description: '当前回路运行状态良好，未检测到需要跟踪处理的诊断异常',
    icon: 'lucide:check-circle-2',
  },
  data: {
    title: '暂无数据',
    description: '请先完成回路配置和历史数据导入后再执行评估',
    icon: 'lucide:database',
  },
  loop: {
    title: '暂无回路',
    description: '请先在回路管理中创建或同步控制回路',
    icon: 'lucide:git-branch',
  },
  task: {
    title: '暂无任务',
    description: '当前筛选条件下没有诊断任务',
    icon: 'lucide:clipboard-list',
  },
  custom: {
    title: '暂无数据',
    description: '',
    icon: 'lucide:inbox',
  },
};

const sceneConfig = computed<SceneConfig>(() => {
  const key = (
    ['tracker', 'data', 'loop', 'task', 'custom'] as SceneKey[]
  ).includes(props.scene as SceneKey)
    ? (props.scene as SceneKey)
    : 'custom';
  return presetScenes[key];
});

const title = computed(() => props.title || sceneConfig.value.title);
const description = computed(
  () => props.description || sceneConfig.value.description,
);

const imageStyle = computed(() => ({
  height: 'auto',
}));
</script>

<style scoped>
.clpm-empty-state {
  padding: 40px 0;
}

.clpm-empty-state__content {
  text-align: center;
}

.clpm-empty-state__title {
  margin-bottom: 4px;
  font-size: 14px;
  font-weight: 500;
  color: hsl(var(--foreground) / 85%);
}

.clpm-empty-state__desc {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.6;
  color: hsl(var(--foreground) / 45%);
}

.clpm-empty-state__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
</style>
