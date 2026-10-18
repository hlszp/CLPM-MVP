# run39037226096 原始终态移交

真实 workflow FAILURE；Backend 与 Frontend 通过，critical 在准备 INPUT 的 TD SELECT 语法处真实退出1，尚未运行四个业务例，不签 F03 闭环。

身份：PRhead `f29b57338688ca9f9c0900f4c10338d3ad54cc2f`，三个 job 原始 `git log` 与 critical `CLPM_BUILD_CODE_SHA` 均为实际 PR merge `adc74b7a365b480f234dd6e5a2df7ac5592335ef`。后续 PR API 已更新到8a438/820aba，不能用该可变值替换旧 run 实际身份。提交态 fixture 原文通过 GitHub contents GET 保存，SHAe39f0b97…。

Backend job117409399162：7477收集，166去选，7311执行=6959 passed+336 skipped+12 xfailed+4 xpassed，0 failed，610.01s，coverage75.75%，原30秒超时仍生效。原含22样本准确率测试所在文件7例全过，ARMAX13/ARX17等价回归均通过；未从该成功日志捏造逐样本指标。历史 Mypy 2256 errors/235files 仍为既有非阻塞例外，不写为 type 全绿。

Frontend job117409398990：106单测文件、1021例passed，原2 smoke通过（7.3s），lint/type/build通过。

Critical job117409399118 原日志3010/3011：未引用 `value` 的 SQL 返回 TAOS 0x2600（十进制9728）语法错误、真实 exit1。Prepare 之前的 17 保护测试和纯 gate contract 并非四业务验收；实际四业务执行0、NPZ0。原失败未取消或重跑。SQL整改及 Root 真实只读负正控另在 td-readback-sql-remediation，不能改判本次历史失败。

artifact12102817123：107482B，SHA82a6bea3590ad44afbc55b6f947b3d955de115bf7d35642fb7dc2fbcde9900e7，与 GitHub digest 一致；78文件的字节数/SHA全部重算。完整三job原日志、完整run原ZIP、run/jobs/PR/artifacts原JSON、每次真实argv/rc/stdout/stderr/hash均保留。只有输入/绑定声明，缺完整seed/NPZ，不给F04虚构可验数值包。全部 GET-only，本代理未改GitHub状态、源码或服务。
