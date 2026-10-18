# F03 workflow runner context 修复 · 非作者局部回执

**PASS，仅限工作流context规则与隔离路径step。F03真实业务/远端CI整体仍BLOCKED。**

复验人未编写该修复，未改候选源码，也没有触发CI/启动服务/投递业务。直接重算作者保留的actionlint 1.7.12二进制SHA `8db11704dc296f096216db4db65d86cd7f0ebfdf4c38453a1da276b137b88388` 与其官方release下载校验记录声明一致，实际执行两份workflow：

- 原 `before-ci.yml`：actionlint **rc=1**，精确3处 job env 中 runner context 不可用。原始输出 `original-invalid.stdout.log` / `stderr.log`；真实命令 `original-invalid.command.json`。
- 当前候选 `.github/workflows/ci.yml`：actionlint **rc=0**，source SHA `7f0fc547d35e394acd0f37c7bdabda183d1ad77c0f805e78b891be2fe69a5cf1`；检查前后源码相同。输出与命令 `fixed-candidate.*`。
- 读取实际workflow的路径step，以真实 `bash -e -c` 在非作者自有TemporaryDirectory执行，**rc=0**，GITHUB_ENV真实生成3条绝对路径，均在RUNNER_TEMP/critical-e2e内；原文/结果 `env-step.json`。临时目录已自动清理，未设置GITHUB_ACTIONS运行产品准备器。
- `minimal-workflow.diff`确认局部变更为移除3条非法job env路径，在Checkout后用RUNNER_TEMP/GITHUB_ENV写相同上传根路径。三服务、四case分母和算法不因本次修改变化。

本次actionlint禁用外部ShellCheck/Pyflakes，仅签实际跑到的语法/context规则；没有把之前纯YAML结构通过冒充context合法。旧远端run39026319367的原始失败由主协调保留；本次没有独立访问/重跑该远端，不能说其业务已执行。新远端必须真正生成jobs并执行真实四例，0skip/retry/flaky、算法oracle和输入/记录/页面证据齐备才签F03整体。
