import json
import logging
import time
import hmac
import datetime
import os
import random
import urllib.error
import urllib.request

from fastapi import Request
from config.comm_config import get_white_list, get_limit_config
from utility.module_enum import ModuleType, get_generate_action
from utility.util import aiflow_hmac_signature

# 常量定义
MAX_FREE_TRIES = 4
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

ALLOWED_AUDIO_TYPES = ['audio/webm', 'audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/mp3', 'audio/m4a', 'audio/x-m4a',  'audio/mp4', 'audio/flac', 'audio/aac', 'audio/ogg',  \
    'video/webm', 'video/mp4', 'video/mov', 'video/avi', 'video/wmv', 'video/flv', 'video/x-flv', 'video/mkv', 'video/webm', 'video/quicktime']
ALLOWED_FILE_EXTENSIONS = ['.mp3', '.wav',  '.m4a', '.flac', '.aac', '.ogg', '.mp4', '.mov', '.avi', '.wmv', '.flv', '.webm', '.mkv', '.quicktime']

_ACTIONCOUNTS_TIMEOUT_SEC = 5

def _fetch_action_counts(logger: logging.Logger, reqid: str, first_ip: str, email: str, userid, domain: str, module: ModuleType) -> tuple[int, int]:
    """向 UserServer 查询当日/近30天行为次数。domain 为空、无对应 action、或请求失败时返回 (0, 0)。"""
    action = get_generate_action(module)
    domain = domain.strip() if isinstance(domain, str) else str(domain or "").strip()
    if not domain or action is None:
        logger.warning(f'reqid:{reqid} skip actioncounts, domain:{domain!r} action:{action} module:{module}')
        return 0, 0

    host = os.environ.get("US_COMMON_USERSERVER_HOST", "").strip()
    port = os.environ.get("US_COMMON_USERSERVER_PORT", "").strip()
    if not host or not port:
        logger.error(f'reqid:{reqid} actioncounts missing US_COMMON_USERSERVER_HOST or US_COMMON_USERSERVER_PORT')
        return 0, 0

    url = f"http://{host}:{port}/inapi/actioncounts"
    uid = "" if userid in (None, "", 0) else str(userid)
    payload = {
        "reqid": reqid,
        "firstip": first_ip or "",
        "email": email or "",
        "uid": uid,
        "domain": domain,
        "action": action,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_ACTIONCOUNTS_TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or "day_count" not in parsed or "month_count" not in parsed:
                logger.error(f'reqid:{reqid} actioncounts bad response:{raw[:500]}')
                return 0, 0
            return int(parsed["day_count"]), int(parsed["month_count"])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error(f'reqid:{reqid} actioncounts failed url:{url} err:{exc}')
        return 0, 0

def get_real_ip(request: Request):
    real_ip = ""
    if 'x-forwarded-for' in request.headers :
        real_ip = request.headers['x-forwarded-for']
    elif 'x-real-ip' in request.headers :
        real_ip = request.headers['x-real-ip']
    return real_ip

def validate_request(logger: logging.Logger, real_ip: str, referer: str, reqid: str, email: str, subscript: int, today: str, userid: int, module: ModuleType = ModuleType.TTS, domain: str = "") -> tuple[int, str]:
    """
    验证请求的合法性

    Args:
        request: FastAPI请求对象
        reqid: 请求ID
        email: 用户邮箱
        subscript: 订阅状态
        today: 当前日期
        domain: 站点域名，传给 UserServer actioncounts

    Returns:
        tuple[int, str]: (错误码, 错误信息)。错误码为0表示验证通过
    """
    ret = 0
    msg = "ok"
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    domain = domain.strip() if isinstance(domain, str) else str(domain or "").strip()
    if not domain:
        ret = 1
        msg = f'domain is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} ip:{real_ip} email:{email} subscript:{subscript} refer:{referer}, msg: {msg}')
        return ret, msg

    if real_ip != "":
        white_list = get_white_list()
        if first_ip not in white_list:
            day_count, month_count = _fetch_action_counts(logger, reqid, first_ip, email, userid, domain, module)
            logger.debug(f'11 reqid:{reqid} ip:{real_ip} email:{email} domain:{domain} day_count:{day_count} month_count:{month_count}')
            if subscript != 1:
                month_limit = get_limit_config().get('month_count', 15)
                logger.debug(f'22 reqid:{reqid} ip:{real_ip} email:{email} domain:{domain} day_count:{day_count} month_count:{month_count} month_limit:{month_limit}')
                if day_count > MAX_FREE_TRIES or month_count > month_limit:
                    ret = 2
                    msg = 'You have exceeded the free usage limit for today or last month.'
                    logger.info(f'33 reqid:{reqid} ip:{real_ip} email:{email} subscript:{subscript} referer:{referer}, day_count:{day_count} month_count:{month_count} exceeded.')
                    return ret, msg

    if referer == "Not provided":
        ret = 4
        msg = "Need api? Please mail us!"
        logger.info(f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, email:{email}, subscript:{subscript}, refer:{referer}')

    return ret, msg

def validate_signature(logger: logging.Logger, timestamp, signature, text, datetime, reqid, first_ip, real_ip, userid, email, subscript, referer, module: ModuleType = ModuleType.TTS):
    """
    验证请求签名的有效性
    
    Args:
        logger: 日志记录器
        timestamp: 时间戳
        signature: 客户端提供的签名
        datetime: 格式化的日期时间
        reqid: 请求ID
        first_ip: 第一个IP地址
        real_ip: 真实IP地址
        userid: 用户ID
        email: 用户邮箱
        subscript: 订阅状态
        referer: 请求来源
        module: 模块类型
    Returns:
        tuple[int, str]: (错误码, 错误信息)。错误码为0表示验证通过
    """
    ret = 0
    msg = 'ok'
    
    timestamp = int(timestamp)
    if timestamp == 0 or signature == '':
        ret = 1
        msg = f'validate_signature, timestamp:{timestamp} or signature:{signature} is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} ip:{real_ip} email:{email}, msg: {msg}')
        return ret, msg
        
    current_time = int(time.time())  # UTC timestamp
    if abs(current_time - timestamp) > 600:  # 10分钟超时
        ret = 3 # 权限认证失败
        msg = f'Auth failed, please contact us!'
        logger.info(f'validate_signature22 diff time:{abs(current_time - timestamp)}, {datetime} reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, text:{text}' \
            f'email:{email}, subscript:{subscript}, refer:{referer}, Signature expired,current time:{current_time}, tstamp({timestamp})')
        return ret, msg

    server_sign = aiflow_hmac_signature(text, timestamp, module)
    logger.info(f'validate_signature33, {datetime} reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, text:{text},' \
        f'email:{email}, subscript:{subscript}, refer:{referer}, server_sign:{server_sign}, signature:{signature}')
    if not server_sign or not signature or not hmac.compare_digest(signature, server_sign):
        ret = 3 # 权限认证失败
        msg = f'Auth failed, please contact us!'
        logger.info(f'validate_signature44, {datetime} reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, text:{text}' \
            f'email:{email}, subscript:{subscript}, refer:{referer}, Invalid signature, timestamp:{timestamp},'  \
            f'client signature:{signature}, server signature:{server_sign}')
        return ret, msg
    
    return ret, msg

def validate_text_length(text: str, max_length: int = 500, reqid: str = "", logger: logging.Logger = None) -> dict:
    """
    验证文本长度是否超过限制
    
    Args:
        text: 要验证的文本
        max_length: 最大允许长度，默认500
        reqid: 请求ID，用于日志记录
        logger: 日志记录器，可选
    
    Returns:
        dict: 包含 ret(错误码), msg(错误信息), reqid(请求ID) 的字典
              当 ret=0 时表示验证通过，ret=3 时表示文本长度超出限制
    """
    textlen = len(text)
    if textlen == 0:
        if logger:
            logger.info(f'reqid:{reqid}, textlen:{textlen} is empty, text:{text}!')
        return {
            'ret': 1,
            'msg': f'Text is empty',
            'reqid': reqid
        }
    if textlen > max_length:
        if logger:
            logger.info(f'reqid:{reqid}, textlen:{textlen} exceeds limit of {max_length}, text:{text}!')
        return {
            'ret': 1,
            'msg': 'Text length exceeds characters limit',
            'reqid': reqid
        }
    
    return {
        'ret': 0,
        'msg': 'ok',
        'reqid': reqid
    }

ALLOWED_GENDER = ['male', 'female', 'duet']
def validate_gender(gender: str) -> bool:
    if gender in ALLOWED_GENDER:
        return True
    return False

def validate_audio_url(converturl: str, reqid: str, first_ip: str, email: str, today: str, datetime: str, logger: logging.Logger) -> tuple[int, str, dict]:
    ret = 0
    msg = 'ok'
    upfileinfo = {}

    if not converturl.startswith('/uploads/'):
        ret = 1
        msg = f'Invalid converturl format: {converturl}, reqid:{reqid}'
        logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
        return ret, msg, upfileinfo

    relative_path = converturl.replace('/uploads/', './data/uploads/')
    logger.info(f'relative_path:{relative_path}')

    # cm 与 处理分开，不在一台机器，文件存在的检查去掉。
    #if not os.path.exists(relative_path):
    #    ret = 1
    #    msg = f'Input audio file not found: {relative_path}, reqid:{reqid}'
    #    logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
    #    return ret, msg, upfileinfo

    path_parts = relative_path.split('/')
    if len(path_parts) != 5:
        ret = 1
        msg = f'Invalid converturl format: {converturl}, reqid:{reqid}'
        logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
        return ret, msg, upfileinfo

    update_str = path_parts[3]
    upload_filename = path_parts[4]

    parts = os.path.splitext(upload_filename)
    if len(parts) != 2:
        ret = 1
        msg = f'Invalid filename format: {upload_filename}'
        logger.error(f'reqid:{reqid} msg:{msg}')
        return ret, msg, upfileinfo
    filename_without_ext = parts[0]
    file_ext = parts[1].lower()
    if file_ext not in ALLOWED_FILE_EXTENSIONS:
        ret = 1
        msg = f'Invalid file extension[{file_ext}]. Allowed extensions are: {", ".join(ALLOWED_FILE_EXTENSIONS)}, reqid:{reqid}'
        logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
        return ret, msg, upfileinfo
    # 将文件名按照下划线分割为三部分：时间、IP和随机数
    parts = filename_without_ext.split('_', 2)  # 最多分割2次，确保upname部分完整保留
    if len(parts) != 3:
        ret = 1
        msg = f'Invalid filename format: {filename_without_ext}'
        logger.error(f'reqid:{reqid} msg:{msg}')
        return ret, msg, upfileinfo
    uploadtime, userip, randomnum = parts

    #示例：/uploads/2025-03-09/20250309122346_194.48.154.88_673.mp3
    # 针对samples文件，需要将update_str设置为samples_2025-03-09
    if update_str == 'samples':
        uploadtime = datetime
        update_str = f'samples_{today}'
        userip = f'{userip}-{first_ip}'
        random_number = random.randint(1, 1000)
        randomnum = f'{randomnum}{random_number}'
        filename_without_ext = f'{datetime}_{userip}_{randomnum}'

    upfileinfo['relative_path'] = relative_path  # 上传文件相对路径，上面的路径
    upfileinfo['update_str'] = update_str  # 上传日期字符串， 2025-02-22
    upfileinfo['upfilename_without_ext'] = filename_without_ext  # 上传文件名(不含扩展名)，202502221755042_83.229.17.48_lastnight-morgan-male
    upfileinfo['file_ext'] = file_ext  # 上传文件扩展名，mp3
    upfileinfo['uploadtime'] = uploadtime  # 上传时间+roundnum，202502221755042
    upfileinfo['userip'] = userip  # 上传者IP，83.229.17.48
    upfileinfo['randomnum'] = randomnum  # 上传随机数，673
    logger.debug(f'reqid:{reqid} validate_audio_url, upfileinfo:{upfileinfo}')

    return ret, msg, upfileinfo 

def validate_internal_tts_url(converturl: str, reqid: str, first_ip: str, email: str, today: str, datetime: str, logger: logging.Logger) -> tuple[int, str, dict]:
    ret = 0
    msg = 'ok'
    upfileinfo = {}

    if not converturl.startswith('/outdt/'):
        ret = 1
        msg = f'Invalid converturl format: {converturl}, reqid:{reqid}'
        logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
        return ret, msg, upfileinfo

    relative_path = converturl.replace('/outdt/wavcl/', './data/tts/')
    logger.info(f'relative_path:{relative_path}')

    # cm 与 处理分开，不在一台机器，文件存在的检查去掉。
    #if not os.path.exists(relative_path):
    #    ret = 1
    #    msg = f'Input audio file not found: {relative_path}, reqid:{reqid}'
    #    logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
    #    return ret, msg, upfileinfo

    # ./data/tts/2026-10-08/userclm-tts-default/gpt_wenqian-femalea_20261008110120_563.mp3
    path_parts = relative_path.split('/')
    if len(path_parts) != 6:
        ret = 1
        msg = f'Invalid converturl format: {converturl}, reqid:{reqid}'
        logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
        return ret, msg, upfileinfo

    update_str = path_parts[3]
    upload_filename = path_parts[5]

    parts = os.path.splitext(upload_filename)
    if len(parts) != 2:
        ret = 1
        msg = f'Invalid filename format: {upload_filename}'
        logger.error(f'reqid:{reqid} msg:{msg}')
        return ret, msg, upfileinfo
    filename_without_ext = parts[0]
    file_ext = parts[1].lower()
    if file_ext not in ALLOWED_FILE_EXTENSIONS:
        ret = 1
        msg = f'Invalid file extension[{file_ext}]. Allowed extensions are: {", ".join(ALLOWED_FILE_EXTENSIONS)}, reqid:{reqid}'
        logger.warning(f'reqid:{reqid} ip:{first_ip} email:{email}, msg: {msg}')
        return ret, msg, upfileinfo

    upfileinfo['relative_path'] = relative_path  # 上传文件相对路径，上面的路径
    upfileinfo['update_str'] = update_str  # 上传日期字符串， 2025-02-22
    upfileinfo['upfilename_without_ext'] = filename_without_ext
    upfileinfo['file_ext'] = file_ext  # 上传文件扩展名，mp3
    logger.debug(f'reqid:{reqid} validate_internal_tts_url, converturl:{converturl}, upfileinfo:{upfileinfo}')

    return ret, msg, upfileinfo 