# Beat 单测进程边界隔离整改移交

作者局部检查通过；F04 非作者复验另册。只修改 `test_stop_beat_keeps_pid_file_owned_by_existing_process` 函数，函数外字节等原；完整测试文件 SHA `3d6c5e15d71f04a1d08812a1e0cfb3cdf5f89f28bdbb76420ee00a4c01c51eef` /7757B。产品 main SHA5f59552d61e4e1229d4b784471b17f3c00a42af797d810dc5194f73c6624252b 未改，watchdog/conftest 未改。

现在从实际 `_beat_pidfile_path()` 在自有 tmp/logs 创建假 PID 文件；显式 mock 进程发现、遗留终止和自有进程组终止边界，并断言发现/兜底调用与 PID 文本保留。上下文结束后检查六个模块属性和 cwd 都恢复原对象，避免 `_celery_ever_touched`、日志句柄等污染后续测试或退出清理。包含性用两侧 resolve，兼容 macOS /var→/private/var 别名。

F04 未改纯 AST probe：`01-original-independent-probe.*` 原测试本身通过，却能触达测试外的发现/终止边界，真实 wrapper rc1；`02b-fixed-independent-probe.*` 与 `05-candidate-independent-probe.*` rc0，所有外部发现/终止调用0、product import0、network0、signals0。该探针使用记录的真实 argv 证明调用可达性，**不反推历史真实发信号者**。原件与探针全文路径/SHA 在 source-manifest；未覆盖独立原件。

初稿仅路径文本包含性断言因 macOS 别名失败（rc1，外部 calls0，信号0），`draft-v1-test_runtime_regressions.py`、`new-probe/` 和原输出完整保留；修正为规范化路径后上述原探针通过。未隐藏失败、未削 PID 文件合同。

`06-candidate-runtime-and-singleton.*` 实际15 passed /0 failed，1.83s；原 runtime 文件6例+singleton9例。候选 Ruff/format/diffcheck 均0，实际 argv/env/stdout/stderr/rc/hash 保留。pytest 明确沿既有 CLPM_TEST_MODE=1、CLPM_SKIP_EXIT_HOOKS=1，不启动服务。本代理未启停实际进程或提交/推送；与 SQL 双反引号修复分属独立提交单元，真实四业务与整套最终 CI 仍待验。
