"""左闭右开区间 [start, end) 的集合运算，供时间窗合并、求交与扣减使用。"""

def merge(intervals):
    """合并相互重叠或首尾相接的区间，返回按起点排序的不重叠列表。"""
    ordered = sorted((s, e) for s, e in intervals if e > s)
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged

def total_seconds(intervals):
    return sum((end - start).total_seconds() for start, end in merge(intervals))

def minutes(intervals):
    return total_seconds(intervals) / 60.0

def intersect(first, second):
    a = merge(first)
    b = merge(second)
    i = j = 0
    out = []
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if end > start:
            out.append((start, end))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return out

def subtract(intervals, removed):
    """从 intervals 中扣去 removed，返回剩余区间。"""
    blocks = merge(removed)
    out = []
    for start, end in merge(intervals):
        cursor = start
        for rstart, rend in blocks:
            if rend <= cursor:
                continue
            if rstart >= end:
                break
            if rstart > cursor:
                out.append((cursor, min(rstart, end)))
            cursor = max(cursor, rend)
            if cursor >= end:
                break
        if cursor < end:
            out.append((cursor, end))
    return out

def clip(intervals, start, end):
    """把区间裁剪到 [start, end) 内。"""
    return [(max(s, start), min(e, end)) for s, e in intervals if min(e, end) > max(s, start)]
