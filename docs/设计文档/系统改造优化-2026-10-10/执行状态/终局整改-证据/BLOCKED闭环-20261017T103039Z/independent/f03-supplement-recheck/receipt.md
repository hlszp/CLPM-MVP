# F03 CI补充变更非作者局部回执

2026-10-17。**本次补充变更局部PASS；F03真实业务整体仍BLOCKED。** 非作者没有修改候选源码、启停/投递/接数据库、触发CI或提交推送。

输入为`f03/remote-run-39027107897/supplement-source-manifest.json`（SHA-256 `5d4c04f8161252b4f1e9ad5bb70c63233ee32321b13841532ff77222e65e1fdc`），基线`148dd21ac8450cd85d763f89a557adf68fd29fdb`。非作者逐一重算19个声明文件SHA/size，并另核准确性测试文件与基线字节一致；`source-before.json` / `source-after.json`前后相同。

独立运行`verify_independent.py`，原命令`verification.command.json`：11:17:44Z→11:17:47Z，真实rc=0，`result.json` **24项全true**。没有运行作者31项检查脚本作为替代。

- `.github/workflows/ci.yml:69/194`三个job无needs/if/continue-on-error，关键/前端步骤均阻塞，各自前置独立；critical拥有PG/Redis/TD及bootstrap、真实lifespan、锁定安装、输入准备、门禁与always artifact。任一关键步骤失败仍令job/workflow失败，Cleanup的容错仅处理收尾；Backend原有`Mypy type check`单步非阻塞例外（151–153）保持，未扩大。
- 把本轮两处五线程env、新诊断步骤及needs移除逆向恢复后，完整YAML语义与基线相同。原30s测试timeout、coverage60、四业务ID、critical配置/门禁/测试、原两smoke/config和数值算法输入未改。
- 官方actionlint1.7.12二进制重算SHA=`8db11704dc296f096216db4db65d86cd7f0ebfdf4c38453a1da276b137b88388`；非作者执行当前workflow，rc0（禁用外部shellcheck/pyflakes，原argv/输出在`actionlint.*`）。
- 非作者提取两段原数值诊断，唯一替换是使用既有backend venv Python，真实bash `-e -o pipefail`执行均rc0；五变量均1，critical `numeric-runtime.json`实际文件与stdout数据一致。只证明诊断可执行，不证明Linux速度或原超时已修。
- app直接devDependency为`@playwright/test: catalog:`，lock仅该importer增加1.60.0；移除新增声明后package/lock与基线语义相同，原resolution/catalog未变。非作者实际从app解析1.60.0，路径属于候选自己的frontend依赖。
- 非作者在CI=true下实际执行原Playwright `--list --reporter=json`，rc0且原两条smoke标题均在。**只是收集，没有执行smoke或四业务**。旧配置注释仍写非阻塞属于历史注释，本次实际YAML已阻塞；不以该注释判门禁放行。

局部结论允许root在既有授权内提交冻结三文件。总体仍须新Linux workflow三个job真实通过、原两smoke实跑、AUTH/PERM/CALC/EVIDENCE各一次passed且0skip/retry/flaky、完整原始输入与NPZ/manifest/配置血缘、独立数值复算。PR运行必须同时登记head SHA与实际checkout merge SHA，校验输入包/CLPM_BUILD_CODE_SHA/run日志一致，不能只用分支head代替实际执行版本。任何job失败不得merge。本回执不签D04、不扩大四豁免，G6/G7=CONDITIONAL_PASS不变。
