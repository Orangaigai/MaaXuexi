"""Short-lived SQLite connections with transactional, once-only schema setup."""
from contextlib import contextmanager
import sqlite3


@contextmanager
def store_connection(path, initialize, *, write=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] == 0:
            # Serialize first use across both threads and processes. Recheck
            # after the lock; a different connection may have initialized it.
            with db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('PRAGMA user_version').fetchone()[0] == 0:
                    initialize(db)
                    db.execute('PRAGMA user_version=1')
        with db:
            if write:
                # Read/check/write operations, such as a public-post reservation,
                # must acquire the writer lock before inspecting existing rows.
                db.execute('BEGIN IMMEDIATE')
            yield db
    finally:
        db.close()
