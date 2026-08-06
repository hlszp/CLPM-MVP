/**
 * LLM 配置 API（P3-04 自然语言诊断解读配套）
 *
 * 让管理员在系统管理中自助配置 LLM 服务的 BaseURL / API Key / 模型 / 超时，
 * 而非代码写死。遵循 OpenAI 兼容接口协议，任何兼容服务均可接入。
 *
 * 配置存储在 sys_config 表（6 个 key），GET 返回时 API Key 脱敏。
 */

import { requestClient } from '#/api/request';

export namespace LlmApi {
  /** LLM 配置响应（GET，API Key 脱敏） */
  export interface LlmConfig {
    /** 是否启用 LLM 解读 */
    enabled: boolean;
    /** BaseURL（API 根地址，不含 /v1） */
    endpoint?: null | string;
    /** API Key（脱敏，形如 sk-***xxxx；未配置时为 null） */
    apiKey?: null | string;
    /** API Key 是否已配置（前端据此区分空值与未配置，决定是否要求重填） */
    apiKeyConfigured: boolean;
    /** 模型名 */
    model?: null | string;
    /** 超时秒数 */
    timeout: number;
    /** 最大输出 token 数（推理模型建议 ≥4096） */
    maxTokens: number;
    /** 最近更新时间 ISO 8601 */
    updatedAt?: null | string;
    /** 最近更新人 */
    updatedBy?: null | string;
  }

  /** LLM 配置保存请求（POST） */
  export interface LlmConfigSaveParams {
    enabled: boolean;
    endpoint?: null | string;
    /** API Key（空=保留原值，非空=更新） */
    apiKey?: null | string;
    model?: null | string;
    timeout: number;
    /** 最大输出 token 数 */
    maxTokens: number;
  }

  /** LLM 连接测试结果 */
  export interface LlmTestResult {
    success: boolean;
    latencyMs?: null | number;
    model?: null | string;
    message: string;
  }
}

const LLM_BASE = '/configs/llm';

/**
 * 获取当前 LLM 配置（API Key 脱敏返回）
 *
 * 权限：ADMIN/IC_ENGINEER/PE_ENGINEER 可查看。
 */
export function getLlmConfigApi() {
  return requestClient.get<LlmApi.LlmConfig>(LLM_BASE);
}

/**
 * 更新 LLM 配置（仅 ADMIN）
 *
 * apiKey 为空时保留原值（前端未改 key 场景），非空时更新。
 */
export function saveLlmConfigApi(data: LlmApi.LlmConfigSaveParams) {
  return requestClient.post<LlmApi.LlmConfig>(LLM_BASE, data);
}

/**
 * 连接测试（仅 ADMIN）
 *
 * 向已配置的 LLM 服务发一条 ping 请求，返回成功/失败 + 延迟。
 */
export function testLlmConnectionApi() {
  return requestClient.post<LlmApi.LlmTestResult>(`${LLM_BASE}/test`, {});
}
