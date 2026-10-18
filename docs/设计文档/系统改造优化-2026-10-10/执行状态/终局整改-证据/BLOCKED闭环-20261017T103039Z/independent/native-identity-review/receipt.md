# 外置 native 身份采证：非作者工具复核

**采证工具局部 PASS；真实七身份业务span仍PENDING。** 输入源码 `observe_capacity_native_identity.py` SHA-256 `f9ef43b64c5b3deeed24d8429f6a5d2eaaf96ccd40c3e41a99ddceff26a245f4`，作者6项测试SHA `d3a37b11a62f6f6e93c287e7fe49a64d5bb9f46f1817d1e60d5f3a3f106340b1`。原样源保全为 `sampler.original.py`。

非作者自编7项纯fake-main/解析控制全部通过，作者原6项独立运行通过。所有PS/git/lsof/时钟均为假对象，不连接实际服务或发信号；验证同PID/lstart变化产生新identity而STAT变化不产生、PS失败/错SHA/env漂移/相关畸形行实际非0；保存原选中行、原始lsof rc/stdout/stderr与外部引用hash。lsof失败本身仍可完成“采集”，但summary明确PENDING；后续业务验收必须拒绝该缺口，不得据采集rc0放行。原始日志及命令rc在 `native-independent-final.*`、`native-author-independent.*`。

另只读恢复 Root 实际停机 smoke 的plan/summary/JSONL，整档SHA/bytes/planSHA全匹配，实际3poll、maxgap2.0121680829906836s，members全部为空，不当作7PID已验。真实自身PID的-Fn/-Ffn控制原件rc均0、stdout逐字相等且均含fcwd/ftxt；`source-and-raw-check.json`、`actual-own-pid-lsof-control.json`在案，没有凭参数字面推断缺字段。

首次自编测试lint因局部变量名`l`触发E741，原失败日志保留；更名后format/lint和同7项全部0。不是产品缺陷或测试放宽。

正式验收使用投前冻结的 `协议方法补充-05-原生进程连续取证.md`：重算每个原文件hash和每个真实任务span的7身份/父链/启动时间/argv/cwd/txt与连续最大5s覆盖；原生时间按真实时区转换；HEAD与源码字节证据分别核；实际executorPid仍unknown/NULL。缺原件、失败lsof、身份漂移或时序缺口保持BLOCKED，不以本回执扩签20批容量或本地四业务。
