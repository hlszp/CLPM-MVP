# 受控 TD REST 代理移交

工具只服务本轮专属测试资源错误取证，不在正式20批中注错，不改候选源码/算法/expected/分母。实际启动17125、切应用env、ARM/DISARM、任务投递和恢复均由唯一 Keeper 执行。本代理只启动过自己的临时127.0.0.1:0 stub和代理，自验后均已停止；真实TD/API/Worker/Beat未由本代理启停。

## 冻结与自验

`td-proxy-source-sha256-v1.json`：

- `controlled_td_rest_proxy.py`：b36666a90663499339625f01d0179d32340d5dfcf03a82eb6b80b834cec042d0。
- `test_controlled_td_rest_proxy.py`：159fdb71c815c585f4a4da7ff85831bbe0831820053eb40c632af36e122e70a5。

作者 `td-proxy-selftest-frozen.*` 33passed/0失败/真实rc0，ruff原件 `td-proxy-ruff-frozen.*` rc0。包括真临时CLI进程启动/单请求/自身SIGTERM/退出0/清理、DISARMED真透传、前2错后真上游恢复、并发计数严格2、持续12次仍故障不自行解除、手动新epoch解除、错误scope/SQL/窗/默认未serve拒绝、上游真错误保原、Authorization和credential SQL/响应不落秘密原文。

较早v2自验保留真实1failed/32passed/rc1：临时HTTP默认backlog5在并发连接自验产生ECONNRESET，已明确提升本工具/自有stub backlog128并复跑；未删除该失败。最终本工具与产品源码无关，不能用这些33例代签真实1/1000故障持久化或20批容量。

## Keeper执行前

1. 等最终候选SHA、20正式窗输入审查完成并冻结。只读从PG实际映射取PERF-T0004/PV UUID，核确是FLOW、真实BASE≤1s的HF复用计划；品质/输入不能凭标签猜。若多个tagGroup仍重复PV读，前2次HTTP错误不等于2个local attempts，必须按实际日志重算，不能接受“恢复”口头解释。
2. 复制 `td-proxy-policy.template.json`为Keeper自有policy.json，填实际PV UUID和最终40hex。模板两个故障窗预定2026-10-16T08/09Z（在正式20窗最后Oct16T07之后，又是当前过去窗，避免旧latest迟到）；Keeper确认/生成这两个窗真实输入再冻结。两个窗严格不计正常20批，也不得与其计划重叠。
3. policy.normalPlan.path/sha256指向真实完整20窗计划。policy文件必须显式 `armed:false`，启动拒绝ARMED。候选SHA只是声明，仍须另核真实已加载进程。
4. 由Keeper在新输出目录启动：`python controlled_td_rest_proxy.py --serve --policy <policy.json绝对路径> --out <新代理原件目录> --listen-port 17125 --upstream http://127.0.0.1:17115/rest/sql`。无 `--serve`不会监听。只允许127.0.0.1监听及无口令loopback上游URL。
5. Keeper读 `http://127.0.0.1:17125/__audit_proxy_health`，确认READY/armed=false/正确epoch/inFlight0；以DISARMED读取SHOW或受控目标SELECT，核原上游响应与日志一致。只有Keeper切该测试runtime TDENGINE_PORT=17114（客户端REST=+11→17125）；真实上游固定17115，不写生产或其他容器。原env保护备份不复制口令到证据。应用模块在导入时缓存REST端口，切env后须按统一lifespan受控重启，不能以reload假装Worker已更新。
6. 每次policy变更用**全新epoch+原子replace**，不复用旧epoch/原字节覆盖；源hash和每个epoch原件会存档。启动已经DISARMED后，Keeper才将armed=true并选择recovery或exhaustion。同一epoch不得改变字节，否则HTTP503/POLICY_INVALID/transportRc78明确拒绝；不能默默转成成功。未变epoch累计计数保留，不因并发重置。

## 精确命中与真实证据

只匹配实际point单点events SQL全形态：SELECT point_id,ts,`value`,quality_class FROM clpm_capacity_20261017_s01.st_point_data_v1 WHERE point_id IN ('冻结PV UUID') AND ts >= '冻结start' AND ts <= '冻结end' ORDER BY ts ASC LIMIT 正整数。数据库、PV、窗均逐项比冻结policy；不猜初值查询、其他点/多点、count/SHOW/DDL/半开上界、其它表/附加SQL。非命中请求真实透传；认证头仅真实转发，不落日志。输入源审查的半开窗与业务events闭窗是不同合同，此代理沿业务真实闭窗。

- recovery仅前2个**匹配请求**返回HTTP200原错误形状 `{"code":1850,"desc":"Query memory exhausted"}`，第3及以后真实透传。实际产品local attempt需原日志另核。
- exhaustion持续所有匹配错误，**不会**在9/12或任何次数自动解除。Keeper必须等真实Celery父/TaskRecord终末FAILED、attempt1/2/3、本地重试与最终单条FAILED record原件齐全才切新epoch DISARMED。累计HTTP错误数不能代父/本地attempt数。
- `requests.jsonl`每请求seq/开始结束UTC/policyepoch+hash/SQLhash/匹配原因/序号/注入或透传/HTTPstatus/transportRc/TDcode/原响应hash；匹配target额外保存原SQL。原响应内容寻址gzip保存，rawSha+gzipSha可恢复；原SQL带credential关键字时不存SQL/响应正文，只留hash并明确redacted，绝不保存Authorization值或policy秘密。
- transportRc0只说明HTTP传输已完成，TDcode1850仍是业务失败；70为真实上游传输错误/HTTP502，78为policy无效/HTTP503。真实进程退出rc由Keeper命令driver保存，不把这些传输分类当shell退出码。
- 每个policy epoch原字节、startup/shutdown源SHA/时刻/计数/inFlight均保存。对代理的actualrequests与产品local retry/Celery events/attempt/immutable record全链交叉，不用代理自报匹配替代产品闭环。

结束时Keeper先新epoch DISARMED，核真实目标透传恢复；等health.inFlight=0且业务任务/队列收束，再停止代理。保存shutdown.inFlightAtStop、进程真实rc、所有文件SHA，恢复测试env/原profile/原运行树和服务；原资源错误、正常20批或失败series一律保留。只有独立非作者实际核过此链才可收F02故障持久化，不由作者33例代签。

