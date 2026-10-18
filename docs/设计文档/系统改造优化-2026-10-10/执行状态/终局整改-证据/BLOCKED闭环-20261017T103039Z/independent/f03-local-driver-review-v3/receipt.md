# F03 v3停机屏障：非作者退件

**REWORK，已由v4收紧；本件永久保留原反例。** de1a8981f8beb491265cbda24c3619a83b63c4cb3a80c5795b0c9f592df26801的read_barrier只核新offset/时间后的app.main精确stop Worker/Beat JSON，会接受退出兜底路径。

非作者提取原reader只读合成日志，不启服务；正常顺序正控通过，只有generic stop而无lifespan入口时仍返回barrier，拒绝断言真实1failed+1passed/rc1。见barrier-original-red.*。真实旧首次API.log也印证该形态：133行Finished server process，134/136行精确app.main stop Worker/Beat，整档没有Waiting for application shutdown。该实际对照的原行/offset/SHA另封于../f03-local-driver-review-v4/real-atexit-source-crosscheck.json。

root接受最小补丁：必须同次信号后先见Uvicorn Waiting for application shutdown原行，再见app.main stop JSON，并保留两段原文SHA。没有改产品或四条spec，没有新增豁免；修后回执另在v4目录，不改写此原件。
