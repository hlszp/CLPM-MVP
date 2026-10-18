# ARMAX 作者影子等价与性能移交

candidate edfa未改；仅证据夹内原型。源身份见source-manifest.json，旧protected ARMAX SHA22cc3ddf0aee6339a91b4e54c02b2a9d907273dc585bfa31debc6227224dc11b。拟落点为armax.py中E_phi/Phi/y_reg按列索引拷贝＋相邻13例数学/边界回归。不动递推eps_hat、e/e_final累加次序、200次迭代、收敛、候选、随机种子、原22数据或30s门槛。非标准数组保留旧标量路径。

两次真实影子cProfile：old6.459s→prototype4.741s（本机且含等量取证开销，不称Linux/pytest时限通过）。完整原22case/22全字段识别对象/27次ARMAX/4676个实际lstsq输入矩阵。outputs.json逐字节SHA115d40231a21c96c22d6b61d10ef5a3c768ee00fe686ce99a15d06a753de990a相同；matrix-bytes.json SHA1d390144eb414fdfc5d786eb6a912b20087714b04617c7d833f681af1ec8bf65相同；参数+u/y记录+ARMAX结果SHA6eec88c52804173e2b1bbcd9e34d05bda42dfd1fcad0581c737940dde18e34f4相同。实际u/y可从对应armax-inputs.npz恢复；profile/matrix/完整输出raw均在案。核心准确率16/18=88.9%未改。

51种边界（阶次、nc超行数、零阶、dtype、PyInt最小64位、list/tuple/子类/掩码/Nx1/步长/负stride/只读/空/负delay/短u/异常）旧新结果与所有矩阵字节全部相同。拟入库13回归真实在旧/新各通过；这些是等价语义守护，旧码同样PASS，性能red依据是保留的真实Linux30s失败。首次两次draft runner发生循环import，原rc1/stdout/stderr保留，最终用独立模块别名修正后unit-v3两边13/0；没有隐藏失败。

profile源码先按工具默认88格式化，正式候选按backend实际100格式化前保留profiled-source-before-repository-format.py；format-transition.json证明仅格式、AST全等。候选source/test已按真实backend配置lint/format均0。尚未动candidate、protected清单或提交；等Root核准且本机服务已停才落地，再跑原coverage单例及相称相关测试。protected仅Root另批准ARMAX单条hash，不新增豁免、不批量生成。F04负责非作者等价/边界与后续真正Linux门。
