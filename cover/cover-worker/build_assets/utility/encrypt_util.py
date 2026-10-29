import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config.comm_config import get_url_secret_key

def encrypt_url(url):
    """使用Fernet对URL进行加密"""
    # 从密钥派生Fernet密钥
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'echobeat_static_salt',  # 在生产环境中应使用随机salt并存储
        iterations=100000,
    )
    secret_key = get_url_secret_key()
    key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))
    f = Fernet(key)
    
    # 将数据转为JSON字符串并加密
    encrypted_data = f.encrypt(url.encode())
    
    return base64.urlsafe_b64encode(encrypted_data).decode()

def decrypt_url(encrypted_data):
    """使用Fernet解密URL"""
    # 从密钥派生相同的Fernet密钥
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'echobeat_static_salt',  # 必须与加密时使用相同的salt
        iterations=100000,
    )
    secret_key = get_url_secret_key()  # 必须与加密时使用相同的密钥
    key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))
    f = Fernet(key)
    
    # 解码Base64并解密
    decoded_data = base64.urlsafe_b64decode(encrypted_data.encode())
    decrypted_data = f.decrypt(decoded_data)
    
    # 返回解密后的URL
    return decrypted_data.decode()
