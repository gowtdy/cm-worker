# -*- coding: utf-8 -*-
"""Server-to-server Bearer shared-secret auth for internal APIs (/inapi/*).

Usage (FastAPI)::

    from fastapi import Depends
    from utility.internal_auth import require_internal_auth

    @router.post("/inapi/foo")
    async def foo(request: Request, _auth: None = Depends(require_internal_auth)):
        ...

Caller sends: ``Authorization: Bearer <INAPI_AUTH_TOKEN>``.

If ``INAPI_AUTH_TOKEN`` is empty, verification is skipped (local/dev convenience;
production must set the env var).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

__all__ = [
    'get_inapi_auth_token',
    'extract_bearer_token',
    'verify_bearer_token',
    'check_internal_auth',
    'require_internal_auth',
    'bearer_auth_header',
]

_logger = logging.getLogger(__name__)
_warned_skip = False


def get_inapi_auth_token() -> str:
    """Shared secret for server-to-server /inapi Bearer auth.

    Set via env ``INAPI_AUTH_TOKEN``. Empty means auth is skipped (dev only).
    """
    return (os.getenv('INAPI_AUTH_TOKEN') or '').strip()


def extract_bearer_token(authorization: Optional[str]) -> str:
    """Parse ``Authorization: Bearer <token>``; return token or empty string."""
    if not authorization:
        return ''
    value = str(authorization).strip()
    if not value:
        return ''
    lower = value.lower()
    if not lower.startswith('bearer '):
        return ''
    return value[7:].strip()


def verify_bearer_token(provided: str, expected: str) -> bool:
    """Constant-time compare; both sides must be non-empty to succeed."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def check_internal_auth(
    request: Request,
    expected_token: Optional[str] = None,
    logger=None,
) -> Optional[str]:
    """Validate Bearer token on request.

    Returns:
        None if ok (or auth skipped because expected token is empty).
        Failure reason string otherwise: ``missing_token`` / ``invalid_token``.
    """
    global _warned_skip
    log = logger or _logger
    expected = (expected_token if expected_token is not None else get_inapi_auth_token()) or ''
    expected = str(expected).strip()
    if not expected:
        if not _warned_skip:
            _warned_skip = True
            log.warning(
                'internal_auth: INAPI_AUTH_TOKEN empty, Bearer check skipped'
            )
        return None

    provided = extract_bearer_token(request.headers.get('authorization'))
    if not provided:
        log.warning('internal_auth: missing or malformed Authorization Bearer')
        return 'missing_token'
    if not verify_bearer_token(provided, expected):
        log.warning('internal_auth: invalid Bearer token')
        return 'invalid_token'
    return None


def require_internal_auth(request: Request) -> None:
    """FastAPI dependency: raise 401 when Bearer shared secret check fails."""
    from fastapi import HTTPException
    reason = check_internal_auth(request, logger=_logger)
    if reason is not None:
        raise HTTPException(status_code=401, detail='Unauthorized')


def bearer_auth_header(token: Optional[str] = None) -> dict:
    """Build Authorization header dict for outbound internal calls."""
    value = (token if token is not None else get_inapi_auth_token()) or ''
    value = str(value).strip()
    if not value:
        return {}
    return {'Authorization': f'Bearer {value}'}
