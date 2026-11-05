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

# 与前端 activeTab / sourceFields 一致：'article' | 'upload'
VIDEO_SOURCES = frozenset({"article", "upload"})
DEFAULT_VIDEO_SOURCE = "article"

# 与前端 FRAME_PRESET_IDS 一致
VIDEO_STYLES = frozenset({
    "capsule",
    "code-editorial",
    "daisy-days",
    "creative-mode",
    "editorial-forest",
    "cartesian",
    "blockframe",
    "broadside",
    "bold-poster",
    "blue-professional",
    "biennale-yellow",
    "cobalt-grid",
    "coral",
})
DEFAULT_VIDEO_STYLE = "capsule"
