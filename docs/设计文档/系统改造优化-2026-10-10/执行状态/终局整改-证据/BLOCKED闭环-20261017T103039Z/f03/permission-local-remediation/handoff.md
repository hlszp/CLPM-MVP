# 权限合同及诚实本机 profile 增量

Root基于真实run39031454818批准仅三源修复，并已正常提交 `edfa831afd6069963e7f6f73c2721637d6a199b5`（未推送）。源冻结12:21:24Z，精确身份见source-manifest；before/after整源与source.patch均在案。

PERM保留精确API403并加强ERR_PERMISSION_DENIED/null断言；真实/me必须是SPONSOR且无page:workbench.main/metric:task-run；逻辑path/query精确；必须出现现行404组件的两段确切文案且工作台adapter不存在；另建独立ADMIN context同目标正控，避免复用SPONSOR localStorage。AUTH、CALC、EVIDENCE及所有auth/route helper未放宽，原四IDs/单次/0retry不变。

原真实trace中SPONSOR /me与403 body已直接恢复为actual-sponsor-me.json/actual-sponsor-403.json，无headers/token导出；contract-sources为六份原生产源码。applyPagePermFilter会同时移除known-path，故此场景确切404；没有把任意错误页泛化为拒入成功。

新增disposable-local仅白名单PG clpm_critical_20261017_local/17102、TD clpm_critical_20261017_local_ts/17104、Redis17124、API17261/UI17266、全loopback、LOCAL_RUN_ID及真实完整gitHEAD；拒共享/capacity/production身份、错误SHA、GITHUB_ACTIONS冒充。CI默认原guard保留，算法数据生成/七角色TD写入/指标断言不变。

作者实际自验：12 guard tests rc0、type rc0、gate contract8/8 rc0、原4 IDs collect rc0（仅收集，不是业务通过）、ruff/format rc0；每条原始stdout/stderr/rc/SHA在本目录。非作者独立门由F04另写于 E/independent/f03-permission-local-review；作者不代签。

本机整链执行单在相邻local-four-case-plan，由Root唯一Keeper执行，尚无实际四业务/NPZ/独立数值PASS。本机与远端均须分别跑通，四项用户豁免保留不动，不新增豁免。
