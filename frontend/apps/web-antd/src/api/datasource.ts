/**
 * CLPM 数据源配置 API — 对接外部历史数据 API + 实时 SignalR Hub.
 *
 * 对齐 docs/设计文档/05-IDS/HisDATA_API.md 与 RealDATA_API.md。
 */
import { requestClient } from '#/api/request';

export namespace DataSourceApi {
  /** 数据源类型（保留兼容，固定 remote_api） */
  export type DataSourceType = 'remote_api' | 'tdengine';

  /** 网络模式：lan 局域网直连 / wan 公网走 Tailscale */
  export type NetworkMode = 'lan' | 'wan';

  /** Tailscale 切换结果 */
  export interface TailscaleSwitchResult {
    /** 状态：success 成功 / failed 失败 / skipped 跳过（容器环境） */
    status: 'failed' | 'skipped' | 'success';
    /** 提示消息 */
    message: string;
    /** 耗时（毫秒），skipped 时为 null */
    latencyMs: null | number;
    /** 切换失败时已回滚 sys_config networkMode（仅 failed 时出现） */
    rolledBack?: boolean;
  }

  /** 数据源配置信息 */
  export interface DataSourceConfig {
    /** 历史数据源类型（保留字段，固定 remote_api） */
    dataSourceType: DataSourceType;
    /** 网络模式：lan 局域网直连 / wan 公网走 Tailscale */
    networkMode: NetworkMode;
    /** 外部历史数据 API 地址 */
    historyApiUrl: null | string;
    /** 外部历史数据 API 鉴权 Token（打码返回，保留前后各 4 位，不可用于真实调用） */
    historyApiToken: null | string;
    /** 外部历史数据 API 超时（秒） */
    historyApiTimeout: number;
    /** 实时数据 SignalR Hub URL */
    signalrHubUrl: null | string;
    /** 是否启用实时数据订阅 */
    signalrEnabled: boolean;
    /** SignalR 断线重连间隔（秒） */
    signalrReconnectInterval: number;
    /** 是否将实时数据写回本地 TDengine 宽表（仅 tdengine 模式生效） */
    realtimeWritebackEnabled: boolean;
    /** 当前生效的历史数据 Provider（启动时初始化，UI 用于提示"需重启生效"） */
    historyProviderActive: string;
    /** 实时订阅器真实运行状态（非配置镜像；启停变更需重启后端生效，UI 用于提示"需重启生效"） */
    signalrSubscriberRunning: boolean;
    /** tailscale 客户端是否可用（容器内为 false） */
    tailscaleAvailable: boolean;
    /** Tailscale 切换结果（仅 networkMode 变化时返回，GET 时为 null） */
    tailscaleSwitch: null | TailscaleSwitchResult;
  }

  /** 更新数据源配置参数（所有字段可选；不传=不变，字符串字段传空串=显式清空） */
  export interface DataSourceConfigUpdate {
    /** 已废弃，保留兼容（后端固定 remote_api） */
    dataSourceType?: DataSourceType;
    /** 网络模式：lan 局域网直连 / wan 公网走 Tailscale */
    networkMode?: NetworkMode;
    /** 不传=不变，空串=清空 */
    historyApiUrl?: string;
    /** 不传=不变，空串=清空，打码值回传=后端忽略 */
    historyApiToken?: string;
    historyApiTimeout?: number;
    signalrHubUrl?: string;
    signalrEnabled?: boolean;
    signalrReconnectInterval?: number;
    realtimeWritebackEnabled?: boolean;
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
  return requestClient.get<DataSourceApi.DataSourceConfig>(
    '/datasource/config',
  );
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
