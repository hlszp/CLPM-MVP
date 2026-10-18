# G3 三失败只读返回路径定位

实质阻塞保持 REWORK，根因未定；不改源码、门限、预期或数据。原始运行是 remote-run-39037749058，实际merge820aba，三job与全部原日志/完整run ZIP已封；同后台tree29d1caaa…的f29/ADC前次绿不能覆盖本次红。

固定输入来自 test_g3_app_applicability.py:222–224 `_two_segments_fopdt(1.4,700,521)`；两段用seed521/571，间200个NaN，默认模型FOPDT/SOPDT/IPDT。三个断言依赖同一“joint胜出”前提，而非三个独立算法必然错误。

实际失败 repr 是 n_train=420/n_val=140/n_test=140、joint_estimate=None、单窗 lowSNR−2.13。pipeline:1632左右仅在 joint_sort_key **严格大于** 逐窗 sort_key 时替换胜者，否则保留逐窗模型。排序是可信度等级→fitting_score→residual_test_passed。joint evidence 在3706左右会记录两段训练总数而非单段420；最终化不重新选择 best_model。现有失败与逐窗结果胜出/未被joint替换一致，尚不能区分joint候选未生成还是排序未胜出。

pipeline:1738左右只有最终胜者就是joint_best时，才补“逐段ΣS/ΣN聚合” lowSNR；否则保留内层单窗 lowSNR。第二项用joint审计的segments对一个单窗胜者的lowSNR独立重算聚合，得到−3.145与−2.13差异，不能据此先改SNR算法或把单窗结果硬标为joint。第三项快照在保存前就要求joint胜出并失败，尚不足单独证明序列化有错误。

预算：整个identification目录未见monotonic/perf_counter/deadline，公开注释明确墙钟预算未执行、budgetTermination=None；现有预算为候选窗/方法结构计数，不能归因运行慢。两个真实profile除SIMD检测段外字段完全一致、uv同步包版本同；AVX512可用与否是关联，未证因果。

下一步由F02纯probe捕获：原固定输入全量hash、选中窗、joint审计与候选列表、每个逐窗及joint候选的完整排序键/拟合参数/门禁原因、最终lowSNR basis；必要保存每次矩阵/RHS字节及数值profile，定位首处分歧。不得用挑机器重跑、删断言、加skip、放宽容差或更改原输入代替修复。Root当前local9f真实服务运行，此分析没有执行算法、pytest、生命周期函数或服务操作。
