import os
import time
import logging
import argparse
import random
import hmac
import hashlib
import datetime
import yaml
import asyncio

from fastapi import FastAPI, Request, Query

from utility.util import gen_reqid, gen_res_dict, map_lang_abbrev
from utility.encrypt_util import encrypt_url
from utility.request_util import get_real_ip, validate_request, validate_signature, validate_text_length, validate_entapi_user_auth, \
    validate_gender, validate_audio_url
from utility.module_enum import ModuleType

from tasks_interface import gpu_gen_sound, gpu_gen_tts, gpu_gen_indextts, gpu_gen_indextts25, gpu_cover, gpu_vocal, gpu_instrument, task_audio_extract

from config.ttsmodel_config import get_tts_model_lang_map

app = FastAPI()

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'
DEFAULT_FORMAT = "mp3"
sounds_output_dir = 'output'

cover_model_dir = '/app/models/cover_service_models'

logging.getLogger("multipart").setLevel(logging.INFO)
logging.getLogger("multipart.multipart").setLevel(logging.INFO)
#logdir = get_logdir()
#os.makedirs(logdir, exist_ok=True)

# 确保 log 目录存在并有正确权限
log_dir = '/app/log'
os.makedirs(log_dir, exist_ok=True)
# 设置目录权限为777，确保所有用户都能写入
os.chmod(log_dir, 0o777)

LOG_FORMAT = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"

_MODULE_LOG_CONFIG = (
    ('soundeffect', 'soundeffect_log.txt'),
    ('tts', 'tts_log.txt'),
    ('echobeat_tts', 'echobeat_tts_log.txt'),
    ('entapi_tts', 'entapi_tts_log.txt'),
    ('cover', 'cover_log.txt'),
    ('echobeat_cover', 'echobeat_cover_log.txt'),
    ('audio_extract', 'audio_extract_log.txt')
)

# 创建日志记录器
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# 添加通用的控制台处理器（用于非特定模块的日志）
general_console_handler = logging.StreamHandler()
general_console_handler.setLevel(logging.INFO)
general_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
general_console_handler.setFormatter(general_formatter)
logger.addHandler(general_console_handler)

file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
for module_name, log_filename in _MODULE_LOG_CONFIG:
    module_logger = logging.getLogger(module_name)
    module_logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(os.path.join(log_dir, log_filename))
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(file_formatter)
    module_logger.addHandler(fh)

logger_sounds_effect = logging.getLogger('soundeffect')
logger_tts = logging.getLogger('tts')
logger_echobeat_tts = logging.getLogger('echobeat_tts')
logger_entapi_tts = logging.getLogger('entapi_tts')
logger_cover = logging.getLogger('cover')
logger_echobeat_cover = logging.getLogger('echobeat_cover')
logger_audio_extract = logging.getLogger('audio_extract')

model = None
sample_rate = 32000
sample_size = 1920000

MAX_TTS_TEXT_LENGTH = 700
MAX_ENTTTS_TEXT_LENGTH = 400
MAX_SOUND_TEXT_LENGTH = 100

# IndexTTS-2.5 emotion type whitelist (mapped to vectors inside indextts-worker)
INDEXTTS_EMO_TYPES = frozenset({
    "default", "happy", "angry", "sad", "afraid",
    "disgusted", "melancholic", "surprised", "calm",
})


def parse_emo_params(payload):
    """
    Parse emo_type / emo_alpha from a request payload (JSON dict or form mapping).

    emo_alpha: invalid/unparseable -> 1.0; below 0 -> 0; above 1 -> 1 (no error).

    Returns:
        (ok, emo_type, emo_alpha, err_msg)
        ok=True  -> emo_type is normalized lowercase str, emo_alpha is float in [0, 1]
        ok=False -> err_msg explains the validation failure (emo_type only)
    """
    raw_type = payload.get("emo_type", "default") if payload is not None else "default"
    raw_alpha = payload.get("emo_alpha", 1.0) if payload is not None else 1.0

    if raw_type is None or (isinstance(raw_type, str) and not raw_type.strip()):
        emo_type = "default"
    elif not isinstance(raw_type, str):
        return False, None, None, f"emo_type must be a string, got {type(raw_type).__name__}"
    else:
        emo_type = raw_type.strip().lower()
        if emo_type not in INDEXTTS_EMO_TYPES:
            return False, None, None, (
                f"emo_type '{raw_type}' is invalid; allowed: {', '.join(sorted(INDEXTTS_EMO_TYPES))}"
            )

    if raw_alpha is None or raw_alpha == "":
        emo_alpha = 1.0
    else:
        try:
            emo_alpha = float(raw_alpha)
        except (TypeError, ValueError):
            logger.warning(f"invalid emo_alpha {raw_alpha!r}, fallback to 1.0")
            emo_alpha = 1.0
        else:
            if emo_alpha < 0.0:
                logger.warning(f"emo_alpha {emo_alpha} below 0.0, clamp to 0.0")
                emo_alpha = 0.0
            elif emo_alpha > 1.0:
                logger.warning(f"emo_alpha {emo_alpha} above 1.0, clamp to 1.0")
                emo_alpha = 1.0

    return True, emo_type, emo_alpha, ""


def parse_speed_volume_params(payload):
    """
    Parse speed / volume_db from a request payload (JSON dict or form mapping).

    speed: invalid/unparseable -> 1.0; below 0.5 -> 0.5; above 2.0 -> 2.0 (no error).
    volume_db: invalid/unparseable -> 0.0; below -6.0 -> -6.0; above 6.0 -> 6.0 (no error).

    Returns:
        (ok, speed, volume_db, err_msg)
        Always ok=True after clamp/fallback; err_msg is empty.
    """
    raw_speed = payload.get("speed", 1.0) if payload is not None else 1.0
    raw_volume_db = payload.get("volume_db", 0.0) if payload is not None else 0.0

    if raw_speed is None or raw_speed == "":
        speed = 1.0
    else:
        try:
            speed = float(raw_speed)
        except (TypeError, ValueError):
            logger.warning(f"invalid speed {raw_speed!r}, fallback to 1.0")
            speed = 1.0
        else:
            if speed < 0.5:
                logger.warning(f"speed {speed} below 0.5, clamp to 0.5")
                speed = 0.5
            elif speed > 2.0:
                logger.warning(f"speed {speed} above 2.0, clamp to 2.0")
                speed = 2.0

    if raw_volume_db is None or raw_volume_db == "":
        volume_db = 0.0
    else:
        try:
            volume_db = float(raw_volume_db)
        except (TypeError, ValueError):
            logger.warning(f"invalid volume_db {raw_volume_db!r}, fallback to 0.0")
            volume_db = 0.0
        else:
            if volume_db < -6.0:
                logger.warning(f"volume_db {volume_db} below -6.0, clamp to -6.0")
                volume_db = -6.0
            elif volume_db > 6.0:
                logger.warning(f"volume_db {volume_db} above 6.0, clamp to 6.0")
                volume_db = 6.0

    return True, speed, volume_db, ""

@app.post("/sapi/gensound")
async def gensoundTask(request: Request):
    ret = 0
    msg = 'ok'
    start_time = time.time()
    real_ip = ""
    first_ip = ""
    cnt = 0
    req_uri = "/sapi/gensound"

    localtime = time.localtime(start_time)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d_%H%M%S', localtime)
    random_number = random.randint(1, 1000)
    real_ip = get_real_ip(request)

    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")
    
    data = await request.form()
    email = data.get('email', '') 
    userid = data.get('userid', 0)
    text = data.get('text', '')
    subscript = int(data.get('subscript', 0))
    length = int(data.get('length', 15))
    num = int(data.get('num', 1))
    domain = data.get('domain', '')

    if isinstance(userid, int):
        uid_hash = userid & 0xFFFFFFFF  # 直接取32位
    else:
        uid_hash = hash(str(userid)) & 0xFFFFFFFF
    reqid = gen_reqid(uid_hash, start_time)

    ip = real_ip
    logger_sounds_effect.debug(f'reqid:{reqid} ip:{real_ip} email:{email} text:{text} userid:{userid} subscript:{subscript}')

    ret, msg = validate_request(logger_sounds_effect, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.SOUND, domain)
    if ret != 0 :
        logger_sounds_effect.info(f'reqid:{reqid} ip:{real_ip} email:{email} text:{text} userid:{userid} subscript:{subscript} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)

    # 验证文本长度
    text_validation = validate_text_length(text, MAX_SOUND_TEXT_LENGTH, reqid, logger_sounds_effect)
    if text_validation['ret'] != 0:
        logger_sounds_effect.info(f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid},' \
            f'email:{email}, subscript:{subscript}, refer:{referer}, text:{text} textlen:{len(text)} exceeds limit!')
        return text_validation
    
    textlen = len(text)  # 重新定义 textlen 用于后续逻辑
    
    timestamp = data.get('tstamp', 0)
    signature = data.get('snature', '')
    ret, msg = validate_signature(logger_sounds_effect, timestamp, signature, text, datetime, reqid, first_ip, real_ip, userid, email, subscript, referer, ModuleType.SOUND)
    if ret != 0 :
        logger_sounds_effect.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    add_ip_sounds_record(first_ip, today, text, reqid, req_uri, textlen, email, real_ip)
    gpu_gen_sound.delay(text,length, num, today, datetime, random_number, first_ip, real_ip, email, userid, ret, cnt, reqid)

    timeout: int = 180
    interval: float = 2.0

    uri = get_out_path_by_id(reqid, timeout, interval)
    if uri is None:
        ret = 6
        msg = "System is busy now, please try again later!"
        logger_sounds_effect.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} process failed, get_out_path_by_id returned None')
        return gen_res_dict(ret, msg)
    encrypted_uri = encrypt_url(uri)

    res = {
        "ret": ret,
        "msg": msg,
        "uri": uri,
        "enurl": encrypted_uri,
        "reqid": reqid
    }
    
    endtm = time.time()
    cost = int(endtm - start_time)

    logmsg = f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, email:{email}, subscript:{subscript}, cost:{cost}, refer:{referer}, today req cnt:{cnt}, ret:{ret}, uri:{uri}, enurl:{encrypted_uri}'
    logger_sounds_effect.info(logmsg)
    #logger.info(logmsg)
    return res

# for web api, 同步请求
@app.post("/api/genaudio")
async def tts_endpoint(request: Request):
    ret = 0
    msg = 'ok'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()
    
    # 验证签名
    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    userid = json_post_raw.get("userid", 0)
    if isinstance(userid, int):
        userid_hash = userid & 0xFFFFFFFF  # 直接取32位
    else:
        userid_hash = hash(str(userid)) & 0xFFFFFFFF
    reqid = gen_reqid(userid_hash, tm)
    localtime = time.localtime(tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)
    cnt = 0
    # 用户是否订阅,有4个取值, 0 未订阅, 1 已订阅, 2 虽订阅,字符已用完,
    subscript = json_post_raw.get("subscript", 0)
    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')

    ret, msg = validate_request(logger_tts, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.TTS, domain)
    if ret != 0 :
        logger_tts.info(f'reqid:{reqid} ip:{real_ip} email:{email} userid:{userid} subscript:{subscript} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)

    output_format = json_post_raw.get("output_format", DEFAULT_FORMAT)

    text = json_post_raw.get("text", '')
    # 验证文本长度
    text_validation = validate_text_length(text, MAX_TTS_TEXT_LENGTH, reqid, logger_tts)
    if text_validation['ret'] != 0:
        logger_tts.info(f'{datetime} reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid},' \
            f'email:{email}, subscript:{subscript}, refer:{referer}, textlen:{len(text)} exceeds limit!')
        return text_validation
    
    textlen = len(text)  # 重新定义 textlen 用于后续逻辑
    
    ret, msg = validate_signature(logger_tts, timestamp, signature, text, datetime, reqid, first_ip, real_ip, userid, email, subscript, referer, ModuleType.TTS)
    if ret != 0 :
        logger_tts.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    modelcat = json_post_raw.get("modelcat", '')
    modelname = json_post_raw.get("modelname", '')
    modellang = json_post_raw.get("modellang", 'english')

    model_file_prefix = modelname
    model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'

    model_desc = f'{model_path}/{model_file_prefix}_desc.txt'
    if not os.path.exists(model_path) or not os.path.exists(model_desc) :
        ret = 1
        msg = f'modelname:{modelname} is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} model_path:{model_path}, modelname:{modelname}, msg:{msg}')
        return gen_res_dict(ret, msg)

    add_ip_use_record(first_ip, today, text, reqid, '/api/genaudio', modelcat, modelname, textlen, email, real_ip)

    # 判断调用哪一个任务
    tts_modeltype = "gsv" # 默认使用sovits模型, gsv和fish-speed公用一个task
    with open(model_desc, 'r', encoding='utf-8') as f :
        model_desc_content = f.readline().strip('\n')
        logger.debug(f'handle, model_desc_content:{model_desc_content}')
        items = model_desc_content.split('|')
        if len(items) >= 6: 
            modellang = items[5] # model lang, en, ja, zh
        if len(items) >= 9:
            tts_modeltype = items[7] # index-tts, gsv
            tts_modelversion = items[8]  # index-tts, v2, v2.5
    if tts_modeltype == "index-tts" :
        if tts_modelversion == "v2.5" :
            ok, emo_type, emo_alpha, emo_err = parse_emo_params(json_post_raw)
            if not ok:
                logger_tts.warning(f'reqid:{reqid} parse_emo_params failed: {emo_err}')
                return gen_res_dict(1, emo_err)
            ok2, speed, volume_db, sv_err = parse_speed_volume_params(json_post_raw)
            if not ok2:
                logger_tts.warning(f'reqid:{reqid} parse_speed_volume_params failed: {sv_err}')
                return gen_res_dict(1, sv_err)
            gpu_gen_indextts25.delay(
                modelname, text, modelcat, userid, t, reqid, output_format, modellang, email,
                emo_type=emo_type, emo_alpha=emo_alpha,
                speed=speed, volume_db=volume_db,
            )
        else :
            gpu_gen_indextts.delay(modelname, text, modelcat, userid, t, reqid, output_format, modellang, email)
    else :
        modellang = map_lang_abbrev(modellang, modellang)
        gpu_gen_tts.delay(modelname, text, modelcat, userid, t, reqid, output_format, modellang, email)

    timeout: int = 180
    interval: float = 1.0

    uri, task_status, task_detail = get_tts_out_path_by_id(reqid, timeout, interval)
    if uri is None:
        ret = 6
        msg = "System is busy now, please try again later!"
        logger_tts.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} process failed, task_status:{task_status}, task_detail:{task_detail}')
        return gen_res_dict(ret, msg)
    encrypted_uri = encrypt_url(uri)

    endtm = time.time()
    cost = int(endtm - tm)

    #torch.cuda.empty_cache()

    res = {
        "ret": 0,
        "msg": 'ok',
        "uri": uri,
        "enurl": encrypted_uri,
        "textlen": textlen,
        "reqid": reqid
    }
    
    logmsg = f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, modelcat:{modelcat}, modellang:{modellang} email:{email}, subscript:{subscript}, cost:{cost}, modelname:{modelname}, refer:{referer}, today req cnt:{cnt}, ret:{ret}, uri:{uri}, enurl:{encrypted_uri}'
    logger_tts.info(logmsg)
    #logger.info(logmsg)
    return res

# for app asyn api
@app.post("/api/asyngenaudio")
async def tts_async_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    start_time = time.time()
    cnt = 0
    req_uri = "/api/asyngenaudio"
    email = ''

    localtime = time.localtime(start_time)
    today =  time.strftime(DAY_FORMAT, localtime)
    time_str = time.strftime('%Y%m%d_%H%M%S', localtime)
    current_date = datetime.datetime.now()
    # Calculate date range for last 30 days
    query_enddate = current_date.date()
    query_startdate = query_enddate - datetime.timedelta(days=30)

    random_number = random.randint(1, 1000)
    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""

    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "echobeat")

    # 改为JSON格式接收数据
    json_data = await request.json()
    modelcat = json_data.get('modelcat', '')
    modelname = json_data.get('modelname', '')
    sid = json_data.get('sid', 'defaultsid') # 端的id 
    text = json_data.get('text', '')
    token = json_data.get('token', '')
    timestamp = json_data.get('tstamp', 0)
    signature = json_data.get('snature', '')
    domain = json_data.get('domain', '')
    modellang = json_data.get('modellang', 'english') # 目前移动端接口没有传递该值，都是 english

    output_format = json_data.get('out_format', DEFAULT_FORMAT)

    if isinstance(sid, int):
        sid_hash = sid & 0xFFFFFFFF  # 直接取32位
    else:
        sid_hash = hash(str(sid)) & 0xFFFFFFFF
    reqid = gen_reqid(sid_hash, start_time)

    logger_echobeat_tts.info(f'tts async 111, req_uri:{req_uri} reqid:{reqid} ip:{real_ip} domain:{domain} sid:{sid} modelcat:{modelcat} modelname:{modelname} modellang:{modellang} tstamp:{timestamp} snature:{signature} token:{token}, text:{text}')

    uid = ''
    email = ''
    user_subscript = 0
    # 通过ip，action 和 domain 查询当天和最近30天使用次数
    if len(token.strip()) != 0 :
        usertoken_info = decrypt_userinfo(token)
        if usertoken_info is not None:
            uid = usertoken_info.get('uid', '')
            email = usertoken_info.get('email', '')
            user_subscript = usertoken_info.get('user_subscript', 0)
        else:
            ret = 1
            msg = "token is invalid!"
            logger_echobeat_tts.info(f'tts async 111-1, req_uri:{req_uri} reqid:{reqid} ip:{real_ip} domain:{domain} sid:{sid} modelcat:{modelcat} modelname:{modelname} modellang:{modellang} tstamp:{timestamp} snature:{signature} token:{token}, text:{text} ret:{ret}, msg:{msg}')
            return gen_res_dict(ret, msg)

    logger_echobeat_tts.info(f'tts async 222, user_subscript:{user_subscript}, uid:{uid}, email:{email}')
    if user_subscript != 1:
        logger_echobeat_tts.info(f'need_gen_limit, first_ip:{first_ip}, uid:{uid}, sid:{sid}, domain:{domain}, startdate:{query_startdate}, enddate:{query_enddate}')
        if need_gen_limit(logger_echobeat_tts, first_ip, uid, sid, domain, ModuleType.TTS, query_startdate, query_enddate):
            ret = 2
            msg = "You have exceeded the free usage limit for today or last month."
            logger_echobeat_tts.info(f'tts async need_gen_limit true reqid:{reqid} first_ip:{first_ip}, uid:{uid}, sid:{sid}, ret:{ret}, msg:{msg}')
            return gen_res_dict(ret, msg)

    # 移动端有单独的统计逻辑，这里不需要再统计
    #ret, msg = validate_request(logger_echobeat_tts, real_ip, referer, reqid, email, user_subscript, today, sid, ModuleType.TTS, domain)
    #if ret != 0 :
    #    logger_echobeat_tts.info(f'reqid:{reqid} ip:{real_ip} sid:{sid} token:{token} timestamp:{timestamp} signature:{signature} domain:{domain} ret:{ret}, msg:{msg}')
    #    return gen_res_dict(ret, msg)
    # 验证文本长度
    text_validation = validate_text_length(text, MAX_TTS_TEXT_LENGTH, reqid, logger_echobeat_tts)
    if text_validation['ret'] != 0:
        logger_echobeat_tts.info(f'{time_str} reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, sid:{sid},' \
            f'email:{email}, subscript:{user_subscript}, refer:{referer}, textlen:{len(text)} exceeds limit!')
        return text_validation
    
    textlen = len(text)  # 重新定义 textlen 用于后续逻辑
    
    # 暂时先注释掉，先测试
    ret, msg = validate_signature(logger_echobeat_tts, timestamp, signature, text, time_str, reqid, first_ip, real_ip, sid, email, user_subscript, referer, ModuleType.TTS)
    if ret != 0 :
        logger_echobeat_tts.error(f'reqid:{reqid} real_ip:{real_ip} sid:{sid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    t = 1 # 1 为共有模型， 2 为私有模型， 默认使用通用模型
    #用sid代替userid
    add_ip_use_record(first_ip, today, text, reqid, req_uri, modelcat, modelname, textlen, email, real_ip)
    # 判断调用哪一个任务
    model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
    model_desc = f'{model_path}/{modelname}_desc.txt'
    if not os.path.exists(model_path) or not os.path.exists(model_desc) :
        ret = 1
        msg = f'modelname:{modelname} is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} real_ip:{real_ip} sid:{sid} email:{email} model_path:{model_path}, modelname:{modelname}, msg:{msg}')
        return gen_res_dict(ret, msg)
    tts_modeltype = "gsv" # 默认使用sovits模型, gsv和fish-speed公用一个task
    with open(model_desc, 'r', encoding='utf-8') as f :
        model_desc_content = f.readline().strip('\n')
        logger.debug(f'handle, model_desc_content:{model_desc_content}')
        items = model_desc_content.split('|')
        if len(items) >= 6:
            modellang = items[5] # model lang, en, ja, zh, ext
        if len(items) >= 9:
            tts_modeltype = items[7] # index-tts, gsv
            tts_modelversion = items[8]  # index-tts, v2, v2.5
    if tts_modeltype == "index-tts" :
        if tts_modelversion == "v2.5" :
            ok, emo_type, emo_alpha, emo_err = parse_emo_params(json_data)
            if not ok:
                logger_echobeat_tts.warning(f'reqid:{reqid} parse_emo_params failed: {emo_err}')
                return gen_res_dict(1, emo_err)
            ok2, speed, volume_db, sv_err = parse_speed_volume_params(json_data)
            if not ok2:
                logger_echobeat_tts.warning(f'reqid:{reqid} parse_speed_volume_params failed: {sv_err}')
                return gen_res_dict(1, sv_err)
            gpu_gen_indextts25.delay(
                modelname, text, modelcat, sid, t, reqid, output_format, modellang,
                emo_type=emo_type, emo_alpha=emo_alpha,
                speed=speed, volume_db=volume_db,
            )
        else :
            gpu_gen_indextts.delay(modelname, text, modelcat, sid, t, reqid, output_format, modellang)
    else :
        modellang = map_lang_abbrev(modellang, modelname)
        gpu_gen_tts.delay(modelname, text, modelcat, sid, t, reqid, output_format, modellang)

    #timeout: int = 180
    #interval: float = 2.0
    #uri = get_tts_out_path_by_id(reqid, timeout, interval)
    #if uri is None:
    #    ret = 500
    #    msg = "System is busy now, please try again later!"
    #    logger_tts.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} process failed, get_tts_out_path_by_id returned None')
    #    return gen_res_dict(ret, msg)
    #encrypted_uri = encrypt_url(uri)

    endtm = time.time()
    cost = int(endtm - start_time)

    #torch.cuda.empty_cache()

    res = {
        "ret": 0,
        "msg": 'ok',
        "reqid": f'{reqid}'
    }
    
    logmsg = f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, sid:{sid}, modelcat:{modelcat}, email:{email}, subscript:{user_subscript}, cost:{cost}, modelname:{modelname}, refer:{referer}, ret:{ret}'
    logger_echobeat_tts.info(logmsg)
    #logger.info(logmsg)
    return res

# for app asyn api, 查询生成状态
@app.post("/api/checkgenaudio")
async def check_genaudio(request: Request):
    ret = 0
    msg = 'ok'
    start_time = time.time()
    cnt = 0
    req_uri = "/api/checkgenaudio"
    email = ''

    localtime = time.localtime(start_time)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d_%H%M%S', localtime)
    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""

    # 改为JSON格式接收数据
    json_data = await request.json()
    taskid = json_data.get('taskid', '')
    sid = json_data.get('sid', '')
    token = json_data.get('token', '')
    domain = json_data.get('domain', 'echobeat_default')
    
    logger_echobeat_tts.info(f'check_genaudio, taskid:{taskid}, sid:{sid}, token:{token}, domain:{domain}')

    uid = ''
    email = ''
    user_subscript = 0
    usable_lastday = ''
    sub_productid = ''
    if len(token.strip()) != 0 :
        usertoken_info = decrypt_userinfo(token)
        if usertoken_info is not None:
            uid = usertoken_info.get('uid', '')
            email = usertoken_info.get('email', '')
            user_subscript = usertoken_info.get('user_subscript', 0)
            usable_lastday = usertoken_info.get('usable_lastday', '')
            sub_productid = usertoken_info.get('sub_productid', '')
        else:
            ret = 1
            msg = "token is invalid!"
            logger_echobeat_tts.info(f'check_genaudio 111-1, taskid:{taskid}, sid:{sid}, token:{token}, domain:{domain} ret:{ret}, msg:{msg}')
            return gen_res_dict(ret, msg)

    logger_echobeat_tts.info(f'check_genaudio, taskid:{taskid}, sid:{sid}, uid:{uid}, email:{email} user_subscript:{user_subscript}')
    if len(taskid.strip()) == 0 :
        ret = 1
        msg = "taskid is empty!"
        logger_echobeat_tts.warning(f'check_genaudio, taskid is empty, sid:{sid}, uid:{uid}, email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)
    
    if isinstance(sid, int):
        sid_hash = sid & 0xFFFFFFFF  # 直接取32位
    else:
        sid_hash = hash(str(sid)) & 0xFFFFFFFF
    reqid = gen_reqid(sid_hash, start_time)
    
    uri = ""
    encrypted_url = ""
    status = "pending"
    task_status, uri, record = check_tts_out_path_by_id(taskid)
    if task_status == 'complete':   
        if uri is None:
            ret = 6
            msg = "System is busy now, please try again later!"
            status = "unknown"
            logger_echobeat_tts.error(f'reqid:{reqid} taskid:{taskid} real_ip:{real_ip} sid:{sid} email:{email} text:{record.text} process failed, get_tts_out_path_by_id returned None')
        else:
            status = "complete"
            encrypted_url = encrypt_url(uri)
            textlen = record.character_count
            # 异步记录用户行为，不阻塞响应
            asyncio.create_task(add_action_record_async(first_ip, real_ip, domain, ModuleType.TTS.value, today, email, uid, sid, record.modelcat, record.modelname))
            logger_echobeat_tts.info(f'check_genaudio, add_action_record_async, first_ip:{first_ip}, real_ip:{real_ip}, domain:{domain}, ModuleType.TTS, today:{today}, email:{email}, uid:{uid}, sid:{sid}, record.modelcat:{record.modelcat}, record.modelname:{record.modelname}')
            # 异步更新用户统计信息，不阻塞响应
            asyncio.create_task(update_user_stats_async(logger_echobeat_tts, uid, email, user_subscript, usable_lastday, sub_productid, 
                tts_charcount=textlen, cover_count=0))
            logger_echobeat_tts.info(f'check_genaudio, update_user_stats_async, uid:{uid}, email:{email}, user_subscript:{user_subscript}, usable_lastday:{usable_lastday}, sub_productid:{sub_productid}, tts_charcount:{textlen}, cover_count:0')

    else:
        status = task_status
        logger_echobeat_tts.error(f'reqid:{reqid} taskid:{taskid} real_ip:{real_ip} sid:{sid} email:{email} task_status:{task_status}')
    
    res = {
        "taskid": taskid,
        "ret": ret,
        "msg": msg,
        "status": status,
        "uri": uri,
        "encrypted_url": encrypted_url,
        "reqid": f'{reqid}'
    }
    logger_echobeat_tts.info(f'reqid:{reqid} taskid:{taskid} real_ip:{real_ip} sid:{sid} email:{email} status:{status} ret:{ret}')
    return res

# 加载通用的克隆模型， 供企业api
common_cloned_models = []
def load_common_cloned_models () :
    global common_cloned_models
    service_models_path = "/app/models/service_model"
    common_models_path = service_models_path + "/common_models/common_models.yaml"
    with open(common_models_path , encoding="UTF-8") as f:
        cfg = f.read()
        models_yaml = yaml.load(cfg, Loader=yaml.FullLoader)
        models_yaml = models_yaml['models']
        for cat in models_yaml :
            cat_models = {}
            catid = cat
            catname = cat
            items = models_yaml[cat]
            model_data = []
            for item in items :
                catid = item['catid']
                catname = item['catname']
                model = {}
                model['model_name'] = item['name']
                model['model_display'] = item['display']
                model['wavplay'] = item['wavplay']
                model_data.append(model)
            model_data.sort(key=lambda x:x['model_display'])
            cat_models['modelcat_name'] = catid
            cat_models['modelcat_display'] = catname
            cat_models['modelcat_data'] = model_data
            common_cloned_models.append(cat_models)
    logger_entapi_tts.info(f'load common cloned models for entapi, count:{len(common_cloned_models)}')

def validate_cloned_model(modelcat, modelname):
    """Validate if modelcat exists in common_clone_models"""
    is_valid_model = False
    for cat_model in common_cloned_models:
        if cat_model['modelcat_name'] == modelcat:
            for model in cat_model['modelcat_data']:
                if model['model_name'] == modelname:
                    is_valid_model = True
    return is_valid_model

# 企业 api 相关的接口
@app.get("/eapi/getmodels")
async def get_models(
    request: Request,
    email: str = Query("", description="User email"),
    api_key: str = Query("", description="User Api Key")
):
    ret = 0
    msg = 'ok'
    start_tm = time.time()
    localtime = time.localtime(start_tm)
    today = time.strftime(DAY_FORMAT, localtime)

    real_ip = get_real_ip(request)

    email_hash = int(hashlib.md5(email.encode()).hexdigest()[:8], 16) if email else 0
    reqid = gen_reqid(email_hash, start_tm)
    
    # 使用抽象的认证函数
    ret, msg, user = validate_entapi_user_auth(email, api_key, real_ip, reqid, logger_entapi_tts)
    if ret != 0:
        logger_entapi_tts.info(f'/eapi/getmodels auth failed, reqid:{reqid}, ip:{real_ip}, email:{email}, msg:{msg}')
        return gen_res_dict(ret, msg)

    models = {}
    models['ret'] = ret
    models['msg'] = msg
    models['common_models'] = common_cloned_models
    models['reqid'] = reqid
    end_tm = time.time()
    cost = int(end_tm - start_tm)
    logger_entapi_tts.info(f'/eapi/getmodels, reqid:{reqid}, email:{email}, ip:{real_ip}, cost:{cost}')
    return models

@app.post("/eapi/genaudio")
async def ent_genaudio(request: Request):
    ret = 0
    msg = 'ok'
    start_tm = time.time()
    localtime = time.localtime(start_tm)
    date = time.strftime(DAY_FORMAT, localtime)
    real_ip = get_real_ip(request)
    data = await request.form()
    email = data.get('email', '')
    api_key = data.get('api_key', '')
    text = data.get('text', '')
    modelcat = data.get('modelcat', '')
    modelname = data.get('modelname', '')
    modellang = data.get('modellang', 'english')

    # 提前生成 reqid 用于日志记录
    email_hash = int(hashlib.md5(email.encode()).hexdigest()[:8], 16) if email else 0
    reqid = gen_reqid(email_hash, start_tm)

    output_format = data.get('out_format', DEFAULT_FORMAT)
    
    # 使用抽象的认证函数
    ret, msg, user = validate_entapi_user_auth(email, api_key, real_ip, reqid, logger_entapi_tts)
    if ret != 0:
        logger_entapi_tts.info(f'/eapi/genaudio auth failed, reqid:{reqid}, ip:{real_ip}, email:{email}, msg:{msg}')
        return gen_res_dict(ret, msg)
    
    # 验证其他必需参数
    if len(text.strip())==0 or len(modelcat.strip())==0 or len(modelname.strip())==0:
        ret = 1
        msg = "text, modelcat, modelname must have values!"
        logger_entapi_tts.info(f'/eapi/genaudio, email:{email}, ip:{real_ip}, modelcat:{modelcat}, modelname: {modelname}')
        return gen_res_dict(ret, msg)
        
    if not validate_cloned_model(modelcat, modelname):
        ret = 2
        msg = f"Invalid model combination: modelcat={modelcat}, modelname={modelname}"
        logger_entapi_tts.info(f'/eapi/genaudio invalid model, reqid:{reqid}, email:{email}, ip:{real_ip}, modelcat:{modelcat}, modelname:{modelname}')
        return gen_res_dict(ret, msg)
    
    # 验证文本长度（企业API限制为500字符）
    text_validation = validate_text_length(text.strip(), MAX_ENTTTS_TEXT_LENGTH, reqid, logger_entapi_tts)
    if text_validation['ret'] != 0:
        text_validation['msg'] = f"Text length ({len(text.strip())}) exceeds maximum allowed length of {MAX_ENTTTS_TEXT_LENGTH} characters"
        logger_entapi_tts.info(f'/eapi/genaudio email:{email}, ip:{real_ip}, modelcat:{modelcat}, modelname:{modelname}, msg: {text_validation["msg"]}')
        return text_validation

    userid = 0
    t = 1 # 1 为共有模型， 2 为私有模型

    textlen = len(text)
    add_api_user_record(email, date, reqid, '/eapi/genaudio', user_action="genaudio", firstip=None, allip=real_ip , text=text,
                       character_count=textlen, modelcat=modelcat, modelname=modelname)
 
    # 判断调用哪一个任务
    model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
    model_desc = f'{model_path}/{modelname}_desc.txt'
    if not os.path.exists(model_path) or not os.path.exists(model_desc) :
        ret = 1
        msg = f'modelname:{modelname} is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} model_path:{model_path}, modelname:{modelname}, msg:{msg}')
        return gen_res_dict(ret, msg)
    tts_modeltype = "gsv" # 默认使用sovits模型, gsv和fish-speed公用一个task
    with open(model_desc, 'r', encoding='utf-8') as f :
        model_desc_content = f.readline().strip('\n')
        logger.debug(f'handle, model_desc_content:{model_desc_content}')
        items = model_desc_content.split('|')
        if len(items) >= 6:
            modellang = items[5] # model lang, en, ja, zh, ext
        if len(items) >= 9:
            tts_modeltype = items[7] # index-tts, gsv
            tts_modelversion = items[8]  # index-tts, v2, v2.5
    if tts_modeltype == "index-tts" :
        if tts_modelversion == "v2.5" :
            ok, emo_type, emo_alpha, emo_err = parse_emo_params(data)
            if not ok:
                logger_entapi_tts.warning(f'reqid:{reqid} parse_emo_params failed: {emo_err}')
                return gen_res_dict(1, emo_err)
            ok2, speed, volume_db, sv_err = parse_speed_volume_params(data)
            if not ok2:
                logger_entapi_tts.warning(f'reqid:{reqid} parse_speed_volume_params failed: {sv_err}')
                return gen_res_dict(1, sv_err)
            gpu_gen_indextts25.delay(
                modelname, text, modelcat, userid, t, str(reqid) + '.eapi', output_format,
                emo_type=emo_type, emo_alpha=emo_alpha,
                speed=speed, volume_db=volume_db,
            )
        else :
            gpu_gen_indextts.delay(modelname, text, modelcat, userid, t, str(reqid) + '.eapi', output_format)
    else :
        modellang = map_lang_abbrev(modellang, modelname)
        gpu_gen_tts.delay(modelname, text, modelcat, userid, t, str(reqid) + '.eapi', output_format)

    timeout: int = 180
    interval: float = 2.0

    uri = get_tts_out_path_by_id(str(reqid) + '.eapi', timeout, interval)
    if uri is None:
        ret = 6
        msg = "System is busy now, please try again later!"
        logger_entapi_tts.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} process failed, get_tts_out_path_by_id(ent) returned None')
        return gen_res_dict(ret, msg)
    encrypted_uri = encrypt_url(uri)

    end_tm = time.time()
    cost = int(end_tm - start_tm)
    res = {}
    res['ret'] = 0
    res['msg'] = 'ok'
    res['reqid'] = reqid
    from_domain = user.from_domain
    res['uri'] = "https://" + from_domain + uri
    res['enurl'] = encrypted_uri
    res['textlen'] = textlen
    usecnt = int(user.used_char_count) + int(textlen)

    update_data = {}
    update_data['used_char_count'] = usecnt
    update_api_user_by_email(email, **update_data)

    logger_entapi_tts.info(f'/eapi/genaudio, reqid:{reqid}, email:{email}, ip:{real_ip}, modelcat:{modelcat}, modelname:{modelname}, text:{text}, cost:{cost}')
    return res

@app.post("/coverapi/cover")
async def cover_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/coverapi/cover'
    start_tm = time.time()
    localtime = time.localtime(start_tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d%H%M%S', localtime)

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    data = await request.form()
    uid = data.get('uid', "avuiddefault")
    email = data.get('email', '')
    subscript = int(data.get('subscript', 0))
    modelname = data.get('modelname', '')
    modelcat = data.get('modelcat', '')
    modelgender = data.get('modelgender', '').lower()
    tabtype = data.get('tabtype', '')
    lang = data.get('lang', 'english')
    converturl = data.get('converturl', '')
    upgender = data.get('upgender', '').lower()
    domain = data.get('domain', '')

    reqid = gen_reqid(uid, start_tm)
    logger_cover.debug(f'reqid:{reqid} real_ip:{real_ip} referer:{referer} uid:{uid} email:{email} subscript:{subscript} modelname:{modelname} modelcat:{modelcat}' \
        f'tabtype:{tabtype} lang:{lang} converturl:{converturl} upgender:{upgender} modelgender:{modelgender}')

    ret, msg = validate_request(logger_cover, real_ip, referer, reqid, email, subscript, today, uid, ModuleType.COVER, domain)
    if ret != 0 :
        logger_cover.info(f'reqid:{reqid} ip:{real_ip} referer:{referer} uid:{uid} email:{email} subscript:{subscript} modelname:{modelname} modelcat:{modelcat}' \
            f'tabtype:{tabtype} lang:{lang} converturl:{converturl} upgender:{upgender} modelgender:{modelgender} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)

    if not validate_gender(upgender) or not validate_gender(modelgender):
        ret = 1
        msg = f'upgender:{upgender} or modelgender:{modelgender} is invalid, reqid:{reqid}!'
        logger_cover.warning(f'cover, reqid:{reqid} ip:{real_ip} uid:{uid} email:{email}, msg: {msg}')
        return gen_res_dict(ret, msg)

    if (len(modelname.strip()) == 0 or len(modelcat.strip()) == 0 or len(lang.strip()) == 0 or len(converturl.strip()) == 0):
        ret = 1
        msg = f'modelname:{modelname} or modelcat:{modelcat} or lang:{lang} or converturl:{converturl} is invalid, reqid:{reqid}!'
        logger_cover.warning(f'reqid:{reqid} ip:{real_ip} email:{email}, msg: {msg}')
        return gen_res_dict(ret, msg)
    
    ret, msg, upfileinfo = validate_audio_url(converturl, reqid, first_ip, email, today, datetime, logger_cover)
    if ret != 0:
        logger_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    timestamp = data.get('tstamp', 0)
    signature = data.get('snature', '')
    ret, msg = validate_signature(logger_cover, timestamp, signature, converturl, datetime, reqid, first_ip, real_ip, uid, email, subscript, referer, ModuleType.COVER)
    if ret != 0 :
        logger_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    model_path = f'{cover_model_dir}/{lang}/{modelcat}/{modelname}'
    model_index_path = f'{model_path}/{modelname}_v2.index'
    model_file_path = f'{model_path}/{modelname}_weights.pth'
    if not os.path.exists(model_path) or not os.path.exists(model_index_path) or not os.path.exists(model_file_path):
        ret = 1
        msg = f'model {modelcat}-{modelname} is not exists, reqid:{reqid}!'
        logger_cover.warning(f'reqid:{reqid} ip:{real_ip} email:{email},  {model_index_path} or {model_file_path} is not exists,  msg: {msg}')
        return gen_res_dict(ret, msg)

    ret = add_ip_tool_record(first_ip, today, ToolType.COVER, task_id=reqid, uid=uid, user_email=email, allip_address=real_ip, task_cmd=task_cmd)
    if ret != 0:
        logger_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record failed: {msg}')
        return gen_res_dict(ret, msg)

    logger_cover.info(f'cover, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record done, add to cover task queue')
    gpu_cover.delay(modelcat, modelname, modelgender, upfileinfo, upgender, task_id=reqid, first_ip=first_ip, email=email, lang=lang)  
    logger_cover.info(f'cover, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} waiting for cover worker task done')

    timeout: int = 180
    interval: float = 2.0
    dbret, uri = get_tool_out_path_by_task_id(reqid, timeout, interval)
    if dbret != 0 :
        ret = 6
        msg = "System is busy now, please try again later!"
        if dbret == 2:
            logger_cover.error(f'cover reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process failed, dbret:{dbret}, uri:{uri}, msg: {msg}')
        elif dbret == 1:
            logger_cover.error(f'cover reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} proceess timeout, dbret:{dbret}, uri:{uri}, msg: {msg}')
        else :
            logger_cover.error(f'cover reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process unknown error, dbret:{dbret}, uri:{uri}, msg: {msg}')
        return gen_res_dict(ret, msg)

    encrypted_uri = encrypt_url(uri)
    endtm = time.time()
    cost = int(endtm - start_tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "uri": uri,
        "enurl": encrypted_uri,
        "reqid": reqid
    }
    
    logmsg = f'cover, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, modelcat:{modelcat}, modellang:{lang} email:{email}, subscript:{subscript}, cost:{cost}, modelname:{modelname}, refer:{referer}, ret:{ret}, uri:{uri}, enurl:{encrypted_uri}'
    logger_cover.info(logmsg)

    return res

# app 使用异步生成接口
@app.post("/coverapi/asyncover")
async def asyn_cover_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/coverapi/asyncover'
    start_tm = time.time()
    localtime = time.localtime(start_tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    time_str = time.strftime('%Y%m%d%H%M%S', localtime)
    current_date = datetime.datetime.now()
    # Calculate date range for last 30 days
    query_enddate = current_date.date()
    query_startdate = query_enddate - datetime.timedelta(days=30)

    email = ""
    uid = ''
    user_subscript = 0

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "echobeat")

    data = await request.json()
    modelcat = data.get('modelcat', '')
    modelname = data.get('modelname', '')
    modelgender = data.get('modelgender', '').lower()
    converturl = data.get('converturl', '')
    sid = data.get('sid', "ebdefaultsid")
    token = data.get('token', "")
    upgender = data.get('upgender', '').lower()
    domain = data.get('domain', 'echobeat')
    lang = data.get('lang', 'english')

    reqid = gen_reqid(sid, start_tm)
    logger_echobeat_cover.debug(f'reqid:{reqid} real_ip:{real_ip} referer:{referer} sid:{sid} email:{email} subscript:{user_subscript} modelname:{modelname} modelcat:{modelcat}' \
        f'lang:{lang} converturl:{converturl} upgender:{upgender} modelgender:{modelgender}')
    
    # 通过ip，action 和 domain 查询当天和最近30天使用次数
    if len(token.strip()) != 0 :
        usertoken_info = decrypt_userinfo(token)
        if usertoken_info is not None:
            uid = usertoken_info.get('uid', '')
            email = usertoken_info.get('email', '')
            user_subscript = usertoken_info.get('user_subscript', 0)
        else:
            ret = 1
            msg = "token is invalid!"
            logger_echobeat_cover.info(f'cover async 111-1, reqid:{reqid} first_ip:{first_ip}, uid:{uid}, sid:{sid},  email:{email}, user_subscript:{user_subscript} ret:{ret}, msg:{msg}')
            return gen_res_dict(ret, msg)

    logger_echobeat_cover.info(f'cover async 11, reqid:{reqid} first_ip:{first_ip}, uid:{uid}, sid:{sid},  email:{email}, user_subscript:{user_subscript}')
    if user_subscript != 1:
        logger_echobeat_cover.info(f'cover async need_gen_limit, first_ip:{first_ip}, uid:{uid}, sid:{sid}, domain:{domain}')
        if need_gen_limit(logger_echobeat_cover, first_ip, uid, sid, domain, ModuleType.COVER, query_startdate, query_enddate):
            ret = 2
            msg = "You have exceeded the free usage limit for today or last month."
            logger_echobeat_cover.info(f'cover async need_gen_limit true reqid:{reqid} first_ip:{first_ip}, uid:{uid}, sid:{sid}, ret:{ret}, msg:{msg}')
            return gen_res_dict(ret, msg)
    # 移动端有单独的统计逻辑，这里不需要再统计
    #ret, msg = validate_request(logger_echobeat_cover, real_ip, referer, reqid, email, user_subscript, today, sid, ModuleType.COVER, domain)
    #if ret != 0 :
    #    logger_echobeat_cover.info(f'reqid:{reqid} ip:{real_ip} referer:{referer} sid:{sid} email:{email} subscript:{user_subscript} modelname:{modelname} modelcat:{modelcat}' \
    #        f'lang:{lang} converturl:{converturl} upgender:{upgender} modelgender:{modelgender} ret:{ret}, msg:{msg}')
    #    return gen_res_dict(ret, msg)

    if not validate_gender(upgender) or not validate_gender(modelgender):
        ret = 1
        msg = f'upgender:{upgender} or modelgender:{modelgender} is invalid, reqid:{reqid}!'
        logger_echobeat_cover.warning(f'cover, reqid:{reqid} ip:{real_ip} sid:{sid} email:{email}, msg: {msg}')
        return gen_res_dict(ret, msg)

    if (len(modelname.strip()) == 0 or len(modelcat.strip()) == 0 or len(lang.strip()) == 0 or len(converturl.strip()) == 0):
        ret = 1
        msg = f'modelname:{modelname} or modelcat:{modelcat} or lang:{lang} or converturl:{converturl} is invalid, reqid:{reqid}!'
        logger_echobeat_cover.warning(f'reqid:{reqid} ip:{real_ip} sid:{sid} email:{email}, msg: {msg}')
        return gen_res_dict(ret, msg)
    
    ret, msg, upfileinfo = validate_audio_url(converturl, reqid, first_ip, email, today, time_str, logger_cover)
    if ret != 0:
        logger_echobeat_cover.error(f'reqid:{reqid} real_ip:{real_ip} sid:{sid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    timestamp = data.get('tstamp', 0)
    signature = data.get('snature', '')
    ret, msg = validate_signature(logger_cover, timestamp, signature, converturl, time_str, reqid, first_ip, real_ip, sid, email, user_subscript, referer, ModuleType.COVER)
    if ret != 0 :
        logger_echobeat_cover.error(f'reqid:{reqid} real_ip:{real_ip} sid:{sid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    model_path = f'{cover_model_dir}/{lang}/{modelcat}/{modelname}'
    model_index_path = f'{model_path}/{modelname}_v2.index'
    model_file_path = f'{model_path}/{modelname}_weights.pth'
    if not os.path.exists(model_path) or not os.path.exists(model_index_path) or not os.path.exists(model_file_path):
        ret = 1
        msg = f'model {modelcat}-{modelname} is not exists, reqid:{reqid}!'
        logger_echobeat_cover.warning(f'reqid:{reqid} ip:{real_ip} sid:{sid} email:{email},  {model_index_path} or {model_file_path} is not exists,  msg: {msg}')
        return gen_res_dict(ret, msg)

    ret = add_ip_tool_record(first_ip, today, ToolType.COVER, task_id=reqid, uid=sid, user_email=email, allip_address=real_ip, task_cmd=task_cmd)
    if ret != 0:
        logger_echobeat_cover.error(f'reqid:{reqid} real_ip:{real_ip} sid:{sid} email:{email} add_ip_tool_record failed: {msg}')
        return gen_res_dict(ret, msg)

    logger_echobeat_cover.info(f'cover, reqid:{reqid} real_ip:{real_ip} sid:{sid} email:{email} add_ip_tool_record done, add to cover task queue')
    gpu_cover.delay(modelcat, modelname, modelgender, upfileinfo, upgender, task_id=reqid, first_ip=first_ip, email=email, lang=lang)  
    logger_echobeat_cover.info(f'cover, reqid:{reqid} real_ip:{real_ip} sid:{sid} email:{email} waiting for cover worker task done')

    endtm = time.time()
    cost = int(endtm - start_tm)

    res = {
        "ret": ret,
        "msg": msg,
        "reqid": f'{reqid}'
    }
    
    logmsg = f'cover async, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, sid:{sid}, modelcat:{modelcat}, modellang:{lang} email:{email}, subscript:{user_subscript}, cost:{cost}, modelname:{modelname}, refer:{referer}, ret:{ret}'
    logger_echobeat_cover.info(logmsg)

    return res

ALLOWED_TRANSCRIBE_FORMATS = ['json', 'txt', 'srt', 'vtt', 'ass']
VIDEO_TTS_TRANSCRIBE = "video_tts_transcribe"

@app.post("/api/transcribe")
async def transcribe_endpoint(request: Request):
    ret = 0
    msg = 'ok'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()
    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    userid = json_post_raw.get("userid", 0)
    if isinstance(userid, int):
        userid_hash = userid & 0xFFFFFFFF
    else:
        userid_hash = hash(str(userid)) & 0xFFFFFFFF
    reqid = gen_reqid(userid_hash, tm)
    today = time.strftime(DAY_FORMAT, time.localtime(tm))
    datetime = time.strftime("%Y%m%d_%H%M%S", time.localtime(tm))
    subscript = json_post_raw.get("subscript", 0)
    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')

    upurl = json_post_raw.get("upurl", '')
    language = json_post_raw.get("language", 'en')
    output_format = (json_post_raw.get("format", 'json') or 'json').strip().lower()

    ret, msg = validate_request(logger_tts, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.TRANSCRIBE, domain)
    if ret != 0:
        logger_tts.warning(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} validate_request failed: {msg}')
        return gen_res_dict(ret, msg)

    if output_format not in ALLOWED_TRANSCRIBE_FORMATS:
        logger_tts.warning(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} Invalid format: {output_format}')
        return gen_res_dict(1, f'Invalid format: {output_format}')

    if email == VIDEO_TTS_TRANSCRIBE:
        ret, msg, upfileinfo = validate_internal_tts_url(upurl, reqid, first_ip, email, today, datetime, logger_tts)
        validate_fn_name = 'validate_internal_tts_url'
    else:
        ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger_tts)
        validate_fn_name = 'validate_audio_url'
    if ret != 0:
        logger_tts.warning(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} {validate_fn_name} failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg = validate_signature(logger_tts, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, userid, email, subscript, referer, ModuleType.TRANSCRIBE)
    if ret != 0:
        logger_tts.warning(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret = add_ip_tool_record(first_ip, today, ToolType.TRANSCRIBE, task_id=reqid, uid=userid,
                             user_email=email, allip_address=real_ip, task_cmd='/api/transcribe')
    if ret != 0:
        logger_tts.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} add_ip_tool_record failed')
        return gen_res_dict(ret, 'add_ip_tool_record failed')

    logger_tts.info(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} '
                    f'language:{language} format:{output_format} add_ip_tool_record done, dispatch transcribe task')
    resolve_transcribe_task(language).delay(
        upfileinfo, language, output_format,
        task_id=reqid, userid=userid, first_ip=first_ip, email=email,
    )

    timeout: int = 300
    interval: float = 1.0
    dbret, uri = get_tool_out_path_by_task_id(reqid, timeout, interval)
    if dbret != 0 or uri is None:
        logger_tts.warning(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} transcribe timeout or failed, dbret:{dbret}, uri:{uri}')
        return gen_res_dict(6, "System is busy now, please try again later!")

    encrypted_uri = encrypt_url(uri)
    logmsg = (f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, '
              f'email:{email}, subscript:{subscript}, language:{language}, format:{output_format}, '
              f'refer:{referer}, uri:{uri}')
    logger_tts.info(logmsg)

    return {
        "ret": 0,
        "msg": 'ok',
        "uri": uri,
        "enurl": encrypted_uri,
        "reqid": str(reqid),
    }

# for app asyn api, 查询生成状态
@app.post("/coverapi/checkcover")
async def check_cover(request: Request):
    ret = 0
    msg = 'ok'
    start_time = time.time()
    cnt = 0
    req_uri = "/coverapi/checkcover"
    email = ''
    subscript = 0

    localtime = time.localtime(start_time)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d_%H%M%S', localtime)
    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""

    # 改为JSON格式接收数据
    json_data = await request.json()
    taskid = str(json_data.get('taskid', ''))
    sid = json_data.get('sid', '')
    token = json_data.get('token', '')
    domain = json_data.get('domain', 'echobeat_default')
    uid = ''
    email = ''
    user_subscript = 0
    usable_lastday = ''
    sub_productid = ''
    if len(token.strip()) != 0 :
        usertoken_info = decrypt_userinfo(token)
        if usertoken_info is not None:
            uid = usertoken_info.get('uid', '')
            email = usertoken_info.get('email', '')
            user_subscript = usertoken_info.get('user_subscript', 0)
            usable_lastday = usertoken_info.get('usable_lastday', '')
            sub_productid = usertoken_info.get('sub_productid', '')
        else:
            ret = 1
            msg = "token is invalid!"
            logger_echobeat_cover.info(f'check_cover 111-1, taskid:{taskid}, sid:{sid}, token:{token}, domain:{domain} ret:{ret}, msg:{msg}')
            return gen_res_dict(ret, msg)

    if len(taskid.strip()) == 0 :
        ret = 1
        msg = "taskid is empty!"
        logger_echobeat_cover.warning(f'check_cover, taskid is empty, sid:{sid}, uid:{uid}, email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)
    
    reqid = gen_reqid(sid, start_time)
    
    uri = ""
    encrypted_url = ""
    status = "pending"
    task_status, uri, record = check_tool_out_path_by_task_id(taskid)
    if task_status == 'complete':   
        status = "complete"
        encrypted_url = encrypt_url(uri)
        # 异步记录用户行为，不阻塞响应
        asyncio.create_task(add_action_record_async(first_ip, real_ip, domain, ModuleType.COVER.value, today, email, uid, sid))
        # 异步更新用户统计信息，不阻塞响应
        asyncio.create_task(update_user_stats_async(logger, uid, email, user_subscript, usable_lastday, sub_productid, 
            tts_charcount=0, cover_count=1))
    else:
        status = task_status
        logger_echobeat_cover.error(f'reqid:{reqid} taskid:{taskid} real_ip:{real_ip} sid:{sid} email:{email} task_status:{task_status}')
    
    res = {
        "taskid": taskid,
        "ret": ret,
        "msg": msg,
        "status": status,
        "uri": uri,
        "encrypted_url": encrypted_url,
        "reqid": f'{reqid}'
    }
    logger_echobeat_cover.info(f'check cover, reqid:{reqid} taskid:{taskid} real_ip:{real_ip} sid:{sid} email:{email} status:{status} ret:{ret}')
    return res

@app.post("/coverapi/vocal")
async def vocal_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/coverapi/vocal'
    start_tm = time.time()
    localtime = time.localtime(start_tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d%H%M%S', localtime)

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    data = await request.form()
    uid = data.get('uid', "avuiddefault")
    email = data.get('email', '')
    subscript = int(data.get('subscript', 0))
    upurl = data.get('upurl', '')
    domain = data.get('domain', '')

    reqid = gen_reqid(uid, start_tm)

    ret, msg = validate_request(logger_cover, real_ip, referer, reqid, email, subscript, today, uid, ModuleType.VOCAL_ISOLATOR, domain)
    if ret != 0 :
        logger_echobeat_cover.info(f'reqid:{reqid} ip:{real_ip} referer:{referer} uid:{uid} email:{email} subscript:{subscript} upurl:{upurl} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)

    if (len(upurl.strip()) == 0):
        ret = 1
        msg = f'upurl is invalid, reqid:{reqid}!'
        logger_echobeat_cover.warning(f'reqid:{reqid} ip:{real_ip} uid:{uid} email:{email} subscript:{subscript} msg:{msg} upurl:{upurl} referer:{referer}')
        return gen_res_dict(ret, msg)

    timestamp = data.get('tstamp', 0)
    signature = data.get('snature', '')
    ret, msg = validate_signature(logger_cover, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, uid, email, subscript, referer, ModuleType.VOCAL_ISOLATOR)
    if ret != 0 :
        logger_echobeat_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    # 验证音频URL
    ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger_cover)
    if ret != 0:
        logger_echobeat_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    ret = add_ip_tool_record(first_ip, today, ToolType.VOCAL_ISOLATOR, task_id=reqid, uid=uid, user_email=email, allip_address=real_ip, task_cmd=task_cmd)
    if ret != 0:
        logger_echobeat_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record failed: {msg}')
        return gen_res_dict(ret, msg)

    logger_echobeat_cover.info(f'vocal, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record done, add to vocal task queue')
    gpu_vocal.delay(upfileinfo, task_id=reqid, first_ip=first_ip, email=email)  
    logger_echobeat_cover.info(f'vocal, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} waiting for vocal worker task done')

    timeout: int = 180
    interval: float = 2.0
    dbret, uri = get_tool_out_path_by_task_id(reqid, timeout, interval)
    if dbret != 0 :
        ret = 6
        msg = "System is busy now, please try again later!"
        if dbret == 2:
            logger_echobeat_cover.error(f'vocal reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process failed, dbret:{dbret}, uri:{uri}, msg: {msg}')
        elif dbret == 1:
            logger_echobeat_cover.error(f'vocal reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} proceess timeout, dbret:{dbret}, uri:{uri}, msg: {msg}')
        else :
            logger_echobeat_cover.error(f'vocal reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process unknown error, dbret:{dbret}, uri:{uri}, msg: {msg}')
        return gen_res_dict(ret, msg)

    encrypted_uri = encrypt_url(uri)

    endtm = time.time()
    cost = int(endtm - start_tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "uri": uri,
        "enurl": encrypted_uri,
        "reqid": reqid
    }
    
    logmsg = f'vocal, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, email:{email}, subscript:{subscript}, cost:{cost}, refer:{referer}, ret:{ret}, uri:{uri}, enurl:{encrypted_uri}'
    logger_echobeat_cover.info(logmsg)

    return res

@app.post("/coverapi/instrument")
async def instrument_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/coverapi/instrument'
    start_tm = time.time()
    localtime = time.localtime(start_tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d%H%M%S', localtime)

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    data = await request.form()
    uid = data.get('uid', "avuiddefault")
    email = data.get('email', '')
    subscript = int(data.get('subscript', 0))
    upurl = data.get('upurl', '')
    domain = data.get('domain', '')

    reqid = gen_reqid(uid, start_tm)

    ret, msg = validate_request(logger_cover, real_ip, referer, reqid, email, subscript, today, uid, ModuleType.VOCAL_REMOVER, domain)
    if ret != 0 :
        logger_cover.info(f'reqid:{reqid} ip:{real_ip} referer:{referer} uid:{uid} email:{email} subscript:{subscript} upurl:{upurl} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)

    if (len(upurl.strip()) == 0):
        ret = 1
        msg = f'upurl is invalid, reqid:{reqid}!'
        logger_cover.warning(f'reqid:{reqid} ip:{real_ip} uid:{uid} email:{email} subscript:{subscript} msg:{msg} upurl:{upurl} referer:{referer}')
        return gen_res_dict(ret, msg)

    timestamp = data.get('tstamp', 0)
    signature = data.get('snature', '')
    ret, msg = validate_signature(logger_cover, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, uid, email, subscript, referer, ModuleType.VOCAL_REMOVER)
    if ret != 0 :
        logger_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    # 验证音频URL
    ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger_cover)
    if ret != 0:
        logger_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    ret = add_ip_tool_record(first_ip, today, ToolType.VOCAL_REMOVER, task_id=reqid, uid=uid, user_email=email, allip_address=real_ip, task_cmd=task_cmd)
    if ret != 0:
        logger_cover.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record failed: {msg}')
        return gen_res_dict(ret, msg)

    logger_cover.info(f'instrument, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record done, add to instrument task queue')
    gpu_instrument.delay(upfileinfo, task_id=reqid, first_ip=first_ip, email=email)  
    logger_cover.info(f'instrument, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} waiting for instrument worker task done')

    timeout: int = 180
    interval: float = 2.0
    dbret, uri = get_tool_out_path_by_task_id(reqid, timeout, interval)
    if dbret != 0 :
        ret = 6
        msg = "System is busy now, please try again later!"
        if dbret == 2:
            logger_cover.error(f'instrument reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process failed, dbret:{dbret}, uri:{uri}, msg: {msg}')
        elif dbret == 1:
            logger_cover.error(f'instrument reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} proceess timeout, dbret:{dbret}, uri:{uri}, msg: {msg}')
        else :
            logger_cover.error(f'instrument reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process unknown error, dbret:{dbret}, uri:{uri}, msg: {msg}')
        return gen_res_dict(ret, msg)
    encrypted_uri = encrypt_url(uri)

    endtm = time.time()
    cost = int(endtm - start_tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "uri": uri,
        "enurl": encrypted_uri,
        "reqid": reqid
    }
    
    logmsg = f'instrument, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, email:{email}, subscript:{subscript}, cost:{cost}, refer:{referer}, ret:{ret}, uri:{uri}, enurl:{encrypted_uri}'
    logger_cover.info(logmsg)

    return res

@app.post("/upapi/extractaudio")
async def extractaudio_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/upapi/extractaudio'
    start_tm = time.time()
    localtime = time.localtime(start_tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d%H%M%S', localtime)

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else ""
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    data = await request.form()
    uid = data.get('uid', "avuiddefault")
    email = data.get('email', '')
    subscript = int(data.get('subscript', 0))
    upurl = data.get('upurl', '')
    domain = data.get('domain', '')

    reqid = gen_reqid(uid, start_tm)

    ret, msg = validate_request(logger_audio_extract, real_ip, referer, reqid, email, subscript, today, uid, ModuleType.AUDIO_EXTRACTOR, domain)
    if ret != 0 :
        logger_audio_extract.info(f'reqid:{reqid} ip:{real_ip} referer:{referer} uid:{uid} email:{email} subscript:{subscript} upurl:{upurl} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)

    if (len(upurl.strip()) == 0):
        ret = 1
        msg = f'upurl is invalid, reqid:{reqid}!'
        logger_audio_extract.warning(f'reqid:{reqid} ip:{real_ip} uid:{uid} email:{email} subscript:{subscript} msg:{msg} upurl:{upurl} referer:{referer}')
        return gen_res_dict(ret, msg)

    timestamp = data.get('tstamp', 0)
    signature = data.get('snature', '')
    ret, msg = validate_signature(logger_audio_extract, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, uid, email, subscript, referer, ModuleType.AUDIO_EXTRACTOR)
    if ret != 0 :
        logger_audio_extract.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    # 验证音频URL
    ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger_audio_extract)
    if ret != 0:
        logger_audio_extract.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    ret = add_ip_tool_record(first_ip, today, ToolType.AUDIO_EXTRACTOR, task_id=reqid, uid=uid, user_email=email, allip_address=real_ip, task_cmd=task_cmd)
    if ret != 0:
        logger_audio_extract.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record failed: {msg}')
        return gen_res_dict(ret, msg)

    logger_audio_extract.info(f'extractaudio, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} add_ip_tool_record done, add to extractaudio task queue')
    task_audio_extract.delay(upfileinfo, task_id=reqid, first_ip=first_ip, email=email)  
    logger_audio_extract.info(f'extractaudio, reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} waiting for extractaudio worker task done')

    timeout: int = 180
    interval: float = 2.0
    dbret, uri = get_tool_out_path_by_task_id(reqid, timeout, interval)
    if dbret != 0 :
        ret = 6
        msg = "System is busy now, please try again later!"
        if dbret == 2:
            logger_audio_extract.error(f'extractaudio reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process failed, dbret:{dbret}, uri:{uri}, msg: {msg}')
        elif dbret == 1:
            logger_audio_extract.error(f'extractaudio reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} proceess timeout, dbret:{dbret}, uri:{uri}, msg: {msg}')
        else :
            logger_audio_extract.error(f'extractaudio reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} process unknown error, dbret:{dbret}, uri:{uri}, msg: {msg}')
        return gen_res_dict(ret, msg)

    encrypted_uri = encrypt_url(uri)

    endtm = time.time()
    cost = int(endtm - start_tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "uri": uri,
        "enurl": encrypted_uri,
        "reqid": reqid
    }
    
    logmsg = f'vocal, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, email:{email}, subscript:{subscript}, cost:{cost}, refer:{referer}, ret:{ret}, uri:{uri}, enurl:{encrypted_uri}'
    logger_audio_extract.info(logmsg)
    return res

@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化操作"""
    logger_entapi_tts.info('load common cloned models for entapi')
    load_common_cloned_models()
    logger_entapi_tts.info('load common cloned models for entapi done')

#if __name__ == "__main__":
#    import uvicorn

#    parser = argparse.ArgumentParser(description='Simple TCP server.')
#    parser.add_argument('--port', type=int, required=True, help='Port number to listen on')
#    args = parser.parse_args()
    
#    log_config = uvicorn.config.LOGGING_CONFIG
#    log_config["formatters"]["access"]["fmt"] = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"
#    log_config["formatters"]["default"]["fmt"] = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"

#    uvicorn.run(app, host="0.0.0.0", port=args.port, reload=False, log_config=log_config)