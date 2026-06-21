/**
 * Mock 数据层统一导出
 *
 * 所有 mock 数据从此文件统一导入，便于将来切换到真实 API 时
 * 只需替换此文件的实现（改为 fetch 调用），各页面代码无需改动。
 */

export * from './types';
export * from './plantNodes';
export * from './aasTags';
export * from './loops';
export * from './kpi';
export * from './diagnosis';
export * from './tracker';
export * from './timeseries';
export * from './users';
