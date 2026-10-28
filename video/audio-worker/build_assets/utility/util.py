# -*- coding: utf-8 -*-
import numpy as np
from config.comm_config import get_hmac_secret_key
import hmac
import hashlib
import time

ALLOWED_GENDER = ['male', 'female', 'duet']

def gen_reqid(uid, timestamp):
    if isinstance(uid, int):
        uid_hash = uid & 0xFFFFFFFF  # 直接取32位
    else:
        uid_hash = hash(str(uid)) & 0xFFFFFFFF
    seed = np.random.randint(low=1, high=100)
    id = ((int(uid_hash<<10) ^ int(timestamp)) << 2) | int(seed) 
    #print(f'id:{id}, userid:{userid}, timestamp:{timestamp}')
    return id

def gen_res_dict(ret: int, msg: str, dt={}):
    d = {}
    d.update(dt)
    dt['ret'] = ret
    dt['msg'] = msg
    return dt

def validate_gender(gender: str) -> bool:
    if gender in ALLOWED_GENDER:
        return True
    return False

def verify_cover_signature(text: str, timestamp: int) -> str:
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
    
    # 3. 计算 HMAC
    expected_signature = hmac.new(
        get_hmac_secret_key().encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return expected_signature    

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

if __name__ == "__main__":
    userid = 1
    timestamp = time.time()
    id = genid(userid, timestamp)
    print(f'id:{id}')
