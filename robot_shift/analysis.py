"""把多来源记录整理成可解释的有效作业窗口，并按工位与任务难度汇总。

口径说明：
- 有效作业分钟给出 [low, high] 区间：low 只计入 confirmed 窗口（各来源时钟
  偏差内都成立）并扣去所有可能的停机；high 计入 possible 包络、只扣去确定的停机。
- 班次内没有任何记录覆盖的时间计入 unaccounted（空档），不解释为有效生产。
- 只有时长、没有时间戳的记录计入 unplaced，并从 low/high 两端保守扣减。
- 跨班次充电按时间窗与各班次窗口的实际交集归属，不按上报班次字段归属。
"""
from collections import defaultdict
from .evidence import at
from .intervals import merge, subtract, intersect, clip, minutes as span_minutes
from .windows import job_window, event_window, find_conflicts, parse_ts, DEFAULT_SKEW_SECONDS

def _r(value):
    return round(value, 2)

def _iso(span):
    return [span[0].isoformat(), span[1].isoformat()] if span else None

def _clip1(span, start, end):
    if span is None:
        return None
    hit = clip([span], start, end)
    return hit[0] if hit else None

def _nested():
    return defaultdict(list)

def analyze(db, known_at, skew_seconds=DEFAULT_SKEW_SECONDS):
    """按 known_at 可见的资料版本重建各班次有效作业窗口。"""
    issues = []
    shifts = {}
    for doc in at(db, 'shift', known_at):
        start = parse_ts(doc['payload'].get('start'))
        end = parse_ts(doc['payload'].get('end'))
        if start is None or end is None or end <= start:
            issues.append({'kind': 'shift', 'id': doc['id'], 'reason': '班次窗口无效'})
            continue
        shifts[doc['id']] = {'id': doc['id'], 'start': start, 'end': end,
                             'robots': list(doc['payload'].get('robots', [])),
                             'digest': doc['digest']}

    jobs = at(db, 'job', known_at)
    jobs_by_id = {doc['id']: doc for doc in jobs}
    per_shift_jobs = defaultdict(list)
    unassigned_jobs = []
    for doc in jobs:
        payload = doc['payload']
        sid = payload.get('shift')
        shift = shifts.get(sid)
        if shift is None or payload.get('robot') not in shift['robots']:
            unassigned_jobs.append({'job': doc['id'], 'shift': sid, 'robot': payload.get('robot'),
                                    'status': '班次归属待核', 'source': doc['digest']})
            continue
        per_shift_jobs[sid].append(doc)

    sources = defaultdict(set)
    unattributed = []

    # 充电：优先按时间窗与各班次窗口的交集归属；无时间窗则按上报班次计入 unplaced。
    charge_conf = defaultdict(_nested)
    charge_poss = defaultdict(_nested)
    charge_unplaced = defaultdict(float)
    for doc in at(db, 'charge', known_at):
        payload = doc['payload']
        window = event_window(payload, skew_seconds)
        if window is None or window['possible'] is None:
            sid = payload.get('shift')
            if sid in shifts:
                charge_unplaced[sid] += float(payload.get('minutes') or 0)
                sources[sid].add(doc['digest'])
            else:
                unattributed.append({'kind': 'charge', 'id': doc['id'], 'reason': '无时间窗且班次未知',
                                     'minutes': payload.get('minutes'), 'source': doc['digest']})
            continue
        robot = payload.get('robot')
        for sid, shift in shifts.items():
            if robot not in shift['robots']:
                continue
            hit_conf = _clip1(window['confirmed'], shift['start'], shift['end'])
            hit_poss = _clip1(window['possible'], shift['start'], shift['end'])
            if hit_poss is None:
                continue
            charge_poss[sid][robot].append(hit_poss)
            if hit_conf is not None:
                charge_conf[sid][robot].append(hit_conf)
            sources[sid].add(doc['digest'])
        own_windows = [(s['start'], s['end']) for s in shifts.values() if robot in s['robots']]
        leftover = subtract([window['possible']], own_windows)
        if leftover:
            unattributed.append({'kind': 'charge', 'id': doc['id'], 'reason': '班次窗口外',
                                 'minutes': _r(span_minutes(leftover)), 'source': doc['digest']})

    # 工位等待：按时间窗与班次窗口的交集归属到工位。
    wait_conf = defaultdict(_nested)
    wait_poss = defaultdict(_nested)
    wait_unplaced = defaultdict(float)
    for doc in at(db, 'wait', known_at):
        payload = doc['payload']
        station = payload.get('station')
        window = event_window(payload, skew_seconds)
        if window is None or window['possible'] is None:
            sid = payload.get('shift')
            if sid in shifts:
                wait_unplaced[sid] += float(payload.get('minutes') or 0)
                sources[sid].add(doc['digest'])
            else:
                unattributed.append({'kind': 'wait', 'id': doc['id'], 'reason': '无时间窗且班次未知',
                                     'minutes': payload.get('minutes'), 'source': doc['digest']})
            continue
        for sid, shift in shifts.items():
            hit_poss = _clip1(window['possible'], shift['start'], shift['end'])
            if hit_poss is None:
                continue
            wait_poss[sid][station].append(hit_poss)
            hit_conf = _clip1(window['confirmed'], shift['start'], shift['end'])
            if hit_conf is not None:
                wait_conf[sid][station].append(hit_conf)
            sources[sid].add(doc['digest'])
        leftover = subtract([window['possible']], [(s['start'], s['end']) for s in shifts.values()])
        if leftover:
            unattributed.append({'kind': 'wait', 'id': doc['id'], 'reason': '班次窗口外',
                                 'minutes': _r(span_minutes(leftover)), 'source': doc['digest']})

    # 人工接管：staff_minutes 是人工成本；有时间窗时同时作为机器人停机扣减。
    takeover_conf = defaultdict(_nested)
    takeover_poss = defaultdict(_nested)
    takeover_unplaced = defaultdict(float)
    takeover_staff = defaultdict(float)
    takeover_count = defaultdict(int)
    job_staff = defaultdict(float)
    for doc in at(db, 'takeover', known_at):
        payload = doc['payload']
        minutes = float(payload.get('minutes') or 0)
        job_doc = jobs_by_id.get(payload.get('job'))
        if job_doc is None:
            unattributed.append({'kind': 'takeover', 'id': doc['id'], 'reason': '作业未知',
                                 'minutes': payload.get('minutes'), 'source': doc['digest']})
            continue
        sid = job_doc['payload'].get('shift')
        if sid not in shifts:
            continue
        takeover_count[sid] += 1
        takeover_staff[sid] += minutes
        job_staff[payload.get('job')] += minutes
        sources[sid].add(doc['digest'])
        window = event_window(payload, skew_seconds)
        if window is None or window['possible'] is None:
            takeover_unplaced[sid] += minutes
            continue
        shift = shifts[sid]
        hit_poss = _clip1(window['possible'], shift['start'], shift['end'])
        if hit_poss is not None:
            takeover_poss[sid][payload.get('job')].append(hit_poss)
        hit_conf = _clip1(window['confirmed'], shift['start'], shift['end'])
        if hit_conf is not None:
            takeover_conf[sid][payload.get('job')].append(hit_conf)

    groups = {}
    shift_rows = []
    for sid in sorted(shifts, key=lambda key: (shifts[key]['start'], key)):
        shift = shifts[sid]
        lo, hi = shift['start'], shift['end']
        nominal = (hi - lo).total_seconds() / 60.0
        flags = []
        sources[sid].add(shift['digest'])

        timeline = []
        reported_only = []
        raw_by_robot = defaultdict(list)
        for doc in sorted(per_shift_jobs.get(sid, []), key=lambda item: item['id']):
            payload = doc['payload']
            window = job_window(payload, lo, hi, skew_seconds)
            if window['possible'] is None:
                reported_only.append({'job': doc['id'], 'robot': payload.get('robot'),
                                      'station': payload.get('station'), 'task': payload.get('task'),
                                      'difficulty': payload.get('difficulty') or '未标注',
                                      'status': window['status'],
                                      'units': payload.get('units', 0),
                                      'reported_minutes': payload.get('minutes', 0),
                                      'source': doc['digest']})
                sources[sid].add(doc['digest'])
                continue
            possible = _clip1(window['possible'], lo, hi)
            if possible is None:
                flags.append('作业 %s 时间窗完全在班次窗口外' % doc['id'])
                continue
            confirmed = _clip1(window['confirmed'], lo, hi)
            raw_start = parse_ts(payload.get('started_at'))
            raw_end = parse_ts(payload.get('ended_at'))
            if (raw_start and raw_start < lo) or (raw_end and raw_end > hi):
                flags.append('作业 %s 超出班次窗口' % doc['id'])
            if raw_start and raw_end and raw_end >= raw_start:
                raw_by_robot[payload.get('robot')].append((doc['id'], raw_start, raw_end))
            reported = payload.get('minutes')
            if window['status'] == '完整' and isinstance(reported, (int, float)):
                actual = (raw_end - raw_start).total_seconds() / 60.0
                if abs(actual - reported) * 60.0 > skew_seconds:
                    flags.append('作业 %s 上报时长与时间窗不一致' % doc['id'])
            timeline.append({'doc': doc, 'status': window['status'],
                             'confirmed': confirmed, 'possible': possible})
            sources[sid].add(doc['digest'])

        # 同一机器人作业窗重叠超过时钟偏差：双方都只能进入 possible。
        conflicted = set()
        for robot, items in sorted(raw_by_robot.items()):
            for key_a, key_b, overlap in find_conflicts(items, skew_seconds):
                conflicted.add(key_a)
                conflicted.add(key_b)
                flags.append('作业 %s 与 %s 时间戳冲突（重叠 %.0f 分钟）' % (key_a, key_b, overlap / 60.0))
        for item in timeline:
            if item['doc']['id'] in conflicted:
                item['status'] = '时间戳冲突'
                item['confirmed'] = None

        robot_low = defaultdict(list)
        robot_high = defaultdict(list)
        robot_coverage = defaultdict(list)
        station_robots = defaultdict(set)
        job_rows = []
        for item in timeline:
            payload = item['doc']['payload']
            robot = payload.get('robot')
            station = payload.get('station')
            jid = item['doc']['id']
            station_robots[station].add(robot)
            down_poss = charge_poss[sid][robot] + wait_poss[sid][station] + takeover_poss[sid][jid]
            down_conf = charge_conf[sid][robot] + wait_conf[sid][station] + takeover_conf[sid][jid]
            low_iv = subtract([item['confirmed']], down_poss) if item['confirmed'] else []
            high_iv = subtract([item['possible']], down_conf) if item['possible'] else []
            robot_low[robot].extend(low_iv)
            robot_high[robot].extend(high_iv)
            robot_coverage[robot].append(item['possible'])
            job_rows.append({'job': jid, 'robot': robot, 'station': station,
                             'task': payload.get('task'),
                             'difficulty': payload.get('difficulty') or '未标注',
                             'status': item['status'], 'units': payload.get('units', 0),
                             'reported_minutes': payload.get('minutes', 0),
                             'effective_minutes': {'low': _r(span_minutes(low_iv)),
                                                   'high': _r(span_minutes(high_iv))},
                             'confirmed_window': _iso(item['confirmed']),
                             'possible_window': _iso(item['possible']),
                             'source': item['doc']['digest']})
            key = (station, payload.get('difficulty') or '未标注')
            group = groups.setdefault(key, {'station': station, 'difficulty': key[1], 'jobs': 0,
                                            'units': 0, 'low_by_robot': defaultdict(list),
                                            'high_by_robot': defaultdict(list),
                                            'wait_confirmed': 0.0, 'wait_possible': 0.0,
                                            'takeover_staff_minutes': 0.0})
            group['jobs'] += 1
            group['units'] += payload.get('units', 0)
            group['low_by_robot'][robot].extend(low_iv)
            group['high_by_robot'][robot].extend(high_iv)
            if item['confirmed']:
                group['wait_confirmed'] += span_minutes(intersect(wait_conf[sid][station], [item['confirmed']]))
            group['wait_possible'] += span_minutes(intersect(wait_poss[sid][station], [item['possible']]))
            group['takeover_staff_minutes'] += job_staff.get(jid, 0.0)

        for robot, ivs in charge_poss[sid].items():
            robot_coverage[robot].extend(ivs)
        for station, ivs in wait_poss[sid].items():
            for robot in station_robots.get(station, ()):
                robot_coverage[robot].extend(ivs)
        for jid, ivs in takeover_poss[sid].items():
            job_doc = jobs_by_id.get(jid)
            if job_doc is not None:
                robot_coverage[job_doc['payload'].get('robot')].extend(ivs)

        unplaced = charge_unplaced[sid] + wait_unplaced[sid] + takeover_unplaced[sid]
        robot_rows = []
        low_total = 0.0
        high_total = 0.0
        for robot in sorted(shift['robots']):
            robot_low_min = span_minutes(robot_low[robot])
            robot_high_min = span_minutes(robot_high[robot])
            low_total += robot_low_min
            high_total += robot_high_min
            robot_rows.append({'robot': robot,
                               'effective_minutes': {'low': _r(robot_low_min), 'high': _r(robot_high_min)},
                               'unaccounted_minutes': _r(span_minutes(subtract([(lo, hi)], robot_coverage[robot])))})
        low_total = max(0.0, low_total - unplaced)
        high_total = max(0.0, high_total - unplaced)

        shift_coverage = []
        for ivs in robot_coverage.values():
            shift_coverage.extend(ivs)
        shift_coverage.extend(iv for ivs in wait_poss[sid].values() for iv in ivs)
        unaccounted = span_minutes(subtract([(lo, hi)], shift_coverage))

        units = sum(row['units'] for row in job_rows)
        throughput_low = _r(units / (high_total / 60.0)) if high_total > 0 else None
        throughput_high = _r(units / (low_total / 60.0)) if low_total > 0 else None
        if low_total <= 0:
            flags.append('有效作业下界为 0，吞吐率上界不可定')

        shift_rows.append({
            'shift': sid,
            'window': [lo.isoformat(), hi.isoformat()],
            'nominal_minutes': _r(nominal),
            'units': units,
            'effective_minutes': {'low': _r(low_total), 'high': _r(high_total)},
            'unplaced_down_minutes': _r(unplaced),
            'charge_minutes': {'confirmed': _r(span_minutes([iv for ivs in charge_conf[sid].values() for iv in ivs])),
                               'possible': _r(span_minutes([iv for ivs in charge_poss[sid].values() for iv in ivs])),
                               'unplaced': _r(charge_unplaced[sid])},
            'wait_minutes': {'confirmed': _r(span_minutes([iv for ivs in wait_conf[sid].values() for iv in ivs])),
                             'possible': _r(span_minutes([iv for ivs in wait_poss[sid].values() for iv in ivs])),
                             'unplaced': _r(wait_unplaced[sid])},
            'takeover': {'count': takeover_count[sid],
                         'staff_minutes': _r(takeover_staff[sid]),
                         'confirmed_window_minutes': _r(span_minutes([iv for ivs in takeover_conf[sid].values() for iv in ivs])),
                         'possible_window_minutes': _r(span_minutes([iv for ivs in takeover_poss[sid].values() for iv in ivs])),
                         'unplaced_minutes': _r(takeover_unplaced[sid])},
            'unaccounted_minutes': _r(unaccounted),
            'throughput_per_hour': {'low': throughput_low, 'high': throughput_high},
            'robots': robot_rows,
            'jobs': job_rows,
            'reported_only': reported_only,
            'flags': flags,
            'sources': sorted(sources[sid]),
        })

    group_rows = []
    for key in sorted(groups):
        group = groups[key]
        low = sum(span_minutes(ivs) for ivs in group['low_by_robot'].values())
        high = sum(span_minutes(ivs) for ivs in group['high_by_robot'].values())
        row = {'station': group['station'], 'difficulty': group['difficulty'],
               'jobs': group['jobs'], 'units': group['units'],
               'effective_minutes': {'low': _r(low), 'high': _r(high)},
               'wait_minutes': {'confirmed': _r(group['wait_confirmed']),
                                'possible': _r(group['wait_possible'])},
               'takeover_staff_minutes': _r(group['takeover_staff_minutes']),
               'throughput_per_hour': {'low': _r(group['units'] / (high / 60.0)) if high > 0 else None,
                                       'high': _r(group['units'] / (low / 60.0)) if low > 0 else None}}
        if low <= 0:
            row['note'] = '有效作业下界为 0，吞吐率上界不可定'
        group_rows.append(row)

    return {'known_at': known_at,
            'skew_seconds': skew_seconds,
            'shifts': shift_rows,
            'station_difficulty': group_rows,
            'unassigned_jobs': unassigned_jobs,
            'unattributed': unattributed,
            'issues': issues}
