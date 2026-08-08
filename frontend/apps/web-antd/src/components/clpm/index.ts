export { default as ClpmAiDrawer } from './ai-drawer.vue';
export { default as ClpmBulletChart } from './bullet-chart.vue';
export { default as ClpmAiInsight } from './ai-insight.vue';
export { default as ClpmAlertDslEditor } from './alert-dsl-editor.vue';
export { default as ClpmModal } from './clpm-modal.vue';
export { default as ClpmColumnSettings } from './column-settings.vue';
export { default as ClpmConfidenceBadge } from './confidence-badge.vue';
export { default as ClpmDangerConfirmModal } from './danger-confirm-modal.vue';
export { default as ClpmDataCanvas } from './data-canvas.vue';
export { default as ClpmDataHealthBadges } from './data-health-badges.vue';
export { default as ClpmDispositionTimeline } from './disposition-timeline.vue';
export type { TimelineEvent as ClpmTimelineEvent } from './disposition-timeline.vue';
export { default as ClpmEmptyState } from './empty-state.vue';
export { default as ClpmImplementRecordModal } from './implement-record-modal.vue';
export type { ImplementSubmitData as ClpmImplementSubmitData } from './implement-record-modal.vue';
export { default as ClpmInfoTip } from './info-tip.vue';
export { default as ClpmInterpretationPanel } from './interpretation-panel.vue';
export { default as ClpmKpiCard } from './kpi-card.vue';
export { default as ClpmKpiStrip } from './kpi-strip.vue';
export type { KpiStripItem } from './kpi-strip.vue';
export { default as ClpmLoopContextHeader } from './loop-context-header.vue';
export { default as ClpmLoopLink } from './loop-link.vue';
export { default as ClpmNumeric } from './numeric.vue';
export { default as ClpmObjectSummaryBar } from './object-summary-bar.vue';
export type { SummaryAction, SummaryItem } from './object-summary-bar.vue';
export { default as ClpmOnboardingTour } from './onboarding-tour.vue';
export { default as ClpmPageToolbar } from './page-toolbar.vue';
export { default as ClpmPredictionCard } from './prediction-card.vue';
export { default as ClpmRealtimeStatus } from './realtime-status.vue';
export { default as ClpmSeverityBadge } from './severity-badge.vue';
export { default as ClpmStandardActions } from './standard-actions.vue';
export { default as ClpmStateOverlay } from './state-overlay.vue';
export { default as ClpmStatusPanel } from './status-panel.vue';
export { default as ClpmStructuredDiagnosisReport } from './structured-diagnosis-report.vue';
export { default as ClpmTagAssociationBadge } from './tag-association-badge.vue';

export { default as ClpmThresholdTuneModal } from './threshold-tune-modal.vue';
export { default as ClpmToolbarButton } from './toolbar-button.vue';
export {
  TOOLBAR_DEFAULT_VARIANT,
  TOOLBAR_ICON_COLOR,
  TOOLBAR_ICON_MAP,
  TOOLBAR_STATE_COLOR,
  type ToolbarAction,
  type ToolbarVariant,
} from './toolbar-config';

export { default as ClpmToolbarDivider } from './toolbar-divider.vue';

/**
 * UI-05 ClpmTable 表格规范封装（v6.1 §7.16 / §15.2 UI-05）
 *
 * 不新建组件，而是基于 Ant Design Table + 工业风格 utility class 提供规范：
 *
 * 1. 表格容器：使用 <ClpmDataCanvas> 或直接 <a-table>
 * 2. 等宽数字列：在 column 定义中设置 `class: 'clpm-num'`，或单元格使用 <ClpmNumeric>
 * 3. 行内进度条：使用 `.clpm-row-progress` + `.clpm-row-progress__track` + `.clpm-row-progress__fill[--ok|--warning|--error]`
 * 4. hover reveal 次要操作：将次要操作包在 `<span class="clpm-row-actions">` 中
 * 5. 状态标签：使用 <a-tag> + useIndustrialStatus() 提供的 meta.color/bgColor
 * 6. 列设置：<ClpmColumnSettings> 已存在
 * 7. 密度切换：根容器添加 `.clpm-density-touch` 切换为触控密度（44px）
 *
 * 详见 `docs/设计文档/06-UIUX/ui-ux-design-guidelines.md` §7.16 + §15.2 UI-05
 */
export const CLPM_TABLE_GUIDE = {
  numericClass: 'clpm-num',
  rowActionsClass: 'clpm-row-actions',
  rowProgressClass: 'clpm-row-progress',
  densityTouchClass: 'clpm-density-touch',
  borderDefaultClass: 'clpm-border-default',
  radiusIndustrialClass: 'clpm-radius-industrial',
} as const;
