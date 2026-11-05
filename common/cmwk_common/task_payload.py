# -*- coding: utf-8 -*-
"""Celery task payload helpers shared by uscm producer and workers."""

from __future__ import annotations

from typing import Any, Mapping


def envelope(task_id, uid, email: str = '', domain: str = '') -> dict:
    """Build the common envelope keys required on every task payload."""
    return {
        'task_id': '' if task_id is None else str(task_id),
        'uid': 0 if uid is None else uid,
        'email': '' if email is None else str(email),
        'domain': '' if domain is None else str(domain),
    }


def build_payload(task_id, uid, email: str = '', domain: str = '', **biz) -> dict:
    """Merge envelope with task-specific business fields."""
    payload = envelope(task_id=task_id, uid=uid, email=email, domain=domain)
    for key, value in biz.items():
        payload[key] = value
    return payload


def dispatch(sig, payload: Mapping[str, Any]):
    """Send Celery signature with a single keyword payload dict."""
    if not isinstance(payload, dict):
        payload = dict(payload)
    return sig.delay(payload=payload)
