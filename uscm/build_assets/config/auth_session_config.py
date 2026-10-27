# -*- coding: utf-8 -*-
"""Multi-site session config registry (domain -> session_conf).

Sites are registered in code (get_session_sites). Change config by editing
this file and restarting the service; there is no runtime overlay.
"""

import copy
from urllib.parse import urlparse


class SessionConfError(ValueError):
    """Raised when domain is empty or not registered."""


def normalize_domain(raw):
    if raw is None:
        return ''
    value = str(raw).strip().lower().rstrip('/')
    if not value:
        return ''
    if '://' in value:
        parsed = urlparse(value)
        host = (parsed.netloc or '').lower()
    else:
        host = value.split('/')[0]
    if not host:
        return ''
    if host.endswith(':443') or host.endswith(':80'):
        host = host.rsplit(':', 1)[0]
    return host


def _caluma_site_conf():
    return {
        'site_id': 'caluma.ai',
        'auth_key': 'yaJ8HLH9oIzRJTSGvdeDZMdh31NqTDqDFN_iYis1vs4',
        'auth_kid': 'v1',
        'session_ttl': 1800,
        'google_client_id': (
            '186081097376-dgdb561s2hegvbsme0bcd4m3d2nq1gug.apps.googleusercontent.com'
        ),
        'allowed_origins': [
            'https://caluma.ai',
            'https://www.caluma.ai',
            'https://api.caluma.ai',
        ],
        'cookie_domain': 'caluma.ai',
        'cookie_name': '__Secure-caluma_session',
        'token_typ': 'caluma_auth',
    }


def get_session_sites():
    """Built-in sites; every resolvable domain must be registered here."""
    caluma = _caluma_site_conf()
    return {
        caluma['site_id']: caluma,
    }


def resolve_session_conf(domain):
    site_key = normalize_domain(domain)
    if not site_key:
        raise SessionConfError('domain is required')
    conf = get_session_sites().get(site_key)
    if conf is None:
        raise SessionConfError('unknown domain: %s' % site_key)
    return copy.deepcopy(conf)


def get_session_conf(domain):
    """Same as resolve_session_conf; domain is required (no default site)."""
    return resolve_session_conf(domain)
