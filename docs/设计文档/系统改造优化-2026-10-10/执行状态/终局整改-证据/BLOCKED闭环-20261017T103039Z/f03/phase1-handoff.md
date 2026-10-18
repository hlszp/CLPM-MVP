# F03 第一批可提交冻结

先修两处确定前置缺陷，业务四case及算法/血缘标准未降。当前13份源码/依赖身份及冻结时间见`phase1-source-manifest.json`；本批新增修改仅4文件，作者暂不再修改这4文件，供主协调提交。

| 文件 | 本批变化 |
|---|---|
| `.github/workflows/ci.yml` | critical明确CLPM_TEST_MODE=0；专库bootstrap/stamp后check；真实lifespan前执行外联禁用配置；补前端/Worker/Beat日志artifact |
| `e2e/tests/critical-business.spec.ts` | 四处成功码按真实后端协议严格断言字符串`"0"` |
| `e2e/scripts/prepare-critical-fixture.py` | 新--configure-services，复用全部既有CI/loopback/库名/run/SHA guard，guard通过才import asyncpg；仅在专库事务内禁AAS/SignalR/外部历史/LLM/回填，重读严格相等才提交。fixture另存七角色完整输入序列、SHA及executionLocation元数据 |
| `e2e/scripts/test_critical_fixture.py` | 5例保护；两个写入口在非CI下均rc2、import/网络前拒绝；配置禁用范围按实际运行键核对 |

原始证据：`success-envelope.*`从真实success()/ApiResponse只读生成`real-success-envelope.json`；`reverse-success-code.*`对实际spec旧断言rc1（Expected 0 / Received "0"）；`forward-success-code.*`对同一断言新源码rc0，并拒整数0及错误码。`fixture-protection-tests.*`5/5、`typecheck.*`rc0、`gate-contract.*`8/8且0skip（正常0/不可达2/失败1/skip3/flaky拒绿）、`workflow-preflight.*`rc0。Python仅归属2文件ruff check/format均0。

`业务CI前置与执行方案.md`已给真实远端和本地专属CI执行顺序。根工作流无自动生产部署；主协调需PR触发并绑定实际head/mergeSHA。本批未运行真实四业务case，不据离线契约签业务PASS。

后续独立数值核验所需dataset/config/DataBlock专库只读导出将作为下一批极小artifact补充，先不耽误本批提交。作者不会新增产品API、修改算法或向共享业务投递；主协调唯一Keeper/提交推送人。
