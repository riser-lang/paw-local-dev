"""PAW Security Module - Windows DPAPI Encryption & Sensitive Data Sanitization.

Enforces Requirements:
- 4.2.1: API Key encrypted storage via Windows DPAPI, NO plaintext on disk.
- 4.2.2: Key never echoed back to UI, only mask (e.g., sk-...8f4a).
- 4.2.3: Sensitive key tokens sanitized from logs, exceptions, and diagnostics.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import logging
import os
import re
import sys
from typing import Optional

logger = logging.getLogger("paw.security")

# Token sanitization regex for log filtering
SENSITIVE_KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{8,})|(key-[A-Za-z0-9_\-]{8,})", re.IGNORECASE)


class SensitiveDataLogFilter(logging.Filter):
    """Logging filter that automatically redacts API keys from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = SENSITIVE_KEY_PATTERN.sub(r"sk-...[REDACTED]", record.msg)
        if record.args:
            clean_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    clean_args.append(SENSITIVE_KEY_PATTERN.sub(r"sk-...[REDACTED]", arg))
                else:
                    clean_args.append(arg)
            record.args = tuple(clean_args)
        return True


# ── Windows DPAPI Implementation ─────────────────────────────────────────────

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_encrypt(data: bytes) -> bytes:
    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    # 0x01 = CRYPTPROTECT_UI_FORBIDDEN
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), "PAW_Secret", None, None, None, 0x01, ctypes.byref(blob_out)
    ):
        raise ctypes.WinError()
    buf = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return buf


def _dpapi_decrypt(data: bytes) -> bytes:
    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)
    ):
        raise ctypes.WinError()
    buf = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return buf


class DPAPIProtectionError(RuntimeError):
    """Raised when Windows DPAPI encryption fails. Fallback to base64 is strictly forbidden."""
    pass


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string using Windows DPAPI.
    
    If encryption fails, fallback to plaintext/base64 is strictly forbidden.
    Raises DPAPIProtectionError with human-readable error guidance.
    """
    if not plaintext:
        return ""
    # If already encrypted, return as is
    if plaintext.startswith("dpapi:"):
        return plaintext

    if sys.platform == "win32":
        try:
            raw_enc = _dpapi_encrypt(plaintext.encode("utf-8"))
            return "dpapi:" + base64.b64encode(raw_enc).decode("ascii")
        except Exception as e:
            logger.error(f"DPAPI encryption failed: {e}")
            raise DPAPIProtectionError("Windows 数据保护服务不可用，请检查账户权限。") from e
    else:
        # Non-Windows environment without DPAPI: strictly forbid plaintext/base64 storage for API Keys
        raise DPAPIProtectionError("Windows 数据保护服务不可用，请检查账户权限。")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt an encrypted payload back to plaintext string."""
    if not ciphertext:
        return ""
    if ciphertext.startswith("dpapi:") and sys.platform == "win32":
        try:
            raw = base64.b64decode(ciphertext[6:])
            return _dpapi_decrypt(raw).decode("utf-8")
        except Exception as e:
            logger.error(f"DPAPI decryption failed: {e}")
            return ""
    elif ciphertext.startswith("b64:"):
        try:
            return base64.b64decode(ciphertext[4:]).decode("utf-8")
        except Exception:
            return ""
    # If unencrypted legacy string, return as is (and it will be migrated on next save)
    return ciphertext


def mask_secret(secret: str) -> str:
    """Return a masked representation (e.g., sk-...8f4a) for UI display without leaking key."""
    if not secret:
        return ""
    clean = secret
    if clean.startswith("dpapi:") or clean.startswith("b64:"):
        clean = decrypt_secret(clean)
    if len(clean) <= 6:
        return "******"
    prefix = clean[:3] if clean.startswith("sk-") else clean[:2]
    suffix = clean[-4:]
    return f"{prefix}...{suffix}"


# ── R5 Local Service Security & Anti-CSRF ────────────────────────────────────

import secrets
from urllib.parse import urlparse

# Random launch token generated on each server startup (R5)
LOCAL_LAUNCH_TOKEN: str = secrets.token_urlsafe(32)

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "127.0.0.1:8800", "localhost:8800"})
ALLOWED_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost"})


def is_allowed_origin(origin: Optional[str]) -> bool:
    """Validate that Origin/Referer comes strictly from 127.0.0.1 or localhost."""
    if not origin:
        return True  # Native/direct local request
    try:
        parsed = urlparse(origin)
        hostname = parsed.hostname or ""
        return hostname.lower() in ALLOWED_ORIGIN_HOSTS
    except Exception:
        return False


def verify_request_security(host: Optional[str], origin: Optional[str], method: str, token_header: Optional[str] = None, session_cookie: Optional[str] = None) -> tuple[bool, str]:
    """Verify request security against malicious cross-origin requests (R5)."""
    # 1. Host header validation
    if host:
        host_clean = host.split(":")[0].lower()
        if host_clean not in ALLOWED_ORIGIN_HOSTS:
            return False, f"Invalid Host header: {host}"

    # 2. Origin validation (prohibit wildcard CORS and foreign web origins)
    if origin and not is_allowed_origin(origin):
        return False, f"Rejected cross-origin request from untrusted origin: {origin}"

    # 3. For mutating requests (POST, PUT, DELETE, PATCH), enforce local authorization
    if method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
        # If local token header is provided, it must match
        if token_header is not None:
            if token_header == LOCAL_LAUNCH_TOKEN:
                return True, "ok"
            return False, "Invalid local token header"
        # Allow if paw_session cookie is set (local workbench session)
        if session_cookie and session_cookie == LOCAL_LAUNCH_TOKEN:
            return True, "ok"
        # If native direct process (no origin) and local host
        if not origin and host and host.split(":")[0].lower() in ALLOWED_ORIGIN_HOSTS:
            return True, "ok"
        return False, "CSRF / Local authorization token required for mutating requests"

    return True, "ok"

