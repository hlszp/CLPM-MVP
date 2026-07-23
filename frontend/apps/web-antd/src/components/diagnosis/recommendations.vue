<script lang="ts" setup>
/**
 * FE-13 诊断解决方案推荐组件
 *
 * 展示优先级排序的建议列表，每条建议包含：
 * - 优先级标签（高/中/低，颜色区分）
 * - 标签 + 行动项
 * - 详细描述
 * - 目标模块（整定/跟踪/none）
 *
 * 用于诊断详情页和回路详情页的"智能诊断"Tab。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

import { Button, Card, Empty, Spin, Tag } from 'ant-design-vue';

defineOptions({ name: 'DiagnosisRecommendations' });

const props = defineProps<{
  /** 是否显示"采纳并创建跟踪"按钮（F6） */
  adoptable?: boolean;
  /** 是否显示卡片边框（嵌入 Tab 时可关闭） */
  bordered?: boolean;
  /** 加载中 */
  loading?: boolean;
  /** 推荐数据 */
  recommendations?: DiagnosisApi.RecommendationItem[];
}>();

const emit = defineEmits<{
  /** F6：采纳建议 → 创建/跳转跟踪 */
  (e: 'adopt', rec: DiagnosisApi.RecommendationItem): void;
}>();

/** 优先级配置 */
const priorityConfig: Record<number, { color: string; label: string }> = {
  1: { color: 'red', label: '高' },
  2: { color: 'orange', label: '中' },
  3: { color: 'blue', label: '低' },
};

/** 默认优先级配置（兜底，避免 noUncheckedIndexedAccess 报错） */
const defaultPriorityConfig = { color: 'default', label: '低' };

/** 安全获取优先级配置 */
function getPriorityConfig(priority: number) {
  return priorityConfig[priority] ?? defaultPriorityConfig;
}

/** 目标模块颜色映射 */
const moduleColorMap: Record<string, string> = {
  整定: 'purple',
  跟踪: 'cyan',
  none: 'default',
};

/** 按优先级分组 */
const groupedRecommendations = computed(() => {
  const recs = props.recommendations ?? [];
  const groups: Record<number, DiagnosisApi.RecommendationItem[]> = {
    1: [],
    2: [],
    3: [],
  };
  for (const rec of recs) {
    const p = rec.priority ?? 3;
    if (!groups[p]) groups[p] = [];
    groups[p].push(rec);
  }
  return groups;
});

const totalCount = computed(() => props.recommendations?.length ?? 0);
</script>

<template>
  <Card
    :bordered="bordered !== false"
    :body-style="{ padding: bordered === false ? '0' : undefined }"
    title="解决方案推荐"
  >
    <template #extra>
      <span class="text-sm text-gray-500"> 共 {{ totalCount }} 条建议 </span>
    </template>

    <Spin :spinning="loading">
      <div v-if="totalCount > 0" class="space-y-4">
        <div v-for="priority in [1, 2, 3]" :key="priority">
          <div
            v-if="groupedRecommendations[priority]?.length"
            class="mb-2 flex items-center gap-2"
          >
            <Tag :color="getPriorityConfig(priority).color">
              {{ getPriorityConfig(priority).label }}优先级
            </Tag>
            <span class="text-xs text-gray-400">
              {{ groupedRecommendations[priority].length }} 条建议
            </span>
          </div>
          <div class="space-y-2">
            <div
              v-for="(rec, idx) in groupedRecommendations[priority]"
              :key="`${priority}-${idx}`"
              class="rounded border border-gray-200 bg-gray-50 p-3 transition hover:border-blue-300 hover:bg-blue-50"
            >
              <div class="mb-2 flex flex-wrap items-center gap-2">
                <span class="font-medium text-gray-800">
                  {{ rec.action }}
                </span>
                <Tag size="small" :color="getPriorityConfig(priority).color">
                  P{{ rec.priority }}
                </Tag>
                <Tag size="small" color="blue">
                  {{ rec.labelName || rec.label }}
                </Tag>
                <Tag
                  v-if="rec.targetModule && rec.targetModule !== 'none'"
                  size="small"
                  :color="moduleColorMap[rec.targetModule] || 'default'"
                >
                  {{ rec.targetModule }}
                </Tag>
              </div>
              <div class="text-sm leading-relaxed text-gray-600">
                {{ rec.description }}
              </div>
              <div v-if="adoptable" class="mt-2 flex justify-end">
                <Button type="link" size="small" @click="emit('adopt', rec)">
                  采纳并创建跟踪
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <Empty v-else description="暂无解决方案推荐" />
    </Spin>
  </Card>
</template>
