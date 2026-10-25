# -*- coding: utf-8 -*-
"""language → engine 的配置化路由。

v1：只部署 whisper，未部署引擎一律回退 whisper；
后续上线 funasr/parakeet 时，把对应引擎加入 DEPLOYED_ENGINES 即可，代码零改动。
"""

# target 映射（后续按语言启用不同 transcribe image）
LANG_TO_ENGINE = {
    'en': 'whisper',
    #'zh': 'funasr',
    #'en': 'parakeet',
    # 其余 / auto / 未知 → 走 default
}
DEFAULT_ENGINE = 'whisper'

# v1：仅 whisper 已部署
DEPLOYED_ENGINES = {'whisper'}


def resolve_transcribe_engine(language) -> str:
    key = (language or '').strip().lower()
    target = LANG_TO_ENGINE.get(key, DEFAULT_ENGINE)
    return target if target in DEPLOYED_ENGINES else DEFAULT_ENGINE
