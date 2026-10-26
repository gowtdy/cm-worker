# -*- coding: utf-8 -*-
"""Caluma 声音模型解析与查询。"""
import logging

import yaml

from utility.file_reloader import FileReloader, default_reloader

logger = logging.getLogger(__name__)

MODELS_NAME = "caluma"
MODELS_PATH = "/app/models/aivoice_models/caluma_common_models.yaml"


def _parse_caluma(text: str) -> dict:
    """把 caluma YAML 的 models.* 展平为 {catid:name: entry}。

    单个条目不是 dict、或缺少 catid/name 时跳过并记 warning，不拖累整份文件。
    """
    data = yaml.safe_load(text) or {}
    result = {}
    for cat_models in (data.get("models") or {}).values():
        if not isinstance(cat_models, list):
            continue
        for item in cat_models:
            if not isinstance(item, dict):
                logger.warning("caluma model entry is not a dict, skipped: %r", item)
                continue
            catid = item.get("catid")
            name = item.get("name")
            if not catid or not name:
                logger.warning("caluma model entry missing catid/name, skipped: %r", item)
                continue
            result[f"{catid}:{name}"] = item
    return result


def register_caluma(reloader: FileReloader | None = None) -> None:
    r = reloader or default_reloader
    data = r.register(MODELS_NAME, MODELS_PATH, _parse_caluma)
    logger.info("caluma models loaded: %d", len(data or {}))


def get_common_model(catid: str, name: str, reloader: FileReloader | None = None) -> dict | None:
    r = reloader or default_reloader
    data = r.get(MODELS_NAME)
    return (data or {}).get(f"{catid}:{name}")