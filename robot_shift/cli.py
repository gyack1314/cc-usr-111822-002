"""读取业务资料并输出分析。

兼容旧用法：python -m robot_shift.cli data/example.json 等同于 report 子命令。
新口径：
    python -m robot_shift.cli analyze data/example_timeline.json
    python -m robot_shift.cli compare data/example_timeline.json --base 2026-10-10T00:00:00+08:00
"""
import argparse
import json
import sys
from pathlib import Path
from .evidence import connect, receive
from .staffing import shift_report
from .analysis import analyze
from .compare import compare
from .windows import DEFAULT_SKEW_SECONDS

COMMANDS = ('report', 'analyze', 'compare')

def _load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    db = connect()
    for item in data['documents']:
        receive(db, item)
    return data, db

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS:
        argv = ['report'] + argv
    parser = argparse.ArgumentParser(prog='robot_shift')
    sub = parser.add_subparsers(dest='command', required=True)
    p_report = sub.add_parser('report', help='按上报班次汇总的旧口径')
    p_report.add_argument('input')
    p_analyze = sub.add_parser('analyze', help='按时间窗重建有效作业窗口')
    p_analyze.add_argument('input')
    p_analyze.add_argument('--at', help='已知时间点，默认取资料里的 known_at')
    p_analyze.add_argument('--skew-seconds', type=int, default=DEFAULT_SKEW_SECONDS,
                           help='来源间最大时钟偏差（秒）')
    p_compare = sub.add_parser('compare', help='对比排班版本与当前版本的产能结论')
    p_compare.add_argument('input')
    p_compare.add_argument('--base', help='排班所用版本的已知时间点，默认取资料里的 scheduling_known_at')
    p_compare.add_argument('--at', help='当前版本的已知时间点，默认取资料里的 known_at')
    p_compare.add_argument('--skew-seconds', type=int, default=DEFAULT_SKEW_SECONDS,
                           help='来源间最大时钟偏差（秒）')
    args = parser.parse_args(argv)
    data, db = _load(args.input)
    try:
        if args.command == 'report':
            result = shift_report(db, data['known_at'])
        elif args.command == 'analyze':
            result = analyze(db, args.at or data['known_at'], args.skew_seconds)
        else:
            base = args.base or data.get('scheduling_known_at')
            if not base:
                parser.error('compare 需要 --base 或资料里的 scheduling_known_at')
            result = compare(db, base, args.at or data['known_at'], args.skew_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db.close()

if __name__ == '__main__':
    main()
