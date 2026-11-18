"""核对版本对比：哪些班次的产能结论真正改变，排班版本可追溯。"""
import json
import unittest
from pathlib import Path
from robot_shift.evidence import connect, receive
from robot_shift.analysis import analyze
from robot_shift.compare import compare

DATA = json.loads((Path(__file__).resolve().parents[1] / 'data' / 'example_timeline.json').read_text(encoding='utf-8'))
BASE = DATA['scheduling_known_at']
NOW = DATA['known_at']
SKEW = 300
WAVE2_CUTOFF = '2026-10-10T00:00:00+08:00'

def wave1_docs():
    return [item for item in DATA['documents'] if item['received_at'] < WAVE2_CUTOFF]

def wave2_docs():
    return [item for item in DATA['documents'] if item['received_at'] >= WAVE2_CUTOFF]

class Compare(unittest.TestCase):
    def setUp(self):
        self.db = connect()
        for item in DATA['documents']:
            receive(self.db, item)
        self.result = compare(self.db, BASE, NOW, SKEW)

    def tearDown(self):
        self.db.close()

    def row(self, sid):
        return next(row for row in self.result['shifts'] if row['shift'] == sid)

    def test_material_change_only_when_ranges_disjoint(self):
        day8 = self.row('day-08')
        self.assertTrue(day8['changed'])
        self.assertTrue(day8['material'])
        self.assertEqual(day8['base']['effective_minutes'], {'low': 280.0, 'high': 320.0})
        self.assertEqual(day8['current']['effective_minutes'], {'low': 340.0, 'high': 400.0})

    def test_overlapping_change_is_not_material(self):
        day7 = self.row('day-07')
        self.assertTrue(day7['changed'])
        self.assertFalse(day7['material'])
        self.assertEqual(day7['base']['effective_minutes'], {'low': 348.0, 'high': 828.0})
        self.assertEqual(day7['current']['effective_minutes'], {'low': 428.0, 'high': 743.0})

    def test_untouched_shift_unchanged(self):
        self.assertEqual(self.result['unchanged_shifts'], ['day-09'])
        day9 = self.row('day-09')
        self.assertFalse(day9['changed'])
        self.assertFalse(day9['material'])

    def test_reasons_explain_documents(self):
        reasons = '；'.join(self.row('day-07')['reasons'])
        self.assertIn('作业 j-102 修订 r1→r2', reasons)
        self.assertIn('充电记录 c-201 修订 r1→r2', reasons)
        self.assertIn('作业 j-102 状态 缺结束记录→完整', reasons)
        reasons8 = '；'.join(self.row('day-08')['reasons'])
        self.assertIn('新增充电记录 c-203', reasons8)

    def test_scheduling_version_reproducible(self):
        """排班所用版本在后续资料到达后仍可原样重放。"""
        db = connect()
        try:
            for item in wave1_docs():
                receive(db, item)
            before = analyze(db, BASE, SKEW)
            for item in wave2_docs():
                receive(db, item)
            after = analyze(db, BASE, SKEW)
            self.assertEqual(before, after)
        finally:
            db.close()

    def test_compare_is_deterministic(self):
        again = compare(self.db, BASE, NOW, SKEW)
        self.assertEqual(self.result, again)

if __name__ == '__main__':
    unittest.main()
