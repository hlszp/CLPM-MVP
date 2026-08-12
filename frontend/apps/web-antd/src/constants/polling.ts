/**
 * 轮询间隔常量（P3-12）
 *
 * 集中管理各业务页轮询间隔，消除散落在页面内的魔法数字，便于统一调优。
 * 选用原则：
 * - 活跃任务轮询（PENDING/RUNNING）需要较快反馈，5s 平衡时效与后端压力；
 * - 徽章/计数刷新属辅助信息，30s 即可；
 * - 报表/导出等异步任务进度可更短（3s）以提升感知流畅度；
 * - 监控类实时刷新由用户在页面自行配置（loop/monitor），不在此约束。
 *
 * 修改历史：
 * - 2026-08-10 抽取自各页硬编码值（task/list、metric/recompute、diagnosis/tasks 等）
 */

/** 活跃任务轮询间隔（任务列表/详情中 PENDING/RUNNING 状态刷新） */
export const TASK_POLLING_INTERVAL = 5000;

/** 徽章计数自动刷新间隔（Tab Badge、通知计数等辅助信息） */
export const BADGE_REFRESH_INTERVAL = 30_000;

/** 异步任务进度轮询间隔（报表生成、重算进度等需较快反馈场景） */
export const PROGRESS_POLLING_INTERVAL = 3000;
