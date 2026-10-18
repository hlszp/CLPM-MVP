# F03 ARMAX 矩阵优化非作者复验

判定：**PASS（数学等价及本机原 30s 单例门）**。旧基线为 edfa 的 `armax.py` SHA-256 `22cc3ddf0aee6339a91b4e54c02b2a9d907273dc585bfa31debc6227224dc11b`；冻结新源为 `90e7982083505f83d1ba11b48f2d0d88574377c400ae4b380a24634f8cf3bb8d`。候选实际落地字节与冻结原型一致。

独立从 git edfa 取原 22 例测试及原标注数据，确认与候选逐字节一致，再用独立捕获器分别执行旧、新 ARMAX。22 份完整 IdentificationResult、22 行评估结果、全算法管线 24,018 次最小二乘输入矩阵和右端数组的完整原值哈希，全部一致；整个捕获文件 SHA 均为 `88dd324a6ab35fbc837942adf915065eecd9307637f615991381e2b7c9f25249`。证据在 `own-full22-{old,new}.{json,summary.json,command.json,stdout.log,stderr.log}` 与 `own-full22-exact-comparison.json`。这包含原来不成功的案例原貌，未宣称 22 例均辨识成功；原核心准确率仍 16/18=88.9%，原 85% 断言不变。

独立 AST 比较证明，除 E_phi 与 Phi/y_reg 的两段矩阵填充外，其余 AST 完全相同：残差、创新递推、参数求解、收敛判断、max_iter、tol 与最终结果逻辑均未动。作者原 22 例输出与独立捕获的完整结果结构也逐值相同；两套作者 NPZ 各 27 次 ARMAX 输入、54 个数组已重载逐项校验 shape/dtype/原值 SHA，可恢复。见 `source-and-original-proof-crosscheck.json`。

本次独立执行：

- 自写 24 个边界的最终结果、异常/警告及每次矩阵原字节均与旧源一致：普通/float32/非连续/逆序数组、列表、tuple、-2**63 与大 Python int、int64、列向量、MaskedArray、NaN、短/空输入、短 u、na/nb/nc/延迟/迭代数边界。逐例命令、精确 rc 和结果在 `case-*.{command.json,json,stdout.log,stderr.log}`，汇总 `own-boundaries-v2-summary.json`。
- 独立执行拟入库回归文件：旧、新各 13 passed、rc0，原件 `author-unit-independent-{original,prototype}.*`。
- 候选原 `tests/test_annotated_loops_evaluation.py::test_core_loops_identification_accuracy` 真实 1 passed / 4.42s / rc0，见 `original-timeout-case.*`。原 pyproject 与 edfa 完全同字节，timeout=30、timeout_method=thread 未改。独立捕获的旧 5.262s、新 3.957s 只作本机观测，不替代远端 CI。

边界说明：无穷值 inf 的旧/新成对比较在 4s 工具时限内无完整结果，单列未验证，不计入 24 项通过，也不据此认定任一版本回归。最初未分例的自写比较器被主动终止，实际 rc=-15 与进程原件保留于 `own-boundaries-interrupted-v1.*`、`own-comparator-timeout-*`；不是产品测试失败或放宽产品时限。

限制：未签远端 Linux 本轮全量 CI、真实四业务、KPI 独立数值或容量 PASS。受保护 golden 的任何登记仍须主协调明确授权、仅对应 ARMAX 单 entry，并独立核其余 entry/测试/保护范围未改。本回执不自行授权改 golden、提交、推送、合入或部署。
