# -*- coding: utf-8 -*-
def gen_res_dict(ret: int, msg: str, dt=None):
    d = {}
    if dt:
        d.update(dt)
    d['ret'] = ret
    d['msg'] = msg
    return d


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