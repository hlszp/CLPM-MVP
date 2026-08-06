/**
 * AI 洞察通用 API（4 场景统一入口）
 *
 * 单端点 POST /ai-insight/{scene} 服务 4 场景：
 * - diagnosis：回路诊断解读
 * - performance：性能评估分析
 * - tuning：回路整定建议
 * - workbench：工作台运维洞察
 *
 * 前端只传 scene + 可选 loopId/taskId + mode，后端按 scene 自取上下文。
 * LLM 未启用或失败时自动 fallback 规则模板，功能不阻断。
 */

import { requestClient } from '#/api/request';

export namespace AiInsightApi {
  /** 生成模式 */
  export type InsightMode = 'auto' | 'llm' | 'template';

  /** 场景标识 */
  export type SceneId = 'diagnosis' | 'performance' | 'tuning' | 'workbench';

  /** 生成请求体 */
  export interface InsightParams {
    mode?: InsightMode;
    loopId?: null | string;
    taskId?: null | string;
  }

  /** 生成响应 */
  export interface InsightResult {
    /** 洞察文本（结构化纯文本） */
    insight: string;
    /** 实际来源：template（规则模板）/ llm（LLM 生成） */
    source: 'llm' | 'template';
    /** LLM 模型名（source=llm 时有值） */
    model?: null | string;
    /** 场景标识 */
    scene: string;
    /** 生成时间 ISO 8601 */
    generatedAt: string;
  }

  /** 场景元信息 */
  export interface SceneInfo {
    sceneId: SceneId;
    sceneName: string;
    requiredParams: string;
  }
}

const BASE = '/ai-insight';

/**
 * 生成 AI 洞察（4 场景统一入口）
 *
 * 权限：ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT（SPONSOR 只读禁止）。
 */
export function generateAiInsightApi(
  scene: AiInsightApi.SceneId | string,
  data?: AiInsightApi.InsightParams,
) {
  return requestClient.post<AiInsightApi.InsightResult>(
    `${BASE}/${scene}`,
    data ?? {},
  );
}

/**
 * 列出可用的 AI 洞察场景（供前端动态渲染按钮/卡片）。
 */
export function listAiInsightScenesApi() {
  return requestClient.get<AiInsightApi.SceneInfo[]>(`${BASE}/scenes`);
}
