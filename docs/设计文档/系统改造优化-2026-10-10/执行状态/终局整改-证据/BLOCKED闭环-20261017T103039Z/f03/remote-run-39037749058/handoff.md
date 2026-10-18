# run39037749058 原始终态移交

真实 workflow FAILURE：Backend 有3项 G3 辨识断言失败，critical 在 SQL INPUT 准备失败；Frontend 通过。本次是 SQL 修复前的8a历史运行，不是9f或新本机业务验收。未取消、重跑或按 flaky 消除失败。

身份：PRhead `8a438372d21395fdb5246e4d8f73a0a5b7bf9c97`，三个 job 的实际 checkout 均 `820aba1aecc204578b34d7892b543a21369d650c`。GitHub actual merge 根树/commit 原件证明：此前 f29绿 ADC merge 与本次820 merge 的 backend 子树都为 `29d1caaa377552d88f596b3a906604ec16a3d77a`，全部 tracked backend 源码/测试/锁/文件模式一致；不把此证据外推为未跟踪环境一致。

Backend117410887852：7477收集，166去选，7311执行=6956 passed+3 failed+336 skipped+12 xfailed+4 xpassed，802.06s，coverage75.73%，原30秒仍生效，原22样本准确率所在文件7例通过。三个真实失败全部在 test_g3_app_applicability.py：joint 胜者要求遇 joint_estimate=None；单窗标注−2.13dB与聚合独立重算−3.1450297dB不等；joint 快照前置同样未胜出。原日志3736起与5052–5063，完整摘要含精确 node ID。既有 Mypy2256errors/235files仍非阻塞，不能替代或掩盖这3项实质失败。

两轮记录的 NumPy2.5.0/4CPU/affinity0..3/五线程变量1/OpenBLAS版本等 profile 字段完全一致，uv同步输出版本映射也相同；唯一已记录 profile 差异是 SIMD：f29绿有 X86_V3/X86_V4/AVX512_ICL，本次红只有 X86_V3。这是关联事实，未证因果。管线未见墙钟预算，不能拿运行较慢或挑机器重跑解释。Root 已派 F02 纯 profile/候选排序定位，本代理未运行算法、pytest或碰当前live9f环境。

Frontend117410887796：106文件/1021单测passed，原2 smoke passed（5.0s），lint/type/build通过。Critical117410887613原日志3015/3016：未引用 value 的 SELECT 返回TAOS0x2600/9728，真实 exit1；业务执行0/NPZ0。纯 gate contract 与17输入保护测试不是4业务通过。

artifact12102244862：107935B，SHA969c04163b8307ad23973d786d67eb14299dba801708d7e516980f063e5947ab，与 GitHub digest相等；78件逐条大小/hash重算一致。全部3job完整原日志、完整run ZIP、run/jobs/PR/artifact/source/git树原JSON及每次真实argv/rc/stdout/stderr/hash保存。无可得的Backend JUnit，workflow未配置junitxml且上传artifact无XML，不对Runner未上传文件作反推。原始证据保持完整；本结论不升格整体CI通过或合入/部署。
