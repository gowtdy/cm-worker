PUNCTUATION_PAUSE_DURATION = {
    # long pause
    ".": 0.5,
    "。": 0.5,
    "?": 0.5,
    "？": 0.5,
    "!": 0.5,
    "！": 0.5,
    "...": 0.5,
    "…": 0.5,
    # medium pause
    ";": 0.3,
    "；": 0.3,
    ":": 0.3,
    #"：": 0.3,
    # short pause
    #",": 0.2,
    #"，": 0.2,
    #"、": 0.2,
}

SENTENCE_END_TOKENS = set(PUNCTUATION_PAUSE_DURATION.keys()) | {
    # Keep sentence-ending variants explicit for readability.
    "▁.",
    "▁?",
    "▁...",
}
SENTENCE_END_CHARS = {token for token in SENTENCE_END_TOKENS if len(token) == 1}


def normalize_token_for_pause(token):
    if token is None:
        return ""
    normalized = str(token).strip()
    if normalized.startswith("▁"):
        normalized = normalized[1:]
    return normalized


def get_pause_duration_from_segment_tokens(
    tokens,
    default_interval_silence=300,
    punctuation_pause_duration=None,
):
    pause_duration_map = punctuation_pause_duration or PUNCTUATION_PAUSE_DURATION
    default_pause = max(default_interval_silence, 0) / 1000.0

    if not tokens:
        return default_pause

    for token in reversed(tokens):
        normalized = normalize_token_for_pause(token)
        if not normalized:
            continue

        if normalized.endswith("...") or normalized.endswith("…"):
            return pause_duration_map["..."]

        last_char = normalized[-1]
        if last_char in pause_duration_map:
            return pause_duration_map[last_char]

        if normalized in pause_duration_map:
            return pause_duration_map[normalized]

    return default_pause


def is_sentence_end_segment(
    seg,
    sentence_end_tokens=None,
    sentence_end_chars=None,
):
    if not seg:
        return False

    end_tokens = sentence_end_tokens or SENTENCE_END_TOKENS
    end_chars = sentence_end_chars or SENTENCE_END_CHARS

    tail = str(seg[-1]).strip()
    if tail in end_tokens:
        return True
    if tail.startswith("▁"):
        tail = tail[1:]
    if tail in end_tokens:
        return True
    if tail.endswith("...") or tail.endswith("…"):
        return True
    return tail[-1:] in end_chars
