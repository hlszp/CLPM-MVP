# 首次远端工作流校验失败 · 最小修复

原run39026319367，head=d8200eedd02aa785105eeceb5246dc6bf297e6b4，event=push、failure、jobs/checkruns为空；失败在workflow校验，不是业务case运行失败。原件由主协调保留在上级remote-run-39026319367。

已独立读取GitHub HTML第761行注释：job env第204/205/206行的runner.temp为Unrecognized named-value。源HTML SHA=`ac6f58e88e6b686396df8c2338135415b374f66544cec7cbbcc35265e49cf5d3`，提取原文及位置见github-annotation.json；修前ci.yml原件保留。

仅修改`.github/workflows/ci.yml`：删除job env中三条runner.temp路径，在critical Checkout后首个run步骤使用RUNNER_TEMP+GITHUB_ENV赋值同样三个绝对路径。CLPM_BUILD_CODE_SHA仍取合法github.sha；上传路径、三服务、四业务case/断言/分母与算法均未更改。

actionlint **1.7.12** 从官方release下载，归档按官方checksums核验一致，版本/URL/二进制hash/下载真实rc在tool-download-records.json、tool-location.json。修前actionlint rc1且精确报告三处非法context；修后rc0。两个检查保留实际argv/stdout/stderr/sourceSHA。只运行actionlint内置语法/上下文检查（未调用外部ShellCheck/Pyflakes），另外以真实bash执行新的路径设置步骤，rc0，三条路径与预期逐字相同、都在已有always上传根，独占temp已清理。raw为actual-path-step-check.json。

初次本核验调用系统python3缺PyYAML，rc1发生在执行actionlint之前；记录于initial-verifier-environment-error.json，随后改用已有backend venv成功。该工具环境错误不是产品反例。

冻结时间、当前HEAD及13文件SHA见source-manifest.json，除ci.yml外12项与phase2完全不变。现在可由唯一主协调正常提交/推送，等待GitHub真正创建jobs及真实四case；作者未触发CI、提交/合并或操作服务。此前纯YAML结构预检没有覆盖GitHub context规则，本次actionlint反例补足，不能把先前结构预检rc0写成合法workflow已验。
