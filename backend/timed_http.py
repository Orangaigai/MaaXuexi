"""Reusable HTTP connections with an absolute deadline for timed answers."""
import asyncio
from concurrent.futures import CancelledError, TimeoutError as FutureTimeout
import threading
import time
import httpx


class RequestCancelled(httpx.RequestError):
    pass


class AnswerSession:
    def __init__(self, *, async_transport=None, **options):
        self._regular=httpx.Client(**options)
        self._options=dict(options)
        if async_transport is not None:self._options['transport']=async_transport
        self._lock=threading.RLock();self._ready=threading.Event()
        self._loop=None;self._thread=None;self._async=None;self._pending=set();self._closed=False;self._generation=0

    @property
    def headers(self):return self._regular.headers

    def post(self,*args,**kwargs):return self._regular.post(*args,**kwargs)

    def _run(self):
        loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
        with self._lock:
            self._loop=loop;closed=self._closed;self._ready.set()
        try:
            if not closed:loop.run_forever()
        finally:
            tasks=asyncio.all_tasks(loop)
            for task in tasks:task.cancel()
            if tasks:loop.run_until_complete(asyncio.gather(*tasks,return_exceptions=True))
            if self._async is not None:loop.run_until_complete(self._async.aclose())
            loop.close()

    async def _post(self,url,deadline,kwargs):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError()
        async with asyncio.timeout(remaining):
            if self._async is None:self._async=httpx.AsyncClient(**self._options)
            return await self._async.post(url,**kwargs)

    def post_bounded(self,url,*,total_timeout,stop_requested,**kwargs):
        deadline=time.monotonic()+total_timeout
        with self._lock:
            if self._closed:raise RequestCancelled('Answer client is closed')
            generation=self._generation
            if self._thread is None:
                self._thread=threading.Thread(target=self._run,name='answer-http',daemon=True)
                self._thread.start()
        if not self._ready.wait(max(0,deadline-time.monotonic())):
            raise httpx.ReadTimeout('Total answer deadline reached')
        with self._lock:
            if self._closed or generation!=self._generation:raise RequestCancelled('Answer request cancelled')
            future=asyncio.run_coroutine_threadsafe(self._post(url,deadline,kwargs),self._loop)
            self._pending.add(future)
        try:
            while True:
                if stop_requested():
                    future.cancel();raise RequestCancelled('Answer request cancelled')
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    future.cancel();raise httpx.ReadTimeout('Total answer deadline reached')
                try:return future.result(timeout=min(.05,remaining))
                except FutureTimeout:
                    if future.done():raise httpx.ReadTimeout('Total answer deadline reached') from None
                except CancelledError:raise RequestCancelled('Answer request cancelled') from None
        finally:
            with self._lock:self._pending.discard(future)

    def cancel_pending(self):
        with self._lock:
            self._generation+=1
            for future in tuple(self._pending):future.cancel()

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True;self.cancel_pending();loop=self._loop;thread=self._thread
        self._regular.close()
        if loop and not loop.is_closed():
            try:loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:pass  # The loop may have finished closing.
        if thread and thread is not threading.current_thread():thread.join(2)
