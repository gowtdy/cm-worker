# -*- coding: utf-8 -*-
import numpy as np
import time
import hmac
import hashlib

from utility.voice_config import get_hmac_secret_key

def genid(userid:int, timestamp:int):
    seed = np.random.randint(low=1, high=100)
    id = ((int(userid<<10) ^ int(timestamp)) << 2) | int(seed) 
    print(f'id:{id}, userid:{userid}, timestamp:{timestamp}')
    return id

def gen_res_dict(ret: int, msg: str, dt=None):
    d = {}
    if dt:
        d.update(dt)
    d['ret'] = ret
    d['msg'] = msg
    return d

def verify_signature(text: str, timestamp: int) -> str:
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
