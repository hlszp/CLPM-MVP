# F02 采证工具 v3 · 非作者投前回执

2026-10-17。**PASS，仅限采证接口/离线工具契约；真实7000点输入、真实20批容量、资源恢复/耗尽/FAILED持久化和独立业务复跑仍未由本回执验收。** 复验人不是F02作者，没有改作者工具/候选源码，没有连接DB/TD/Redis或启动/投递业务。

## 实际执行及反向证据

- 初版audit SHA `437c96db4887e137dc4dd957e5abff4bb41f18e2391d32aafd633bdae9904357`存在两个可复现问题：manifest重复一行成为1001却接受；insufficient PV全GOOD却接受。`counterexamples-v1.json`记录真实函数返回，原源保存在`sources-v1/`。
- 固定非作者反例`test_input_contract_independent.py`使用自建1000回路/7000点形状；合法A及完整B(800/100/50/50)控制均先通过，B的100个insufficient seed使用独立RNG顺序恢复品质直方图。再分别增加重复manifest行、仅把第801回路PV替为3600GOOD/0BAD，原判据不变。
- **同一反例文件**对旧437c96源：2 failed / rc=1，`reverse-v1-stronger.{command.json,stdout.log,stderr.log}`；对新audit SHA `8bd8cb604032d85948cc6bc1f429974b93424e3ab1f7648af62cebcc2e1a3b6d`：**2 passed / rc=0**，`forward-audit-v2.*`。不是依靠非法A/B配比的附带拒绝来冒充质量直方图已核。
- 作者v3 7文件全部重算匹配`evidence-tools-source-sha256-v3.json`，前后相同，保存独立源快照`sources-v3/`；`tools-v3-source-check.json`。
- 从该固定快照独立执行两份实际工具测试文件：**16 passed / 0 failed / rc=0**，`tools-v3-independent.{command.json,stdout.log,stderr.log}`。没有只照抄作者16/0。

新audit按原生成器seed/场景恢复数量与PV品质直方图，不降低生产门槛、不改变expected；拒绝manifest行/unique不齐、重复loop-role及7000点不齐、错场景/seed、低报点数和TD资源异常。输入summary采用实际私库且双时间界限。summary/raw samples明确不是全7000点逐值hash，没有因此宣称整窗原始数值已全部归档。

## 采集接口与执行约束

collector固定SHA `c8cb9cdf6682c2c62392adf1f7ee155cb9c672d160e1da296c554aca9f4ae820`：每轮关键PG行在同一RR/RO事务，完整对象数及窗口/关联列可独立核；JSONB经保存后只在无损适配中解析，不能当缺字段。NODE输入`payload_loop_record_ids`、snake `fitness_tags`、`snapshotStatus`选择已纠正；最终full immutable payload可按**相同recordId、关键列一致、创建不晚于见证**补全文，不能用后来的节点/latest投影替换较早poll。

3容器cgroup命令原始argv/rc/stdout保全，memory.current/stat/events直接读字节；anon/shmem分别保存，没有自动相加重复。私有event Receiver是被动观察且wakeup=false；快子任务不能只靠2s inspect。日志保存inode/offset/hash/pendingBytes，gzip对象原文/压缩hash均可恢复核对。

这些接口可用于正式采集，但Keeper必须在实际证据中证明：

- env精确绑定独占PG/TD与broker17123；名字前缀不是正确broker端点的自动证明。工具只读接口不授权复验人接环境。
- --containers实际含完整PG/TD/私Redis三容器；应用PID集合覆盖API/master/全部pool/Beat。按runtime字符串筛选的列表必须与真实PID链核对，空列表或漏一个子进程不能继续宣称组RSS齐全。
- memory.current是charge，anon/shmem是分项；预算公式投前冻结，避免相互叠加或实跑后切换较小指标。原始CPU/IO/连接/VMavailable/压力及OOM事件不由某个RSS摘要替代。
- 每批前≥60s、实际间隔≤5s、屏障后≥30s；超时/失采/日志pendingBytes未收束明确不齐，不以COLLECTED或SMOKE_ONLY的rc0推导业务PASS。
- 真实源、四维缓存态、正式TRACE0、父子与全部1000明细、原始资源故障/取消/终末FAILED、至少一组独立重跑，继续按冻结协议验。源或profile变更先另冻，不拼绿series。

当前没有由本回执将F02整体BLOCKED改为PASS，也没有扩签D04、生产资源能力或四项用户豁免。
