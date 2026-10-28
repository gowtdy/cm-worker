# -*- coding: utf-8 -*-
import numpy as np
import hmac
import hashlib
import time

from config.comm_config import get_tts_secret_key, get_cover_secret_key, get_sound_secret_key, get_transcribe_secret_key
from utility.module_enum import ModuleType

def gen_reqid(uid, timestamp):
    if isinstance(uid, int):
        uid_hash = uid & 0xFFFFFFFF  # 直接取32位
    else:
        uid_hash = hash(str(uid)) & 0xFFFFFFFF
    seed = np.random.randint(low=1, high=100)
    id = ((int(uid_hash<<10) ^ int(timestamp)) << 3) | int(seed) 
    #print(f'id:{id}, userid:{userid}, timestamp:{timestamp}')
    return id

def gen_res_dict(ret: int, msg: str, dt=None):
    d = {}
    if dt:
        d.update(dt)
    d['ret'] = ret
    d['msg'] = msg
    return d

def aiflow_hmac_signature(text: str, timestamp: int, module: ModuleType) -> str:
    """验证 HMAC 签名
    对text字段进行签名验证，签名格式为：text=<utf8_encoded_text>&timestamp=<timestamp>
    Args:
        text: 需要验证的文本
        timestamp: UTC时间戳
    Returns:
        str: 服务器端计算的签名值
    """
    # 1. 将文本内容转换为 UTF-8 编码的字节数组的十六进制表示
    utf8_text = text.encode('utf-8').hex()
    
    # 2. 组合签名字符串
    message = f"text={utf8_text}&timestamp={timestamp}"
    
    secret_key = get_tts_secret_key()
    
    if module == ModuleType.SOUND:
        secret_key = get_sound_secret_key()
    elif module in (ModuleType.COVER, ModuleType.VOCAL_ISOLATOR, ModuleType.VOCAL_REMOVER,
                    ModuleType.AUDIO_EXTRACTOR):
        secret_key = get_cover_secret_key()
    elif module == ModuleType.TRANSCRIBE:
        secret_key = get_transcribe_secret_key()
    
    # 3. 计算 HMAC
    expected_signature = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return expected_signature

# model_desc 中为缩写，gpu_gen_tts 需要全名（与 language_map / fish_support_langs 一致）
# en, zh, ja, ko, ar, yue(cantonese), fr, pt, es, it, pl, ru, de, nl
LANG_ABBREV_MAP = {
    'en': 'english',
    'zh': 'china',
    'ja': 'japan',
    'ko': 'korea',
    'ar': 'arab',
    'yue': 'china_yueyu',
    'fr': 'france',
    'pt': 'portugal',
    'es': 'spain',
    'it': 'italy',
    'pl': 'poland',
    'ru': 'russia',
    'de': 'germany',
    'nl': 'holland',
}

def map_lang_abbrev(lang: str, default: str = 'english') -> str:
    """将语言缩写映射为全名，未命中则返回 default。"""
    return LANG_ABBREV_MAP.get(lang, default)

if __name__ == "__main__":
    userid = 1
    timestamp = time.time()
    id = genid(userid, timestamp)
    print(f'id:{id}')
