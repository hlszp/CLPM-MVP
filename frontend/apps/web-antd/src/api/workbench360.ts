/**
 * 回路工作台（新版 workbench360）API 封装
 *
 * 对齐《回路工作台-API对接契约-2026-10-02.md》：
 * - 组件内禁止直连 URL，一律经本文件（契约 §3.1）；
 * - P1 仅接趋势所需端点（waveform 自定义起止，用于 12H/7D 等无 trendWindow
 *   预设的档位）；旅程条/剖面数据端点在 P2-P4 按矩阵扩展。
 */
import { requestClient } from '#/api/request';

export namespace Workbench360Api {
  /** 波形数据点（后端 WaveformPoint，camelCase） */
  export interface WaveformPoint {
    /** ISO 8601 时间戳 */
    timestamp: string;
    pv?: null | number;
    sp?: null | number;
    op?: null | number;
    /** 控制模式原始值（数值 → 标签经 modeMapping 解析） */
    mode?: null | number;
    /** PV 质量码（1=Good, 0=Bad；后端二值口径） */
    pvQuality?: null | number;
    /** valid_mask 标记（false=无效/异常） */
    valid: boolean;
    /** 异常原因码（FROZEN/JUMP/SPIKE/OUT_OF_RANGE 等，逗号分隔） */
    outlierReason?: null | string;
  }

  /** 波形响应（后端 WaveformResponse） */
  export interface WaveformResponse {
    loopId: string;
    tagName?: null | string;
    timeRange: { endTime: string; startTime: string };
    points: WaveformPoint[];
    samplingFreq: string;
    qualityPolicy: string;
    validRate: number;
    downsampled: boolean;
    pointCount: number;
  }
}

/**
 * 波形数据（LTTB ≤2000 点；时间窗 ≤30 天）。
 *
 * 供趋势窗口无 trendWindow 预设的档位（12H/7D/自定义）使用；
 * 有预设的档位优先走 getLoopMonitorDetailApi（同契约 §1.2）。
 */
export function getWaveformApi(
  loopId: string,
  startTime: string,
  endTime: string,
  maxPoints = 2000,
) {
  return requestClient.get<Workbench360Api.WaveformResponse>(
    `/timeseries/${loopId}/waveform`,
    {
      params: { endTime, maxPoints, startTime },
      // 大窗口趋势扫描 TDengine 可超过默认 10s
      timeout: 30_000,
    },
  );
}
