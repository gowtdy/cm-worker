# -*- coding: utf-8 -*-
"""fastText language detection helpers (shareable across workers)."""

from __future__ import annotations

import os
from typing import Iterable, Mapping, Optional

try:
    import fasttext
except ImportError:
    fasttext = None

# IndexTTS text-processing langs with dedicated normalizers / casing rules
INDEXTTS_SUPPORTED_LANGS = frozenset({'zh', 'en', 'ja', 'es', 'ar', 'zhen'})

# Common modellang / API aliases -> ISO-ish codes used by IndexTTS
LANG_ALIAS_MAP = {
    'english': 'en',
    'english_role': 'en',
    'china': 'zh',
    'chinese': 'zh',
    'japan': 'ja',
    'japanese': 'ja',
    'spain': 'es',
    'spanish': 'es',
    'zh-cn': 'zh',
    'zh-tw': 'zh',
    'arabic': 'ar',
    'arab': 'ar',
}

_fasttext_model = None
_DEFAULT_MODEL_PATH = "/app/models/fasttext_models/lid.176.bin"


def is_loaded() -> bool:
    return _fasttext_model is not None


def load_fasttext_model(model_path: Optional[str] = None, logger=None):
    """
    Load lid.176 (or given path) once into module state.
    Safe to call repeatedly; no-op if already loaded.
    """
    global _fasttext_model
    if _fasttext_model is not None:
        return _fasttext_model

    if fasttext is None:
        _log(logger, 'warning', 'fasttext package not installed, language detection disabled')
        return None

    path = model_path or _DEFAULT_MODEL_PATH
    if not os.path.isfile(path):
        _log(logger, 'warning', f'fasttext model not found at {path}, language detection disabled')
        return None

    _log(logger, 'debug', f'Loading fasttext model from {path}')
    _fasttext_model = fasttext.load_model(path)
    _log(logger, 'debug', 'fasttext model loaded')
    return _fasttext_model


def detect_language(
    text: str,
    threshold: float = 0.7,
    min_len: int = 10,
    logger=None,
) -> str:
    """
    Detect text language via fastText.
    Returns ISO language code (e.g. 'en', 'zh') or 'unknown'.
    """
    if not text or len(text.strip()) < min_len:
        return 'unknown'
    if _fasttext_model is None:
        return 'unknown'
    try:
        # fastText predict rejects newlines
        cleaned = text.replace('\n', ' ').strip()
        label, prob = _fasttext_model.predict(cleaned, k=1)
        if prob[0] > threshold:
            return label[0].replace('__label__', '')
        return 'unknown'
    except Exception as e:
        _log(logger, 'warning', f'detect_language error: {e}')
        return 'unknown'


def normalize_lang(
    lang: str,
    default: str = 'en',
    alias_map: Optional[Mapping[str, str]] = None,
    supported_langs: Optional[Iterable[str]] = None,
) -> str:
    """Map aliases to codes; optionally restrict to a supported set."""
    if not lang or not str(lang).strip():
        return default
    aliases = LANG_ALIAS_MAP if alias_map is None else alias_map
    code = aliases.get(str(lang).strip().lower(), str(lang).strip().lower())
    if supported_langs is not None and code not in supported_langs:
        return default
    return code


def resolve_text_lang(
    text: str,
    fallback_lang: str = 'en',
    supported_langs: Optional[Iterable[str]] = None,
    alias_map: Optional[Mapping[str, str]] = None,
    threshold: float = 0.7,
    min_len: int = 10,
    logger=None,
) -> str:
    """
    Prefer fastText detection when confident and (if given) supported;
    otherwise fall back to fallback_lang (after alias normalization).
    """
    allowed = INDEXTTS_SUPPORTED_LANGS if supported_langs is None else frozenset(supported_langs)
    fallback = normalize_lang(
        fallback_lang,
        default='en',
        alias_map=alias_map,
        supported_langs=allowed,
    )
    detected = detect_language(text, threshold=threshold, min_len=min_len, logger=logger)
    if detected != 'unknown' and detected in allowed:
        return detected
    return fallback


def _log(logger, level: str, msg: str):
    if logger is None:
        return
    fn = getattr(logger, level, None)
    if callable(fn):
        fn(msg)
