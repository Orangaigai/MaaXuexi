"""Checked, ordered historical transactions; independent of script task results."""
from datetime import date
import re


def checked_ledger(day, points, entries):
    try:
        parsed = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ValueError('流水日期无效。') from None
    if (parsed.isoformat()!=day or not 1970<=parsed.year<=2100 or type(points) is not int
            or not 0<=points<=1000 or not isinstance(entries,list)):
        raise ValueError('流水日期或当日积分无效。')
    cleaned = []
    for entry in entries:
        if not isinstance(entry,dict):
            raise ValueError('流水记录格式无效。')
        clock, name, amount = entry.get('time'), entry.get('name'), entry.get('points')
        if (not isinstance(clock, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock)
                or not isinstance(name, str) or not name.strip() or len(name)>500
                or type(amount) is not int or not -1000<=amount<=1000
                or type(entry.get('only_total',False)) is not bool):
            raise ValueError('流水时间、项目或分数无效。')
        cleaned.append(dict(time=clock, name=name.strip(), points=amount,
                            only_total=bool(entry.get('only_total'))))
    if any(a['time']<b['time'] for a,b in zip(cleaned, cleaned[1:])):
        raise ValueError('流水时间顺序尚未核实。')
    if sum(entry['points'] for entry in cleaned)!=points:
        raise ValueError('流水合计与平台当日积分不一致，未保存该日流水。')
    return dict(day=day, points=points, entries=cleaned)


class LedgerStore:
    def import_day_ledgers(self, days, *, observed_at=None):
        from points_history import beijing_time
        observed = beijing_time(observed_at)
        validated = {}
        for value in days:
            day = checked_ledger(value['day'], value['points'], value['entries'])
            if day['day']>=observed.date().isoformat():
                raise ValueError('历史流水只保存往日记录；今天保留实时核分。')
            if day['day'] in validated and validated[day['day']]!=day:
                raise ValueError('同一天的流水出现冲突。')
            validated[day['day']] = day
        counts = dict(added=0, updated=0, unchanged=0, ignored=0, entries=0)
        stamp = observed.isoformat(timespec='microseconds')
        with self.connect() as db:
            for key, value in validated.items():
                previous = db.execute('SELECT * FROM ledger_days WHERE day=?', (key,)).fetchone()
                if previous and previous['observed_at']>stamp:
                    counts['ignored'] += 1
                    continue
                old = [tuple(row) for row in db.execute('''SELECT time,name,points,only_total
                         FROM score_transactions WHERE day=? ORDER BY position''', (key,))]
                entries = [(row['time'],row['name'],row['points'],int(row['only_total'])) for row in value['entries']]
                counts['added' if previous is None else 'unchanged' if old==entries else 'updated'] += 1
                counts['entries'] += len(entries)
                db.execute('DELETE FROM score_transactions WHERE day=?', (key,))
                # Positions preserve genuine repeated gains at the same minute.
                db.executemany('INSERT INTO score_transactions VALUES(?,?,?,?,?,?,?)',
                    [(key,index,*row,stamp) for index,row in enumerate(entries)])
                db.execute('''INSERT INTO ledger_days VALUES(?,?,?,?,?)
                    ON CONFLICT(day) DO UPDATE SET points=excluded.points,entries=excluded.entries,
                    observed_at=excluded.observed_at,source=excluded.source''',
                    (key,value['points'],len(entries),stamp,'screen_ocr_reconciled'))
        return counts

    def ledger_index(self):
        with self.connect() as db:
            return {row['day']:dict(row) for row in db.execute('SELECT * FROM ledger_days')}

    def ledger_day(self, day):
        checked_ledger(day,0,[])
        with self.connect() as db:
            summary = db.execute('SELECT * FROM ledger_days WHERE day=?', (day,)).fetchone()
            entries = db.execute('''SELECT time,name,points,only_total FROM score_transactions
                                   WHERE day=? ORDER BY position''',(day,)).fetchall()
        return dict(day=day,available=summary is not None,
                    summary=dict(summary) if summary else None,
                    entries=[dict(row,only_total=bool(row['only_total'])) for row in entries])
