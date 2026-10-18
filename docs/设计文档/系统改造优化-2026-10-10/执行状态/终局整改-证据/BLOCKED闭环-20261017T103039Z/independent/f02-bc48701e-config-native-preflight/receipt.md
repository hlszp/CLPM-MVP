# bc48701e 配置与有限原生前缀非作者回执

**PASS，限本配置原件和已完成的 128 poll 投前前缀。** 不签 formalReady、整轮 CI/G3、未来 native series、容量或生产。本次新实时 DB/API/Docker/ps/lsof 调用均为 **0**；只运行指定原独立工具的 Git/文件检查及自有纯文件比较。

## 配置门

`configuration.command.json` 原命令真实 rc0，`configuration-independent.json` 为原独立 `verify_configuration_freeze_raw.py` 的完整结果。源原件不改：

- M：`bc48701efe90e2a27555a4f8794f0965e82b0902`；status **FROZEN**、revision **3**，digest `374f9964e62d6ae784403e664861c940a74df63b1c819c317a1a3af380eed2c9`。
- config-freeze.json SHA `29b4723a1bc49da34fc16619e056961c5f13513efefeb7f6f0bad13c09168c1c`；原 manifest SHA `e4f0adcacf9f0d2f8e06227b1522d4d91cf19f5f3b1a87989332fe92c9e28592`，137 项全核。
- 原 before/after 的 25 表 raw/canonical 全量在案；两次 RR READ ONLY 身份均专库 `clpm_capacity_20261017_s01`，MVCC 均 `1051340:1051340:`。API 原响应、revision、全局参数与 PG 前后完整表原件经原独立工具重建，未把摘要替代 after 全文。
- 实测 1000 loops / 7000 mappings / 20 nodeScopes；36 组全局 metric/control，**36000** 组每回路有效参数逐组重算。1000 回路控制类型 STABLE500/FAST250/SLOW250，版本 KPI_CALC_v2.0 / pre_v2 / tqpv3。
- catalog SHA `be8cad9a52879bb11f7bb490276a0452882de1a20bef3931e973b53962907464`；4050 tracked blobs、166649746 B 均从实际 M Git 对象重算并对 runtime catalog 字节；root tree `78e991ea35d769a67a48350faa24e6b920c1a926`。

额外非作者检查见 `finite-prefix-result.json:tablesSameAsS01Persisted`：新 freeze 的 before/after **全部 25 张白名单表**都与刚验收的 S01 persisted 全行事实一致，7000 合成 history 的两列没有再次改变，其他 24 表无额外漂移。按真实列语义比较带时区 timestamp 的同一 UTC 瞬间；naive timestamp 保留原值，不擅自附时区；其余字段严格比较。sys_config 仍限冻结白名单，不扩为全库秘密配置保证。

未重新登录或调用 API，只读冻结目录里的原 HTTP 响应。root 旧 wrapper 因把成功 code 限成 int0 而留下的 rc1 不覆盖；本回执依原 HTTP/PG/独立复算证据，不把该 wrapper 历史错误改写为成功执行记录。

## 七原生身份投前门

源 `f02/series-s01/native-normal20-bc48701e/observations.jsonl` 持续追加。本次一次读取后，只截取最后完整换行前的 **128 行**到自有 `native-prefix/`，并再次确认源此前缀字节未变；没有改源或补造结尾。具体大小/SHA/丢弃不完整尾字节记录在 `native-prefix-capture.json`。复制了其引用的首见 lsof 原件，127134 B，SHA `a8a7736cd1c28e7c62aff484c15e2619095d01a2be74897302250243bd3ede4a`。

有限观察跨度为 **2026-10-17T15:29:26.422232Z 至 15:33:40.985598Z**，最大相邻原生 poll 间隔 **2.007089666s**（≤5s）。128 行都核出完整且固定的七成员：API90715、Beat90836、master90843、pool90858/90861/90862/90865。逐条核：

- 原始 PS 行能独立解析回 pid/ppid/lstart/state/argv；七个 birth identity、PPID、argv 和角色集合固定；master/Beat 是 API 子进程，四 pool 是 master 子进程。STATE 仅作状态使用，未误作 elapsed time；Beat 实际为 T 停止态。
- 原 PS/Git 命令均 rc0、stderr 空，源 M/backend tree、native-env SHA 和 taos-config SHA 在该完整前缀固定；源 sampler plan 固定 f9ef43b6…。
- 原首见 `lsof -a -p … -d cwd,txt -Fn` rc0；按真实 p/f/n 字段逐 PID 解析，七 CWD 均 runtime/backend，七者 txt 均有真实映射解释器 `/Users/zhangping/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12`，不以 argv0 的 symlink 推断实际 exe。
- 固定原 env SHA `27d6fc10…`、taos SHA `5cab803b…`；没有读取秘密值、没有改变配置。

这只是所存前缀中原生观测与配置/catalog 的有限门，不证明未来完整 series、具体任务 executorPid 或 Worker 内存中每个算法对象。futureSeriesAccepted/formalReadyClaim/loadedAlgorithmMemoryClaim 均明确 false。

## 审核纠偏与边界

本代理首轮文件比较器错误假设所有 `_at` 时间必带时区，对真实 naive 字段触发断言；`initial-naive-*` 保留原代码/命令/rc1。修正仅为：带时区列才统一 UTC，naive 原值保持，其后全行比较及原生检查真实 rc0。作者原件、源代码和期望均未改，初始告警是 reviewer 假设错误，不是产品 REWORK。

原四项用户豁免保持；容量仍 **0/20**。完成即封档，不延伸本前缀、不签整轮 CI、不新增探针或启动/停止任何进程。
