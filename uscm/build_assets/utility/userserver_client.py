# -*- coding: utf-8 -*-
"""UserServer HTTP 客户端：actioncounts / generateadd 等内部接口。"""

import logging
import os

import httpx

from utility.internal_auth import bearer_auth_header
from utility.module_enum import ModuleType, get_generate_action

_ACTIONCOUNTS_TIMEOUT_SEC = 3.0
_GENERATEADD_TIMEOUT_SEC = 3.0


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
        ret = 1
        msg = f'actioncounts failed'
        logger.error(f'reqid:{reqid} actioncounts failed, missing US_COMMON_USERSERVER_HOST or US_COMMON_USERSERVER_PORT')
        return ret, msg

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
    try:
        response = httpx.post(
            url, json=payload, headers=bearer_auth_header(), timeout=_ACTIONCOUNTS_TIMEOUT_SEC,
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict) or "day_count" not in parsed or "month_count" not in parsed:
            logger.error(f'reqid:{reqid} actioncounts bad response:{response.text[:500]}')
            return 0, 0
        return int(parsed["day_count"]), int(parsed["month_count"])
    except (httpx.HTTPError, TypeError, ValueError) as exc:
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
    task_cmd: str = "",
    task_req: str = "",
) -> tuple[int, str]:
    """向 UserServer /inapi/generateadd 写入一条生成记录；失败只记日志，不抛异常。"""
    ret = 0
    msg = "ok"
    generate_type = module.value
    if not domain or not first_ip or generate_type is None:
        ret = 1
        msg = f'firstip:{first_ip!r} or domain:{domain!r} is empty'
        logger.warning(
            f'reqid:{reqid} generateadd failed, {msg}, generate_type:{generate_type} module:{module}'
        )
        return ret, msg

    base = _userserver_base_url(logger, reqid, "generateadd")
    if not base:
        ret = 1
        msg = "generateadd failed"
        logger.error(
            f'reqid:{reqid} generateadd failed, msg:{msg}, '
            f'missing US_COMMON_USERSERVER_HOST or US_COMMON_USERSERVER_PORT'
        )
        return ret, msg

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
        "task_id": reqid,
        "task_cmd": task_cmd or "",
        "task_req": task_req or "",
    }
    try:
        response = httpx.post(
            url, json=payload, headers=bearer_auth_header(), timeout=_GENERATEADD_TIMEOUT_SEC,
        )
        response.raise_for_status()
        parsed = response.json()
        ret = parsed.get("ret")
        if ret != 0:
            msg = f'generateadd failed'
            logger.error(f'reqid:{reqid}, {msg}, bad response:{response.text[:500]}')
            return ret, msg
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        ret = 1
        msg = f'generateadd failed, url:{url} err:{exc}'
        logger.error(f'reqid:{reqid} {msg}')
        return ret, msg

    return ret, msg