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
from utility.request_util import get_real_ip, validate_request, validate_signature, validate_text_length, \
    validate_gender, validate_audio_url
from utility.module_enum import ModuleType

from tasks_interface import gpu_gen_tts, gpu_gen_indextts25

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

    #add_ip_use_record(first_ip, today, text, reqid, '/api/genaudio', modelcat, modelname, textlen, email, real_ip)

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

    uri = ''
    encrypted_uri = ''
    #uri, task_status, task_detail = get_tts_out_path_by_id(reqid, timeout, interval)
    #if uri is None:
    #    ret = 6
    #    msg = "System is busy now, please try again later!"
    #    logger_tts.error(f'reqid:{reqid} real_ip:{real_ip} userid:{userid} email:{email} process failed, task_status:{task_status}, task_detail:{task_detail}')
    #    return gen_res_dict(ret, msg)
    #encrypted_uri = encrypt_url(uri)

    endtm = time.time()
    cost = int(endtm - tm)

    #torch.cuda.empty_cache()

    res = {
        "ret": 0,
        "msg": 'ok',
        "uri": uri,
        "enurl": encrypted_uri,
        "reqid": reqid
    }
    
    logmsg = f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, modelcat:{modelcat}, modellang:{modellang} email:{email}, subscript:{subscript}, cost:{cost}, modelname:{modelname}, refer:{referer}, today req cnt:{cnt}, ret:{ret}, uri:{uri}, enurl:{encrypted_uri}'
    logger_tts.info(logmsg)
    #logger.info(logmsg)
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