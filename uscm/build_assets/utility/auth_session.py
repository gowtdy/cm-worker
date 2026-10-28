import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from config.auth_session_config import (
    SessionConfError,
    normalize_domain,
    resolve_session_conf,
)

# Re-export for callers / tests
__all__ = [
    'SessionConfError',
    'SessionInfo',
    'authenticate_request',
    'check_origin',
    'clear_session_cookie',
    'issue_token',
    'normalize_domain',
    'read_session_token',
    'log_auth_failure',
    'require_origin',
    'resolve_session_conf',
    'set_session_cookie',
    'should_renew',
    'verify_token',
    'verify_token_with_reason',
    'warn_origin_domain_mismatch',
]


def _conf(conf):
    if conf is None:
        raise SessionConfError('session conf is required')
    return conf


def _now(now):
    if now is None:
        return int(time.time())
    return int(now)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(data: str) -> bytes:
    pad = '=' * ((-len(data)) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _header(headers, name: str) -> str:
    if headers is None:
        return ''
    for key in (name, name.lower()):
        value = headers.get(key)
        if value:
            return value
    return ''


def _request_origin(request) -> str:
    headers = getattr(request, 'headers', None)
    origin = _header(headers, 'Origin')
    if origin:
        return origin.rstrip('/')
    referer = _header(headers, 'Referer')
    if not referer:
        return ''
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return ''
    return f'{parsed.scheme}://{parsed.netloc}'


def issue_token(email, now=None, conf=None):
    conf = _conf(conf)
    now_ts = _now(now)
    exp = now_ts + int(conf['session_ttl'])
    payload = {
        'sub': email,
        'typ': conf['token_typ'],
        'iat': now_ts,
        'exp': exp,
        'kid': conf['auth_kid'],
        'aud': conf['site_id'],
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    sig = hmac.new(
        conf['auth_key'].encode('utf-8'),
        payload_b64.encode('ascii'),
        hashlib.sha256,
    ).digest()
    token = payload_b64 + '.' + _b64url_encode(sig)
    return token, exp


def verify_token_with_reason(token, now=None, conf=None):
    try:
        if not isinstance(token, str):
            return None, 'invalid_token'
        parts = token.split('.')
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None, 'invalid_token'
        conf = _conf(conf)
        now_ts = _now(now)
        payload_b64, sig_b64 = parts
        sig = _b64url_decode(sig_b64)
        expected = hmac.new(
            conf['auth_key'].encode('utf-8'),
            payload_b64.encode('ascii'),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(sig, expected):
            return None, 'invalid_token'
        payload = json.loads(_b64url_decode(payload_b64).decode('utf-8'))
        if not isinstance(payload, dict):
            return None, 'invalid_token'
        if payload.get('typ') != conf['token_typ']:
            return None, 'invalid_token'
        if payload.get('kid') != conf['auth_kid']:
            return None, 'invalid_token'
        aud = payload.get('aud')
        if not isinstance(aud, str) or not aud:
            return None, 'aud_mismatch'
        if aud != conf.get('site_id'):
            return None, 'aud_mismatch'
        sub = payload.get('sub')
        if not isinstance(sub, str) or not sub:
            return None, 'invalid_token'
        exp = payload.get('exp')
        if exp is None:
            return None, 'invalid_token'
        if int(exp) <= now_ts:
            return None, 'expired_token'
        return payload, None
    except SessionConfError:
        raise
    except Exception:
        return None, 'invalid_token'


def verify_token(token, now=None, conf=None):
    payload, _reason = verify_token_with_reason(token, now=now, conf=conf)
    return payload


@dataclass
class SessionInfo:
    email: str
    iat: int
    exp: int
    site_id: str = ''


def read_session_token(request, conf) -> str:
    conf = _conf(conf)
    cookies = getattr(request, 'cookies', None)
    if cookies is None:
        return ''
    return cookies.get(conf['cookie_name'], '') or ''


def set_session_cookie(response, token, conf=None):
    conf = _conf(conf)
    response.set_cookie(
        key=conf['cookie_name'],
        value=token,
        max_age=int(conf['session_ttl']),
        domain=conf['cookie_domain'],
        path='/',
        secure=True,
        httponly=True,
        samesite='lax',
    )


def clear_session_cookie(response, conf=None):
    conf = _conf(conf)
    response.delete_cookie(
        key=conf['cookie_name'],
        domain=conf['cookie_domain'],
        path='/',
        secure=True,
        httponly=True,
        samesite='lax',
    )


def check_origin(request, conf=None) -> bool:
    conf = _conf(conf)
    origin = _request_origin(request)
    if not origin:
        return False
    allowed = conf.get('allowed_origins') or []
    return origin in allowed


def warn_origin_domain_mismatch(request, domain, conf=None, logger=None):
    origin = _request_origin(request)
    if not origin:
        return
    try:
        if conf is None:
            conf = resolve_session_conf(domain)
        else:
            conf = _conf(conf)
    except SessionConfError:
        return
    domain_key = normalize_domain(domain)
    origin_host = normalize_domain(origin)
    site_id = conf.get('site_id') or ''
    allowed = conf.get('allowed_origins') or []
    host_mismatch = bool(origin_host) and origin_host != domain_key
    origin_not_allowed = origin not in allowed
    if not host_mismatch and not origin_not_allowed:
        return
    if logger is None:
        return
    url = getattr(request, 'url', None)
    path = getattr(url, 'path', '') if url is not None else ''
    path = path or ''
    logger.warning(
        'origin_domain_mismatch, domain:%s, origin:%s, site_id:%s, path:%s, '
        'host_mismatch:%s, origin_not_allowed:%s',
        domain_key,
        origin,
        site_id,
        path,
        int(host_mismatch),
        int(origin_not_allowed),
    )


def _request_log_fields(request, conf=None):
    method = getattr(request, 'method', '') or ''
    url = getattr(request, 'url', None)
    path = getattr(url, 'path', '') if url is not None else ''
    path = path or ''
    client = getattr(request, 'client', None)
    client_host = getattr(client, 'host', '') if client is not None else ''
    client_host = client_host or ''
    headers = getattr(request, 'headers', None)
    origin = _header(headers, 'Origin')
    referer = _header(headers, 'Referer')
    ua = _header(headers, 'User-Agent')
    cookie_name = ''
    if conf is not None:
        cookie_name = conf.get('cookie_name') or ''
    has_cookie = 0
    cookies = getattr(request, 'cookies', None)
    if cookies is not None and cookie_name:
        if cookies.get(cookie_name, '') or '':
            has_cookie = 1
    return (
        'method:%s, path:%s, client:%s, has_cookie:%s, origin:%s, referer:%s, ua:%s'
        % (method, path, client_host, has_cookie, origin, referer, ua)
    )


def log_auth_failure(kind, reason, request, conf=None, logger=None):
    if logger is None:
        return
    try:
        logger.warning(
            '%s failed, reason:%s, %s',
            kind,
            reason,
            _request_log_fields(request, conf),
        )
    except Exception:
        try:
            logger.warning('%s failed, reason:%s', kind, reason)
        except Exception:
            pass


def require_origin(request, conf=None, logger=None):
    """Return None if origin is allowed, else a reason. Never raises HTTPException.

    reason values: missing_conf, invalid_origin
    """
    if conf is None:
        log_auth_failure('require_origin', 'missing_conf', request, logger=logger)
        return 'missing_conf'
    if not check_origin(request, conf=conf):
        log_auth_failure('require_origin', 'invalid_origin', request, conf, logger=logger)
        return 'invalid_origin'
    return None


def authenticate_request(
    request,
    domain,
    *,
    conf=None,
    log_kind='authenticate_request',
    logger=None,
) -> tuple:
    """Verify cookie + token only. Return (SessionInfo, None) or (None, reason).

    Never raises HTTPException. Does not query the user database — userserver
    uses ``utility.caluma_require_session.require_session`` for that.

    reason values:
      missing_domain, unknown_domain, missing_cookie,
      invalid_token, expired_token, aud_mismatch, verify_error
    """
    kind = log_kind or 'authenticate_request'
    conf_resolved = conf
    try:
        if conf_resolved is None:
            conf_resolved = resolve_session_conf(domain)
        else:
            conf_resolved = _conf(conf_resolved)
    except SessionConfError as exc:
        msg = str(exc)
        reason = 'missing_domain' if 'required' in msg else 'unknown_domain'
        log_auth_failure(kind, reason, request, logger=logger)
        return None, reason
    except Exception:
        log_auth_failure(kind, 'verify_error', request, conf_resolved, logger=logger)
        return None, 'verify_error'

    conf = conf_resolved
    warn_origin_domain_mismatch(request, domain, conf=conf, logger=logger)

    try:
        token = read_session_token(request, conf)
        if not token:
            log_auth_failure(kind, 'missing_cookie', request, conf, logger=logger)
            return None, 'missing_cookie'
        payload, reason = verify_token_with_reason(token, conf=conf)
        if not payload:
            reason = reason or 'invalid_token'
            log_auth_failure(kind, reason, request, conf, logger=logger)
            return None, reason
        iat = int(payload['iat'])
        exp = int(payload['exp'])
        email = payload['sub']
        site_id = conf.get('site_id') or ''
    except Exception:
        log_auth_failure(kind, 'verify_error', request, conf, logger=logger)
        return None, 'verify_error'

    return SessionInfo(email=email, iat=iat, exp=exp, site_id=site_id), None


def should_renew(session, now=None, conf=None) -> bool:
    conf = _conf(conf)
    now_ts = _now(now)
    remaining = session.exp - now_ts
    return remaining < (conf['session_ttl'] / 2)
