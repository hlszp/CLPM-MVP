# F03 v4：仅停机 reader 增量

固定产品 edfa831afd6069963e7f6f73c2721637d6a199b5，产品/四 spec/CI 均未改。v4 driver SHA83462d20d8fb8cbd0224722ae269eb3f87df5eb6a41b89c653edaff53c39f622。v3 de1a原件不动；删去双方stop.read_barrier源码后字节完全相同，v3-v4.patch可审。

实际独立反证说明：首轮API原log没有Uvicorn Waiting for application shutdown原行，却在Finished server process之后由atexit输出stop Worker/Beat JSON。v3 reader接受该JSON，不能证明watchdog停止。v4要求本次signal后offset内先出现精确Uvicorn入口原行，再接受精确app.main stop JSON；分别保存raw/offset/SHA/时间。跨poll的入口只可用于同一signalAt。

原实际log＋新边界9例执行：old-barrier-red rc1，6fail/3pass；new-barrier-green rc0，9/9。原v3生命周期fake控制适配完整入口后仍8/8rc0。lint/format0。对应.command.json/stdout/stderr完整保留；没有启服务或写DB。作者不签非作者/业务PASS。

Root真实纯静态v3已完成旧rc1/新rc0（256TCP+4冷Chromium），handler/supervisor/其余stop在v4逐字不变，因此不为reader增量重跑静态。F04增量控制门后，按相邻local-transport-v3/Root执行单-v3.md运行真实四例，只将driver改成本目录keeper-local-f03-v4.py，使用Root指定全新run-id/out。原资源归档/首轮失败不覆盖。四case、输入-only→oracle expected封SHA→结果对照、真实远端/正式容量的独立完成边界不变。
