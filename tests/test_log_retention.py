"""Only known, closed diagnostics are eligible; no recursive user-data deletion."""
from pathlib import Path
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from log_retention import prune_logs


class ExtendedRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.now=time.time()

    def file(self,name,*,age=20*86400):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'keep or prune')
        stamp=self.now-age;os.utime(path,(stamp,stamp))
        return path

    def test_rotated_framework_logs_are_pruned_but_active_and_unknown_files_survive(self):
        old=self.file('maa/maafw.bak.2026.10.17-07.39.56.701.log')
        active=self.file('maa/maafw.log')
        unknown=self.file('maa/important.log')
        fresh=self.file('maa/maafw.bak.2026.10.22-10.00.00.1.log',age=1)
        result=prune_logs(self.root,now=self.now,max_bytes=0)
        self.assertEqual(result['deleted_files'],1)
        self.assertFalse(old.exists())
        self.assertTrue(all(p.exists() for p in (active,unknown,fresh)))

    def test_daily_and_subscription_evidence_preserve_the_current_run(self):
        keep='a'*12
        current=[self.file(f'daily-finish/{keep}/end.png'),self.file(f'subscriptions-{keep}/result.json')]
        old=[self.file('daily-finish/'+'b'*12+'/end.png'),
             self.file('local-channel-'+'b'*12+'/score.json'),
             self.file('fun-race/20261017-103000-123456/network.png')]
        result=prune_logs(self.root,now=self.now,keep_trace=keep,max_bytes=0)
        self.assertEqual(result['deleted_files'],3)
        self.assertTrue(all(p.exists() for p in current))
        self.assertTrue(all(not p.exists() for p in old))

    def test_unknown_files_exclude_an_entire_evidence_folder(self):
        image=self.file('daily-finish/'+'c'*12+'/screen.png')
        record=self.file('daily-finish/'+'c'*12+'/owner-notes.txt')
        arbitrary=self.file('acceptance-manual/screen.png')
        prune_logs(self.root,now=self.now,max_bytes=0)
        self.assertTrue(all(p.exists() for p in (image,record,arbitrary)))

    def test_recent_frame_protects_its_whole_evidence_group(self):
        old=self.file('daily-finish/'+'b'*12+'/start.json')
        recent=self.file('daily-finish/'+'b'*12+'/end.png',age=1)
        prune_logs(self.root,now=self.now,max_bytes=0)
        self.assertTrue(old.exists());self.assertTrue(recent.exists())
