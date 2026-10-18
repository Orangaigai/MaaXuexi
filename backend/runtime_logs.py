"""One local log writer; task threads enqueue without sleeping or file I/O."""
import atexit
import logging
from logging.handlers import QueueHandler,QueueListener
from pathlib import Path
from queue import SimpleQueue
import threading
import time


class DailyFile(logging.Handler):
    def __init__(self,root):super().__init__();self.root=Path(root)

    def emit(self,record):
        try:
            self.root.mkdir(parents=True,exist_ok=True)
            with (self.root/time.strftime('events-%Y%m%d.log')).open('a',encoding='utf-8') as stream:
                stream.write(record.getMessage()+'\n')
        except OSError:pass


class LogWriter:
    def __init__(self,root):
        self.queue=SimpleQueue();self.logger=logging.Logger('maa-events')
        self.logger.addHandler(QueueHandler(self.queue));self.logger.setLevel(logging.INFO)
        self.listener=QueueListener(self.queue,DailyFile(root));self.listener.start()

    def write(self,line):self.logger.info(line)

    def close(self):self.listener.stop()


_lock=threading.Lock()
_writers={}


def write_event(line,root='logs'):
    key=str(Path(root).resolve())
    with _lock:
        writer=_writers.get(key)
        if writer is None:writer=_writers[key]=LogWriter(key)
        writer.write(line)


def close_logs():
    with _lock:
        for writer in _writers.values():writer.close()
        _writers.clear()


atexit.register(close_logs)
