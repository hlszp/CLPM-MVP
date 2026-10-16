/**
 * 回路工作台统一上下文与路由协议（P6-02a O02-CORE，2026-10-14）
 *
 * 契约事实源：执行状态/P6-01a-契约与文件Owner §4.1/§4.2 与 09号文 §2/§3：
 * - `WorkbenchContext` 是 SingleLoopWorkbench 的唯一业务输入：loopId 必需，
 *   其余键（section/window/evidenceRefs/workRef/scope/profileRef/configRevision）
 *   参与完整上下文身份——同回路不同证据/窗口也是不同会话（wb360ContextKey）。
 * - 路由 Adapter 优先级：有效显式 mode → embed=1 → 无 mode 非空 loopId → 默认 all；
 *   非法显式 mode/section 直接提示（errors），不静默降级；window/record/work/scope
 *   等辅助参数非法时 notices 显式列出被忽略项，不静默丢弃。
 * - 全模式选中对象写 `mode=all&loopId=…`（wb360QueryFromContext），刷新后不被
 *   "无 mode + loopId → single" 兼容规则误切。
 *
 * 本文件只放类型与纯函数（不依赖 vue / vue-router），Adapter 优先级矩阵由
 * vitest 直接覆盖。
 */

/** 对外剖面名（09 §2：section=diagnosis 为对外名称，内部 diag 由核心映射） */
export type Wb360SectionExternal = 'assess' | 'diagnosis' | 'handling' | 'tuning';

/** 统一上下文（01a §4.1；record/版本以具体服务已有类型校验，不接受 URL 自由声明权限） */
export interface WorkbenchContext {
  /** 必需的稳定 ID（缺失时 Adapter 直接报错，不开始请求） */
  loopId: string;
  /** 初始剖面（对外名；默认 assess；禁用剖面由核心显示不可用原因） */
  section?: Wb360SectionExternal;
  /** 范围定位（装置/单元稳定 nodeId 与筛选回显） */
  scope?: { filters?: Record<string, unknown>; nodeId?: string };
  /** 精确时间窗（ISO 起止 + IANA 时区） */
  window?: { timezone: string; tsEnd: string; tsStart: string };
  /** 证据引用（记录/结果 ID 与版本） */
  evidenceRefs?: Array<{ id: string; kind: string; version?: string }>;
  /** 工作引用（核查/行动项/工单；服务端验证与回路一致） */
  workRef?: { id: string; kind: 'action_item' | 'handling_order' | 'investigation' };
  profileRef?: string;
  configRevision?: string;
}

/** 呈现方式：仅尺寸/外观适配，不改变业务规则 */
export type Wb360Presentation = 'drawer' | 'page' | 'panel';

/** 工作台模式：全回路树宿主 / 指定单回路 */
export type Wb360Mode = 'all' | 'single';

/** result-changed 载荷：loopId + 工作/结果 ID + 变更种类；宿主定向刷新，不把"计算完成"当闭环 */
export interface Wb360ResultChangedPayload {
  changeKind: string;
  loopId: string;
  workRef?: { id: string; kind: string };
}

/** navigate-request 载荷：已校验目标种类 + context + returnRef；宿主构造路由，核心不写 URL */
export type Wb360NavigateRequest =
  | {
      /** 同宿主更新上下文（切对象/单项清除 window/record/work/scope 等） */
      context: WorkbenchContext;
      returnRef?: string;
      target: 'update-context';
    }
  | {
      /** 请求返回来源页（returnRef 由宿主在构造时注入，如 ?from=） */
      returnRef?: string;
      target: 'return';
    };

/* ── 上下文身份 ── */

/**
 * 完整上下文身份键：loopId + 窗口 + 证据/工作/配置/profile 身份。
 * 宿主以此 key 建立 keyed 会话；切相同回路不同证据也换会话（01a §4.1）。
 */
export function wb360ContextKey(ctx: WorkbenchContext): string {
  const ev = (ctx.evidenceRefs ?? [])
    .map((e) => `${e.kind}:${e.id}:${e.version ?? ''}`)
    .toSorted()
    .join('|');
  const win = ctx.window
    ? `${ctx.window.tsStart}~${ctx.window.tsEnd}@${ctx.window.timezone}`
    : '';
  const work = ctx.workRef ? `${ctx.workRef.kind}:${ctx.workRef.id}` : '';
  const scope = ctx.scope?.nodeId ?? '';
  return [
    ctx.loopId,
    ctx.section ?? '',
    win,
    ev,
    work,
    scope,
    ctx.profileRef ?? '',
    ctx.configRevision ?? '',
  ].join('¬');
}

/* ── 路由协议（Adapter 纯函数） ── */

/** Adapter 管理的 query 键（写入时整体重建这组键，未管理键原样保留） */
export const WB360_MANAGED_QUERY_KEYS = [
  'mode',
  'loopId',
  'section',
  'embed',
  'tsStart',
  'tsEnd',
  'tz',
  'recordKind',
  'recordId',
  'recordVersion',
  'workKind',
  'workId',
  'nodeId',
  'profile',
  'configRevision',
] as const;

/** 路由解析结果（errors 非空 = 硬错误，Adapter 显示原因不渲染工作台） */
export interface Wb360RouteResolution {
  /** 携带项（section/window/record/work/scope/profile/configRevision）：
   *  独立于 loopId 暴露——all 模式未指定对象时，用户后续选中回路仍消费 */
  carried: Omit<WorkbenchContext, 'loopId'>;
  /** embed=1 精简模式（隐藏树、显示返回；仅 single 时有意义） */
  compact: boolean;
  /** 解析出的统一上下文（硬错误或无 loopId 时为 null） */
  context: null | WorkbenchContext;
  /** 硬错误（非法显式 mode/section、single 缺 loopId）：逐条显示，不静默 */
  errors: string[];
  /** all 模式预选对象（URL 携带 loopId 时） */
  initialLoopId: null | string;
  mode: Wb360Mode;
  /** 软提示（非法 window/record/work 参数被忽略）：逐条显示，不静默丢弃 */
  notices: string[];
  /** 返回目标（?from=，精简模式返回按钮用） */
  returnRef: null | string;
}

/** query 值取首个字符串（vue-router LocationQueryValue 口径） */
function str(v: unknown): string {
  if (Array.isArray(v)) return typeof v[0] === 'string' ? v[0] : '';
  return typeof v === 'string' ? v : '';
}

/** 对外/内部剖面别名 → 对外规范名（diagnosis 为对外名；diag 为内部旧键） */
const SECTION_ALIAS: Record<string, Wb360SectionExternal> = {
  assess: 'assess',
  diag: 'diagnosis',
  diagnosis: 'diagnosis',
  handling: 'handling',
  tuning: 'tuning',
};

const WORK_KINDS = new Set(['action_item', 'handling_order', 'investigation']);

/**
 * 解析 /loop/workbench360 路由参数（优先级：有效显式 mode → embed=1 →
 * 无 mode 非空 loopId → 默认 all）。
 *
 * @param query route.query 风格键值（值可为 string | string[] | undefined）
 * @param hashLoopId 浏览器真实 URL(hash) 中的 loopId（tabbar persist 恢复态
 *   与 URL 不一致时以 hash 为准——沿用旧页 initialLoopId 口径）；无则传 null
 */
export function resolveWb360Route(
  query: Record<string, unknown>,
  hashLoopId: null | string = null,
): Wb360RouteResolution {
  const errors: string[] = [];
  const notices: string[] = [];
  const loopId = hashLoopId || str(query.loopId) || null;
  const returnRef = str(query.from) || null;

  // ── 模式优先级 ──
  const rawMode = str(query.mode);
  let mode: Wb360Mode;
  let compact = false;
  if (rawMode) {
    if (rawMode === 'single' || rawMode === 'all') {
      mode = rawMode;
    } else {
      // 非法显式 mode：直接提示，不通过低优先级参数掩盖错误（09 §2）
      errors.push(
        `无法识别的 mode 参数“${rawMode}”（仅支持 single / all），请检查链接`,
      );
      mode = 'all';
    }
  } else if (str(query.embed) === '1') {
    mode = 'single';
    compact = true;
  } else if (loopId) {
    // 旧 loopId 深链（无 mode）规范为 single
    mode = 'single';
  } else {
    mode = 'all';
  }

  // ── 必需对象 ──
  if (errors.length === 0 && mode === 'single' && !loopId) {
    errors.push('单回路模式缺少必需对象 loopId，请从来源页重新进入');
  }

  // ── 剖面 ──
  let section: undefined | Wb360SectionExternal;
  const rawSection = str(query.section);
  if (rawSection) {
    const mapped = SECTION_ALIAS[rawSection];
    if (mapped) {
      section = mapped;
    } else {
      errors.push(
        `无法识别的 section 参数“${rawSection}”（仅支持 assess / diagnosis / tuning / handling）`,
      );
    }
  }

  // ── 时间窗（辅助参数：非法显式列出，不静默丢弃） ──
  let window_: WorkbenchContext['window'];
  const tsStart = str(query.tsStart);
  const tsEnd = str(query.tsEnd);
  if (tsStart || tsEnd) {
    const a = Date.parse(tsStart);
    const b = Date.parse(tsEnd);
    if (tsStart && tsEnd && Number.isFinite(a) && Number.isFinite(b) && b > a) {
      window_ = {
        tsStart: new Date(a).toISOString(),
        tsEnd: new Date(b).toISOString(),
        timezone:
          str(query.tz) || Intl.DateTimeFormat().resolvedOptions().timeZone,
      };
    } else {
      notices.push(
        '时间窗参数（tsStart/tsEnd）缺失或无效，已忽略并按默认窗口展示',
      );
    }
  }

  // ── 证据引用（record 三元组） ──
  let evidenceRefs: WorkbenchContext['evidenceRefs'];
  const recordKind = str(query.recordKind);
  const recordId = str(query.recordId);
  if (recordKind || recordId) {
    if (recordKind && recordId) {
      evidenceRefs = [
        {
          kind: recordKind,
          id: recordId,
          ...(str(query.recordVersion)
            ? { version: str(query.recordVersion) }
            : {}),
        },
      ];
    } else {
      notices.push('记录参数（recordKind/recordId）不完整，已忽略');
    }
  }

  // ── 工作引用 ──
  let workRef: WorkbenchContext['workRef'];
  const workKind = str(query.workKind);
  const workId = str(query.workId);
  if (workKind || workId) {
    if (workKind && workId && WORK_KINDS.has(workKind)) {
      workRef = { kind: workKind as never, id: workId };
    } else {
      notices.push('工作引用参数（workKind/workId）无效，已忽略');
    }
  }

  // ── 范围 / profile / 配置版本 ──
  const nodeId = str(query.nodeId);
  const profileRef = str(query.profile);
  const configRevision = str(query.configRevision);

  // 携带项独立于 loopId 暴露：all 模式未指定对象时，?section=/window 等
  // 在用户后续选中回路时仍被消费（不静默丢失）
  const carried: Omit<WorkbenchContext, 'loopId'> = {
    ...(section ? { section } : {}),
    ...(nodeId ? { scope: { nodeId } } : {}),
    ...(window_ ? { window: window_ } : {}),
    ...(evidenceRefs ? { evidenceRefs } : {}),
    ...(workRef ? { workRef } : {}),
    ...(profileRef ? { profileRef } : {}),
    ...(configRevision ? { configRevision } : {}),
  };

  const context: null | WorkbenchContext =
    errors.length === 0 && loopId ? { loopId, ...carried } : null;

  return {
    compact,
    carried,
    context,
    errors,
    initialLoopId: mode === 'all' ? loopId : null,
    mode,
    notices,
    returnRef,
  };
}

/**
 * 旧 embed props 协议解析（alert/events.vue、diagnosis/records.vue 抽屉内嵌
 * index.vue 的既有调用方式；02b 迁移前由 Adapter 保持兼容）。
 * 语义同 single 模式：显式对象 + 可选剖面；缺对象/非法剖面直接提示。
 */
export function resolveWb360Embed(
  embedLoopId: string,
  embedSection?: string,
): Wb360RouteResolution {
  const errors: string[] = [];
  const carried: Omit<WorkbenchContext, 'loopId'> = {};
  if (!embedLoopId) {
    errors.push('内嵌模式缺少必需对象 loopId');
  }
  if (embedSection) {
    const mapped = SECTION_ALIAS[embedSection];
    if (mapped) {
      carried.section = mapped;
    } else {
      errors.push(
        `无法识别的 section 参数“${embedSection}”（仅支持 assess / diagnosis / tuning / handling）`,
      );
    }
  }
  return {
    carried,
    compact: false,
    context:
      errors.length === 0 ? { loopId: embedLoopId, ...carried } : null,
    errors,
    initialLoopId: null,
    mode: 'single',
    notices: [],
    returnRef: null,
  };
}

/**
 * 由模式 + 上下文构造受管 query 键（宿主选中/上下文更新写 URL 用）。
 * 全模式选中对象恒写 `mode=all&loopId=…`（防刷新被兼容规则误切 single）。
 * section 为一次性消费键，不回写（随下次选中/更新从 URL 清除）。
 */
export function wb360QueryFromContext(
  mode: Wb360Mode,
  context: null | WorkbenchContext,
): Record<string, string> {
  const out: Record<string, string> = { mode };
  if (context?.loopId) out.loopId = context.loopId;
  if (context?.window) {
    out.tsStart = context.window.tsStart;
    out.tsEnd = context.window.tsEnd;
    out.tz = context.window.timezone;
  }
  const ev = context?.evidenceRefs?.[0];
  if (ev) {
    out.recordKind = ev.kind;
    out.recordId = ev.id;
    if (ev.version) out.recordVersion = ev.version;
  }
  if (context?.workRef) {
    out.workKind = context.workRef.kind;
    out.workId = context.workRef.id;
  }
  if (context?.scope?.nodeId) out.nodeId = context.scope.nodeId;
  if (context?.profileRef) out.profile = context.profileRef;
  if (context?.configRevision) out.configRevision = context.configRevision;
  return out;
}
