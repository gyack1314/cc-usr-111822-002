"""核对时间窗重建：区间运算、时钟偏差、缺结束与冲突的口径。"""
import json
import unittest
from datetime import datetime
from pathlib import Path
from robot_shift.evidence import connect, receive
from robot_shift.intervals import merge, subtract, intersect, clip, minutes
from robot_shift.windows import job_window, event_window, find_conflicts
from robot_shift.analysis import analyze

DATA = json.loads((Path(__file__).resolve().parents[1] / 'data' / 'example_timeline.json').read_text(encoding='utf-8'))
BASE = DATA['scheduling_known_at']
NOW = DATA['known_at']
SKEW = 300

def ts(text):
    return datetime.fromisoformat(text)

def load(docs=None):
    db = connect()
    for item in (docs if docs is not None else DATA['documents']):
        receive(db, item)
    return db

def shift_row(report, sid):
    return next(row for row in report['shifts'] if row['shift'] == sid)

def job_row(row, jid):
    return next(job for job in row['jobs'] if job['job'] == jid)

class Intervals(unittest.TestCase):
    def test_merge_and_minutes(self):
        ivs = [(ts('2026-10-07T08:00+08:00'), ts('2026-10-07T09:00+08:00')),
               (ts('2026-10-07T08:30+08:00'), ts('2026-10-07T10:00+08:00')),
               (ts('2026-10-07T11:00+08:00'), ts('2026-10-07T11:30+08:00'))]
        self.assertEqual(minutes(ivs), 150.0)
        self.assertEqual(len(merge(ivs)), 2)

    def test_subtract(self):
        a = [(ts('2026-10-07T08:00+08:00'), ts('2026-10-07T12:00+08:00'))]
        b = [(ts('2026-10-07T09:00+08:00'), ts('2026-10-07T09:30+08:00'))]
        self.assertEqual(minutes(subtract(a, b)), 210.0)

    def test_intersect_and_clip(self):
        a = [(ts('2026-10-07T08:00+08:00'), ts('2026-10-07T12:00+08:00'))]
        b = [(ts('2026-10-07T11:00+08:00'), ts('2026-10-07T13:00+08:00'))]
        self.assertEqual(minutes(intersect(a, b)), 60.0)
        self.assertEqual(minutes(clip(a, ts('2026-10-07T10:00+08:00'), ts('2026-10-07T14:00+08:00'))), 120.0)

class Windows(unittest.TestCase):
    SHIFT_START = ts('2026-10-07T08:00:00+08:00')
    SHIFT_END = ts('2026-10-07T16:00:00+08:00')

    def test_complete_window_skew(self):
        w = job_window({'started_at': '2026-10-07T09:00:00+08:00',
                        'ended_at': '2026-10-07T10:00:00+08:00'}, self.SHIFT_START, self.SHIFT_END, 300)
        self.assertEqual(w['status'], '完整')
        self.assertEqual(w['confirmed'], (ts('2026-10-07T09:05:00+08:00'), ts('2026-10-07T09:55:00+08:00')))
        self.assertEqual(w['possible'], (ts('2026-10-07T08:55:00+08:00'), ts('2026-10-07T10:05:00+08:00')))

    def test_missing_end_bounded_by_shift(self):
        w = job_window({'started_at': '2026-10-07T13:00:00+08:00'}, self.SHIFT_START, self.SHIFT_END, 300)
        self.assertEqual(w['status'], '缺结束记录')
        self.assertIsNone(w['confirmed'])
        self.assertEqual(w['possible'][1], self.SHIFT_END)

    def test_inverted_timestamps_conflict(self):
        w = job_window({'started_at': '2026-10-07T10:00:00+08:00',
                        'ended_at': '2026-10-07T09:00:00+08:00'}, self.SHIFT_START, self.SHIFT_END, 300)
        self.assertEqual(w['status'], '时间戳冲突')
        self.assertIsNone(w['confirmed'])
        self.assertEqual(w['possible'], (ts('2026-10-07T08:55:00+08:00'), ts('2026-10-07T10:05:00+08:00')))

    def test_no_timestamps(self):
        self.assertEqual(job_window({'minutes': 30}, self.SHIFT_START, self.SHIFT_END)['status'], '无时间戳')
        self.assertEqual(job_window({'started_at': 'not-a-time'}, self.SHIFT_START, self.SHIFT_END)['status'],
                         '时间戳无法解析')

    def test_event_window_from_minutes(self):
        w = event_window({'started_at': '2026-10-07T09:30:00+08:00', 'minutes': 12}, 300)
        self.assertEqual(w['possible'][1], ts('2026-10-07T09:47:00+08:00'))
        self.assertIsNone(event_window({'minutes': 12}, 300))

    def test_find_conflicts_respects_skew(self):
        items = [('a', ts('2026-10-07T08:00+08:00'), ts('2026-10-07T09:00+08:00')),
                 ('b', ts('2026-10-07T08:58+08:00'), ts('2026-10-07T10:00+08:00')),
                 ('c', ts('2026-10-07T10:30+08:00'), ts('2026-10-07T11:00+08:00'))]
        self.assertEqual(find_conflicts(items, 300), [])
        self.assertEqual(len(find_conflicts(items, 60)), 1)

class AnalyzeBase(unittest.TestCase):
    def setUp(self):
        self.db = load()
        self.report = analyze(self.db, BASE, SKEW)

    def tearDown(self):
        self.db.close()

    def test_shift_effective_ranges(self):
        self.assertEqual(shift_row(self.report, 'day-07')['effective_minutes'], {'low': 348.0, 'high': 828.0})
        self.assertEqual(shift_row(self.report, 'day-08')['effective_minutes'], {'low': 280.0, 'high': 320.0})
        self.assertEqual(shift_row(self.report, 'day-09')['effective_minutes'], {'low': 210.0, 'high': 230.0})

    def test_open_and_conflicted_jobs_not_confirmed(self):
        day7 = shift_row(self.report, 'day-07')
        self.assertEqual(job_row(day7, 'j-102')['status'], '缺结束记录')
        self.assertEqual(job_row(day7, 'j-102')['effective_minutes']['low'], 0.0)
        self.assertEqual(job_row(day7, 'j-104')['status'], '时间戳冲突')
        self.assertEqual(job_row(day7, 'j-105')['status'], '时间戳冲突')
        self.assertTrue(any('时间戳冲突' in flag for flag in day7['flags']))

    def test_cross_shift_charge_counted_on_reported_shift_only_without_window(self):
        day7 = shift_row(self.report, 'day-07')
        day8 = shift_row(self.report, 'day-08')
        self.assertEqual(day7['charge_minutes']['unplaced'], 30.0)
        self.assertEqual(day8['charge_minutes']['unplaced'], 90.0)

    def test_gaps_not_treated_as_effective(self):
        for row in self.report['shifts']:
            nominal = row['nominal_minutes']
            for robot in row['robots']:
                self.assertLessEqual(robot['effective_minutes']['high'] + robot['unaccounted_minutes'],
                                     nominal + 1e-6)
        day8 = shift_row(self.report, 'day-08')
        robot_r2 = next(r for r in day8['robots'] if r['robot'] == 'R2')
        self.assertEqual(robot_r2['unaccounted_minutes'], 480.0)
        self.assertEqual(day8['units'], 56)
        self.assertEqual([r['job'] for r in day8['reported_only']], ['j-108'])

    def test_sources_traceable(self):
        day7 = shift_row(self.report, 'day-07')
        self.assertTrue(day7['sources'])
        self.assertEqual(len(day7['sources']), len(set(day7['sources'])))

class AnalyzeCurrent(unittest.TestCase):
    def setUp(self):
        self.db = load()
        self.report = analyze(self.db, NOW, SKEW)

    def tearDown(self):
        self.db.close()

    def test_corrected_charge_splits_by_window(self):
        day7 = shift_row(self.report, 'day-07')
        day8 = shift_row(self.report, 'day-08')
        self.assertEqual(day7['charge_minutes']['confirmed'], 80.0)
        self.assertEqual(day7['charge_minutes']['possible'], 95.0)
        self.assertEqual(day8['charge_minutes']['unplaced'], 0.0)
        self.assertEqual(day8['charge_minutes']['confirmed'], 15.0)
        outside = {item['id']: item['minutes'] for item in self.report['unattributed']}
        self.assertEqual(outside, {'c-201': 5.0, 'c-204': 10.0})

    def test_late_end_record_narrows_range(self):
        day7 = shift_row(self.report, 'day-07')
        self.assertEqual(job_row(day7, 'j-102')['status'], '完整')
        self.assertEqual(day7['effective_minutes'], {'low': 428.0, 'high': 743.0})
        self.assertTrue(any('时长与时间窗不一致' in flag for flag in day7['flags']))

    def test_station_difficulty_rollup(self):
        groups = {(g['station'], g['difficulty']): g for g in self.report['station_difficulty']}
        s1b = groups[('S1', 'B')]
        self.assertEqual(s1b['units'], 128)
        self.assertEqual(s1b['effective_minutes'], {'low': 658.0, 'high': 798.0})
        self.assertEqual(s1b['wait_minutes'], {'confirmed': 10.0, 'possible': 30.0})
        self.assertEqual(s1b['takeover_staff_minutes'], 12.0)
        unlabeled = groups[('S2', '未标注')]
        self.assertIsNone(unlabeled['throughput_per_hour']['high'])
        self.assertIn('note', unlabeled)

if __name__ == '__main__':
    unittest.main()
