"""First-use races, interrupted migration and reads during an active writer."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlite_store import store_connection
from comment_tasks import CommentJournal

TEXT = '原来纸巾压花还可以减少分层，学到了！'


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'store.sqlite3'

    def test_concurrent_first_use_initializes_once_and_commits_all_writes(self):
        initialized = []
        def initialize(db):
            initialized.append(True)
            db.execute('CREATE TABLE values_seen(value INTEGER PRIMARY KEY)')
        def write(value):
            with store_connection(self.path, initialize) as db:
                db.execute('INSERT INTO values_seen VALUES(?)', (value,))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(32)))
        with store_connection(self.path, initialize) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM values_seen').fetchone()[0], 32)
        self.assertEqual(len(initialized), 1)

    def test_failed_migration_rolls_back_schema_and_can_restart_cleanly(self):
        def interrupted(db):
            db.execute('CREATE TABLE unfinished(value INTEGER)')
            raise OSError('simulated interruption')
        with self.assertRaises(OSError):
            with store_connection(self.path, interrupted):
                self.fail('An incomplete schema must not reach the caller')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='unfinished'").fetchone())
        with store_connection(self.path, lambda db: db.execute('CREATE TABLE finished(value INTEGER)')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM finished').fetchone()[0], 0)

    def test_recreated_file_is_not_mistaken_for_a_previously_initialized_database(self):
        initialized = []
        def initialize(db):
            initialized.append(True)
            db.execute('CREATE TABLE sample(value INTEGER)')
        with store_connection(self.path, initialize):
            pass
        self.path.unlink()
        with store_connection(self.path, initialize) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)
        self.assertEqual(len(initialized), 2)

    def test_warm_comment_reservations_still_allow_exactly_one_unattended_attempt(self):
        CommentJournal(self.path).recent_texts()  # Schema already exists before the race.
        ready = threading.Barrier(8)
        def reserve(index):
            ready.wait(timeout=5)
            return CommentJournal(self.path).reserve(f'文章{index}', TEXT)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(8))), 1)

    def test_reading_comment_history_does_not_wait_for_an_unrelated_writer(self):
        CommentJournal(self.path).reserve('旧文', TEXT)
        with closing(sqlite3.connect(self.path)) as writer, ThreadPoolExecutor(max_workers=1) as pool:
            writer.execute('BEGIN IMMEDIATE')
            future = pool.submit(CommentJournal(self.path).recent_texts)
            try:
                self.assertEqual(future.result(timeout=2), [TEXT])
            finally:
                writer.rollback()
