# 协议方法补充 04：事件 PID 与闭合进程集合

正式容量投递前补充字段来源。原协议与四份初始工具源不改；20×1000、完整九条件、600s、原30s、历史6+3、四项用户豁免不变。本补充不证明任何任务已执行或通过。

## 原字段与未知执行者

Celery 的 `task-started` 事件在 worker request 接收确认路径发送。实际安装版本的 `worker/request.py` 与 `events/dispatcher.py` 必须作为原件保全：dispatcher 附加的 `pid` 可为事件生产者 master PID，不能直接当作 prefork 执行池 PID。

sidecar 保留完整原事件（包括原 `pid`、hostname、timestamp、type、UUID、parent/root 等实际存在字段）及原文件 SHA。可以另标 `producerPid` 并注明其来源；没有真实执行者证据时，`executorPid` 为 NULL/unknown，禁止填入任一 pool PID。若原始 backend STARTED 状态实际保全了 `result.pid`，须核其真实来源/时点/任务UUID和进程归属后单独补强，不得从终态结果或 producer PID 倒填。

## 有条件推导运行代码身份

规范任务 `runtimeSha` 可以标为 `runtimeShaProvenance.kind=derivedFromClosedWorkerPoolIdentity`，仅在以下条件全部有原件时成立：

1. 实际事件 producer 对应本专属环境唯一 worker master；hostname、完整 request/事件UUID、parent/root、同窗、接收与终态关联均成立。没有旁路 worker、第二 master 或其他消费方。
2. 当时唯一 master 的全部四个实际 pool 子进程都在完整进程清单中；PID、PPID、原生启动时间、argv/cwd/解释器与各自唯一 identity 真实对应。池集合是封闭的，不因仅观察到部分子进程而假定其余不存在。
3. master 与全部 pool 均由实际运行 M 的原始源码/构建/配置启动：核 M 的 tree/完整tracked字节目录、实际安装运行文件、启动原件和父子关系。只写环境 `CLPM_BUILD_CODE_SHA=M` 或 sidecar `loadedSha=M` 不够。候选 head、PR merge checkout M、后续文档head/main/部署SHA分别记录。
4. 每个相关任务从接收至终态的全区间均有原始进程/容器与资源样本，采样严格大于0且≤5s；固定 master/四pool身份无缺口、重启、重载或漂移；隔离环境只有唯一 Keeper 的已记载操作，没有在启动后替换源码/配置。若有进程替换，不能用新旧拼接一组“同样四个”自动推出该任务身份。

这只能推出“所有可能执行者的运行代码均为 M”，不能推出“某一个具体 pool 执行了该任务”。`executorPid` 仍保留 unknown，除非另有上述真实执行者原件。`runtimeShaProvenance` 必须逐项引用原进程/采样/启动/代码目录/事件与实际 Celery 源版本的 SHA，而不是把推导结论冒充原事件字段。

任一条件缺失或生产者不能对应、存在额外消费者、池身份变化/采样缺口，均 BLOCKED/REWORK。不得靠“task SUCCESS”、队列空、四个 PID 数量或单份当前 `ps` 补真相；该推导也不替代 LOOP/NODE 不可变记录、完整节点作用域与 SLO 的独立核验。
