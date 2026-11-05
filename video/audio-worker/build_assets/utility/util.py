# -*- coding: utf-8 -*-


def format_timing_stats(timing_stats: dict) -> str:
    """格式化耗时统计为易读的字符串"""
    if not timing_stats:
        return "{}"
    formatted = []
    for key, value in sorted(timing_stats.items()):
        if isinstance(value, (int, float)):
            formatted.append(f"{key}:{value:.3f}s")
        else:
            formatted.append(f"{key}:{value}")
    return "{" + ", ".join(formatted) + "}"