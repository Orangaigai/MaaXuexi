"""Windows user-scoped DPAPI storage. Never return a key through HTTP."""
import ctypes
import os
from pathlib import Path
from ctypes import wintypes

KEY_PATH = Path('config/deepseek-key.dpapi')

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]

def _crypt(data: bytes, decrypt: bool) -> bytes:
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    target = Blob()
    if decrypt:
        function = crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        function = crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), 'MaaXuexi DeepSeek', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise OSError('Windows could not access the locally encrypted API key')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)

def load_key() -> str:
    if not KEY_PATH.exists():
        return ''
    return _crypt(bytes.fromhex(KEY_PATH.read_text('ascii').strip()), True).decode('utf-16-le')

def save_key(key: str) -> None:
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError('API Key is empty or contains whitespace')
    encrypted = _crypt(key.encode('utf-16-le'), False).hex()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = KEY_PATH.with_suffix('.tmp')
    temporary.write_text(encrypted, encoding='ascii')
    os.replace(temporary, KEY_PATH)

def key_configured() -> bool:
    try:
        return bool(load_key())
    except OSError:
        return False
