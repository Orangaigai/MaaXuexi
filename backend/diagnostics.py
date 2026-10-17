"""Bounded local timings and key-frame replay; never sends input or solves verification."""
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
from pathlib import Path
import threading
import time


class RunDiagnostics:
    def __init__(self, report, log, root=Path('logs/trace')):
        self.report=report;self.log=log;self.lock=threading.RLock()
        self.folder=Path(root)/report.snapshot()['run_id']
        self.started=time.monotonic();self.timings={};self.frames=[]

    def record(self, label, seconds):
        with self.lock:
            row=self.timings.setdefault(label,dict(count=0,seconds=0.,maximum_seconds=0.))
            row['count']+=1;row['seconds']+=max(0.,seconds)
            row['maximum_seconds']=max(row['maximum_seconds'],seconds)

    def flush(self):
        with self.lock:
            summary={label:{key:round(value,4) if isinstance(value,float) else value
                            for key,value in row.items()} for label,row in self.timings.items()}
            self.report.update(diagnostics=dict(timings=summary,key_frames=len(self.frames),
                               trace_path=str(self.folder/'index.json') if self.frames else None))

    @contextmanager
    def phase(self, label):
        previous=self.report.snapshot().get('phase')
        self.report.update(phase=label)
        self.log(label)
        started=time.monotonic()
        try:yield
        finally:
            elapsed=time.monotonic()-started;self.record(label,elapsed);self.flush()
            self.log(f'{label}，耗时{elapsed:.2f}秒')
            # A human-pause or newer stage may already have replaced this label.
            if self.report.snapshot().get('phase')==label:self.report.update(phase=previous)

    def capture(self, scene, records, image):
        if len(self.frames)>=24 or image is None:return
        try:
            from PIL import Image
            self.folder.mkdir(parents=True,exist_ok=True)
            name=f'{len(self.frames)+1:02d}-{scene}.png'
            path=self.folder/name
            Image.fromarray(image[:,:,::-1]).save(path)
            frame=dict(scene=scene,file=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                       seconds=round(time.monotonic()-self.started,3),records=records)
            self.frames.append(frame)
            index=self.folder/'index.json';temporary=index.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(version=1,frames=self.frames),ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(index);self.flush()
        except (OSError,ValueError,TypeError):
            # Diagnostic files cannot change execution or trigger retries.
            return


def record_time(worker,label,seconds):
    diagnostic=getattr(worker,'diagnostics',None)
    if isinstance(diagnostic,RunDiagnostics):diagnostic.record(label,seconds)


def observed(label):
    def decorate(method):
        @wraps(method)
        def wrapped(worker,*args,**kwargs):
            diagnostic=getattr(worker,'diagnostics',None)
            if not isinstance(diagnostic,RunDiagnostics):return method(worker,*args,**kwargs)
            with diagnostic.phase(label):return method(worker,*args,**kwargs)
        return wrapped
    return decorate


def load_replay(index):
    """Only load image paths within this local trace; verify each stored hash."""
    index=Path(index).resolve();folder=index.parent
    value=json.loads(index.read_text('utf-8'))
    if value.get('version')!=1 or not 1<=len(value.get('frames',[]))<=24:
        raise ValueError('Unsupported or empty trace')
    frames=[]
    for frame in value['frames']:
        path=(folder/frame['file']).resolve()
        if not path.is_relative_to(folder) or path.suffix!='.png':raise ValueError('Invalid trace image path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=frame['sha256']:raise ValueError('Trace image hash differs')
        frames.append((frame,path))
    return frames
