"""Secrets DPAPI upgrade regression tests (2026-08-03, codex/major-fixes).

- Windows: new values are encrypted with CryptProtectData (enc:v2:, current-user
  scope); decrypt round-trips in the same Windows session.
- Legacy enc:v1: values (XOR keystream obfuscation) remain decryptable so older
  databases keep working after the upgrade.
- DPAPI failures (e.g. value written by another Windows user) fail safe to "".
- Non-Windows platforms keep the legacy v1 path so the Android build still works.
"""
from __future__ import annotations

import base64
import sys

import pytest

from app import secrets


def test_windows_encrypt_uses_dpapi_v2_prefix():
    if sys.platform != "win32":
        pytest.skip("DPAPI is Windows-only")
    token = secrets.encrypt_secret("sk-test")
    assert token.startswith(secrets.PREFIX_DPAPI)
    assert secrets.decrypt_secret(token) == "sk-test"


def test_dpapi_roundtrip_preserves_unicode():
    if sys.platform != "win32":
        pytest.skip("DPAPI is Windows-only")
    value = "sk-中文密钥-!@#"
    assert secrets.decrypt_secret(secrets.encrypt_secret(value)) == value


def test_v1_legacy_token_still_decryptable():
    """旧 enc:v1: 数据在升级后仍可解密（老库兼容）。"""
    raw = "sk-legacy-key".encode("utf-8")
    nonce = b"\x00" * 16
    cipher = secrets._xor_bytes(raw, secrets._keystream(nonce, len(raw)))
    token = secrets.PREFIX + base64.urlsafe_b64encode(nonce + cipher).decode("ascii")
    assert token.startswith(secrets.PREFIX)
    assert secrets.decrypt_secret(token) == "sk-legacy-key"


def test_non_windows_encrypt_falls_back_to_v1(monkeypatch):
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    token = secrets.encrypt_secret("sk-mobile")
    assert token.startswith(secrets.PREFIX)
    assert not token.startswith(secrets.PREFIX_DPAPI)
    assert secrets.decrypt_secret(token) == "sk-mobile"


def test_dpapi_failure_fails_safe_to_empty(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("DPAPI is Windows-only")

    def boom(_data: bytes) -> bytes:
        raise OSError("CryptUnprotectData failed")

    monkeypatch.setattr(secrets, "_dpapi_unprotect", boom)
    token = secrets.encrypt_secret("sk-test")
    assert token.startswith(secrets.PREFIX_DPAPI)
    assert secrets.decrypt_secret(token) == ""


def test_plaintext_passthrough_unchanged():
    """未加密的旧值（明文 api_key）原样返回，保持兼容。"""
    assert secrets.decrypt_secret("sk-plain") == "sk-plain"
    assert secrets.decrypt_secret("") == ""
