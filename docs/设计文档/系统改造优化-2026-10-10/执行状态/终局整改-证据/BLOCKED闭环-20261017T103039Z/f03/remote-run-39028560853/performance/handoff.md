# ARX 性能根因与等价整改交接

作者本机自验通过；Linux 原 30s 单例与完整 coverage CI 尚待复跑，不签独立验收。

- 基于候选 `add3ae7c6deaf3c1f136f53a14255d8160a68d7d`，本次只改 `backend/app/services/tuning_identification/arx.py` 一处矩阵构造，新增 `backend/tests/test_arx_vectorized_equivalence.py` 12 个语义守卫；未改 pipeline、算法候选数、48 次 uncertainty 重复、选模规则、原 22 样本、准确率门槛、30s timeout、coverage 60% 或 skip。
- 源码冻结 `2026-10-17T11:41:41.550566+00:00`，`source-manifest.json` SHA-256 `58bf13c52e3a195d20c3e9ec60fba399af345c96069611e678514a7272cf1fc2`。源码 patch、新测试副本、原 `arx.before.py` 均在本目录，主协调可单独逻辑提交；作者未提交/推送。

## 要求 → 根因 → 改动

1. 两次真实 Linux CI 的原 `test_core_loops_identification_accuracy` 触发 30s timeout。第一次 run39027107897，第二次 run39028560853；后者原日志 `../job-logs/117384282551.log:513` 证实 NumPy/OpenBLAS、4 CPU 与五项线程变量全部为 1，3368 timeout、3458 rc=1。不能继续把根因写成“线程过量已确认”。
2. `git-history.*` 保留近期改动。实际热点路径为 `app/services/tuning_identification/arx.py`。外部 `profile_original.py` 原样调用原测试函数并保存 22 份完整 IdentificationResult，不改测试源码；cProfile 的 `original-self-profile.txt` 显示 ARX 调用 18,967 次，自身 Python 7.720s，总 14.180s；lstsq 累计只有 0.804s。`np.var` 是中断时栈位置，并非主要耗时证据。
3. 根因是每次 ARX 构造回归矩阵时按样本和阶次逐点进入 Python，重选时大量重复放大成本。按列切片填入同一个 C-order float64 矩阵和响应数组，保留原行边界、滞后索引、分配 dtype/布局、lstsq 与全部后续统计。
4. 写源码前，`verify_vectorized_hypothesis.py` 外部 prototype 的 1,536 组合结果/错误严格相同，其中 1,024 成功输入的矩阵字节、响应字节、dtype、shape 和布局相同；22 份完整结果（候选、终验、不确定性在内）严格相等。`vectorized-hypothesis.json`、`hypothesis.*` 是真实输出和 rc。500 次微基准使用同长度的各自确定性随机样本，仅作辅助；主要等价证据是同一 22 例完整输入与输出。

## 实测证据

- 实际改后 `fixed-full-comparison.json`：22 份完整 IdentificationResult、22 条准确性行与原 baseline 严格相等，16/18=88.9%；外部 cProfile 14.184 → 6.519s。原/新 profile、结果 JSON、pstats 二进制均保留。
- `fixed-single.*`：原测试节点，带 `--cov=app`，原 30s 配置不动，1 passed / 0 failed，17.89s，真实 rc=0。前一轮本机单线程相同命令 28.47s 在 `../../remote-run-39027107897/resource-profile/one-thread.*`。本机 Apple Accelerate 结果不代替 Linux 完整 CI。
- `equivalence.*`：新 12 例全部通过，rc=0。`old-semantic.*` 用外部加载的冻结旧函数执行同一 12 例也全部通过，rc=0。它们保护数学/边界语义，并不伪称旧实现算法算错；性能“旧红”来自原两次 Linux 30s 超时。
- `related-tests.*`：相关辨识套件 278 passed、1 既有 xfailed、0 failed，rc=0。该 xfail 在未改的原测试中；没有新增 skip/xfail。输出末尾的 DLASCL 诊断来自既有数值边界测试，完整摘要见 stdout 第 40 行。
- `final-ruff.*`、`final-format.*`：均 rc=0。
- 初始 format 检查需要格式化新测试，原失败 `format.*` 保留；格式化后检查通过。初始独立数学参照先转 float 再取负，导致整数零的负零字节误差，`equivalence.initial-reference-signed-zero.*` 保留；参照改为先在原 dtype 取负再转 float 后 12/12，通过期间未改变产品补丁。

## 独立复验与发布门槛

核原 22 样本/原门槛/原 30s/SHA 不变，核原/新完整结果严格一致，再由主协调正常 pre-push 与同一 Linux coverage CI 完整执行。不得以本机局部成功替代完整后端分母，或以增加超时、减少候选/重复/样本、增加 skip 来收口。本次未启动共享 API/Worker/Beat，未写共享 DB。
