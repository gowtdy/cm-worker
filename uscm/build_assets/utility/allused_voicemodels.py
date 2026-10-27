# -*- coding: utf-8 -*-
"""全量声音模型解析与查询。"""
import logging

import yaml

from utility.file_reloader import FileReloader, default_reloader

logger = logging.getLogger(__name__)

MODELS_NAME = "allused_voicemodels"
MODELS_PATH = "/app/models/aivoice_models/uscm_allused_voicemodels.yaml"


def _parse_allused_voicemodels(text: str) -> dict:
    """把 YAML 的 models.* 展平为 {catid:name: entry}。

    单个条目不是 dict、或缺少 catid/name 时跳过并记 warning，不拖累整份文件。
    """
    data = yaml.safe_load(text) or {}
    result = {}
    for cat_models in (data.get("models") or {}).values():
        if not isinstance(cat_models, list):
            continue
        for item in cat_models:
            if not isinstance(item, dict):
                logger.warning("allused voicemodel entry is not a dict, skipped: %r", item)
                continue
            catid = item.get("catid")
            name = item.get("name")
            if not catid or not name:
                logger.warning("allused voicemodel entry missing catid/name, skipped: %r", item)
                continue
            result[f"{catid}:{name}"] = item
    return result


def register_allused_voicemodels(reloader: FileReloader | None = None) -> None:
    r = reloader or default_reloader
    data = r.register(MODELS_NAME, MODELS_PATH, _parse_allused_voicemodels)
    logger.info("allused voicemodels loaded: %d", len(data or {}))


def get_common_model(catid: str, name: str, reloader: FileReloader | None = None) -> dict | None:
    r = reloader or default_reloader
    data = r.get(MODELS_NAME)
    return (data or {}).get(f"{catid}:{name}")
