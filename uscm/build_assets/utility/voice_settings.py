# -*- coding: utf-8 -*-
"""Voice setting params for TTS requests (emo / speed / volume)."""

import logging

# IndexTTS-2.5 emotion type whitelist (mapped to vectors inside indextts-worker)
INDEXTTS_EMO_TYPES = frozenset({
    "default", "happy", "angry", "sad", "afraid",
    "disgusted", "melancholic", "surprised", "calm",
})


def parse_emo_params(payload, logger: logging.Logger = None):
    """
    Parse emo_type / emo_alpha from a request payload (JSON dict or form mapping).

    emo_alpha: invalid/unparseable -> 1.0; below 0 -> 0; above 1 -> 1 (no error).

    Returns:
        (ok, emo_type, emo_alpha, err_msg)
        ok=True  -> emo_type is normalized lowercase str, emo_alpha is float in [0, 1]
        ok=False -> err_msg explains the validation failure (emo_type only)
    """
    log = logger or logging.getLogger(__name__)
    raw_type = payload.get("emo_type", "default") if payload is not None else "default"
    raw_alpha = payload.get("emo_alpha", 1.0) if payload is not None else 1.0

    if raw_type is None or (isinstance(raw_type, str) and not raw_type.strip()):
        emo_type = "default"
    elif not isinstance(raw_type, str):
        return False, None, None, f"emo_type must be a string, got {type(raw_type).__name__}"
    else:
        emo_type = raw_type.strip().lower()
        if emo_type not in INDEXTTS_EMO_TYPES:
            return False, None, None, (
                f"emo_type '{raw_type}' is invalid; allowed: {', '.join(sorted(INDEXTTS_EMO_TYPES))}"
            )

    if raw_alpha is None or raw_alpha == "":
        emo_alpha = 1.0
    else:
        try:
            emo_alpha = float(raw_alpha)
        except (TypeError, ValueError):
            log.warning(f"invalid emo_alpha {raw_alpha!r}, fallback to 1.0")
            emo_alpha = 1.0
        else:
            if emo_alpha < 0.0:
                log.warning(f"emo_alpha {emo_alpha} below 0.0, clamp to 0.0")
                emo_alpha = 0.0
            elif emo_alpha > 1.0:
                log.warning(f"emo_alpha {emo_alpha} above 1.0, clamp to 1.0")
                emo_alpha = 1.0

    return True, emo_type, emo_alpha, ""


def parse_speed_volume_params(payload, logger: logging.Logger = None):
    """
    Parse speed / volume_db from a request payload (JSON dict or form mapping).

    speed: invalid/unparseable -> 1.0; below 0.5 -> 0.5; above 2.0 -> 2.0 (no error).
    volume_db: invalid/unparseable -> 0.0; below -6.0 -> -6.0; above 6.0 -> 6.0 (no error).

    Returns:
        (ok, speed, volume_db, err_msg)
        Always ok=True after clamp/fallback; err_msg is empty.
    """
    log = logger or logging.getLogger(__name__)
    raw_speed = payload.get("speed", 1.0) if payload is not None else 1.0
    raw_volume_db = payload.get("volume_db", 0.0) if payload is not None else 0.0

    if raw_speed is None or raw_speed == "":
        speed = 1.0
    else:
        try:
            speed = float(raw_speed)
        except (TypeError, ValueError):
            log.warning(f"invalid speed {raw_speed!r}, fallback to 1.0")
            speed = 1.0
        else:
            if speed < 0.5:
                log.warning(f"speed {speed} below 0.5, clamp to 0.5")
                speed = 0.5
            elif speed > 2.0:
                log.warning(f"speed {speed} above 2.0, clamp to 2.0")
                speed = 2.0

    if raw_volume_db is None or raw_volume_db == "":
        volume_db = 0.0
    else:
        try:
            volume_db = float(raw_volume_db)
        except (TypeError, ValueError):
            log.warning(f"invalid volume_db {raw_volume_db!r}, fallback to 0.0")
            volume_db = 0.0
        else:
            if volume_db < -6.0:
                log.warning(f"volume_db {volume_db} below -6.0, clamp to -6.0")
                volume_db = -6.0
            elif volume_db > 6.0:
                log.warning(f"volume_db {volume_db} above 6.0, clamp to 6.0")
                volume_db = 6.0

    return True, speed, volume_db, ""
