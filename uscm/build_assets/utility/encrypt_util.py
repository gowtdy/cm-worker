import base64
import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config.comm_config import get_url_secret_key

# 与存量密文绑定，不可随意变更
_URL_SALT = b'url_salt'
_URL_ITERATIONS = 100000

def _derive_aes_key(secret_key, salt, iterations):
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(secret_key.encode())

def encrypt_url(url):
    """使用AES-GCM对URL进行加密，生成更短的密文"""
    key = _derive_aes_key(get_url_secret_key(), _URL_SALT, _URL_ITERATIONS)

    # 使用AES-GCM加密
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # GCM推荐12字节nonce
    ciphertext = aesgcm.encrypt(nonce, url.encode(), None)

    # 将nonce和密文组合
    encrypted_data = nonce + ciphertext

    return base64.urlsafe_b64encode(encrypted_data).decode()


def decrypt_url(encrypted_data):
    """使用AES-GCM解密URL"""
    key = _derive_aes_key(get_url_secret_key(), _URL_SALT, _URL_ITERATIONS)

    # 解码数据
    encrypted_data_bytes = base64.urlsafe_b64decode(encrypted_data.encode())

    # 分离nonce和密文
    nonce = encrypted_data_bytes[:12]
    ciphertext = encrypted_data_bytes[12:]

    # 解密
    aesgcm = AESGCM(key)
    decrypted_data = aesgcm.decrypt(nonce, ciphertext, None)

    return decrypted_data.decode()
