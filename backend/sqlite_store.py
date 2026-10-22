"""Short-lived SQLite connections with transactional, once-only schema setup."""
from contextlib import contextmanager
import sqlite3
import threading
import time

_OPEN_LOCK = threading.RLock()


def _open_store(path, initialize):
    # A SQLite transaction starts after connect(). On Windows, serialize that
    # earlier file-creation window too. Release before any business operation;
    # connections remain independent, and schema writes stay transactional.
    # Initializers must contain transactional SQL, never external actions.
    with _OPEN_LOCK:
        for attempt in range(3):
            db = None
            try:
                db = sqlite3.connect(path, timeout=5)
                db.row_factory = sqlite3.Row
                if db.execute('PRAGMA user_version').fetchone()[0] == 0:
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        if db.execute('PRAGMA user_version').fetchone()[0] == 0:
                            initialize(db)
                            db.execute('PRAGMA user_version=1')
            except BaseException as error:
                if db is not None:
                    db.close()
                if (not isinstance(error, sqlite3.OperationalError)
                        or getattr(error, 'sqlite_errorcode', None) != sqlite3.SQLITE_READONLY
                        or attempt == 2):
                    raise
                # Reopen only before yielding. Persistent permissions still
                # fail after three attempts; never replay user/body writes.
                time.sleep(.05)
            else:
                return db


@contextmanager
def store_connection(path, initialize, *, write=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = _open_store(path, initialize)
    try:
        with db:
            if write:
                # Read/check/write operations, such as a public-post reservation,
                # must acquire the writer lock before inspecting existing rows.
                db.execute('BEGIN IMMEDIATE')
            yield db
    finally:
        db.close()
