# -*- coding: utf-8 -*-
"""Caluma 声音模型解析与查询。"""
import yaml


def _parse_caluma(text: str) -> dict:
    """把 caluma YAML 的 models.* 展平为 {catid:name: entry}。"""
    data = yaml.safe_load(text) or {}
    result = {}
    for cat_models in (data.get("models") or {}).values():
        for item in cat_models:
            result[f"{item['catid']}:{item['name']}"] = item
    return result