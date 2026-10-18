# F03 业务 CI 前置与执行方案

角色：F03作者准备与排障；主协调为唯一Keeper/提交推送人；F04作者负责非作者验收。本轮目标为真实AUTH/PERM/CALC/EVIDENCE四例一次通过、零skip/retry/flaky及可追溯远端CI，四项用户豁免不变，不操作生产。

候选起点：`/Users/zhangping/DEV/clpm-remediation-20261017`，`dbb1de31`加上一轮36文件。新前置修复完成后必须重新冻结源码/补丁与真实提交SHA，不能沿用旧`c5baf0fd…`代表新内容。

## 已确认前置与待修复

| 项 | 当前事实 / 处理 |
|---|---|
| 自动部署 | 仓库仅`.github/workflows/ci.yml`；push main/develop、PR目标main/develop触发，权限contents:read，无deploy/ssh/scp/production environment或workflow_run链。候选分支推送本身不触发本CI；主协调创建目标main的候选PR触发，不合main借触发CI。 |
| 独立依赖 | frontend以pnpm锁安装，兄弟e2e独立`npm ci`；Playwright从e2e安装chromium。真实四例不使用frontend的冒烟case替代。 |
| PG/种子 | 任务专属Postgres16、`clpm_critical_e2e`；沿真实`apply_bootstrap_sql.py`载01/02，stamp当前head。admin/sponsor种子明确admin123，角色权限真实；CI仅用私有服务。 |
| 确定缺陷1 | `common.success`和`ApiResponse.code`均为字符串`"0"`；当前业务spec四处断言整数0会误拒真实成功。改为严格字符串0，不兼容其他业务码。修前用真实success/响应模型与同一Playwright断言构造只读反例。 |
| 确定缺陷2 | 种子SignalR启用且地址192.168.100.2，AAS sync默认启用；原工作流直接启动lifespan可能外连。必须在API/Worker/Beat启动前，通过同一CI专库guard将AAS/SignalR/外部历史/LLM置禁用或本地空端点，并断言生效；只改CI库，不改产品默认配置。 |
| 真实计算生命周期 | `ENV=development`、`CLPM_TEST_MODE=0`；API真实lifespan自动启动一个Worker主进程（4池）与Beat，禁止手工再启动。连接只到本job专属PG/Redis/TD。后端健康不能证明Worker已消费，CALC必须终态SUCCESS且1/1。 |
| TD输入 | 专属TDengine3.3.6.6，6030/6041，`clpm_critical_e2e_ts`；真实点表schema，已有确定性generate_capacity_dataset输入函数与导入API，一回路七角色/一小时输入。仅写合成输入，不seed任何KPI/record/snapshot。 |
| 路径/字段 | 登录`/auth/login`，组态`/config/loop`；SPONSOR计算403、工作台拒绝；custom evaluate→tasks loopsTotal/loopsDone→snapshots taskId/loopId/latestOnly→result-record，UI`/metric/loop-evaluation?view=history&taskId=`消费任务过滤并显示dataBlock血缘。继续逐字段复核，确切新缺陷在作者归属范围修复。 |
| 保全 | 每个服务启动/造数/gate真实rc、fixture/import manifest SHA、候选SHA、Playwright collected/executed JSON、trace/截图及task→snapshot→record→dataset/config附件必须上传，即使失败。补保全Worker/Beat日志、PID/父子结构与前端日志。 |

批准修改范围（主协调已确认）：`.github/workflows/ci.yml`、`e2e/tests/critical-business.spec.ts`、`e2e/scripts/prepare-critical-fixture.py`及其相邻无业务副作用测试/运行工具；必要时原门禁脚本。禁止修改业务算法、放宽四例、skip、响应mock或借健康桩签业务通过。

## 执行顺序与停点

1. 作者完成精确修复与修前失败/修后通过的无写入验证、fixture专库拒绝、类型检查、原门禁8类正反例；冻结所有F03文件SHA，给主协调可提交清单。作者不提交/启服务。
2. 主协调按逻辑提交/推送GitHub候选、创建目标main的PR；记录head SHA与PR merge SHA（Actions默认测试merge ref），两者不能混写。每次失败保全完整run/job/step/artifact再定因，不重写旧证据。
3. 正常链必须四个固定ID各一次passed、零skip/retry/flaky；CALC真实计算完整1/1、指标数值有效、原record/data/config血缘相符、UI看到同任务同回路同证据。远端启动/数据库/TD/浏览器错误均照实失败，不能以本地契约通过覆盖。
4. F04非作者核原始输入/执行分母/退出码/哈希、真实trace与血缘，独立反证门禁仍拒skip/失败。未验或失败不合main；只有完整门槛通过才由主协调合入。

## 本地专属 CI 复现（只供Keeper执行，作者本轮不启停）

用途为提前复现相同业务链，**不是远端CI结果**。主协调从实际冻结提交建立独占目录（不能与F02运行树同basename），单独Docker project和可回收PG/Redis/TD容器，不借共享dev数据/队列。资源预算与F02容量运行错开；唯一Keeper先检查端口和进程，保留创建/清理清单。

建议独占宿主端口：PG17432、Redis17389、TD原生17330/REST17341、API17701、UI17666；只绑定127.0.0.1，内部库名仍为`clpm_critical_e2e`/`clpm_critical_e2e_ts`。这些是候选值，须Keeper只读探活确认，冲突则整体重新冻结。

按远端同顺序执行：同步锁依赖→01/02引导/stamp/check→TD建库→CI专库外联禁用配置→真实API lifesp​an→实际Worker/Beat父子结构及专属broker核验→前端以`http://localhost:17701/api/v1`构建并preview17666→合成输入prepare→四例gate→保全→关闭自有API、等待其生命周期清Worker/Beat→关闭UI/自有服务及整库清理。禁止另起手工Worker。

本地复现的CI上下文变量必须显式记录为本地仿CI：CI/GITHUB_ACTIONS的上下文模拟不充当GitHub运行证明，GITHUB_SHA必须为真实冻结提交，run/attempt为专属数字标识，另存executionLocation=local-disposable-ci。所有PG/Redis/TD host为loopback且专库guard保持；更改端口只通过环境变量，调用prepare的`--api-url http://localhost:17701/api/v1`，gate使用E2E_BASE_URL/E2E_API_BASE_URL对应端口。最终GitHub结论仍必须来自真实远端run。

当前状态：前置复核及两处确定修复进行中；没有启动任何服务/容器、业务写入或GitHub运行。实现冻结后追加精确命令与源码清单，文档本身不签业务PASS。
