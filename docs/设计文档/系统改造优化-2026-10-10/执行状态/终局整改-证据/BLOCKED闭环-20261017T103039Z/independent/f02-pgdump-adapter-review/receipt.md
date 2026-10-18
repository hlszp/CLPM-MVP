# pg_dump 容器适配器非作者回执

**PASS，仅限冻结工具源码及与指定 S01 helper 的静态接续。** 本次实际 Docker/数据库/dump 调用均为 **0**，不据此宣称真实备份已生成或可恢复。

被审源：`E/f02/synthetic-capacity-dump-adapter-root/pg_dump_test_container.py`，4031 B，SHA-256 **c27d789a2c8308cfbeebc692d4565d2460b6c65fe2f3bc295fc72f51469fb3c8**。审前/审后及逐字保存的 `source-original.py` 均同 SHA；未修改作者源、candidate、真实目标路径或任何环境。

## Standards

未发现本范围内阻止交付的规范问题。源码范围明确，检查在 dump 前；参数固定、目标用 `xb` 原子拒绝覆盖，写内容前设 0600；秘密只从环境传递。按 [code-review 技能](/Users/zhangping/.agents/skills/code-review/SKILL.md) 分列规范与需求核查；当前对象是 root 指定不可变源 SHA，未扩大为仓库全量或执行额外运维。

## Spec

独立自写 12 项测试，真实 **12 passed / 0 failed，rc0**，原输出 `independent-controls.stderr.log`，原命令与真实 rc 在 `independent-controls.command.json`。所有外部执行入口均替换为没有真实 fallback 的 subprocess stub；全部文件写入自有 TemporaryDirectory，清理后只保留原件及观察记录。

| 核验 | 实测 |
|---|---|
| 7 参数及专库/端点/用户名/格式/目标/选项 | 正常 127.0.0.1、localhost 接受；缺参、加参和每个错范围均在任何 subprocess 前拒绝 |
| 固定容器 | 错 CID/name/image 均拒绝，未进入 version/dump |
| 端口映射 | 缺失、空、错误或混入另一 HostPort 均拒绝；原 17102 双栈映射接受 |
| 同容器版本 | 16.14 正控接受；15/17/未知/空输出拒绝，未 dump |
| 凭证 | 缺密码拒绝；合成秘密未进入 argv、before/result JSON 或日志；实际命令只有 `-e PGPASSWORD` 键名 |
| 文件 | 现有文件字节不变；悬空 symlink、symlink parent 拒绝；成功新文件 0600，byte count/SHA 对实际 stub 原件重算吻合 |
| 错误 | inspect 非零拒绝；dump 非零 23 原样返回，partial 文件保持 0600，真实 rc/size/SHA 保全；无伪成功或自动继续 |

固定独立反例另在内存撤销守卫，不改任何作者字节：`remove-identity` 两例真实失败/rc1；`remove-arguments` 一例真实失败/rc1；恢复原对象后，同三个原例 **3 passed/rc0**。详见 `fixed-remove-identity.*`、`fixed-remove-arguments.*`、`fixed-original.*`，表明控制能够检测所审守卫缺失。

接续实际 helper：`E/f02/synthetic-capacity-binding-preparation/redis-url-guard-v2/prepare_bindings.py`，SHA **e213f27d9abc631ad3cb11233366e6f631a9fb4d88bcbf3b81cdf0d60464650b**。静态核其 `backup` 实际传 7 参数，并用 `xb` 捕获适配器 stdout/stderr，之后拒绝非零 rc、缺文件、非 `PGDMP` 头。因此适配器 dump stderr 的继承由此确切调用方原样捕获；适配器自身不是独立 archive/restore 资格判定器。

最初只读上下文指向旧 base helper `e70bbe5e…`，已在 `caller-contract.json` 保留；随后明确核对上述实际 e213 源，两者 `backup` 全 AST 完全相同，精确身份和原文见 `caller-v2-contract.json`、`caller-v2-backup-*`。没有以旧指针冒充实际工具身份。

作者提供的 host 15.17、container 16.14、CID/name/image/mapping 三份原 preflight 命令/rc/stdout/stderr各自复制保存在 `author-preflight-context/`；本独立回执只说明已读原件，不冒称本次重新连接或探测了实际运行环境。

本回执满足 root 的适配器局部门。真实 dump、`PGDMP`/哈希原件、后续 S01 apply/回读/回退仍由唯一 Keeper 执行并另行核验。无新增算法探针、无生产操作，也未用本工具 PASS 扩签容量或项目终局结论。
