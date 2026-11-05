"""Env-backed file delivery settings. No heavyweight imports."""

import os
import socket


def get_file_sync_transport():
    """Cross-region file delivery: quic by default; only 'rsync' selects socat rsync."""
    value = os.environ.get("FILE_SYNC_TRANSPORT", "quic")
    if value is None:
        return "quic"
    normalized = str(value).strip().lower()
    if normalized == "rsync":
        return "rsync"
    return "quic"


def get_quic_client_host():
    return os.environ.get("QUIC_CLIENT_HOST", "").strip()


def get_quic_client_port():
    raw = os.environ.get("QUIC_CLIENT_PORT", "").strip()
    if not raw:
        return 0
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return 0
    if port < 1 or port > 65535:
        return 0
    return port


def get_quic_upload_timeout_sec():
    raw = os.environ.get("QUIC_UPLOAD_TIMEOUT_SEC", "120")
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        return 120
    return timeout if timeout > 0 else 120


def get_quic_upload_path():
    path = os.environ.get("QUIC_UPLOAD_PATH", "/upload").strip() or "/upload"
    if not path.startswith("/"):
        path = "/" + path
    return path


def get_samearea_host():
    return os.environ.get("UPLOAD_SAMEAREA_RECIEVE_HOST", "").strip()


def get_samearea_port():
    raw = os.environ.get("UPLOAD_SAMEAREA_RECIEVE_PORT", "").strip()
    if not raw:
        return 0
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return 0
    if port < 1 or port > 65535:
        return 0
    return port


def get_samearea_token():
    return os.environ.get("UPLOAD_SAMEAREA_TOKEN", "").strip()


def get_samearea_upload_path():
    path = os.environ.get("UPLOAD_SAMEAREA_UPLOAD_PATH", "/upload").strip() or "/upload"
    if not path.startswith("/"):
        path = "/" + path
    return path


def get_samearea_timeout_sec():
    raw = os.environ.get("UPLOAD_SAMEAREA_TIMEOUT_SEC", "120")
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        return 120
    return timeout if timeout > 0 else 120


def get_quic_sender():
    """Docker container name via hostname (compose sets hostname=container_name)."""
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    cleaned = (host or "").strip().lower().replace("\n", "").replace("\r", "")
    if not cleaned:
        return "unknown"
    return cleaned[:64]
