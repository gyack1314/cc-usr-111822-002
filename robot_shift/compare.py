"""对比两个已知时间点的班次产能结论，标出结论真正改变的班次。

material=true 表示两个版本的有效作业区间 [low, high] 不相交：差异不能被
记录不确定性解释，产能结论真正改变。区间仍有重叠时只标记 changed，
说明数据有修订但结论在不确定范围内。原先用于排班的版本可通过
base_known_at 重放 analyze 得到，资料不可覆盖，结论可追溯。
"""
from collections import defaultdict
from .analysis import analyze
from .evidence import at
from .windows import parse_ts, DEFAULT_SKEW_SECONDS

KIND_LABEL = {'shift': '班次', 'job': '作业', 'charge': '充电记录',
              'wait': '工位等待', 'takeover': '人工接管'}
KINDS = tuple(KIND_LABEL)

def _snapshot(db, known_at):
    return {kind: {doc['id']: doc for doc in at(db, kind, known_at)} for kind in KINDS}

def _document_changes(base, current):
    changes = []
    for kind in KINDS:
        for identity in sorted(set(base[kind]) | set(current[kind])):
            old = base[kind].get(identity)
            new = current[kind].get(identity)
            if old is None:
                changes.append({'kind': kind, 'id': identity, 'base_revision': None,
                                'current_revision': new['revision'], 'payload': new['payload']})
            elif new is None:
                changes.append({'kind': kind, 'id': identity, 'base_revision': old['revision'],
                                'current_revision': None, 'payload': old['payload']})
            elif old['digest'] != new['digest'] or old['revision'] != new['revision']:
                changes.append({'kind': kind, 'id': identity, 'base_revision': old['revision'],
                                'current_revision': new['revision'],
                                'payload': new['payload'], 'base_payload': old['payload']})
    return changes

def _shift_windows(snapshot):
    windows = {}
    for identity, doc in snapshot['shift'].items():
        start = parse_ts(doc['payload'].get('start'))
        end = parse_ts(doc['payload'].get('end'))
        if start is not None and end is not None and end > start:
            windows[identity] = (start, end, set(doc['payload'].get('robots', [])))
    return windows

def _affected_shifts(change, base_snap, current_snap):
    """把资料修订归属到受影响的班次：按上报班次字段、时间窗交集和引用关系。"""
    affected = set()
    if change['kind'] == 'shift':
        affected.add(change['id'])
    windows = {**_shift_windows(base_snap), **_shift_windows(current_snap)}
    job_shift = {}
    for snap in (base_snap, current_snap):
        for identity, doc in snap['job'].items():
            job_shift[identity] = doc['payload'].get('shift')
    station_shifts = defaultdict(set)
    for snap in (base_snap, current_snap):
        for doc in snap['job'].values():
            if doc['payload'].get('shift') and doc['payload'].get('station'):
                station_shifts[doc['payload']['station']].add(doc['payload']['shift'])
    for payload in (change.get('payload'), change.get('base_payload')):
        if not payload:
            continue
        if payload.get('shift'):
            affected.add(payload['shift'])
        if change['kind'] == 'takeover' and payload.get('job') in job_shift:
            affected.add(job_shift[payload['job']])
        if change['kind'] == 'wait' and payload.get('station') in station_shifts:
            affected.update(station_shifts[payload['station']])
        start = parse_ts(payload.get('started_at'))
        if start is not None:
            end = parse_ts(payload.get('ended_at')) or start
            robot = payload.get('robot')
            for sid, (win_start, win_end, robots) in windows.items():
                if change['kind'] == 'charge' and robot and robot not in robots:
                    continue
                if start < win_end and end > win_start:
                    affected.add(sid)
    return affected

def _brief(shift_row):
    if shift_row is None:
        return None
    return {'effective_minutes': shift_row['effective_minutes'],
            'units': shift_row['units'],
            'throughput_per_hour': shift_row['throughput_per_hour'],
            'sources': shift_row['sources']}

def _metrics(shift_row):
    eff = shift_row['effective_minutes']
    thr = shift_row['throughput_per_hour']
    return (eff['low'], eff['high'], shift_row['units'], thr['low'], thr['high'])

def compare(db, base_known_at, current_known_at, skew_seconds=DEFAULT_SKEW_SECONDS):
    """对比 base_known_at（如排班所用版本）与 current_known_at 的班次产能结论。"""
    base = analyze(db, base_known_at, skew_seconds)
    current = analyze(db, current_known_at, skew_seconds)
    base_snap = _snapshot(db, base_known_at)
    current_snap = _snapshot(db, current_known_at)

    per_shift_changes = defaultdict(list)
    unattributed_changes = []
    for change in _document_changes(base_snap, current_snap):
        affected = _affected_shifts(change, base_snap, current_snap)
        if not affected:
            unattributed_changes.append({'kind': change['kind'], 'id': change['id']})
        for sid in sorted(affected):
            per_shift_changes[sid].append(change)

    base_rows = {row['shift']: row for row in base['shifts']}
    current_rows = {row['shift']: row for row in current['shifts']}
    rows = []
    for sid in sorted(set(base_rows) | set(current_rows)):
        old = base_rows.get(sid)
        new = current_rows.get(sid)
        reasons = []
        for change in per_shift_changes.get(sid, []):
            label = KIND_LABEL[change['kind']]
            if change['base_revision'] is None:
                reasons.append('新增%s %s' % (label, change['id']))
            elif change['current_revision'] is None:
                reasons.append('%s %s 在现版本不可见' % (label, change['id']))
            else:
                reasons.append('%s %s 修订 %s→%s' % (label, change['id'],
                                                     change['base_revision'], change['current_revision']))
        if old is not None and new is not None:
            old_status = {job['job']: job['status'] for job in old['jobs']}
            for job in new['jobs']:
                before = old_status.get(job['job'])
                if before is not None and before != job['status']:
                    reasons.append('作业 %s 状态 %s→%s' % (job['job'], before, job['status']))
        changed = old is None or new is None or _metrics(old) != _metrics(new)
        if old is None or new is None:
            material = True
        else:
            old_eff = old['effective_minutes']
            new_eff = new['effective_minutes']
            material = new_eff['high'] < old_eff['low'] or new_eff['low'] > old_eff['high']
        rows.append({'shift': sid, 'changed': changed, 'material': material,
                     'base': _brief(old), 'current': _brief(new), 'reasons': reasons})

    return {'base_known_at': base_known_at,
            'current_known_at': current_known_at,
            'skew_seconds': skew_seconds,
            'material_note': 'material=true 表示两个版本的有效作业区间不相交，产能结论真正改变；'
                             'base 版本可用 analyze 按 base_known_at 重放追溯',
            'shifts': rows,
            'unchanged_shifts': [row['shift'] for row in rows if not row['changed']],
            'unattributed_changes': unattributed_changes}
