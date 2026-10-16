/**
 * 回路工作台实例会话（P6-02a O02-CORE，2026-10-14）
 *
 * 契约（01a §4.3 + 09 §3.2）：
 * - 每个 SingleLoopWorkbench 实例拥有独立会话：诊断/整定预填与工作引用由
 *   实例会话承载，不再使用无对象身份的模块级 reactive（双实例互不污染）；
 * - 每次对象/证据变化递增请求代次（generation）：趋势/量程/历史等迟到响应
 *   同时核对上下文身份（contextKey）与代次，不符丢弃（A→B 不串写）；
 * - 会话 owner（symbol）即实时兴趣注册身份：经 Slice B 冻结 Interface
 *   registerInterest 注册，KeepAlive 停用释放/恢复重算由该层保证；
 * - 尺寸/主题变化不重建业务会话（会话键 = 上下文身份，与布局无关）；
 * - 关闭组件只释放本实例读请求/handler/兴趣，不假称取消已投递计算。
 *
 * 会话栈（sessionStack）供两个 prefill 兼容模块（use-diag-prefill /
 * use-tuning-prefill，剖面组件零改动消费）解析写目标：事件只可能来自当前
 * 可见实例（隐藏页 KeepAlive 停用不触发交互），栈顶即可见实例。
 *
 * 最近继续（Slice G 接线，2026-10-14）：对象确认成功加载后由 use-wb360-loop
 * 调用 recordResume（PUT /me/work-resume/loop/{id}）——失败打开（404/越权/
 * 双来源失败）不产生最近项；存储 context 仅 ID/窗口/证据引用（禁业务结果
 * 副本/任意 URL）；expectedRevision+generation CAS 协议，409 携
 * currentRevision/currentGeneration 刷新重试一次后静默放弃（不阻塞作业、
 * 不显示已保存假象）；同会话同对象同上下文不重复 PUT。
 */
import type { ComputedRef, InjectionKey, Ref } from 'vue';

import type { DiagPrefill } from './use-diag-prefill';
import type { TuningPrefill } from './use-tuning-prefill';

import { computed, getCurrentInstance, onScopeDispose, provide, reactive, ref, watch } from 'vue';

import {
  extractRevisionConflict,
  getWorkResumeApi,
  newRequestId,
  putWorkResumeApi,
} from '#/api/personal-work';

import { wb360ContextKey, type WorkbenchContext } from '../workbench-context';

/** 请求令牌：发起时捕获，响应落地前核对（身份 + 代次双重） */
export interface Wb360RequestToken {
  generation: number;
  key: string;
}

export interface Wb360Session {
  /** 清空诊断预填（对象/证据切换自动调用；剖面发起后也可调用） */
  clearDiagPrefill: () => void;
  clearTuningPrefill: () => void;
  /** 完整上下文身份（loopId+窗口+证据/工作/配置）；变化即换代次 */
  contextKey: ComputedRef<string>;
  /** 诊断预填（实例私有；原模块级 reactive 迁入） */
  diagPrefill: DiagPrefill;
  /** 释放本会话（幂等；组件卸载自动调用） */
  dispose: () => void;
  /** 请求代次（对象/证据变化递增） */
  generation: Ref<number>;
  /** 迟到响应判定：令牌身份与代次任一不符即丢弃 */
  isCurrent: (token: Wb360RequestToken) => boolean;
  /** 发起一次读请求：捕获当前身份与代次 */
  beginRequest: () => Wb360RequestToken;
  /** 实时兴趣 owner（会话实例身份，registerInterest 用） */
  owner: symbol;
  /**
   * 记录最近继续（fire-and-forget）：仅对象确认成功加载后调用；
   * 409 刷新重试一次后静默放弃；同会话同对象同上下文幂等
   */
  recordResume: () => Promise<void>;
  setDiagPrefill: (tsStart: string, tsEnd: string) => void;
  setTuningPrefill: (payload: {
    categoryLabel?: null | string;
    primaryConfidence?: null | number;
    tsEnd: string;
    tsStart: string;
  }) => void;
  tuningPrefill: TuningPrefill;
}

export const WB360_SESSION_KEY: InjectionKey<Wb360Session> =
  Symbol('wb360-session');

/** 存活会话栈（栈顶 = 最近注册 = 实际可见实例；见文件头说明） */
const sessionStack: Wb360Session[] = [];

/** 当前可见会话（prefill 兼容模块写路径解析用；无会话时为 null） */
export function currentWb360Session(): null | Wb360Session {
  return sessionStack.at(-1) ?? null;
}

export function useWb360Session(
  context: Ref<WorkbenchContext>,
): Wb360Session {
  const owner = Symbol('wb360-session');
  const contextKey = computed(() => wb360ContextKey(context.value));
  const generation = ref(0);

  const diagPrefill = reactive<DiagPrefill>({
    from: null,
    tsEnd: null,
    tsStart: null,
  });
  const tuningPrefill = reactive<TuningPrefill>({
    categoryLabel: null,
    from: null,
    primaryConfidence: null,
    tsEnd: null,
    tsStart: null,
  });

  function clearDiagPrefill() {
    diagPrefill.from = null;
    diagPrefill.tsStart = null;
    diagPrefill.tsEnd = null;
  }
  function clearTuningPrefill() {
    tuningPrefill.from = null;
    tuningPrefill.categoryLabel = null;
    tuningPrefill.primaryConfidence = null;
    tuningPrefill.tsStart = null;
    tuningPrefill.tsEnd = null;
  }

  /* ── 最近继续（PUT /me/work-resume/loop/{id}；协议对齐 my-work recordLoopResumeQuietly） ── */
  /** 代际/版本镜像（本会话首次记录时拉取；写响应回收最新值减少自伤 409） */
  let resumeGeneration: null | number = null;
  const resumeRevisions = new Map<string, number>();
  /** 幂等键：同会话同对象同上下文不重复 PUT（仅成功记录后才登记） */
  let resumeRecordedKey: null | string = null;

  async function refreshResumeState(): Promise<boolean> {
    try {
      const data = await getWorkResumeApi({ kind: 'loop', limit: 100 });
      resumeGeneration = data.currentGeneration;
      resumeRevisions.clear();
      for (const it of data.items) {
        resumeRevisions.set(`${it.resourceKind}:${it.resourceId}`, it.revision);
      }
      return true;
    } catch {
      return false;
    }
  }

  async function recordResume(): Promise<void> {
    const c = context.value;
    if (!c.loopId) return;
    // 存储上下文：仅 剖面/窗口/证据引用（个人数据设计：ID、窗口、证据引用
    // 与恢复位置；禁业务结果副本/凭据/任意 URL；workRef 业务身份不入档）
    const resumeContext: Record<string, any> = {};
    if (c.section) resumeContext.section = c.section;
    if (c.window) {
      resumeContext.window = {
        tsStart: c.window.tsStart,
        tsEnd: c.window.tsEnd,
        timezone: c.window.timezone,
      };
    }
    if (c.evidenceRefs && c.evidenceRefs.length > 0) {
      resumeContext.evidenceRefs = c.evidenceRefs.map((e) => ({
        id: e.id,
        kind: e.kind,
        ...(e.version ? { version: e.version } : {}),
      }));
    }
    const dedupKey = `${c.loopId}¬${JSON.stringify(resumeContext)}`;
    if (dedupKey === resumeRecordedKey) return;
    try {
      if (resumeGeneration === null) await refreshResumeState();
      const key = `loop:${c.loopId}`;
      const attempt = async (): Promise<boolean> => {
        const gen = resumeGeneration;
        if (gen === null) return false;
        const result = await putWorkResumeApi('loop', c.loopId, {
          context: resumeContext,
          expectedRevision: resumeRevisions.get(key) ?? 0,
          generation: gen,
          requestId: newRequestId(),
        });
        // 写响应即最新版本/代际：回收进镜像，避免后续写入自伤 409
        resumeRevisions.set(key, result.revision);
        resumeGeneration = result.generation;
        return true;
      };
      let ok = false;
      try {
        ok = await attempt();
      } catch (error) {
        if (!extractRevisionConflict(error)) return;
        await refreshResumeState();
        try {
          ok = await attempt();
        } catch {
          return; /* 重试仍失败：静默放弃，不阻塞作业、不显示已保存 */
        }
      }
      if (ok) resumeRecordedKey = dedupKey;
    } catch {
      /* 静默失败（无已保存假象） */
    }
  }

  const session: Wb360Session = {
    beginRequest: () => ({ generation: generation.value, key: contextKey.value }),
    clearDiagPrefill,
    clearTuningPrefill,
    contextKey,
    diagPrefill,
    dispose: () => {
      const i = sessionStack.indexOf(session);
      if (i !== -1) sessionStack.splice(i, 1);
    },
    generation,
    isCurrent: (token) =>
      token.generation === generation.value && token.key === contextKey.value,
    owner,
    recordResume,
    setDiagPrefill: (tsStart, tsEnd) => {
      diagPrefill.from = 'assess';
      diagPrefill.tsStart = tsStart;
      diagPrefill.tsEnd = tsEnd;
    },
    setTuningPrefill: (payload) => {
      tuningPrefill.from = 'diag';
      tuningPrefill.categoryLabel = payload.categoryLabel ?? null;
      tuningPrefill.primaryConfidence = payload.primaryConfidence ?? null;
      tuningPrefill.tsStart = payload.tsStart;
      tuningPrefill.tsEnd = payload.tsEnd;
    },
    tuningPrefill,
  };

  // 对象/证据变化：递增代次 + 预填随旧会话身份显式失效（不跨对象携带）
  watch(contextKey, () => {
    generation.value += 1;
    clearDiagPrefill();
    clearTuningPrefill();
  });

  sessionStack.push(session);

  if (getCurrentInstance()) {
    provide(WB360_SESSION_KEY, session);
    onScopeDispose(session.dispose);
  }

  return session;
}
