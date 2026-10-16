/**
 * 驾驶舱「进入工作区」统一出口目标构造（IAR-D6/D8，P6-01b-a Slice H）
 *
 * 六页签头区共用：携当前页签上下文经 use-drill 冻结构造点
 * （wb360DrillQuery → wb360QueryFromContext）跳回路工作台——
 * - 剖面：诊断/整定/处置页签 → 对应 section；总览/回路/性能默认 assess
 *   （wb360 Adapter 默认剖面，不显式携带保持 URL 干净）；
 * - 精确时间窗：驾驶舱共享 timeWindow → preciseCockpitWindow 精确起止+时区
 *   （02b 口径：禁止近似滚动窗替换，落地页数字才对得上）；
 * - 节点/回路：仅回路页签（/cockpit/loops）有持久化选中态（store + URL
 *   恢复），选中回路 → mode=single 直达，仅选节点 → mode=all + nodeId
 *   scope；其余页签的选中态为页内瞬态（切页重挂载即复位），不携带——
 *   视页签而定，不编造不存在的上下文；
 * - from：当前页签 fullPath，wb360 返回恢复用。
 *
 * 纯函数（vitest 直接覆盖）；权限降级（canOpenWorkbench360）由调用方处理。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { Wb360SectionExternal } from '#/views/loop/workbench360/workbench-context';

import { preciseCockpitWindow, wb360DrillQuery } from '../wb-comps/use-drill';

/** 回路页签路径（唯一持有持久化节点/回路选中态的页签） */
export const COCKPIT_LOOPS_TAB_PATH = '/cockpit/loops';

/**
 * 页签 → wb360 初始剖面（对外 section 名）。
 * 总览/回路/性能不在表内：默认 assess（Adapter 缺省剖面，不显式携带）。
 */
export const COCKPIT_WORKZONE_SECTION: Readonly<
  Record<string, Wb360SectionExternal>
> = {
  '/cockpit/diagnosis': 'diagnosis',
  '/cockpit/handling': 'handling',
  '/cockpit/tuning': 'tuning',
};

/** 出口目标构造输入（节点/回路仅回路页签传入） */
export interface WorkzoneExitInput {
  /** 当前页签 fullPath（wb360 返回恢复来源；空则不携带） */
  from?: null | string;
  /** 回路页签选中回路（非空 → mode=single 直达） */
  loopId?: null | string;
  /** 回路页签左树选中节点（稳定 nodeId → scope 回显） */
  nodeId?: null | string;
  /** 构造窗口用的基准时刻（测试注入；默认当前） */
  now?: Date;
  /** 当前页签路径（/cockpit 或 /cockpit/*） */
  tabPath: string;
  /** 驾驶舱共享时间窗（24h/7d/30d → 精确起止） */
  timeWindow: CockpitApi.TimeWindow;
}

/**
 * 构造「进入工作区」目标（经冻结构造点 wb360DrillQuery）。
 * 选中回路 → single；否则 all（剖面/窗口/节点 scope 作为携带项，Adapter
 * 在用户后续选中回路时消费）。
 */
export function buildWorkzoneExitTarget(input: WorkzoneExitInput): {
  path: string;
  query: Record<string, string>;
} {
  const isLoopsTab = input.tabPath === COCKPIT_LOOPS_TAB_PATH;
  const loopId = isLoopsTab && input.loopId ? input.loopId : '';
  const nodeId = isLoopsTab && input.nodeId ? input.nodeId : '';
  const section = COCKPIT_WORKZONE_SECTION[input.tabPath];
  return wb360DrillQuery(
    {
      loopId,
      ...(section ? { section } : {}),
      window: preciseCockpitWindow(input.timeWindow, input.now),
      ...(nodeId ? { scope: { nodeId } } : {}),
    },
    { mode: loopId ? 'single' : 'all', from: input.from },
  );
}
