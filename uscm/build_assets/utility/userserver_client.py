# -*- coding: utf-8 -*-
"""UserServer HTTP 客户端：actioncounts / generateadd 等内部接口。"""

import json
import logging
import os
import urllib.error
import urllib.request

from utility.module_enum import ModuleType, get_generate_action

_ACTIONCOUNTS_TIMEOUT_SEC = 5
_GENERATEADD_TIMEOUT_SEC = 5


def _userserver_base_url(logger: logging.Logger, reqid: str, caller: str) -> str | None:
    host = os.environ.get("US_COMMON_USERSERVER_HOST", "").strip()
    port = os.environ.get("US_COMMON_USERSERVER_PORT", "").strip()
    if not host or not port:
        logger.error(f'reqid:{reqid} {caller} missing US_COMMON_USERSERVER_HOST or US_COMMON_USERSERVER_PORT')
        return None
    return f"http://{host}:{port}"


def fetch_action_counts(
    logger: logging.Logger, reqid: str, first_ip: str, email: str, userid, domain: str, module: ModuleType,
) -> tuple[int, int]:
    """向 UserServer 查询当日/近30天行为次数。domain 为空、无对应 action、或请求失败时返回 (0, 0)。"""
    action = get_generate_action(module)
    domain = domain.strip() if isinstance(domain, str) else str(domain or "").strip()
    if not domain or action is None:
        logger.warning(f'reqid:{reqid} skip actioncounts, domain:{domain!r} action:{action} module:{module}')
        return 0, 0

    base = _userserver_base_url(logger, reqid, "actioncounts")
    if not base:
        return 0, 0

    url = f"{base}/inapi/actioncounts"
    uid = "" if userid in (None, "", 0) else str(userid)
    payload = {
        "reqid": reqid,
        "firstip": first_ip or "",
        "email": email or "",
        "uid": uid,
        "domain": domain,
        "action": action,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_ACTIONCOUNTS_TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or "day_count" not in parsed or "month_count" not in parsed:
                logger.error(f'reqid:{reqid} actioncounts bad response:{raw[:500]}')
                return 0, 0
            return int(parsed["day_count"]), int(parsed["month_count"])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error(f'reqid:{reqid} actioncounts failed url:{url} err:{exc}')
        return 0, 0


def post_generate_add(
    logger: logging.Logger,
    reqid: str,
    first_ip: str,
    email: str,
    userid,
    domain: str,
    generate_content: str,
    allip_address: str,
    module: ModuleType = ModuleType.TTS,
) -> None:
    """向 UserServer /inapi/generateadd 写入一条生成记录；失败只记日志，不抛异常。"""
    generate_type = get_generate_action(module)
    domain = domain.strip() if isinstance(domain, str) else str(domain or "").strip()
    if not domain or not first_ip or generate_type is None:
        logger.warning(
            f'reqid:{reqid} skip generateadd, firstip:{first_ip!r} domain:{domain!r} '
            f'generate_type:{generate_type} module:{module}'
        )
        return

    base = _userserver_base_url(logger, reqid, "generateadd")
    if not base:
        return

    url = f"{base}/inapi/generateadd"
    uid = "" if userid in (None, "", 0) else str(userid)
    payload = {
        "reqid": reqid,
        "firstip": first_ip or "",
        "email": email or "",
        "uid": uid,
        "domain": domain,
        "generate_content": generate_content or "",
        "allip_address": allip_address or "",
        "generate_type": generate_type,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_GENERATEADD_TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or parsed.get("ret") != 0:
                logger.error(f'reqid:{reqid} generateadd bad response:{raw[:500]}')
                return
            logger.debug(f'reqid:{reqid} generateadd ok id:{parsed.get("id")}')
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error(f'reqid:{reqid} generateadd failed url:{url} err:{exc}')
