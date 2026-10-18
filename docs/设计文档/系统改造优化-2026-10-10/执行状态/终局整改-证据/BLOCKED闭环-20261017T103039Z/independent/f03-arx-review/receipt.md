# ARX 性能修复与保护指纹非作者复验

结论：本地数学兼容与相称测试 PASS。真实 Linux CI 30s/coverage 门、四个业务例和实际指标血缘仍须分别验收，本回执不签 F03 整体通过。

- 固定源 `backend/app/services/tuning_identification/arx.py` SHA-256 `2fdf77affb47d8edfb451e6445c5d8e2e818cfd450afc5204321cff923a9e011`；边界测试 SHA `52ead2de84e319d79afd8a73868e2337449ca3b59f1965fbdc9be8a1c72200bf`。`check.json`、原22命令记录执行前后源/原22测试/数据集/pyproject 字节不变。
- 独立重放 root 的同一 min-int64 Python list 反例，原标量矩阵 SHA `ef97203f…` 精确命中；首版 vector 反向断言失败 rc=1，修后 rc=0。见 `original-counter-first-vector.*`、`original-counter-fixed.*`。没有缩为 ndarray 支持域。
- 自编纯数学工具 `independent_arx_parity.py` 对原冻结标量源与修后源做 85 组独立比较，85/85 相同：包括 float32/64、int8/64、uint64、正负 stride、不同阶次/延迟、零阶、list/tuple/unbounded Python int、具有特殊标量读取语义的 ndarray subclass、短输入/负延迟/负阶/布尔原错误。逐字节比 Phi/y_reg、布局、完整 ARXResult/稳定性或异常形态；没有调用产品计算服务或用作者通过数代替。原件 `independent-parity-fixed.*`。
- 边界测试由非作者实际另跑 17 passed、rc=0。原有未改的 22 回路准确性 coverage 单例实际另跑，原30s不改，1 passed / 17.78s / rc=0；原门禁仍 16/18=88.9%≥85%，未把已知不足改成通过。原22完整 IdentificationResult 与22行结果在原件间由我独立逐值相等，见 `original22-artifact-comparison.json`。本地时长不替代远端 Linux。
- 保护指纹是一次明确登记的例外：root 主审在正常 pre-push 因旧 ARX 指纹失败后，明确授权本次必要性能修复仅更新 ARX 一条 golden。独立核 golden 全文本恰等于原件中一次旧89c757…→实源2fdf77…替换；41条另40条、baseline_head、保护测试/collector字节完全不变，41个实际受保护源均匹配。新 golden SHA `3f26f6b31860fc8d4b69c6b0fcab7e2dd4b4d8bb8b1f247dab2c6a92fb0a34a9`。非作者明确取消 REFACTOR_UPDATE_GOLDEN 环境变量后实际 guard 1 passed、rc=0。见 `protected-single-entry-check.json`、`protected-guard-independent.*`。不能称“受保护源没变”，也没有批量 regenerate 或修改保护断言。
- 作者增量三个证据 manifest 的全部 61/28/19 份原件由我重算 SHA/size 均一致，见 `author-incremental-evidence-integrity.json`。旧首版红证据、正常推送前5713/1中止证据均保留。

非作者只写本目录工具/证据，未改候选业务源，未提交/推送、启停服务、投递任务或写业务数据。
