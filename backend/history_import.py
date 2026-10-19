"""One local import job and first-use choice; no remote credentials or model calls."""
import copy
import json
from pathlib import Path
import threading
import uuid

from points_history import beijing_time
from task_support import TaskCancelled, TaskIncomplete


ACTIVE_PHASES = {'preparing', 'reading', 'checking', 'waiting', 'saving'}


class HistoryImport:
    def __init__(self, store, reader, path=Path('config/history-import.json')):
        self.store, self.reader, self.path = store, reader, Path(path)
        self.lock = threading.RLock()
        self.thread = None
        self.cancel_event = threading.Event()
        try:
            value = json.loads(self.path.read_text('utf-8-sig'))
            if value.get('choice') not in (None, 'today', 'imported'):
                raise ValueError()
            self.data = dict(choice=value.get('choice'), job=value.get('job'))
        except (OSError, ValueError, TypeError, AttributeError):
            self.data = dict(choice='imported' if store.has_imported_history() else None, job=None)
        job = self.data['job']
        if not isinstance(job, dict):
            self.data['job'] = None
        elif job.get('phase') in ACTIVE_PHASES:
            job.update(phase='interrupted', message='上次导入已中断，原有积分记录保留，可重新导入。')

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def snapshot(self):
        with self.lock:
            return dict(choice=self.data['choice'], show_onboarding=self.data['choice'] is None,
                        active=self.running, job=copy.deepcopy(self.data['job']))

    def skip(self):
        with self.lock:
            if self.running:
                raise ValueError('请等导入结束或取消后，再选择从今天开始记录。')
            self.data['choice'] = 'today'
            self._save()
            return self.snapshot()

    def start(self, *, include_details=False):
        with self.lock:
            if self.running:
                return self.snapshot()
            self.cancel_event = threading.Event()
            self.data['job'] = dict(id=uuid.uuid4().hex[:16], phase='preparing',
                message='正在准备导入', started_at=beijing_time().isoformat(), read_days=0,
                cancel_requested=False, include_details=include_details)
            self._save()
            self.thread = threading.Thread(target=self._run, daemon=True, name='points-history-import')
            self.thread.start()
            return self.snapshot()

    def cancel(self):
        with self.lock:
            if not self.running or self.data['job']['phase'] == 'saving':
                return False
            self.cancel_event.set()
            self.data['job'].update(cancel_requested=True, message='正在取消，当前读取结束后停止。')
            self._save()
            return True

    def _progress(self, phase, message, **fields):
        with self.lock:
            if self.cancel_event.is_set():
                raise TaskCancelled()
            if phase not in ACTIVE_PHASES:
                raise ValueError('导入阶段无效。')
            self.data['job'].update(phase=phase, message=message, **fields)
            self._save()

    def _run(self):
        try:
            options = dict(include_details=True) if self.data['job']['include_details'] else {}
            result = self.reader(self._progress, self.cancel_event, **options)
            self._progress('saving', '正在保存已核实的积分记录')
            counts = self.store.import_daily_totals(result['rows'], return_counts=True)
            ledger_counts = self.store.import_day_ledgers(result.get('ledgers',[]))
            # The reader records these scores before scanning history; an older
            # observation cannot replace a newer same-day live observation.
            if result.get('current_scores'):
                self.store.observe(result['current_scores'], observed_at=result['current_observed_at'])
            with self.lock:
                unconfirmed = result.get('unconfirmed_days', 0)+len(result.get('unconfirmed_ledgers',[]))
                phase = 'partial' if unconfirmed else 'completed'
                self.data['job'].update(phase=phase, finished_at=beijing_time().isoformat(),
                    message='部分日期尚未核实，可重新导入补齐。' if unconfirmed else '历史积分已导入。',
                    counts=counts, read_days=result['read_days'], saved_days=len(result['rows']),
                    unconfirmed_days=result.get('unconfirmed_days',0), date_from=result['date_from'], date_to=result['date_to'],
                    source=result['source'],ledger_counts=ledger_counts,
                    cached_ledger_days=result.get('cached_ledger_days',0),
                    unconfirmed_ledgers=result.get('unconfirmed_ledgers',[]))
                if not unconfirmed:
                    self.data['choice'] = 'imported'
        except TaskCancelled:
            with self.lock:
                self.data['job'].update(phase='cancelled', message='已取消导入，原有积分记录保留。')
        except Exception as error:
            with self.lock:
                message = str(error) if isinstance(error, (TaskIncomplete, ValueError)) else '暂未完成导入，请稍后重试。'
                self.data['job'].update(phase='failed', message=message, error=type(error).__name__)
        finally:
            with self.lock:
                try:
                    self._save()
                except OSError:
                    self.data['job'].update(phase='failed', message='导入状态未能保存，请检查本机磁盘后重试。')
