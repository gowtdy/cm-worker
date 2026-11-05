# -*- coding: utf-8 -*-
"""UserServer HTTP 客户端：按 task_id + domain 更新生成任务状态。"""

import logging
import os

from cmwk_common.module_enum import TaskStatus

_UPDATE_TASK_TIMEOUT_SEC = 3.0


def _userserver_base_url(logger: logging.Logger, task_id: str, caller: str) -> str | None:
    host = os.environ.get("US_COMMON_USERSERVER_HOST", "").strip()
    port = os.environ.get("US_COMMON_USERSERVER_PORT", "").strip()
    if not host or not port:
        logger.error(
            f'task_id:{task_id} {caller} missing US_COMMON_USERSERVER_HOST or US_COMMON_USERSERVER_PORT'
        )
        return None
    return f"http://{host}:{port}"


def post_update_task(
    logger,
    task_id: str,
    domain: str,
    *,
    task_status: str | None = None,
    task_message: str | None = None,
    out_path: str | None = None,
    task_cmd: str | None = None,
    task_req: str | None = None,
    reqid: str | None = None,
    generate_content: str | None = None,
) -> tuple[int, str]:
    """POST /inapi/updatetask。失败只记日志，返回 (1, msg)，不抛异常。"""
    task_id = "" if task_id is None else str(task_id).strip()
    domain = "" if domain is None else str(domain).strip()
    try:
        if not task_id or not domain:
            msg = f'task_id:{task_id!r} or domain:{domain!r} is empty'
            logger.error(f'task_id:{task_id} update_task failed, {msg}')
            return 1, msg

        base = _userserver_base_url(logger, task_id, "updatetask")
        if not base:
            msg = "update_task failed, missing US_COMMON_USERSERVER_HOST or US_COMMON_USERSERVER_PORT"
            return 1, msg

        import requests
        from cmwk_common.internal_auth import bearer_auth_header

        url = f"{base}/inapi/updatetask"
        payload = {
            "task_id": task_id,
            "domain": domain,
        }
        if reqid is not None:
            payload["reqid"] = str(reqid)
        if task_status is not None:
            payload["task_status"] = task_status
        if task_message is not None:
            payload["task_message"] = task_message
        if out_path is not None:
            payload["out_path"] = out_path
        if task_cmd is not None:
            payload["task_cmd"] = task_cmd
        if task_req is not None:
            payload["task_req"] = task_req
        if generate_content is not None:
            payload["generate_content"] = generate_content

        response = requests.post(
            url, json=payload, headers=bearer_auth_header(), timeout=_UPDATE_TASK_TIMEOUT_SEC,
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            msg = f'update_task bad response:{getattr(response, "text", "")[:500]}'
            logger.error(f'task_id:{task_id} {msg}')
            return 1, msg
        raw_ret = parsed.get("ret", 1)
        try:
            ret = int(raw_ret)
        except (TypeError, ValueError):
            ret = 1
        msg = str(parsed.get("msg") or ("ok" if ret == 0 else "update_task failed"))
        if ret != 0:
            logger.error(f'task_id:{task_id} update_task failed, ret:{ret}, response:{getattr(response, "text", "")[:500]}')
            return ret, msg
        return 0, msg
    except Exception as exc:
        msg = f'update_task failed, err:{exc}'
        logger.error(f'task_id:{task_id} {msg}')
        return 1, msg


def update_task_status(
    logger,
    task_id,
    domain,
    task_status: TaskStatus,
    *,
    payload_msg: str | None = None,
    uri: str | None = None,
    generate_content: str | None = None,
) -> tuple[int, str]:
    """uri → out_path，payload_msg → task_message；generate_content 仅在需要时回传。"""
    status_value = task_status.value if isinstance(task_status, TaskStatus) else str(task_status or "")
    reqid = None if task_id is None else str(task_id)
    task_message = None if payload_msg is None else str(payload_msg)
    out_path = None if uri is None else str(uri)
    return post_update_task(
        logger, task_id, domain,
        task_status=status_value or None,
        task_message=task_message,
        out_path=out_path,
        reqid=reqid,
        generate_content=None if generate_content is None else str(generate_content),
    )
