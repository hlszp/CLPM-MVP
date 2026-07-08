/**
 * CLPM 数据源配置 API — 对接外部历史数据 API + 实时 SignalR Hub.
 *
 * 对齐 docs/设计文档/05-IDS/HisDATA_API.md 与 RealDATA_API.md。
 */
import { requestClient } from '#/api/request';

export namespace DataSourceApi {
  /** 数据源类型 */
  export type DataSourceType = 'remote_api' | 'tdengine';

  /** 数据源配置信息 */
  export interface DataSourceConfig {
    /** 历史数据源类型 */
    dataSourceType: DataSourceType;
    /** 外部历史数据 API 地址 */
    historyApiUrl: null | string;
    /** 外部历史数据 API 鉴权 Token */
    historyApiToken: null | string;
    /** 外部历史数据 API 超时（秒） */
    historyApiTimeout: number;
    /** 实时数据 SignalR Hub URL */
    signalrHubUrl: null | string;
    /** 是否启用实时数据订阅 */
    signalrEnabled: boolean;
    /** SignalR 断线重连间隔（秒） */
    signalrReconnectInterval: number;
    /** 当前生效的历史数据 Provider（启动时初始化，UI 用于提示"需重启生效"） */
    historyProviderActive: string;
    /** 实时订阅器是否在运行（启动时初始化，UI 用于提示"需重启生效"） */
    signalrSubscriberRunning: boolean;
  }

  /** 更新数据源配置参数（所有字段可选） */
  export interface DataSourceConfigUpdate {
    dataSourceType?: DataSourceType;
    historyApiUrl?: string;
    historyApiToken?: string;
    historyApiTimeout?: number;
    signalrHubUrl?: string;
    signalrEnabled?: boolean;
    signalrReconnectInterval?: number;
  }

  /** 连通性测试结果 */
  export interface TestResult {
    success: boolean;
    latencyMs: null | number;
    message: string;
  }
}

/** 获取数据源配置 */
export function getDatasourceConfigApi() {
  return requestClient.get<DataSourceApi.DataSourceConfig>('/datasource/config');
}

/** 更新数据源配置 */
export function updateDatasourceConfigApi(
  data: DataSourceApi.DataSourceConfigUpdate,
) {
  return requestClient.put<DataSourceApi.DataSourceConfig>(
    '/datasource/config',
    data,
  );
}

/** 测试历史数据 API 连通性 */
export function testHistoryApiApi() {
  return requestClient.post<DataSourceApi.TestResult>(
    '/datasource/test-history-api',
  );
}

/** 测试 SignalR Hub 连通性 */
export function testSignalrApi() {
  return requestClient.post<DataSourceApi.TestResult>(
    '/datasource/test-signalr',
  );
}
