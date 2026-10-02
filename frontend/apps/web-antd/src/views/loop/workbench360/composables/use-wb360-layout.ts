/**
 * 回路工作台（新版）布局状态机 —— 分屏三态 / 剖面切换 / 可拖尺寸
 *
 * 对齐原型 #split/#ws 行为（设计方案 v3 §3、D17/D18）：
 * - 三态：thumbs（缩略卡 96px）/ half（展开 46%，可拖 18%–80%）/
 *   max（画布隐藏、迷你趋势贴旅程条下方，高度可拖 100px–主列 60%）
 * - 打开剖面 = 展开 half 态并高亮；分屏条按钮只管分屏不切剖面（D8）
 * - 模块热插拔：diagnosis/tuning/handling 禁用时对应剖面不可达（v3 §9）
 */
import { computed, reactive, readonly, ref } from 'vue';

import { moduleEnabled } from '#/composables/use-modules';
import {
  WB360_SECTIONS,
  type WB360SectionKey,
} from '#/constants/clpm-ui';

export type Wb360PanelState = 'half' | 'max' | 'thumbs';

/** 工作区展开态默认高度占比（v3 §3：46%） */
export const WS_DEFAULT_PCT = 46;
/** 工作区高度可拖范围（v3 §3：18%–80%） */
export const WS_MIN_PCT = 18;
export const WS_MAX_PCT = 80;
/** 迷你趋势默认高度（v3 §3：152px；可拖 100px–主列 60%） */
export const MINI_DEFAULT_H = 152;
export const MINI_MIN_H = 100;
/** 左脊柱宽度（v3 §3：默认 236px，可拖 180–420） */
export const SPINE_DEFAULT_W = 236;
export const SPINE_MIN_W = 180;
export const SPINE_MAX_W = 420;

/**
 * 布局状态机。
 *
 * 剖面点击（旅程条/缩略卡/事件徽标）一律走 openSection —— 唯一入口（D19）；
 * 分屏条三按钮走 setPanel，其中 max 再点一次 = 复原 half（原型行为）。
 */
export function useWb360Layout() {
  /** 当前分屏态 */
  const panelState = ref<Wb360PanelState>('thumbs');
  /** 当前活跃剖面（thumbs 态为 null） */
  const activeSection = ref<null | WB360SectionKey>(null);
  /** 展开态工作区高度（%，--ws-h） */
  const wsHeightPct = ref(WS_DEFAULT_PCT);
  /** 迷你趋势高度（px，--mini-h；null=用默认） */
  const miniHeightPx = ref<null | number>(null);
  /** 左脊柱宽度（px） */
  const spineWidth = ref(SPINE_DEFAULT_W);

  /** 模块热插拔过滤后的剖面清单（ journeys/缩略卡/工作区共用） */
  const availableSections = computed(() =>
    WB360_SECTIONS.filter(
      // assess 恒可用（monitor 域）；其余按模块开关
      (s) => s.module === 'assess' || moduleEnabled(s.module),
    ),
  );

  function isSectionAvailable(key: WB360SectionKey): boolean {
    return availableSections.value.some((s) => s.key === key);
  }

  /** 打开剖面（入口层唯一通道）：展开 half 态并点亮对应段 */
  function openSection(key: WB360SectionKey) {
    if (!isSectionAvailable(key)) return;
    activeSection.value = key;
    panelState.value = 'half';
  }

  /** 收起工作区回到缩略卡态 */
  function closeWs() {
    panelState.value = 'thumbs';
    activeSection.value = null;
    miniHeightPx.value = null;
  }

  const exitMax = () => {
    if (panelState.value === 'max') {
      panelState.value = 'half';
      miniHeightPx.value = null;
    }
  };

  /**
   * 分屏条三按钮（对齐原型 split click 逻辑）：
   * - thumbs：收起；
   * - half：退出最大化并确保有活跃剖面（无则开评估）；
   * - max：已最大化→复原 half；否则进入最大化（无活跃剖面默认整定剖面）
   */
  function setPanel(next: Wb360PanelState) {
    if (next === 'thumbs') {
      closeWs();
      return;
    }
    if (next === 'half') {
      exitMax();
      if (!activeSection.value || !isSectionAvailable(activeSection.value)) {
        activeSection.value = availableSections.value[0]?.key ?? null;
      }
      panelState.value = activeSection.value ? 'half' : 'thumbs';
      return;
    }
    // max
    if (panelState.value === 'max') {
      exitMax();
      return;
    }
    if (!activeSection.value) {
      activeSection.value =
        availableSections.value.find((s) => s.key === 'tuning')?.key ??
        availableSections.value[0]?.key ??
        null;
    }
    panelState.value = activeSection.value ? 'max' : 'thumbs';
  }

  /** 工作区高度拖拽（展开态，% 18–80） */
  function resizeWsPct(pct: number) {
    wsHeightPct.value = Math.min(WS_MAX_PCT, Math.max(WS_MIN_PCT, pct));
  }

  /** 迷你趋势高度拖拽（最大化态，px 100–bodyH*0.6） */
  function resizeMiniHeight(px: number, bodyHeight: number) {
    const maxH = Math.max(MINI_MIN_H + 40, bodyHeight * 0.6);
    miniHeightPx.value = Math.round(
      Math.min(maxH, Math.max(MINI_MIN_H, px)),
    );
  }

  function resizeSpine(px: number) {
    spineWidth.value = Math.round(
      Math.min(SPINE_MAX_W, Math.max(SPINE_MIN_W, px)),
    );
  }

  /** 当前剖面元信息 */
  const activeSectionMeta = computed(() =>
    WB360_SECTIONS.find((s) => s.key === activeSection.value) ?? null,
  );

  const state = reactive({
    activeSection,
    miniHeightPx,
    panelState,
    spineWidth,
    wsHeightPct,
  });

  return {
    activeSection,
    activeSectionMeta,
    availableSections,
    closeWs,
    isSectionAvailable,
    openSection,
    readonlyState: readonly(state),
    resizeMiniHeight,
    resizeSpine,
    resizeWsPct,
    setPanel,
  };
}
