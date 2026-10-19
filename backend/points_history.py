"""Local daily score observations; reruns replace snapshots, never add them."""
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import json
import re
import sqlite3
from history_ledger import LedgerStore

from task_support import ALIASES

BEIJING = timezone(timedelta(hours=8))
HISTORY_PATH = Path('config/points-history.sqlite3')
STATES = {'running', 'completed', 'partial', 'needs_human', 'stopped', 'failed'}


def beijing_time(value=None):
    result = datetime.now(BEIJING) if value is None else datetime.fromisoformat(value)
    return (result.replace(tzinfo=BEIJING) if result.tzinfo is None else result.astimezone(BEIJING))


def clean_scores(scores):
    return {name: dict(value=row['value'], maximum=row['maximum'])
            for name, row in scores.items() if name in ALIASES and isinstance(row, dict)
            and type(row.get('value')) is int and type(row.get('maximum')) is int
            and 0 <= row['value'] <= row['maximum'] <= 100 and row['maximum'] > 0}


class PointsHistory(LedgerStore):
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else HISTORY_PATH

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS scores (
                        day TEXT NOT NULL, task TEXT NOT NULL, value INTEGER NOT NULL,
                        maximum INTEGER NOT NULL, observed_at TEXT NOT NULL,
                        PRIMARY KEY(day,task)
                    );
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY, day TEXT NOT NULL, status TEXT NOT NULL,
                        started_at TEXT NOT NULL, finished_at TEXT, selected TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS runs_day ON runs(day,started_at);
                    CREATE TABLE IF NOT EXISTS daily_totals (
                        day TEXT PRIMARY KEY, points INTEGER NOT NULL,
                        observed_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS task_results (
                        day TEXT NOT NULL, task TEXT NOT NULL, state TEXT NOT NULL,
                        reason TEXT NOT NULL, mode TEXT NOT NULL, observed_at TEXT NOT NULL,
                        PRIMARY KEY(day,task)
                    );
                    CREATE TABLE IF NOT EXISTS ledger_days (
                        day TEXT PRIMARY KEY,points INTEGER NOT NULL,entries INTEGER NOT NULL,
                        observed_at TEXT NOT NULL,source TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS score_transactions (
                        day TEXT NOT NULL,position INTEGER NOT NULL,time TEXT NOT NULL,
                        name TEXT NOT NULL,points INTEGER NOT NULL,only_total INTEGER NOT NULL,
                        observed_at TEXT NOT NULL,PRIMARY KEY(day,position)
                    );
                ''')
                yield db
        finally:
            db.close()

    @staticmethod
    def _observe(db, scores, observed):
        stamp = observed.isoformat(timespec='microseconds')
        db.executemany('''INSERT INTO scores VALUES(?,?,?,?,?)
            ON CONFLICT(day,task) DO UPDATE SET value=excluded.value,
            maximum=excluded.maximum,observed_at=excluded.observed_at
            WHERE excluded.observed_at>=scores.observed_at''',
            [(observed.date().isoformat(), name, row['value'], row['maximum'], stamp)
             for name, row in clean_scores(scores).items()])

    def observe(self, scores, *, observed_at=None):
        with self.connect() as db:
            self._observe(db, scores, beijing_time(observed_at))

    def has_imported_history(self):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM daily_totals LIMIT 1').fetchone() is not None

    def import_daily_totals(self, rows, *, observed_at=None, return_counts=False):
        """Import checked past-day totals from the app; never infer task outcomes."""
        observed = beijing_time(observed_at)
        validated = {}
        for row in rows:
            day, points = row.get('day'), row.get('points')
            try:
                parsed = date.fromisoformat(day)
            except (TypeError, ValueError):
                raise ValueError('历史积分日期无效。') from None
            if (parsed.isoformat() != day or not 1970 <= parsed.year <= 2100
                    or parsed >= observed.date() or type(points) is not int or not 0 <= points <= 1000
                    or day in validated and validated[day] != points):
                raise ValueError('请使用已核实的往日积分，同一日期不能有冲突分数。')
            validated[day] = points
        with self.connect() as db:
            existing = {row['day']: row for row in db.execute('SELECT * FROM daily_totals')}
            stamp = observed.isoformat(timespec='microseconds')
            counts = dict(added=0, updated=0, unchanged=0, ignored=0)
            for day, points in validated.items():
                old = existing.get(day)
                key = ('added' if old is None else 'ignored' if old['observed_at'] > stamp else
                       'unchanged' if old['points'] == points else 'updated')
                counts[key] += 1
            db.executemany('''INSERT INTO daily_totals VALUES(?,?,?)
                ON CONFLICT(day) DO UPDATE SET points=excluded.points,observed_at=excluded.observed_at
                WHERE excluded.observed_at>=daily_totals.observed_at''',
                [(day, points, observed.isoformat(timespec='microseconds')) for day, points in validated.items()])
        return counts if return_counts else len(validated)

    def record_run(self, report):
        if not isinstance(report, dict) or report.get('status') not in STATES:
            return False
        run_id, started = report.get('run_id'), report.get('started_at')
        tasks = report.get('tasks')
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', run_id) or not isinstance(tasks, dict):
            return False
        try:
            start = beijing_time(started) if isinstance(started, str) else None
            finish = beijing_time(report['finished_at']) if report.get('finished_at') else None
        except (ValueError, TypeError):
            return False
        if start is None:
            return False
        selected = [name for name in ALIASES if name in tasks]
        if not selected:
            return False
        with self.connect() as db:
            db.execute('''INSERT INTO runs VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,finished_at=excluded.finished_at,selected=excluded.selected
                WHERE runs.finished_at IS NULL OR excluded.finished_at IS NOT NULL''',
                (run_id, start.date().isoformat(), report['status'], start.isoformat(),
                 finish.isoformat() if finish else None, json.dumps(selected, ensure_ascii=False)))
            for name in selected:
                row = tasks[name]
                if not isinstance(row, dict):
                    continue
                state = row.get('state', 'pending')
                if isinstance(state, str) and re.fullmatch(r'[a-z_]{1,32}', state):
                    reason = row.get('reason', '')
                    mode = (row.get('metrics') or {}).get('mode', '') if isinstance(row.get('metrics'), dict) else ''
                    db.execute('''INSERT INTO task_results VALUES(?,?,?,?,?,?)
                        ON CONFLICT(day,task) DO UPDATE SET state=excluded.state,
                        reason=excluded.reason,mode=excluded.mode,observed_at=excluded.observed_at
                        WHERE excluded.observed_at>=task_results.observed_at''',
                        (start.date().isoformat(), name, state, reason[:240] if isinstance(reason, str) else '',
                         mode if mode in ('挑战答题', '双人对战', '四人赛') else '',
                         (finish or start).isoformat(timespec='microseconds')))
                if row.get('before'):
                    self._observe(db, {name: row['before']}, start)
                if row.get('after') and finish and finish.date() == start.date():
                    self._observe(db, {name: row['after']}, finish)
        return True

    def month(self, month=None):
        today = datetime.now(BEIJING).date().isoformat()
        month = month or today[:7]
        if not isinstance(month, str) or not re.fullmatch(r'(?:19[7-9]\d|20\d\d|2100)-(?:0[1-9]|1[0-2])', month):
            raise ValueError('请选择有效月份。')
        with self.connect() as db:
            rows = db.execute('SELECT * FROM scores WHERE day LIKE ? ORDER BY day,task', (month+'-%',)).fetchall()
            runs = db.execute('SELECT * FROM runs WHERE day LIKE ? ORDER BY started_at,run_id', (month+'-%',)).fetchall()
            results = db.execute('SELECT * FROM task_results WHERE day LIKE ?', (month+'-%',)).fetchall()
            totals = db.execute('SELECT * FROM daily_totals WHERE day LIKE ?', (month+'-%',)).fetchall()
            ledgers = db.execute('SELECT * FROM ledger_days WHERE day LIKE ?', (month+'-%',)).fetchall()
            today_points = db.execute('SELECT SUM(value) FROM scores WHERE day=?', (today,)).fetchone()[0]
        days = {}
        for run in runs:
            day = days.setdefault(run['day'], dict(day=run['day'], tasks={}, runs=0, points=None))
            day.update(status=run['status'], selected=json.loads(run['selected']),
                       started_at=run['started_at'], finished_at=run['finished_at'])
            day['runs'] += 1
        for row in rows:
            day = days.setdefault(row['day'], dict(day=row['day'], tasks={}, runs=0, points=None, status='observed'))
            day['tasks'][row['task']] = dict(value=row['value'], maximum=row['maximum'], observed_at=row['observed_at'])
        for row in results:
            if row['day'] in days:
                days[row['day']].setdefault('results', {})[row['task']] = dict(state=row['state'], reason=row['reason'], mode=row['mode'])
        for row in totals:
            day = days.setdefault(row['day'], dict(day=row['day'], tasks={}, runs=0, status='observed'))
            day.update(points=row['points'], points_source='xuexi_points_detail', total_observed_at=row['observed_at'])
        for row in ledgers:
            if row['day'] in days:
                day = days[row['day']]
                day['ledger'] = dict(entries=row['entries'],points=row['points'],source=row['source'],
                                     matches_total=row['points']==day.get('points'))
        for day in days.values():
            if day.get('points_source') != 'xuexi_points_detail':
                day['points'] = sum(row['value'] for row in day['tasks'].values()) if day['tasks'] else None
            if day.get('ledger'):
                day['ledger']['matches_total'] = day['ledger']['points']==day['points']
            day['known_tasks'] = len(day['tasks'])
            day['complete_snapshot'] = len(day['tasks']) == len(ALIASES)
        return dict(month=month, today=today, today_points=today_points, timezone='Asia/Shanghai', days=sorted(days.values(), key=lambda row: row['day']),
                    month_points=sum(day['points'] or 0 for day in days.values()),
                    recorded_days=sum(day['points'] is not None for day in days.values()))
