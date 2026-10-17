"""Account cycle counts, round deduplication and interrupted-follow cleanup."""
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path('config/subscriptions.sqlite3')


def account_key(name, kind='强国号'):
    name = re.sub(r'\s+', '', name)
    return hashlib.sha256((kind + '\n' + name).encode('utf-8')).hexdigest()


class SubscriptionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempts'").fetchone()
            if legacy:
                backup = self.path.with_name(self.path.stem + '.before-counts.sqlite3')
                number = 2
                while backup.exists():
                    backup = self.path.with_name(self.path.stem + f'.before-counts-{number}.sqlite3')
                    number += 1
                snapshot = sqlite3.connect(backup)
                try:
                    db.backup(snapshot)
                finally:
                    snapshot.close()
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    account_key TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    last_round INTEGER NOT NULL DEFAULT 0,
                    pending INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS subscription_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    current_round INTEGER NOT NULL DEFAULT 1,
                    category_cursor INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO subscription_state(singleton) VALUES(1);
            ''')
            if legacy:
                columns = {r['name'] for r in db.execute('PRAGMA table_info(attempts)')}
                round_column = 'round_id' if 'round_id' in columns else '1'
                # Old "cleaned" also included failed follows absent from My subscriptions.
                # Preserve dedup/pending state but do not invent completed cycle counts.
                rows = db.execute(f'''SELECT account_key,name,kind,MAX({round_column}) last_round,
                    MAX(cleanup='pending') pending FROM attempts GROUP BY account_key''').fetchall()
                db.executemany('''INSERT OR IGNORE INTO accounts
                    (account_key,name,kind,last_round,pending) VALUES(?,?,?,?,?)''',
                    [(r['account_key'],r['name'],r['kind'],r['last_round'],r['pending']) for r in rows])
                db.execute('DROP TABLE attempts')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def eligible(self, name, kind='强国号'):
        with self.connect() as db:
            row = db.execute('''SELECT 1 FROM accounts WHERE account_key=? AND
                (last_round=(SELECT current_round FROM subscription_state WHERE singleton=1)
                 OR pending=1)''',(account_key(name,kind),)).fetchone()
        return row is None

    def progress(self):
        with self.connect() as db:
            return dict(db.execute('SELECT current_round,category_cursor FROM subscription_state WHERE singleton=1').fetchone())

    def save_category_cursor(self, cursor):
        with self.connect() as db:
            db.execute('UPDATE subscription_state SET category_cursor=? WHERE singleton=1',(cursor,))

    def finish_round(self, scanned_accounts):
        """Advance only after a full configured scan and cleanup; resume next run."""
        if scanned_accounts <= 0:
            return False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM accounts WHERE pending=1 LIMIT 1').fetchone():
                return False
            if not db.execute('''SELECT 1 FROM accounts WHERE
                last_round=(SELECT current_round FROM subscription_state WHERE singleton=1) LIMIT 1''').fetchone():
                return False
            db.execute('UPDATE subscription_state SET current_round=current_round+1,category_cursor=0 WHERE singleton=1')
            return True

    def begin(self, name, kind='强国号'):
        """Save pending intent before input; it does not count a completed cycle."""
        key = account_key(name,kind)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            round_id = db.execute('SELECT current_round FROM subscription_state WHERE singleton=1').fetchone()[0]
            row = db.execute('SELECT * FROM accounts WHERE account_key=?',(key,)).fetchone()
            if row and (row['pending'] or row['last_round'] == round_id):
                raise ValueError('该账号本轮已尝试或仍待取消')
            db.execute('''INSERT INTO accounts(account_key,name,kind,last_round,pending)
                VALUES(?,?,?,?,1) ON CONFLICT(account_key) DO UPDATE SET
                name=excluded.name,kind=excluded.kind,last_round=excluded.last_round,pending=1''',
                (key,name,kind,round_id))
        return key

    def cleaned(self, key, completed=False):
        """Increment once ONLY after an observed follow was actually cancelled."""
        with self.connect() as db:
            db.execute('''UPDATE accounts SET completed_count=completed_count+CASE
                WHEN pending=1 AND ? THEN 1 ELSE 0 END,pending=0 WHERE account_key=?''',
                (bool(completed),key))
            row = db.execute('SELECT completed_count FROM accounts WHERE account_key=?',(key,)).fetchone()
            return row[0] if row else 0

    def pending(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT *,account_key id FROM accounts WHERE pending=1 ORDER BY rowid')]

    def stats(self):
        with self.connect() as db:
            return dict(db.execute('''SELECT COUNT(*) accounts,
                COALESCE(SUM(completed_count),0) completed_operations,
                COALESCE(SUM(pending),0) pending_cleanup FROM accounts''').fetchone())
