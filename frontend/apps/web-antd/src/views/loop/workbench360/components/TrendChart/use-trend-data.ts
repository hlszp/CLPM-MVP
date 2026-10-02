import type { TrendFrame } from './types';

import type { LoopApi } from '#/api/loop';

/**
 * 趋势窗口取数与实时追加（workbench360 P1，API 契约 §1.2）
 *
 * 历史窗口：
 * - 有 trendWindow 预设的档位（1H/2H/4H/8H/24H/3D）走
 *   GET /loops/{id}/monitor（trend.timestamps 毫秒）；
 * - 12H/7D（后端无预设）走 GET /timeseries/{id}/waveform 自定义起止（≤2000 点）；
 * - 数据域 XDOMAIN = 返回序列的实际 [首 ts, 尾 ts]（非请求窗，诚实呈现后端实际覆盖）。
 *
 * 实时：WS 推送经页面层 onRealtimePoint 转发，appendRealtimePoint 按采样间隔
 * 分桶合并（同一桶内 PV/SP/OP/MODE 更新同一帧，跨桶新起帧），数据驱动窗口右推。
 */
import { computed, ref, shallowRef } from 'vue';

import { getLoopMonitorDetailApi } from '#/api/loop';
import { getWaveformApi } from '#/api/workbench360';
import {
  WB360_SAMPLE_POINTS,
  type WB360WindowPreset,
} from '#/constants/clpm-ui';

import { computeYDomain } from './resample';

/** 实时追加的桶间隔下限（ms）：小于该间隔并入最后一帧 */
const REALTIME_MERGE_MS = 900;

export function useTrendData() {
  const frames = shallowRef<TrendFrame[]>([]);
  const domain = ref<null | { t0: number; t1: number }>(null);
  const loading = ref(false);
  const error = ref<null | string>(null);
  /** 当前窗口档位 key */
  const windowKey = ref<string>('');
  /** 实时模式标志（右推由数据驱动） */
  const live = ref(false);
  /** 趋势实际来源（monitor 预设 / waveform 自定义起止） */
  const source = ref<'' | 'monitor' | 'waveform'>('');
  /** 后端 LTTB 降采样提示（诚实化：点数少于窗口应有密度时告知） */
  const downsampled = ref(false);
  const pointCount = ref(0);

  /** 请求代次（回路/窗口切换竞态守卫） */
  let generation = 0;

  const yDomain = computed(() => computeYDomain(frames.value));

  async function loadWindow(
    loopId: string,
    preset: WB360WindowPreset,
    opts: { maxPoints?: number } = {},
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
      let loaded: TrendFrame[] = [];
      if (preset.trendWindow) {
        source.value = 'monitor';
        const detail: LoopApi.MonitorDetail = await getLoopMonitorDetailApi(
          loopId,
          preset.trendWindow,
        );
        if (gen !== generation) return;
        const t = detail.trend;
        loaded = t.timestamps.map((ts, i) => ({
          mode: t.mode[i] ?? null,
          op: t.op[i] ?? null,
          pv: t.pv[i] ?? null,
          quality: normalizeQuality(t.pvQuality[i] ?? null, true),
          sp: t.sp[i] ?? null,
          ts,
        }));
        downsampled.value = t.downsampled ?? false;
        pointCount.value = t.pointCount ?? loaded.length;
      } else if (preset.spanSeconds) {
        source.value = 'waveform';
        const now = Date.now();
        const res = await getWaveformApi(
          loopId,
          new Date(now - preset.spanSeconds * 1000).toISOString(),
          new Date(now).toISOString(),
          opts.maxPoints ?? 2000,
        );
        if (gen !== generation) return;
        loaded = res.points.map((p) => ({
          mode: p.mode ?? null,
          op: p.op ?? null,
          pv: p.pv ?? null,
          // waveform 质量口径：pvQuality 1/0（GOOD/BAD）；valid=false 视为 BAD；
          // UNCERTAIN 该端点无法表达（诚实化：不虚构）
          quality: normalizeQuality(p, false),
          sp: p.sp ?? null,
          ts: Date.parse(p.timestamp),
        }));
        downsampled.value = res.downsampled;
        pointCount.value = res.pointCount ?? loaded.length;
      } else {
        // custom 占位档由 UI 拦截，不应进入取数
        error.value = '自定义窗口尚未开放（正式版提供起止选择器）';
        return;
      }
      if (gen !== generation) return;
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

/** monitor 字符串质量码直通；waveform 数值/valid 掩码归一 */
function normalizeQuality(
  q: unknown,
  isMonitor: boolean,
): 'BAD' | 'GOOD' | 'UNCERTAIN' | null {
  if (isMonitor) {
    return q === 'BAD' || q === 'GOOD' || q === 'UNCERTAIN' ? q : null;
  }
  // waveform WaveformPoint：pvQuality 1/0 + valid 掩码
  const p = q as { pvQuality?: null | number; valid?: boolean };
  if (p?.valid === false || p?.pvQuality === 0) return 'BAD';
  return 'GOOD';
}
