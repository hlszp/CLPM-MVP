import type { TrendFrame } from './types';

import type { LoopApi } from '#/api/loop';

/**
 * 趋势窗口取数与实时追加（workbench360，API 契约 §1.2）
 *
 * 2026-10-09 用户裁决"复用同一套方法，不要另起一套"：全部档位统一走
 * GET /loops/{id}/monitor（trend.timestamps 毫秒）——
 * - 预设档（1H~7D）走 trendWindow 预设（后端 TREND_WINDOWS 已补
 *   last_12_hours / last_7_days）；
 * - 自定义档走同端点 tsStart/tsEnd 起止（上限 30 天，后端校验）；
 * - 原 waveform 链路（/timeseries/{id}/waveform，另一套取数+降采样口径，
 *   且 LTTB 存在时区偏移缺陷）已从本页移除。
 * - 数据域 XDOMAIN = 返回序列的实际 [首 ts, 尾 ts]（非请求窗，诚实呈现
 *   后端实际覆盖）。
 *
 * 实时：WS 推送经页面层 onRealtimePoint 转发，appendRealtimePoint 按采样
 * 间隔分桶合并（同一桶内 PV/SP/OP/MODE 更新同一帧，跨桶新起帧），数据
 * 驱动窗口右推。
 */
import { computed, ref, shallowRef } from 'vue';

import { getLoopMonitorDetailApi } from '#/api/loop';
import {
  WB360_SAMPLE_POINTS,
  type WB360WindowPreset,
} from '#/constants/clpm-ui';

import { computeYDomain } from './resample';

/** 实时追加的桶间隔下限（ms）：小于该间隔并入最后一帧 */
const REALTIME_MERGE_MS = 900;

/** 自定义起止范围（ISO 8601，UTC） */
export interface CustomRange {
  tsEnd: string;
  tsStart: string;
}

export function useTrendData() {
  const frames = shallowRef<TrendFrame[]>([]);
  const domain = ref<null | { t0: number; t1: number }>(null);
  const loading = ref(false);
  const error = ref<null | string>(null);
  /** 当前窗口档位 key */
  const windowKey = ref<string>('');
  /** 实时模式标志（右推由数据驱动；自定义窗口为历史模式） */
  const live = ref(false);
  /** 趋势实际来源（monitor 预设 / monitor 自定义起止） */
  const source = ref<'' | 'custom' | 'monitor'>('');
  /** 后端 LTTB 降采样提示（诚实化：点数少于窗口应有密度时告知） */
  const downsampled = ref(false);
  const pointCount = ref(0);

  /** 请求代次（回路/窗口切换竞态守卫） */
  let generation = 0;

  const yDomain = computed(() => computeYDomain(frames.value));

  async function loadWindow(
    loopId: string,
    preset: WB360WindowPreset,
    opts: { customRange?: CustomRange } = {},
  ): Promise<void> {
    const gen = ++generation;
    loading.value = true;
    error.value = null;
    frames.value = [];
    domain.value = null;
    downsampled.value = false;
    pointCount.value = 0;
    windowKey.value = preset.key;
    try {
      source.value = opts.customRange ? 'custom' : 'monitor';
      // 自定义起止时后端优先消费 tsStart/tsEnd（trendWindow 仍须为合法值）
      const detail: LoopApi.MonitorDetail = await getLoopMonitorDetailApi(
        loopId,
        preset.trendWindow ?? 'last_24_hours',
        opts.customRange,
      );
      if (gen !== generation) return;
      const t = detail.trend;
      const loaded: TrendFrame[] = t.timestamps.map((ts, i) => ({
        mode: t.mode[i] ?? null,
        op: t.op[i] ?? null,
        pv: t.pv[i] ?? null,
        quality: normalizeQuality(t.pvQuality[i] ?? null),
        sp: t.sp[i] ?? null,
        ts,
      }));
      downsampled.value = t.downsampled ?? false;
      pointCount.value = t.pointCount ?? loaded.length;
      // 升序 + 相同 ts 去重（保后者）
      loaded.sort((a, b) => a.ts - b.ts);
      const dedup: TrendFrame[] = [];
      for (const f of loaded) {
        if (dedup.at(-1)?.ts === f.ts) dedup[dedup.length - 1] = f;
        else dedup.push(f);
      }
      frames.value = dedup;
      domain.value =
        dedup.length > 0 ? { t0: dedup[0]!.ts, t1: dedup.at(-1)!.ts } : null;
      if (dedup.length === 0) {
        error.value = '该窗口暂无数据（本地库不完整时请先在数据管理导入历史）';
      }
    } catch (error_) {
      if (gen !== generation) return;
      error.value =
        error_ instanceof Error ? error_.message : '趋势数据加载失败';
      frames.value = [];
      domain.value = null;
    } finally {
      if (gen === generation) loading.value = false;
    }
  }

  /**
   * 实时追加点（WS 推送，页面层转发）。
   * 与尾帧间隔 ≥ REALTIME_MERGE_MS 时新起帧（其余字段 null），否则并入尾帧。
   * 追加后 XDOMAIN 尾部右推（保持窗口跨度：头部同样前移）。
   */
  function appendRealtimePoint(
    ts: number,
    role: string,
    value: null | number,
    quality?: null | string,
  ) {
    if (!live.value) return;
    const list = frames.value;
    const last = list.at(-1);
    if (!last) return;
    if (ts < last.ts - REALTIME_MERGE_MS) return;
    if (ts - last.ts >= REALTIME_MERGE_MS) {
      const fresh: TrendFrame = {
        mode: null,
        op: null,
        pv: null,
        quality: null,
        sp: null,
        ts,
      };
      applyRole(fresh, role, value, quality);
      list.push(fresh);
    } else {
      const merged: TrendFrame = { ...last, ts: Math.max(last.ts, ts) };
      applyRole(merged, role, value, quality);
      list[list.length - 1] = merged;
    }
    const span = domain.value ? domain.value.t1 - domain.value.t0 : 0;
    const t1 = list.at(-1)!.ts;
    domain.value = span > 0 ? { t0: t1 - span, t1 } : null;
  }

  return {
    appendRealtimePoint,
    domain,
    downsampled,
    error,
    frames,
    live,
    loading,
    loadWindow,
    pointCount,
    samplePoints: WB360_SAMPLE_POINTS,
    source,
    windowKey,
    yDomain,
  };
}

function applyRole(
  frame: TrendFrame,
  role: string,
  value: null | number,
  quality?: null | string,
) {
  switch (role) {
    case 'MODE': {
      frame.mode = value;
      break;
    }
    case 'OP': {
      frame.op = value;
      break;
    }
    case 'PV': {
      frame.pv = value;
      if (quality === 'BAD' || quality === 'GOOD' || quality === 'UNCERTAIN') {
        frame.quality = quality;
      }
      break;
    }
    case 'SP': {
      frame.sp = value;
      break;
    }
    default: {
      break;
    }
  }
}

/** monitor 字符串质量码直通（三态：GOOD/BAD/UNCERTAIN） */
function normalizeQuality(
  q: unknown,
): 'BAD' | 'GOOD' | 'UNCERTAIN' | null {
  return q === 'BAD' || q === 'GOOD' || q === 'UNCERTAIN' ? q : null;
}
