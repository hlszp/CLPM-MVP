# TD 测试资源 profile 的非作者投前审阅

2026-10-17。结论：**明确配置 `queryUseMemoryPool=0` 可以作为本轮受控测试资源 profile；这是可执行的方法选择，当前不是容量 PASS，更不是默认配置资源问题已经修复。** 20 批/1000/600s、真实失败持久化、历史6+3、四项用户豁免均不变。本次只读保存件与版本源码，没有连接 TD、改配置、启停服务或投递任务。

## 已直接核对的事实

以下文件均在本轮证据根 `BLOCKED闭环-20261017T103039Z/`：

- `runtime/td-resource-change.json`：原 `queryUseMemoryPool=1/minReservedMemorySize=0`；`ALTER DNODE 1 'minReservedMemorySize' '1024'` 的真实 HTTP=200，但业务 **code=280 / Invalid parameters**。HTTP 成功不能替代 TD 命令成功。
- `runtime/td-resource-after-rejection.json`：后续 SHOW 的登记值已经显示 `minReservedMemorySize=1024`，但 `queryUseMemoryPool=1`。这与上一条 rc/code 必须共同保留，不能只引用 SHOW 宣称内部 pool 更新成功。新 profile 生效证据须重新采集。
- `f02/vendor-exact-tmempoolInt.h:46–48`：默认 reserve=20%；初始化可用内存下限 **5GiB**，扣 reserve 后可用内存下限 **4GiB**。
- `f02/vendor-exact-tmempool.c:1701–1766`：`tsQueryUseMemoryPool=false` 是明确的 `configured disabled` 正常返回路径；内存不足的自动 disabled 是另两条初始化分支。二者原因不能混称。`1784–1794` 的动态 reserve 更新仍检查两个内存门槛。
- `f02/vendor-exact-tglobal.c:895`：`queryUseMemoryPool` 为 `CFG_DYN_NONE`。仅改某个变量或配置文件、没有受控重启/加载证据，不足以认为新 profile 生效。
- `04-验收与容量基线.md` §4.1 要求记录并冻结实际环境/资源/配置，§4.2 要求真实完整屏障、无OOM/连接耗尽/重复Worker、峰值总RSS≤冻结预算80%；未指定必须开启 TD memory pool。

## 可接受的冻结与结果登记

1. 将完整新资源 profile 在第一批前冻结：TD 精确版本/镜像 digest、实际配置原文 SHA、`queryUseMemoryPool=0`、reserve/queryBuffer/singleQueryMaxMemorySize/查询线程与并发的实际值；记录依赖此 profile 的全部 20 批。保留旧 pool=1 失败和 code280/部分更新原件，不删除或改写。
2. Keeper 受控重启后，核实际 dnode 值、对应 PID/启动时刻和 `configured disabled` 启动日志；不以“内存不足自动禁用”充当配置已生效。投前直接查询与完整数据取数正常只是前置，不能替代 20 批容量。
3. 禁用池保护后的内存风险必须进入实际资源冻结：预算覆盖本轮服务，VM 与宿主机内存域分别观察，不重复计算容器与 VM，也不遗漏共享 VM 的真实可用内存/压力变化。预算不能为通过而事后放大；越界/不足/OOM/连接耗尽/重复 Worker 按原标准停止或失败，不继续冲刷结果。不操作其他项目服务或 Docker 全局资源来凑预算。
4. 20 批同一资源 profile、并发、代码、输入和测量标准。真实结果只可表述为“在该受控 profile 上，固定候选完成/未完成原 KPI 容量标准”；**不能扩签 pool=1 默认配置、其它机器或生产容量，也不能把关闭池写成应用资源错误分类 bug 本身已被此设置修好**。
5. 恢复/耗尽重试及真实 FAILED 记录另在专属代理/环境受控验证，标明是受控故障集成；核真实 provider→Worker→父任务/TaskRecord→不可变 FAILED。它不计 20 批容量成功，也不证明真实 TD 内存池自然故障已经恢复。
6. 恢复责任和配方明确保留：旧配置文件/权限/SHA、旧 `queryUseMemoryPool=1`、旧 reserve=0 与失败部分更新后的真实状态、重启与复核步骤由 Keeper 执行。切回旧默认不自动称已复原正常，仍需核有效值和只读健康/取数结果。此次新 profile 属测试环境参数选择，**不是第五项用户豁免**。

上述条件未完成时，F02 整体仍 **BLOCKED**。若 profile 漂移、真实资源/时间/终态不符合原标准，按 REWORK 留原始反证；不在本次投前审阅先签 PASS。
