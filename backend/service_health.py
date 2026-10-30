"""Cached, read-only service checks; never call a paid completion or launch a device."""
import copy
import threading
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
import httpx
from key_store import load_key

_lock = threading.Lock()
_updated = 0
_cache = dict(checking=False, checked_at=None,
              network=dict(state='unknown', message='尚未检查'),
              deepseek=dict(state='unknown', message='尚未检查', available=None, balances=[]))

def _balance(client):
    result = dict(state='unknown', message='余额暂未查到', available=None, balances=[])
    try:
        key = load_key()
        if not key:
            return dict(result, state='unconfigured', message='未配置密钥')
        response = client.get('https://api.deepseek.com/user/balance', headers={'Authorization':'Bearer '+key})
        if response.status_code != 200:
            messages = {401:('auth','密钥无效'),403:('auth','账号访问受限'),
                        402:('empty','余额不足，请充值'),429:('error','查询暂时受限，稍后刷新')}
            state, message = messages.get(response.status_code, ('error','余额查询暂不可用'))
            return dict(result, state=state, message=message)
        data = response.json()
        balances = [dict(currency=row['currency'], total_balance=row['total_balance']) for row in data['balance_infos']]
        positive = [row for row in balances if Decimal(row['total_balance'])>0]
        usable = positive or balances
        row = next((row for row in usable if row['currency']=='CNY'), usable[0])
        amount = Decimal(row['total_balance'])
        if not amount.is_finite() or type(data['is_available']) is not bool:
            raise ValueError('Invalid balance response')
        symbol = {'CNY':'¥','USD':'US$'}.get(row['currency'], row['currency']+' ')
        value = format(amount, 'f')
        state = ('empty' if not data['is_available'] and not positive else 'unavailable' if not data['is_available'] else
                 'low' if amount < {'CNY':Decimal('1'),'USD':Decimal('.20')}.get(row['currency'],Decimal('0')) else 'ok')
        suffix = {'low':' · 偏低','empty':' · 余额不足','unavailable':' · 账号不可用'}.get(state,'')
        return dict(state=state, message=symbol+value+suffix, available=data['is_available'], balances=balances)
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, IndexError, InvalidOperation):
        return dict(result, state='error', message='余额查询失败，请刷新')

def _check():
    global _cache, _updated
    try:
        with httpx.Client(timeout=httpx.Timeout(5,connect=3),follow_redirects=True) as client:
            try:
                response = client.head('https://www.xuexi.cn/')
                network = (dict(state='ok',message='官网可达') if response.status_code < 400 else
                           dict(state='error',message='官网访问受限',http_status=response.status_code))
            except httpx.HTTPError:
                network = dict(state='error',message='官网连接超时或失败')
            balance = _balance(client)
    except (OSError,httpx.HTTPError):
        network = dict(state='error',message='官网连接检查失败')
        balance = dict(state='error',message='余额查询失败，请刷新',available=None,balances=[])
    with _lock:
        _cache = dict(checking=False, checked_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                      network=network, deepseek=balance)
        _updated = time.monotonic()

def service_health(refresh=False):
    with _lock:
        if not _cache['checking'] and (refresh or _cache['checked_at'] is None or time.monotonic()-_updated>=60):
            _cache['checking'] = True
            threading.Thread(target=_check,daemon=True,name='service-health').start()
        return copy.deepcopy(_cache)
