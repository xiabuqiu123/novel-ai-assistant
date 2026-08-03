from __future__ import annotations

import base64
import ctypes
import getpass
import hashlib
import hmac
import os
import platform
import sys
from ctypes import wintypes
from pathlib import Path

# Legacy v1: XOR keystream keyed by machine/user/home material. This is
# obfuscation only (the key material is public on the machine); it is kept for
# reading values written by older builds. New values on Windows use DPAPI (v2).
PREFIX = "enc:v1:"
PREFIX_DPAPI = "enc:v2:"


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8")
    if sys.platform == "win32":
        token = base64.urlsafe_b64encode(_dpapi_protect(raw)).decode("ascii")
        return PREFIX_DPAPI + token
    nonce = os.urandom(16)
    cipher = _xor_bytes(raw, _keystream(nonce, len(raw)))
    token = base64.urlsafe_b64encode(nonce + cipher).decode("ascii")
    return PREFIX + token


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith(PREFIX_DPAPI):
        try:
            packed = base64.urlsafe_b64decode(value[len(PREFIX_DPAPI) :].encode("ascii"))
        except ValueError:
            return ""
        try:
            plain = _dpapi_unprotect(packed)
        except (OSError, AttributeError):
            # DPAPI is bound to the current Windows user; values written by
            # another account (or read outside Windows) cannot be decrypted.
            return ""
        return _decode_utf8(plain)
    if not value.startswith(PREFIX):
        return value
    try:
        packed = base64.urlsafe_b64decode(value[len(PREFIX) :].encode("ascii"))
    except ValueError:
        return ""
    if len(packed) < 17:
        return ""
    nonce = packed[:16]
    cipher = packed[16:]
    plain = _xor_bytes(cipher, _keystream(nonce, len(cipher)))
    return _decode_utf8(plain)


def _key() -> bytes:
    explicit = os.getenv("NOVEL_AI_KEY_SECRET")
    if explicit:
        material = explicit
    else:
        material = f"{platform.node()}|{getpass.getuser()}|{Path.home()}"
    return hashlib.sha256(material.encode("utf-8")).digest()


def _keystream(nonce: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    key = _key()
    while len(output) < length:
        output.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(output[:length])


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def _setup_dpapi_argtypes(crypt32) -> None:
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    _setup_dpapi_argtypes(crypt32)
    buf = ctypes.create_string_buffer(len(data))
    ctypes.memmove(buf, data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = _DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError(ctypes.get_last_error() or "CryptProtectData failed")
    try:
        return ctypes.string_at(ctypes.c_void_p(blob_out.pbData), blob_out.cbData)
    finally:
        _local_free(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    _setup_dpapi_argtypes(crypt32)
    buf = ctypes.create_string_buffer(len(data))
    ctypes.memmove(buf, data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))
    blob_out = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError(ctypes.get_last_error() or "CryptUnprotectData failed")
    try:
        return ctypes.string_at(ctypes.c_void_p(blob_out.pbData), blob_out.cbData)
    finally:
        _local_free(blob_out.pbData)


def _local_free(ptr) -> None:
    if ptr:
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.LocalFree(ctypes.c_void_p(ptr))


def _decode_utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))
