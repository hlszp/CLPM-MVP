import type { Wb360Session } from './use-wb360-session';

import type { LoopApi } from '#/api/loop';
import type { RealtimeUpdatable } from '#/composables/use-loop-realtime';

/**
 * 回路工作台单回路对象上下文（P6-02a O02-CORE，2026-10-14 重写）
 *
 * 仅供 SingleLoopWorkbench 实例会话使用；全目录（装置树/清单/筛选）在
 * use-wb360-directory，二者不互调。
 *
 * 契约（01a §4.2/§4.3）：
 * - 单对象读取：getLoopDetailApi / getLoopMonitorDetailApi / getLoopModeMappingApi
 *   （+ getLoopTagsApi→getTagDetailApi 取量程）；禁止拉全厂 monitor 列表兜底；
 * - 对象不存在/删除/越权：loadError 明确失败展示原因，不回退任何其他回路；
 *   monitor 端点硬角色拒绝（如 SPONSOR）时 monitorError 如实显示不可用，
 *   基本信息仍按 detail 呈现，不靠任何 prop 绕过权限；
 * - 迟到响应（对象/证据切换后）：趋势/量程/对象读取落地前同时核对会话
 *   上下文身份 + 请求代次（session.isCurrent），不符丢弃；
 * - 实时兴趣经 Slice B 冻结 Interface 注册（owner=会话实例），
 *   不直接调 subscribe / bindLoopInterest；关闭只释放本实例 handler/兴趣。
 */
import { ref, type Ref, watch } from 'vue';

import {
  getLoopDetailApi,
  getLoopModeMappingApi,
  getLoopMonitorDetailApi,
  getLoopTagsApi,
} from '#/api/loop';
import { getTagDetailApi } from '#/api/tag';
import {
  parseTagCode,
  registerInterest,
  useLoopRealtime,
} from '#/composables/use-loop-realtime';

/** PV/OP 位号量程（趋势轴定标；缺失项为 null → 趋势退回数据域） */
export interface Wb360Ranges {
  opRange: null | { hi: number; lo: number };
  pvRange: null | { hi: number; lo: number };
  pvUnit: null | string;
}

/** 实时点载荷（趋势层 onRealtimePoint 转发口径） */
export interface Wb360RealtimePoint {
  collectTime: number;
  quality: null | string;
  role: string;
  tagName: string;
  value: null | number;
}

/** 失败原因归类（请求错误对象 → 用户可读原因，不编造状态码） */
function reasonOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 空实时值（monitor 不可用时的诚实占位：全部 null，不编造读数） */
function emptyCurrentValues(): LoopApi.MonitorCurrentValues {
  return {
    mode: null,
    modeLabel: null,
    op: null,
    pv: null,
    pvQuality: null,
    readAt: null,
    sp: null,
  };
}

export function useWb360Loop(
  loopId: Ref<null | string>,
  session: Wb360Session,
) {
  /** 当前回路对象（单对象接口装配；WS 局部更新实时值） */
  const current = ref<LoopApi.MonitorListItem | null>(null);
  const loading = ref(false);
  /** 对象级失败（不存在/删除/越权/全部来源失败）：明确展示原因，不回退 */
  const loadError = ref<null | string>(null);
  /** monitor 端点失败（如 SPONSOR 硬角色拒绝）：如实显示，基本对象仍呈现 */
  const monitorError = ref<null | string>(null);

  const ranges = ref<Wb360Ranges>({
    opRange: null,
    pvRange: null,
    pvUnit: null,
  });
  const rangesCache = new Map<string, Wb360Ranges>();

  const { applyMessage, connectionStatus, lastMessageAt, onMessage, start } =
    useLoopRealtime();

  /* ── 单对象装配（detail 为基本身份；monitor 为实时/KPI；mapping 为 MODE 映射） ── */
  async function loadLoop(id: string) {
    const token = session.beginRequest();
    loading.value = true;
    loadError.value = null;
    monitorError.value = null;
    const [detailR, monitorR, mappingR] = await Promise.allSettled([
      getLoopDetailApi(id),
      getLoopMonitorDetailApi(id),
      getLoopModeMappingApi(id),
    ]);
    // 迟到响应：对象/证据已切换 → 整体丢弃（身份 + 代次双重核对）
    if (!session.isCurrent(token)) return;

    const detail = detailR.status === 'fulfilled' ? detailR.value : null;
    const monitor = monitorR.status === 'fulfilled' ? monitorR.value : null;

    if (!detail && !monitor) {
      // 对象不存在/删除/越权：明确失败，不回退任何其他回路（A12/A21）
      loadError.value =
        `回路对象不可用（可能不存在、已删除或不在当前授权范围）：` +
        `${reasonOf(detailR.status === 'rejected' ? detailR.reason : (monitorR.status === 'rejected' ? monitorR.reason : '未知错误'))}`;
      current.value = null;
      loading.value = false;
      return;
    }
    if (monitorR.status === 'rejected') {
      // monitor 硬角色拒绝（SPONSOR 等）/ 端点失败：如实显示不可用，
      // 实时值/趋势区域按各自错误呈现，不靠任何 prop 绕过权限
      monitorError.value = `监控数据不可用：${reasonOf(monitorR.reason)}`;
    }

    const mapping: Record<string, string> = {};
    if (mappingR.status === 'fulfilled') {
      for (const m of mappingR.value) {
        mapping[String(m.modeValue)] = m.modeLabel;
      }
    }
    // mapping 失败非致命：resolveModeLabel 链回退默认映射（R17 既有口径）

    const basic = detail?.basicInfo;
    const cv: LoopApi.MonitorCurrentValues = monitor
      ? { ...monitor.currentValues, modeMapping: mapping }
      : { ...emptyCurrentValues(), modeMapping: mapping };
    // monitor 不可用时 controlMode/score 无诚实取值（null）——工作区不渲染
    // 这两字段（页头控制模式读 currentValues.modeLabel），且 monitorError 已
    // 显式呈现不可用原因；类型层面按 MonitorListItem 装配以兼容既有组件 props。
    current.value = {
      controlMode: (monitor?.runtimeParams.controlMode ??
        null) as LoopApi.ControlMode,
      currentValues: cv,
      description: basic?.description ?? '',
      isActive: basic?.isActive ?? true,
      loopId: id,
      loopStatus: (basic?.status ??
        monitor?.loopStatus ??
        null) as LoopApi.LoopStatus,
      ...(basic?.loopType ? { loopType: basic.loopType } : {}),
      modeMapping: mapping,
      readAt: monitor?.currentValues.readAt ?? '',
      score: (monitor?.kpiSummary.composite_score ?? null) as number,
      tagName: basic?.tagName ?? monitor?.tagName ?? id,
      unitName: basic?.unitName ?? '',
      ...(monitor?.kpiStatus ? { kpiStatus: monitor.kpiStatus } : {}),
    };
    loading.value = false;

    // 最近继续：对象确认成功加载（双来源失败已在上方 return，不产生最近项）；
    // fire-and-forget，失败不阻塞作业（Slice G 接线）
    void session.recordResume();

    void loadRanges(id, token);
  }

  /* ── PV/OP 量程（趋势轴定标；迟到响应同样核对身份+代次） ── */
  function validRange(
    d: null | { rangeMax?: null | number; rangeMin?: null | number },
  ) {
    if (
      d &&
      typeof d.rangeMax === 'number' &&
      typeof d.rangeMin === 'number' &&
      d.rangeMax > d.rangeMin
    ) {
      return { hi: d.rangeMax, lo: d.rangeMin };
    }
    return null;
  }

  async function loadRanges(id: string, token: { generation: number; key: string }) {
    const cached = rangesCache.get(id);
    if (cached) {
      if (session.isCurrent(token)) ranges.value = cached;
      return;
    }
    try {
      const tags = await getLoopTagsApi(id);
      if (!session.isCurrent(token)) return;
      const byRole = (role: string) =>
        tags.tags.find((t) => t.role === role && t.tagId) ?? null;
      const pvTag = byRole('PV');
      const opTag = byRole('OP');
      const [pvDetail, opDetail] = await Promise.all([
        pvTag?.tagId
          ? getTagDetailApi(pvTag.tagId).catch(() => null)
          : Promise.resolve(null),
        opTag?.tagId
          ? getTagDetailApi(opTag.tagId).catch(() => null)
          : Promise.resolve(null),
      ]);
      if (!session.isCurrent(token)) return;
      const next: Wb360Ranges = {
        opRange: validRange(opDetail),
        pvRange: validRange(pvDetail),
        pvUnit: pvDetail?.unit ?? null,
      };
      rangesCache.set(id, next);
      ranges.value = next;
    } catch {
      // 量程获取失败：保持空（趋势退回数据域），不阻塞主链路
    }
  }

  // 对象变化 → 重置并重新单对象读取（会话代次随上下文身份递增，
  // 本 watch 内发起的请求即当代；旧请求落地时被 isCurrent 丢弃）
  watch(
    loopId,
    (id) => {
      current.value = null;
      loadError.value = null;
      monitorError.value = null;
      ranges.value = { opRange: null, pvRange: null, pvUnit: null };
      if (id) void loadLoop(id);
    },
    { immediate: true },
  );

  /* ── WS：兴趣经冻结 Interface 注册（owner=会话实例），消息局部更新并转发趋势层 ── */
  const realtimeHandlers = new Set<(payload: Wb360RealtimePoint) => void>();

  registerInterest(session.owner, () => {
    const tag = current.value?.tagName;
    return { loops: tag ? [tag] : [] };
  });

  onMessage((msg) => {
    if (!current.value) return;
    const parsed = parseTagCode(msg.tagCode);
    if (!parsed || parsed.tagName !== current.value.tagName) return;
    // 鸭子类型桥接（MonitorCurrentValues 缺 PID 三值字段，applyMessage
    // 按 tagName 匹配后仅写命中角色；先例 tuning/workbench.vue 同口径）
    const applied = applyMessage(msg, [
      current.value as unknown as RealtimeUpdatable,
    ]);
    if (!applied) return;
    for (const h of realtimeHandlers) {
      const ts = msg.collectTime ? Date.parse(msg.collectTime) : Date.now();
      const value =
        msg.valueValid === false || msg.value === undefined
          ? Number.NaN
          : Number.parseFloat(msg.value);
      // WS 数值质量码（共享契约：1=Good 0=Bad）→ 趋势层字符串口径
      const quality = msg.quality === 0 ? 'BAD' : (msg.quality > 0 ? 'GOOD' : null);
      h({
        collectTime: Number.isNaN(ts) ? Date.now() : ts,
        quality,
        role: parsed.role,
        tagName: parsed.tagName,
        value: Number.isNaN(value) ? null : value,
      });
    }
  });

  /** 趋势层注册实时点回调（realtime 模式追加用） */
  function onRealtimePoint(handler: (payload: Wb360RealtimePoint) => void) {
    realtimeHandlers.add(handler);
    return () => {
      realtimeHandlers.delete(handler);
    };
  }

  return {
    connectionStatus,
    current,
    lastMessageAt,
    loadError,
    loading,
    monitorError,
    onRealtimePoint,
    ranges,
    startRealtime: start,
  };
}
