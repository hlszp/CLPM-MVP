# 数据架构决策记录：导入走远端、计算全本地

| 项目 | 说明 |
|------|------|
| 日期 | 2026-07-20 |
| 决策人 | 用户（项目负责人）定调，zp 机器（Claude Code）实施 |
| 状态 | 已生效（PR #83 / #84 合入 main） |
| 关联 | AGENTS.md §核心决策「数据架构」行；README §7 |

## 1. 决策内容

**导入走远端、计算全本地。**

1. **历史数据两个源，用途严格分离**：
   - 远端 AAS 历史数据接口（remote_api，如 `http://192.168.100.2:81/api/services/v1/HistoryData/Get`）：**有且仅有**「数据管理 → 历史数据导入」手工任务可调用（`backend/app/services/data_import.py` 独立 HTTP 客户端，不经 provider 工厂），用于把远端数据补齐到本地 TDengine。
   - 本地 TDengine：所有性能评估、回路诊断、回路整定等计算任务的**唯一**历史数据来源。
2. **禁止自动降级**：任何计算任务不得自动降级或切换到远端 API 取数；本地数据不完整时按 INCONCLUSIVE / 数据不足提示，由用户通过导入任务补齐。
3. **实时数据源唯一**：SignalR Hub（`signalr_hub_url`，sys_config 管理），开发与生产一致。不存在"远端/本地"两种实时源。
4. **链路 ≠ 数据源**：链路配置页的局域网/公网切换只切换网络链路（Tailscale 子网路由转发），不改变任何数据源的选择；公网/局域网是同一组数据接口的两条网络路径。
5. `DATA_SOURCE_TYPE` 环境变量**废止**（仅作配置兼容保留，不再影响计算路径）。

## 2. 背景与动机

- 2026-07-19 事故：`DATA_SOURCE_TYPE=remote_api` 时 DataPlanner 用无界 `asyncio.gather` 并发查询远端边缘 API，回填时 8 worker × ~54 并发可达 400+ 同时请求，压垮 `192.168.100.2:81`（TCP 可连、HTTP 无应答约 16 小时）。
- 远端挂死期间，remote_api 模式下回填全部空数据块 → 快照全 INCONCLUSIVE/PARTIAL → 装置性能页 TOP5/等级饼图/评级空白。
- 事故表明"计算直接依赖远端在线"是脆弱架构；用户明确目标形态为"导入同步远端 → 计算全本地"。

## 3. 已实施的改动

| 层 | 改动 | PR |
|---|---|---|
| 代码 | `get_provider()` 恒返回本地 TDengineProvider；`DATA_SOURCE_TYPE` 标记废止；TDengine 密码校验/健康检查不再按模式跳过；`deploy.sh`/`rollback.sh` 恒启用 `--profile tdengine` | #83 |
| 保护 | RemoteApiProvider 限流（默认 4 并发）+ 熔断（连续失败 5 次熔断 300s）；SignalR 重连指数退避 5s→30s | #80 |
| 文档 | AGENTS.md 核心决策新增「数据架构」行 + 网络模式行澄清；README §7 重写；链路配置页卡片改名"历史数据导入接口（仅导入时调用）"；DEPLOY-GUIDE/.env.prod.example 部署口径 | #83 #84 |
| 数据验证 | sys_config 保持 `remote_api` 不变，回填仍从本地 TDengine 产出 336 个 SUCCESS 快照，缺口窗口如实 INCONCLUSIVE | — |

## 4. 对各方的影响

- **计算任务（KPI/回填/诊断/趋势/整定）**：只读本地 TDengine（近 1 小时窗口优先 Redis `realtime:history:*` 滚动缓存）；数据缺口窗口状态为 INCONCLUSIVE，不报错、不降级。
- **历史数据导入**：行为不变（直接调远端接口），保留 PR #74 的 chunk 重试 + 降并发韧性。
- **现场运维**：数据页面出现"数据不足"时，含义从"远端挂了"变为"该窗口未导入"——到数据管理页执行导入即可补齐。
- **新员工/下游**：实时数据永远来自 SignalR Hub；历史数据永远在本地 TDengine；远端接口只在导入时被调用。

## 5. 遗留事项

- LaunchAgent 残留 `~/Library/LaunchAgents/com.clpm.realtime-simulator.plist`（zp 机器本机，反复尝试启动已删除脚本刷错误日志）——待用户确认后清理。
- sys_config `datasource.type` 字段成为无害遗留（UI 不暴露、计算不读取）。
- KPI A/B 对比接口（`diagnosis.py:288`）仍返回 501，按 README 列为 P1 待实现。
