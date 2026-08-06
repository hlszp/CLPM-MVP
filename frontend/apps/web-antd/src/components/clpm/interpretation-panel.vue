<script lang="ts" setup>
/**
 * ClpmInterpretationPanel — 自然语言诊断解读面板（P3-04）
 *
 * 将结构化诊断结果翻译为工程师可读的大白话解读，辅助非算法背景用户理解
 * "这个振荡是什么意思、严不严重、该怎么处理"。
 *
 * 混合方案：
 * - 规则模板（默认/离线可用）：基于结构化报告常量拼装【概述】【主因分析】【风险提示】三段
 * - LLM API（增强/可选）：调用 OpenAI 兼容接口生成更自然解读，不可用/失败自动 fallback
 *
 * 自包含：组件内部调用 interpretDiagnosisApi，父组件只需传入 loopId。
 *
 * 设计依据：PRD v6.1, 实现契约 v2.4, IA 整改任务清单 P3-04
 * 对齐 ZL 工业设计规范：Calm UI（低饱和）+ Poka-Yoke（失败有 fallback 不阻断）
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Button, Dropdown, Empty, Spin, Tag, Tooltip } from 'ant-design-vue';

import { interpretDiagnosisApi } from '#/api/diagnosis';
import { getLlmConfigApi } from '#/api/llm';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'ClpmInterpretationPanel' });

interface Props {
  /** 回路 ID */
  loopId: string;
  /** 是否默认展开（首次进入自动生成），默认 false 由用户主动触发 */
  autoLoad?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  autoLoad: false,
});

const emit = defineEmits<{
  (e: 'generated', payload: DiagnosisApi.InterpretResult): void;
}>();

const loading = ref(false);
const error = ref(false);
const errorMsg = ref('');
const result = ref<DiagnosisApi.InterpretResult | null>(null);
/** LLM 是否已启用（控制"仅 AI 解读"选项可见性） */
const llmEnabled = ref(false);

/** 是否已生成过（区分"未生成空状态"与"生成中/已生成"） */
const hasResult = computed(() => result.value !== null);

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

/** 生成解读 */
async function generate(mode: DiagnosisApi.InterpretMode = 'auto') {
  loading.value = true;
  error.value = false;
  errorMsg.value = '';
  try {
    const data = await interpretDiagnosisApi(props.loopId, { mode });
    result.value = data;
    emit('generated', data);
  } catch (e: unknown) {
    error.value = true;
    errorMsg.value = e instanceof Error ? e.message : '解读生成失败';
    result.value = null;
  } finally {
    loading.value = false;
  }
}

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

function handleMenuClick({ key }: { key: number | string }) {
  generate(String(key) as DiagnosisApi.InterpretMode);
}

/** 查询 LLM 启用状态（控制"仅 AI 解读"选项可见性） */
async function loadLlmStatus() {
  try {
    const config = await getLlmConfigApi();
    llmEnabled.value = config.enabled;
  } catch {
    // 查询失败默认不启用，不影响模板解读
    llmEnabled.value = false;
  }
}

/** 首次自动加载 */
onMounted(() => {
  loadLlmStatus();
  if (props.autoLoad) {
    generate('auto');
  }
});
</script>

<template>
  <div class="clpm-interpretation-panel">
    <!-- 头部：标题 + 操作 -->
    <div class="clpm-interpretation-panel__header">
      <div class="flex items-center gap-1.5">
        <IconifyIcon icon="ant-design:bulb-outlined" :size="15" />
        <span class="text-sm font-medium">AI 洞察</span>
        <Tooltip
          title="将诊断结果翻译为工程师可读的自然语言，含主因分析、处置建议与风险提示。AI 解读需在系统配置中开启 LLM 服务。"
        >
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
        class="clpm-interpretation-panel__empty"
      >
        <Empty
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
          description="点击生成诊断解读"
        >
          <Button type="primary" size="small" @click="generate('auto')">
            <IconifyIcon icon="ant-design:bulb-outlined" :size="13" />
            生成解读
          </Button>
        </Empty>
      </div>

      <!-- 错误状态 -->
      <div
        v-else-if="error && !loading"
        class="clpm-interpretation-panel__error"
      >
        <IconifyIcon icon="ant-design:warning-outlined" :size="28" />
        <div class="mt-2 text-sm">{{ errorMsg }}</div>
        <Button type="link" size="small" @click="generate('auto')">
          重试
        </Button>
      </div>

      <!-- 解读结果 -->
      <div v-else-if="result" class="clpm-interpretation-panel__body">
        <!-- 元信息标签栏 -->
        <div class="clpm-interpretation-panel__meta">
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

        <!-- 解读正文（结构化纯文本，保留换行） -->
        <div
          class="clpm-interpretation-panel__text"
          style="white-space: pre-wrap"
        >
          {{ result.interpretation }}
        </div>
      </div>
    </Spin>
  </div>
</template>

<style scoped>
.clpm-interpretation-panel {
  padding: 12px;
  background: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.clpm-interpretation-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  color: hsl(var(--foreground));
}

.clpm-interpretation-panel__empty,
.clpm-interpretation-panel__error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 16px 8px;
  color: hsl(var(--muted-foreground));
}

.clpm-interpretation-panel__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 8px;
}

.clpm-interpretation-panel__text {
  font-size: 13px;
  line-height: 1.7;
  color: hsl(var(--foreground));
  overflow-wrap: break-word;
}
</style>
