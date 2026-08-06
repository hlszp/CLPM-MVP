<script lang="ts" setup>
/**
 * ClpmAiInsight — AI 洞察通用组件（4 场景统一入口）
 *
 * 将结构化业务数据翻译为工程师可读的自然语言洞察，覆盖 4 场景：
 * - diagnosis：回路诊断解读（主因分析 / 处置建议 / 风险提示）
 * - performance：性能评估分析（等级判定 / 短板分析 / 改善建议）
 * - tuning：回路整定建议（模型质量 / 参数解读 / 仿真改善 / 实施风险）
 * - workbench：工作台运维洞察（健康概览 / 待办优先级 / 趋势预警）
 *
 * 混合方案：
 * - 规则模板（诊断小结）：基于结构化数据拼装，LLM 失败时自动 fallback
 * - LLM API（AI 洞察）：调用 OpenAI 兼容接口生成更自然解读
 *
 * 自包含：组件内部调用 generateAiInsightApi，父组件只需传入 scene + 可选 loopId/taskId。
 *
 * LLM 门禁：onMounted 查询 getLlmConfigApi().enabled，
 *   - hideWhenDisabled=true（默认）：未启用时整体不渲染（适合卡片场景，保持 Calm UI）
 *   - hideWhenDisabled=false：未启用时渲染"需启用 LLM"提示（适合 Tab 场景，避免空白 Tab）
 *
 * 设计依据：PRD v6.1, 实现契约 v2.5, IA 整改任务清单 P3-04
 * 对齐 ZL 工业设计规范：Calm UI（低饱和）+ Poka-Yoke（失败有 fallback 不阻断）
 */
import type { AiInsightApi } from '#/api/ai-insight';

import { computed, onMounted, ref } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Button, Dropdown, Empty, Spin, Tag, Tooltip } from 'ant-design-vue';
import { useRouter } from 'vue-router';

import { generateAiInsightApi } from '#/api/ai-insight';
import { getLlmConfigApi } from '#/api/llm';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'ClpmAiInsight' });

interface Props {
  /** 场景标识：diagnosis / performance / tuning / workbench */
  scene: AiInsightApi.SceneId;
  /** 回路 ID（diagnosis/performance/tuning 场景需要） */
  loopId?: null | string;
  /** 整定任务 ID（tuning 场景需要） */
  taskId?: null | string;
  /** 展示形态：card（带边框卡片，默认）/ tab（无外框，嵌入 Tab 面板） */
  variant?: 'card' | 'tab';
  /** 自定义标题（默认按 scene 取 SCENE_META.title） */
  title?: string;
  /** 首次进入是否自动生成，默认 false 由用户主动触发（控制 LLM 成本） */
  autoLoad?: boolean;
  /** LLM 未启用时是否整体隐藏：true=隐藏（默认），false=显示"需启用"提示 */
  hideWhenDisabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loopId: null,
  taskId: null,
  variant: 'card',
  title: '',
  autoLoad: false,
  hideWhenDisabled: true,
});

const emit = defineEmits<{
  (e: 'generated', payload: AiInsightApi.InsightResult): void;
}>();

const router = useRouter();

/** 场景元信息（标题 / 图标 / 提示 / 空状态文案） */
const SCENE_META: Record<
  AiInsightApi.SceneId,
  { description: string; icon: string; title: string; tooltip: string }
> = {
  diagnosis: {
    title: 'AI 洞察',
    icon: 'ant-design:bulb-outlined',
    tooltip:
      '将诊断结果翻译为工程师可读的自然语言，含主因分析、处置建议与风险提示。',
    description: '点击生成诊断解读',
  },
  performance: {
    title: 'AI 性能分析',
    icon: 'ant-design:bar-chart-outlined',
    tooltip: '基于回路 KPI 指标与可信度，分析性能等级、短板项与改善优先级。',
    description: '点击生成性能分析',
  },
  tuning: {
    title: 'AI 整定建议',
    icon: 'ant-design:tool-outlined',
    tooltip: '解读过程模型辨识结果与推荐 PID，给出仿真改善分析与实施风险提示。',
    description: '点击生成整定建议',
  },
  workbench: {
    title: 'AI 运维洞察',
    icon: 'ant-design:dashboard-outlined',
    tooltip:
      '从运维管理视角汇总全局健康度、Top 待办优先级与趋势预警，给出重点关注建议。',
    description: '点击生成运维洞察',
  },
};

/** 来源标签配置 */
const SOURCE_TAG: Record<
  'llm' | 'template',
  { color: string; icon: string; label: string }
> = {
  llm: {
    color: 'geekblue',
    icon: 'ant-design:robot-outlined',
    label: 'AI 洞察',
  },
  template: {
    color: 'default',
    icon: 'ant-design:file-text-outlined',
    label: '诊断小结',
  },
};

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const configLoading = ref(true);
const llmEnabled = ref(false);
const loading = ref(false);
const error = ref(false);
const errorMsg = ref('');
const result = ref<AiInsightApi.InsightResult | null>(null);

const meta = computed(() => SCENE_META[props.scene] ?? SCENE_META.diagnosis);
const displayTitle = computed(() => props.title || meta.value.title);
const hasResult = computed(() => result.value !== null);

/** 重新生成下拉菜单（LLM 未启用时隐藏"仅 AI 洞察"） */
const regenMenuItems = computed(() => {
  const items = [
    { key: 'auto', label: '智能模式（优先 AI）' },
    { key: 'template', label: '仅诊断小结' },
  ];
  if (llmEnabled.value) {
    items.push({ key: 'llm', label: '仅 AI 洞察' });
  }
  return items;
});

// ---------------------------------------------------------------------------
// 生成洞察
// ---------------------------------------------------------------------------

async function generate(mode: AiInsightApi.InsightMode = 'auto') {
  loading.value = true;
  error.value = false;
  errorMsg.value = '';
  try {
    const data = await generateAiInsightApi(props.scene, {
      mode,
      loopId: props.loopId,
      taskId: props.taskId,
    });
    result.value = data;
    emit('generated', data);
  } catch (e: unknown) {
    error.value = true;
    errorMsg.value = e instanceof Error ? e.message : '洞察生成失败';
    result.value = null;
  } finally {
    loading.value = false;
  }
}

function handleMenuClick({ key }: { key: number | string }) {
  generate(String(key) as AiInsightApi.InsightMode);
}

/** 查询 LLM 启用状态 */
async function loadLlmStatus() {
  configLoading.value = true;
  try {
    const config = await getLlmConfigApi();
    llmEnabled.value = config.enabled;
  } catch {
    llmEnabled.value = false;
  } finally {
    configLoading.value = false;
  }
}

/** 跳转 LLM 配置页 */
function goLlmConfig() {
  router.push('/system/llm-config');
}

onMounted(() => {
  loadLlmStatus().then(() => {
    if (llmEnabled.value && props.autoLoad) {
      generate('auto');
    }
  });
});
</script>

<template>
  <!-- 配置加载中：不渲染（避免闪烁） -->
  <template v-if="!configLoading">
    <!-- LLM 未启用 -->
    <div
      v-if="!llmEnabled && !hideWhenDisabled"
      class="clpm-ai-insight clpm-ai-insight--disabled"
      :class="{ 'clpm-ai-insight--tab': variant === 'tab' }"
    >
      <div class="clpm-ai-insight__header">
        <div class="flex items-center gap-1.5">
          <IconifyIcon :icon="meta.icon" :size="15" />
          <span class="text-sm font-medium">{{ displayTitle }}</span>
        </div>
      </div>
      <div class="clpm-ai-insight__hint">
        <IconifyIcon icon="ant-design:lock-outlined" :size="22" />
        <div class="mt-2 text-sm">
          AI 洞察需在
          <Button type="link" size="small" class="px-1" @click="goLlmConfig">
            系统管理 — LLM 配置
          </Button>
          中启用 LLM 服务后可用
        </div>
      </div>
    </div>

    <!-- LLM 已启用：正常渲染 -->
    <div
      v-else-if="llmEnabled"
      class="clpm-ai-insight"
      :class="{ 'clpm-ai-insight--tab': variant === 'tab' }"
    >
      <!-- 头部：标题 + 操作 -->
      <div class="clpm-ai-insight__header">
        <div class="flex items-center gap-1.5">
          <IconifyIcon :icon="meta.icon" :size="15" />
          <span class="text-sm font-medium">{{ displayTitle }}</span>
          <Tooltip :title="meta.tooltip">
            <IconifyIcon
              icon="ant-design:question-circle-outlined"
              :size="13"
              class="cursor-help opacity-50"
            />
          </Tooltip>
        </div>

        <!-- 生成/重新生成按钮 -->
        <Dropdown
          v-if="hasResult && !loading"
          trigger="click"
          :menu="{ items: regenMenuItems, onClick: handleMenuClick }"
        >
          <Button type="text" size="small" @click.stop>
            <IconifyIcon icon="ant-design:reload-outlined" :size="13" />
            重新生成
          </Button>
        </Dropdown>
      </div>

      <!-- 内容区 -->
      <Spin :spinning="loading">
        <!-- 未生成空状态 -->
        <div
          v-if="!result && !loading && !error"
          class="clpm-ai-insight__empty"
        >
          <Empty
            :image="Empty.PRESENTED_IMAGE_SIMPLE"
            :description="meta.description"
          >
            <Button type="primary" size="small" @click="generate('auto')">
              <IconifyIcon :icon="meta.icon" :size="13" />
              生成洞察
            </Button>
          </Empty>
        </div>

        <!-- 错误状态 -->
        <div v-else-if="error && !loading" class="clpm-ai-insight__error">
          <IconifyIcon icon="ant-design:warning-outlined" :size="28" />
          <div class="mt-2 text-sm">{{ errorMsg }}</div>
          <Button type="link" size="small" @click="generate('auto')">
            重试
          </Button>
        </div>

        <!-- 洞察结果 -->
        <div v-else-if="result" class="clpm-ai-insight__body">
          <!-- 元信息标签栏 -->
          <div class="clpm-ai-insight__meta">
            <Tag
              :color="SOURCE_TAG[result.source].color"
              class="m-0"
              style="font-size: 11px; line-height: 18px"
            >
              <IconifyIcon
                :icon="SOURCE_TAG[result.source].icon"
                :size="11"
                class="mr-0.5"
              />
              {{ SOURCE_TAG[result.source].label }}
            </Tag>
            <Tag
              v-if="result.model"
              class="m-0"
              style="font-size: 11px; line-height: 18px"
            >
              {{ result.model }}
            </Tag>
            <span class="text-xs opacity-50">{{
              formatTime(result.generatedAt)
            }}</span>
          </div>

          <!-- 洞察正文（结构化纯文本，保留换行） -->
          <div class="clpm-ai-insight__text" style="white-space: pre-wrap">
            {{ result.insight }}
          </div>
        </div>
      </Spin>
    </div>
  </template>
</template>

<style scoped>
.clpm-ai-insight {
  padding: 12px;
  background: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

/* tab 形态：无外框，嵌入 Tab 面板不重复边框 */
.clpm-ai-insight--tab {
  padding: 0;
  background: transparent;
  border: none;
  border-radius: 0;
}

.clpm-ai-insight--disabled .clpm-ai-insight__hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 20px 8px;
  color: hsl(var(--muted-foreground));
}

.clpm-ai-insight__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  color: hsl(var(--foreground));
}

.clpm-ai-insight__empty,
.clpm-ai-insight__error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 16px 8px;
  color: hsl(var(--muted-foreground));
}

.clpm-ai-insight__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 8px;
}

.clpm-ai-insight__text {
  font-size: 13px;
  line-height: 1.7;
  color: hsl(var(--foreground));
  overflow-wrap: break-word;
}
</style>
