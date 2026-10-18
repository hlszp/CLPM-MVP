# 适配器作者判断纠正

2026-10-17 本作者曾按参数名称推断 `lsof -Fn` 不含 `f` 字段，建议改为 `-Ffn`。该推断未先实测，已撤回。

Root 对自身真实 PID 分别运行两命令，实际 rc 均为 0，stdout 逐字相等，均包含 `fcwd` / `ftxt`。反证正本：`../f02/native-identity-lsof-field-control.json`。外置 sampler `f9ef43b64c5b3deeed24d8429f6a5d2eaaf96ccd40c3e41a99ddceff26a245f4` 未因此改版。

v2 直接重解析原始 `p` / `f` / `n` 行；仅真实缺少 PID / 类型 / cwd / 解释器映射或命令非 0 时阻断，不将静态参数猜测登记为现场失败。此纠正不影响独立已复现的 NULL 身份准入和节点 `cancelled` 矛盾汇总漏拒两个真实缺口。
