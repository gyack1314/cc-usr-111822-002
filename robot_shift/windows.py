"""把任务日志、充电、工位等待与人工接管记录整理成可解释的时间窗。

各来源并不按同一时钟记载：skew_seconds 表示来源之间允许的最大时钟偏差。
- confirmed 窗口 [start+skew, end-skew]：在偏差范围内任何对时方式下都成立的核心段；
- possible 窗口 [start-skew, end+skew]：记录实际可能覆盖的包络。
缺结束、缺开始或时间戳冲突的记录只给出 possible 包络，不进入 confirmed，
避免把无法确定的空档解释成有效生产。
"""
from datetime import datetime, timedelta

DEFAULT_SKEW_SECONDS = 300

def parse_ts(value):
    """解析 ISO 时间戳；缺失或无法解析时返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

def _window(start, end, skew_seconds):
    delta = timedelta(seconds=skew_seconds)
    confirmed = (start + delta, end - delta)
    if confirmed[1] <= confirmed[0]:
        confirmed = None
    return {'confirmed': confirmed, 'possible': (start - delta, end + delta)}

def job_window(payload, shift_start, shift_end, skew_seconds=DEFAULT_SKEW_SECONDS):
    """单个作业的时间窗，返回 {'status', 'confirmed', 'possible'}。

    status: 完整 / 缺结束记录 / 缺开始记录 / 时间戳冲突 / 无时间戳 / 时间戳无法解析。
    """
    raw_start = payload.get('started_at')
    raw_end = payload.get('ended_at')
    start = parse_ts(raw_start)
    end = parse_ts(raw_end)
    if (raw_start and start is None) or (raw_end and end is None):
        return {'status': '时间戳无法解析', 'confirmed': None, 'possible': None}
    if start is None and end is None:
        return {'status': '无时间戳', 'confirmed': None, 'possible': None}
    skew = timedelta(seconds=skew_seconds)
    if start is not None and end is not None:
        if end < start:
            # 结束早于开始：只能确定作业发生在两者包络之内
            return {'status': '时间戳冲突', 'confirmed': None, 'possible': (end - skew, start + skew)}
        window = _window(start, end, skew_seconds)
        window['status'] = '完整'
        return window
    if start is not None:
        # 缺结束记录：可确定的只是“不早于开始、不晚于班次结束”
        return {'status': '缺结束记录', 'confirmed': None, 'possible': (start - skew, shift_end)}
    return {'status': '缺开始记录', 'confirmed': None, 'possible': (shift_start, end + skew)}

def event_window(payload, skew_seconds=DEFAULT_SKEW_SECONDS):
    """充电/等待/接管等事件的时间窗；无法定位时返回 None。

    只有开始时间时，用上报时长 minutes 推出结束；推不出则无法定位。
    """
    raw_start = payload.get('started_at')
    start = parse_ts(raw_start)
    if raw_start and start is None:
        return {'status': '时间戳无法解析', 'confirmed': None, 'possible': None}
    if start is None:
        return None
    end = parse_ts(payload.get('ended_at'))
    if end is None:
        minutes = payload.get('minutes')
        if isinstance(minutes, (int, float)):
            end = start + timedelta(minutes=minutes)
    if end is None:
        return None
    skew = timedelta(seconds=skew_seconds)
    if end < start:
        return {'status': '时间戳冲突', 'confirmed': None, 'possible': (end - skew, start + skew)}
    window = _window(start, end, skew_seconds)
    window['status'] = '完整'
    return window

def find_conflicts(items, skew_seconds=DEFAULT_SKEW_SECONDS):
    """在同一来源（如同一机器人）的 (key, start, end) 列表中找出重叠超过偏差的记录对。"""
    ordered = sorted(items, key=lambda item: (item[1], item[0]))
    pairs = []
    for i, (key_a, start_a, end_a) in enumerate(ordered):
        for key_b, start_b, end_b in ordered[i + 1:]:
            if start_b >= end_a:
                break
            overlap = (min(end_a, end_b) - max(start_a, start_b)).total_seconds()
            if overlap > skew_seconds:
                pairs.append((key_a, key_b, overlap))
    return pairs
