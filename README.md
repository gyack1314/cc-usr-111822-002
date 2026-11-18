# 机器人工位班次记录

项目按来源版本保存业务资料，已有 dispatch、charging、staffing 环节。现有派工、充电和人工接管按上报班次汇总，缺少跨设备时钟、重叠窗口与跨班归属处理。

运行与检查：

    python -m unittest discover -s tests -v
    python -m compileall -q robot_shift tests
    python -m robot_shift.cli data/example.json
