# 机器人工位班次记录

项目按来源版本保存业务资料（`evidence` 不可覆盖、可按 known_at 重放）。两条口径并存：

- 旧口径 `dispatch → charging → staffing`：按上报班次汇总派工、充电和人工接管，保持兼容。
- 时间窗口径 `windows → analysis → compare`：把任务日志、充电事件、工位等待和人工接管
  整理成可解释的有效作业窗口，处理跨设备时钟偏差、重叠窗口与跨班归属。

## 时间窗口径

各来源并不按同一时钟记载，`--skew-seconds`（默认 300）表示来源间最大时钟偏差：

- `confirmed` 窗口 [start+skew, end−skew]：偏差范围内任何对时都成立的核心段；
- `possible` 窗口 [start−skew, end+skew]：记录实际可能覆盖的包络。

有效作业分钟给出区间 `[low, high]`：low 只计入 confirmed 并扣去所有可能的停机，
high 计入 possible 包络、只扣去确定的停机。具体规则：

- 缺结束/缺开始的记录只给出到班次边界的 possible 包络，不进入 confirmed；
- 同一机器人作业窗重叠超过偏差、或结束早于开始，判为时间戳冲突，只进入 possible；
- 跨班次充电按时间窗与各班次窗口的实际交集拆分归属，不按上报班次字段；
- 班内没有任何记录覆盖的时间计入 `unaccounted`（空档），不解释为有效生产；
- 只有时长、没有时间戳的记录计入 `unplaced`，并从 low/high 两端保守扣减；
- 吞吐、等待与接管成本按工位 × 任务难度汇总（`station_difficulty`）。

`compare` 对比排班版本（`--base`）与当前版本：两个版本的有效作业区间不相交时
`material=true`，即产能结论真正改变；区间仍重叠只标 `changed`。每次对比列出是哪些
资料修订导致的（reasons），排班所用旧版本可用 `analyze --at <base>` 原样重放追溯。

## 运行与检查

    python -m unittest discover -s tests -v
    python -m compileall -q robot_shift tests
    python -m robot_shift.cli data/example.json                      # 旧口径（兼容）
    python -m robot_shift.cli analyze data/example_timeline.json     # 时间窗口径
    python -m robot_shift.cli compare data/example_timeline.json \
        --base 2026-10-10T00:00:00+08:00                             # 对比排班版本

`data/example_timeline.json` 含两波上报：`scheduling_known_at` 之前是排班基线
（充电按班次 lump 上报、跨班充电记到下一班、j-102 缺结束记录）；之后是充电策略
调整带来的事件级修订（c-201 改按时间窗归属、补回 j-102 结束记录）。对比可见
day-08 结论真正改变（material），day-07 区间收窄但仍重叠，day-09 未受影响。
