"""Encrypt-at-rest for API keys and other secrets stored in brain.db.

A plaintext value in a SQLite column is exactly as readable as a plaintext
value in a .env file — moving credentials into the database only becomes a
real security improvement if they're actually encrypted. This uses Windows
DPAPI (CryptProtectData/CryptUnprotectData) via raw ctypes calls into
crypt32.dll — no extra pip dependency, and critically, DPAPI ties the
ciphertext to the current Windows user account: the encrypted blob is
useless on a different machine or to a different user, even if the whole
brain.db file leaks.

Not portable off Windows — this project is Windows-only per the spec, so
that's not a gap, but it's why this lives behind a small functional
interface (encrypt/decrypt) rather than being inlined at call sites.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_to_bytes(blob: _DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def encrypt(plaintext: str) -> bytes:
    """Encrypt a secret for storage. Returns raw bytes to store in a BLOB
    column. Empty string in, empty bytes out (nothing to protect)."""
    if not plaintext:
        return b""
    data_in = _blob(plaintext.encode("utf-8"))
    data_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data_in), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return _blob_to_bytes(data_out)
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


def decrypt(ciphertext: bytes) -> str:
    """Inverse of encrypt(). Empty bytes in, empty string out."""
    if not ciphertext:
        return ""
    data_in = _blob(ciphertext)
    data_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data_in), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return _blob_to_bytes(data_out).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


def mask(plaintext: str, keep: int = 4) -> str:
    """For display only — never send full keys back over the API once saved."""
    if len(plaintext) <= keep:
        return "•" * len(plaintext)
    return "•" * (len(plaintext) - keep) + plaintext[-keep:]
