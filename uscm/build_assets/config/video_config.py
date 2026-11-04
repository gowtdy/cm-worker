# -*- coding: utf-8 -*-

VIDEO_ASPECT_RATIO_MAP = {
    "landscape": "16:9",
    "portrait": "9:16",
}
VIDEO_ASPECTS = frozenset(VIDEO_ASPECT_RATIO_MAP.keys())
DEFAULT_VIDEO_ASPECT = "landscape"

# 与前端可选档位一致：30s / 1min / 2min / 3min / 5min
VIDEO_DURATIONS = frozenset({30, 60, 120, 180, 300})
DEFAULT_VIDEO_DURATION = 60

# 与前端 activeTab 一致：'article' | 'upload' | 'link'
VIDEO_SOURCES = frozenset({"article", "upload", "link"})
DEFAULT_VIDEO_SOURCE = "article"
