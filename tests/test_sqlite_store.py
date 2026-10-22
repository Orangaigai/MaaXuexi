"""First-use races, interrupted migration and reads during an active writer."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlite_store import store_connection
import sqlite_store
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

    def test_another_thread_cannot_open_until_the_first_schema_is_committed(self):
        initializing=threading.Event();release=threading.Event()
        second_started=threading.Event();second_opened=threading.Event()
        actual_connect=sqlite3.connect;opened=[]
        def connect(*args,**kwargs):
            opened.append(True)
            if len(opened)==2:second_opened.set()
            return actual_connect(*args,**kwargs)
        def initialize(db):
            initializing.set()
            if not release.wait(5):raise TimeoutError('test did not release initializer')
            db.execute('CREATE TABLE sample(value INTEGER)')
        def read(second=False):
            if second:second_started.set()
            with store_connection(self.path,initialize) as db:
                return db.execute('SELECT COUNT(*) FROM sample').fetchone()[0]
        with patch.object(sqlite_store.sqlite3,'connect',side_effect=connect), ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(read)
            try:
                self.assertTrue(initializing.wait(3))
                second=pool.submit(read,True)
                self.assertTrue(second_started.wait(3))
                self.assertFalse(second_opened.wait(.2),'Second connect reached an uninitialized file')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5),0)
            self.assertEqual(second.result(timeout=5),0)
        self.assertEqual(len(opened),2)

    def test_open_guard_does_not_serialize_business_readers(self):
        initialized=lambda db:db.execute('CREATE TABLE sample(value INTEGER)')
        with store_connection(self.path,initialized):pass
        first_in_body=threading.Event();second_in_body=threading.Event()
        def read(first):
            with store_connection(self.path,initialized):
                if first:
                    first_in_body.set()
                    if not second_in_body.wait(3):raise TimeoutError('open guard leaked into body')
                else:
                    second_in_body.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(read,True)
            self.assertTrue(first_in_body.wait(3))
            second=pool.submit(read,False)
            first.result(timeout=5);second.result(timeout=5)

    @staticmethod
    def operational_error(code=sqlite3.SQLITE_READONLY):
        error=sqlite3.OperationalError('injected storage error')
        error.sqlite_errorcode=code
        return error

    def test_transient_readonly_closes_old_connection_and_rolls_back_before_reopening(self):
        actual_connect=sqlite3.connect;connections=[];attempts=[]
        def connect(*args,**kwargs):
            if connections:
                with self.assertRaises(sqlite3.ProgrammingError):connections[-1].execute('SELECT 1')
            db=actual_connect(*args,**kwargs);connections.append(db);return db
        def initialize(db):
            attempts.append(True)
            db.execute('CREATE TABLE sample(value INTEGER)')
            db.execute('INSERT INTO sample VALUES(1)')
            if len(attempts)<3:raise self.operational_error()
        with patch.object(sqlite_store.sqlite3,'connect',side_effect=connect), patch.object(sqlite_store.time,'sleep') as delay:
            with store_connection(self.path,initialize) as db:
                self.assertEqual(db.execute('SELECT * FROM sample').fetchall()[0][0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sample').fetchone()[0],1)
        self.assertEqual(len(connections),3);self.assertEqual(delay.call_count,2)
        with self.assertRaises(sqlite3.ProgrammingError):connections[-1].execute('SELECT 1')

    def test_persistent_readonly_stops_after_three_attempts_without_yielding(self):
        error=self.operational_error();initialize=Mock(side_effect=error)
        with patch.object(sqlite_store.time,'sleep') as delay:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                with store_connection(self.path,initialize):self.fail('Should not yield')
        self.assertIs(caught.exception,error)
        self.assertEqual(initialize.call_count,3);self.assertEqual(delay.call_count,2)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)

    def test_readonly_after_yield_never_replays_body_or_duplicates_a_write(self):
        initialize=Mock(side_effect=lambda db:db.execute('CREATE TABLE sample(value INTEGER)'))
        body=Mock();error=self.operational_error()
        with patch.object(sqlite_store.time,'sleep') as delay:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                with store_connection(self.path,initialize) as db:
                    body();db.execute('INSERT INTO sample VALUES(1)')
                    raise error
        self.assertIs(caught.exception,error);body.assert_called_once();initialize.assert_called_once()
        delay.assert_not_called()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sample').fetchone()[0],0)

    def test_other_or_unclassified_errors_are_not_retried(self):
        errors=[self.operational_error(sqlite3.SQLITE_READONLY_DBMOVED),
                self.operational_error(sqlite3.SQLITE_BUSY),sqlite3.OperationalError('readonly'),OSError('permissions')]
        for error in errors:
            with self.subTest(error=repr(error)):
                initialize=Mock(side_effect=error)
                with patch.object(sqlite_store.time,'sleep') as delay:
                    with self.assertRaises(type(error)):
                        with store_connection(self.path,initialize):self.fail('Should not yield')
                initialize.assert_called_once();delay.assert_not_called()

    def test_initial_connect_readonly_also_has_a_bounded_reopen(self):
        actual_connect=sqlite3.connect;attempts=[]
        def connect(*args,**kwargs):
            attempts.append(True)
            if len(attempts)<3:raise self.operational_error()
            return actual_connect(*args,**kwargs)
        with patch.object(sqlite_store.sqlite3,'connect',side_effect=connect), patch.object(sqlite_store.time,'sleep'):
            with store_connection(self.path,lambda db:db.execute('CREATE TABLE sample(value INTEGER)')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sample').fetchone()[0],0)
        self.assertEqual(len(attempts),3)

    def test_real_sqlite_readonly_connection_is_closed_before_reopening_writable(self):
        self.path.touch()
        actual_connect=sqlite3.connect;connections=[]
        def connect(*args,**kwargs):
            if not connections:
                db=actual_connect(self.path.as_uri()+'?mode=ro',uri=True)
            else:
                with self.assertRaises(sqlite3.ProgrammingError):connections[-1].execute('SELECT 1')
                db=actual_connect(*args,**kwargs)
            connections.append(db)
            return db
        with patch.object(sqlite_store.sqlite3,'connect',side_effect=connect), patch.object(sqlite_store.time,'sleep') as delay:
            with store_connection(self.path,lambda db:db.execute('CREATE TABLE sample(value INTEGER)')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sample').fetchone()[0],0)
        self.assertEqual(len(connections),2);delay.assert_called_once_with(.05)
