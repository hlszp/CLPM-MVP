/**
 * AI 洞察两级门禁 composable（IA 重构 Phase A·§5.2）
 *
 * 门禁 1（全局 LLM）：endpoint/apiKey/model 均非空 且 llm.enabled=true
 * 门禁 2（页面上下文）：场景需 loopId 时已选回路；无需上下文场景（workbench）恒通过
 *
 * 两级均通过 → 图标激活；否则灰显 + tooltip（Poka-Yoke：灰而不藏）。
 *
 * LLM 配置查询全应用共享缓存（模块级 ref），多页面复用同一次请求；
 * 配置变更后（LLM 配置页保存）可调 refresh() 强制刷新。
 *
 * 注：模块级 sharedState 是前端响应式共享标准模式，与 AGENTS.md 禁止的
 *    "模块级 asyncio.Lock"（后端事件循环绑定问题）无关。
 */
import { computed, ref } from 'vue';

import { getLlmConfigApi } from '#/api/llm';

interface LlmConfigState {
  enabled: boolean;
  /** endpoint+apiKey+model 均非空 */
  configured: boolean;
  loaded: boolean;
}

const sharedState = ref<LlmConfigState>({
  enabled: false,
  configured: false,
  loaded: false,
});

let loadPromise: Promise<void> | null = null;

async function loadLlmConfig(): Promise<void> {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    try {
      const cfg = await getLlmConfigApi();
      const configured = Boolean(
        cfg.endpoint && cfg.apiKeyConfigured && cfg.model,
      );
      sharedState.value = {
        enabled: cfg.enabled,
        configured,
        loaded: true,
      };
    } catch {
      sharedState.value = { enabled: false, configured: false, loaded: true };
    } finally {
      loadPromise = null;
    }
  })();
  return loadPromise;
}

export type AiGateStatus = 'active' | 'disabled-context' | 'disabled-llm';

export function useAiInsightGate() {
  /** 触发加载（在 onMounted 调用；已加载则空操作） */
  function init(): void {
    if (!sharedState.value.loaded) void loadLlmConfig();
  }

  /** 强制刷新（LLM 配置页修改后调用） */
  function refresh(): Promise<void> {
    sharedState.value.loaded = false;
    return loadLlmConfig();
  }

  /** 门禁 1 是否通过 */
  const llmReady = computed(
    () => sharedState.value.enabled && sharedState.value.configured,
  );

  /**
   * 计算门禁状态
   * @param loopId 当前页面选中的回路（场景需 loopId 时传入；null 表示未选）
   * @param requiresLoop 该场景是否需要 loopId 上下文
   */
  function gateStatus(
    loopId: null | string,
    requiresLoop: boolean,
  ): AiGateStatus {
    if (!llmReady.value) return 'disabled-llm';
    if (requiresLoop && !loopId) return 'disabled-context';
    return 'active';
  }

  /** tooltip 文案（§5.2.1） */
  function gateTooltip(status: AiGateStatus): string {
    switch (status) {
      case 'disabled-llm': {
        return '请先在系统管理配置并启用 LLM';
      }
      case 'disabled-context': {
        return '请先选择回路';
      }
      default: {
        return '生成 AI 洞察';
      }
    }
  }

  return {
    gateStatus,
    gateTooltip,
    init,
    llmReady,
    refresh,
    state: sharedState,
  };
}
