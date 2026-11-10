import os
import time
import logging
import argparse
import random
import hmac
import hashlib
import datetime
import json
import yaml
import asyncio

from fastapi import FastAPI, Request, Query

from utility.util import gen_reqid, map_lang_abbrev
from cmwk_common.util import gen_res_dict
from utility.request_util import get_real_ip, validate_request, validate_signature, validate_text_length, \
    validate_gender, validate_audio_url, validate_internal_tts_url, validate_video_create_params, \
    validate_video_intent_params
from cmwk_common.module_enum import ModuleType
from cmwk_common.userserver_producer import post_generate_add
from cmwk_common.task_payload import build_payload, dispatch
from utility.allused_voicemodels import register_allused_voicemodels, get_common_model
from utility.voice_settings import parse_emo_params, parse_speed_volume_params
from config.comm_config import DEFAULT_USER_FIRST_IP
from config.transcribe_routing import resolve_transcribe_engine

from tasks_interface import gpu_gen_tts, gpu_gen_indextts, gpu_gen_sound, audio_extract, gpu_cover, gpu_vocal, \
    gpu_instrument, transcribe_whisper, video_intent, video_create

app = FastAPI()

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'
DEFAULT_FORMAT = "mp3"
sounds_output_dir = 'output'

logging.getLogger("multipart").setLevel(logging.INFO)
logging.getLogger("multipart.multipart").setLevel(logging.INFO)

log_dir = '/app/log'
os.makedirs(log_dir, exist_ok=True)
os.chmod(log_dir, 0o777)

LOG_FORMAT = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"
_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(_formatter)
logger.addHandler(console_handler)

app_handler = logging.FileHandler(os.path.join(log_dir, 'uscm_server.log'))
app_handler.setLevel(logging.DEBUG)
app_handler.setFormatter(_formatter)
logger.addHandler(app_handler)

error_handler = logging.FileHandler(os.path.join(log_dir, 'uscm_server_error.log'))
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(_formatter)
logger.addHandler(error_handler)

register_allused_voicemodels()

model = None
sample_rate = 32000
sample_size = 1920000

MAX_TTS_TEXT_LENGTH = 700
MAX_ENTTTS_TEXT_LENGTH = 400
MAX_SOUND_TEXT_LENGTH = 100

# for web api, 异步提交生成；结果由专门 check 接口查询
# ret 取值及含义:
#   0  - 成功，任务已提交（响应含 task_id）
#   1  - 参数/业务错误：文本为空或超长、签名字段缺失、domain 非法/不支持、
#        模型无效、index-tts 的 emo/speed/volume 参数非法、generateadd 失败等
#   3  - 签名鉴权失败：时间戳过期（>1h）或签名不匹配
#   10 - 需登录才能继续（caluma.ai 未登录且超出未登录免费额度）
#   11 - 登录态失效，需重新登录（email 与 session 不匹配）
#   12 - 免费额度用尽，需升级付费（已登录但超出免费次数）
#   20 - 订阅已过期
#   21 - 订阅额度已用完
@app.post("/api/genaudio")
async def tts_endpoint(request: Request):
    ret = 0
    msg = 'ok'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()
    
    # 验证签名
    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)
    cnt = 0
    
    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    output_format = json_post_raw.get("output_format", DEFAULT_FORMAT)

    text = json_post_raw.get("text", '')
    # 验证文本长度
    ret, msg = validate_text_length(text, MAX_TTS_TEXT_LENGTH, 0, reqid, logger)
    if ret != 0:
        logger.info(f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid},' \
            f'email:{email}, refer:{referer}, textlen:{len(text)} {msg}!')
        return gen_res_dict(ret, msg)
    
    ret, msg = validate_signature(logger, timestamp, signature, text, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.TTS)
    if ret != 0 :
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.TTS, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0 :
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} email:{email} uid:{uid} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    modelcat = json_post_raw.get("modelcat", '')
    modelname = json_post_raw.get("modelname", '')
    modellang = json_post_raw.get("modellang", 'english')

    model_entry = get_common_model(modelcat, modelname)
    if not model_entry:
        ret = 1
        msg = f'modelname:{modelname} is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} modelcat:{modelcat}, modelname:{modelname}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    # 判断调用哪一个任务（来自 allused_voicemodels 字典）
    modellang = model_entry.get("modellang") or modellang
    tts_modeltype = model_entry.get("model_type") or "gsv"  # 默认 sovits；gsv 与 fish-speed 共用 task
    tts_modelversion = model_entry.get("model_typeversion") or ""
    logger.debug(
        f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} handle, '
        f'modellang:{modellang} model_type:{tts_modeltype} model_typeversion:{tts_modelversion}'
    )
    emo_type, emo_alpha, speed, volume_db = 'default', 1.0, 1.0, 0.0
    if tts_modeltype == "index-tts":
        ok, emo_type, emo_alpha, emo_err = parse_emo_params(json_post_raw, logger)
        if not ok:
            logger.warning(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} parse_emo_params failed: {emo_err}')
            return gen_res_dict(1, emo_err, auth_info)
        ok2, speed, volume_db, sv_err = parse_speed_volume_params(json_post_raw, logger)
        if not ok2:
            logger.warning(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} parse_speed_volume_params failed: {sv_err}')
            return gen_res_dict(1, sv_err, auth_info)

    task_cmd = '/api/genaudio'
    if tts_modeltype == "index-tts":
        payload = build_payload(
            reqid, uid, email, domain,
            modelname=modelname, text=text, modelcat=modelcat,
            output_format=output_format, modellang=modellang,
            emo_type=emo_type, emo_alpha=emo_alpha, speed=speed, volume_db=volume_db,
        )
        task_sig = gpu_gen_indextts
    else:
        modellang = map_lang_abbrev(modellang, modellang)
        payload = build_payload(
            reqid, uid, email, domain,
            modelname=modelname, text=text, modelcat=modelcat,
            output_format=output_format, modellang=modellang,
        )
        task_sig = gpu_gen_tts
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.TTS,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(task_sig, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} tts.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    endtm = time.time()
    cost = int(endtm - tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }
    
    logger.info(
        f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'modelcat:{modelcat}, modellang:{modellang} email:{email}, '
        f'cost:{cost}, modelname:{modelname}, refer:{referer}, today req cnt:{cnt}, '
        f'ret:{ret}'
    )
    return res

# for web api, 异步提交生成；结果由专门 check 接口查询
@app.post("/api/gensound")
async def gensoundTask(request: Request):
    ret = 0
    msg = 'ok'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()

    # 验证签名
    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)
    cnt = 0

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    text = json_post_raw.get("text", '')
    length = json_post_raw.get("length", 15)
    num = json_post_raw.get("num", 1)
    try:
        length = int(length)
    except (TypeError, ValueError):
        length = 15
    try:
        num = int(num)
    except (TypeError, ValueError):
        num = 1

    # 验证文本长度
    ret, msg = validate_text_length(text, MAX_SOUND_TEXT_LENGTH, 0, reqid, logger)
    if ret != 0:
        logger.info(f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid},' \
            f'email:{email}, refer:{referer}, length:{length}, num:{num} {msg}!')
        return gen_res_dict(ret, msg)

    ret, msg = validate_signature(logger, timestamp, signature, text, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.SOUND)
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.SOUND, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} email:{email} uid:{uid} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    task_cmd = '/api/gensound'
    payload = build_payload(reqid, uid, email, domain, text=text, length=length, num=num)
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.SOUND,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(gpu_gen_sound, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} gpu_gen_sound.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    endtm = time.time()
    cost = int(endtm - tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'email:{email}, length:{length}, num:{num}, '
        f'cost:{cost}, refer:{referer}, today req cnt:{cnt}, '
        f'ret:{ret}'
    )
    return res

@app.post("/api/extractaudio")
async def extractaudio_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/api/extractaudio'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()

    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today = time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    upurl = json_post_raw.get("upurl", '')

    if len(upurl.strip()) == 0:
        ret = 1
        msg = f'upurl is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} ip:{real_ip} uid:{uid} email:{email} msg:{msg} upurl:{upurl} referer:{referer}')
        return gen_res_dict(ret, msg)

    # 验证音频URL
    ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg = validate_signature(logger, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.AUDIO_EXTRACTOR)
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.AUDIO_EXTRACTOR, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} email:{email} uid:{uid} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    payload = build_payload(reqid, uid, email, domain, upfileinfo=upfileinfo)
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.AUDIO_EXTRACTOR,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(audio_extract, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} audio_extract.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    endtm = time.time()
    cost = int(endtm - tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'email:{email}, cost:{cost}, refer:{referer}, upurl:{upurl}, '
        f'ret:{ret}'
    )
    return res

@app.post("/api/cover")
async def cover_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/api/cover'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()

    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today = time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    modelname = json_post_raw.get("modelname", '')
    modelcat = json_post_raw.get("modelcat", '')
    modelgender = json_post_raw.get("modelgender", '').lower()
    lang = json_post_raw.get("lang", 'english')
    converturl = json_post_raw.get("converturl", '')
    upgender = json_post_raw.get("upgender", '').lower()

    if not validate_gender(upgender) or not validate_gender(modelgender):
        ret = 1
        msg = f'upgender:{upgender} or modelgender:{modelgender} is invalid, reqid:{reqid}!'
        logger.warning(f'cover, reqid:{reqid} ip:{real_ip} uid:{uid} email:{email}, msg: {msg}')
        return gen_res_dict(ret, msg)

    if (len(modelname.strip()) == 0 or len(modelcat.strip()) == 0 or len(lang.strip()) == 0 or len(converturl.strip()) == 0):
        ret = 1
        msg = f'modelname:{modelname} or modelcat:{modelcat} or lang:{lang} or converturl:{converturl} is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} ip:{real_ip} email:{email}, msg: {msg}')
        return gen_res_dict(ret, msg)

    # 验证音频URL
    ret, msg, upfileinfo = validate_audio_url(converturl, reqid, first_ip, email, today, datetime, logger)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg = validate_signature(logger, timestamp, signature, converturl, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.COVER)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.COVER, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    payload = build_payload(
        reqid, uid, email, domain,
        modelcat=modelcat, modelname=modelname, modelgender=modelgender,
        upfileinfo=upfileinfo, upgender=upgender, lang=lang,
    )
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.COVER,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(gpu_cover, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} gpu_cover.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'cover, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'modelcat:{modelcat}, lang:{lang} email:{email}, '
        f'modelname:{modelname}, refer:{referer}, ret:{ret}'
    )
    return res

@app.post("/api/vocal")
async def vocal_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/api/vocal'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()

    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today = time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    upurl = json_post_raw.get("upurl", '')

    if len(upurl.strip()) == 0:
        ret = 1
        msg = f'upurl is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} ip:{real_ip} uid:{uid} email:{email} msg:{msg} upurl:{upurl} referer:{referer}')
        return gen_res_dict(ret, msg)

    # 验证音频URL
    ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg = validate_signature(logger, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.VOCAL_ISOLATOR)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.VOCAL_ISOLATOR, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    payload = build_payload(reqid, uid, email, domain, upfileinfo=upfileinfo)
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.VOCAL_ISOLATOR,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(gpu_vocal, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} gpu_vocal.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'vocal, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'email:{email}, refer:{referer}, ret:{ret}'
    )
    return res

@app.post("/api/instrument")
async def instrument_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/api/instrument'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()

    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today = time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    upurl = json_post_raw.get("upurl", '')

    if len(upurl.strip()) == 0:
        ret = 1
        msg = f'upurl is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} ip:{real_ip} uid:{uid} email:{email} msg:{msg} upurl:{upurl} referer:{referer}')
        return gen_res_dict(ret, msg)

    # 验证音频URL
    ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_audio_url failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg = validate_signature(logger, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.VOCAL_REMOVER)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.VOCAL_REMOVER, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    payload = build_payload(reqid, uid, email, domain, upfileinfo=upfileinfo)
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.VOCAL_REMOVER,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(gpu_instrument, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} gpu_instrument.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'instrument, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'email:{email}, refer:{referer}, ret:{ret}'
    )
    return res

@app.post("/api/video/intent")
async def video_intent(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/api/video/intent'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()

    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today = time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    content = json_post_raw.get("content", '')
    uri = json_post_raw.get("uri", "")

    ret, msg, video_params = validate_video_intent_params(
        logger, json_post_raw, content, uri, reqid, first_ip, real_ip, uid, email, referer,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_video_intent_params failed: {msg}')
        return gen_res_dict(ret, msg)
    source = video_params["source"]
    sign_text = video_params["sign_text"]

    ret, msg = validate_signature(logger, timestamp, signature, sign_text, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.VIDEO_INTENT)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} source:{source} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.VIDEO_INTENT, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    payload = build_payload(
        reqid, uid, email, domain,
        content=content, uri=uri, source=source,
    )
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.VIDEO_INTENT,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(video_intent, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} recognize_intent.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'instrument, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'email:{email}, refer:{referer}, source:{source}, uri:{uri}, content length:{len(content)}, ret:{ret}'
    )
    return res

@app.post("/api/video/create")
async def create_video(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/api/video/create'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    # 获取HTTP请求的Referer
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()

    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    localtime = time.localtime(tm)
    today = time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    content = json_post_raw.get("content", '')
    if len(content) > 10000:
        logger.warning(
            f'reqid:{reqid} ip:{real_ip} uid:{uid} email:{email} '
            f'content truncated from {len(content)} to 10000 referer:{referer}'
        )
        content = content[:10000]

    message = json_post_raw.get("message", '')
    ret, msg = validate_signature(logger, timestamp, signature, message, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.VIDEO_CREATE)
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} message:{message} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    intent_taskid = json_post_raw.get("intent_taskid", "")
    ret, msg, video_params = validate_video_create_params(
        logger, json_post_raw, content, reqid, first_ip, real_ip, uid, email, referer,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} validate_video_create_params failed: {msg}')
        return gen_res_dict(ret, msg)


    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.VIDEO_CREATE, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    payload = build_payload(
        reqid, uid, email, domain,
        content=content,
        **video_params,
    )
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.VIDEO_CREATE,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    try:
        dispatch(video_create, payload)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} video_create.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'instrument, reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'email:{email}, refer:{referer}, content length:{len(content)}, ret:{ret}'
    )
    return res

ALLOWED_TRANSCRIBE_FORMATS = ['json', 'txt', 'srt', 'vtt', 'ass']
VIDEO_TTS_TRANSCRIBE = "video_tts_transcribe"

@app.post("/api/transcribe")
async def transcribe_endpoint(request: Request):
    ret = 0
    msg = 'ok'
    task_cmd = '/api/transcribe'

    real_ip = get_real_ip(request)
    first_ip = real_ip.split(',')[0].strip() if real_ip else DEFAULT_USER_FIRST_IP
    referer = request.headers.get("Referer", "Not provided")

    json_post_raw = await request.json()
    timestamp = json_post_raw.get('tstamp', 0)
    signature = json_post_raw.get('snature', '')

    tm = time.time()
    uid = json_post_raw.get("uid", 0)
    reqid = gen_reqid(uid, tm)
    today = time.strftime(DAY_FORMAT, time.localtime(tm))
    datetime = time.strftime("%Y%m%d_%H%M%S", time.localtime(tm))

    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')

    upurl = json_post_raw.get("upurl", '')
    language = json_post_raw.get("language", 'en')
    output_format = (json_post_raw.get("format", 'json') or 'json').strip().lower()

    if output_format not in ALLOWED_TRANSCRIBE_FORMATS:
        logger.warning(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} Invalid format: {output_format}')
        return gen_res_dict(1, f'Invalid format: {output_format}')

    if email == VIDEO_TTS_TRANSCRIBE:
        ret, msg, upfileinfo = validate_internal_tts_url(upurl, reqid, first_ip, email, today, datetime, logger)
        validate_fn_name = 'validate_internal_tts_url'
    else:
        ret, msg, upfileinfo = validate_audio_url(upurl, reqid, first_ip, email, today, datetime, logger)
        validate_fn_name = 'validate_audio_url'
    if ret != 0:
        logger.warning(f'reqid:{reqid} real_ip:{real_ip} uid:{uid} email:{email} {validate_fn_name} failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg = validate_signature(logger, timestamp, signature, upurl, datetime, reqid, first_ip, real_ip, uid, email, ModuleType.TRANSCRIBE)
    if ret != 0:
        logger.warning(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg, login, subscript = validate_request(logger, real_ip, referer, reqid, email, today, uid, ModuleType.TRANSCRIBE, domain, request)
    auth_info = {"islogin": login, "subscript": subscript}
    if ret != 0:
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg, auth_info)

    payload = build_payload(
        reqid, uid, email, domain,
        upfileinfo=upfileinfo, language=language, output_format=output_format,
    )
    task_req = json.dumps(payload, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, uid, domain, real_ip, ModuleType.TRANSCRIBE,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg, auth_info)

    engine = resolve_transcribe_engine(language)
    try:
        if engine == 'whisper':
            dispatch(transcribe_whisper, payload)
        else:
            return gen_res_dict(1, f'unsupported transcribe engine: {engine}', auth_info)
    except Exception as e:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} uid:{uid} email:{email} transcribe_whisper.delay failed: {e}')
        return gen_res_dict(1, f'dispatch failed: {e}', auth_info)

    endtm = time.time()
    cost = int(endtm - tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "task_id": reqid,
        "islogin": login,
        "subscript": subscript,
    }

    logger.info(
        f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, uid:{uid}, '
        f'email:{email}, language:{language}, format:{output_format}, '
        f'cost:{cost}, refer:{referer}, ret:{ret}'
    )
    return res

#if __name__ == "__main__":
#    import uvicorn

#    parser = argparse.ArgumentParser(description='Simple TCP server.')
#    parser.add_argument('--port', type=int, required=True, help='Port number to listen on')
#    args = parser.parse_args()
    
#    log_config = uvicorn.config.LOGGING_CONFIG
#    log_config["formatters"]["access"]["fmt"] = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"
#    log_config["formatters"]["default"]["fmt"] = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"

#    uvicorn.run(app, host="0.0.0.0", port=args.port, reload=False, log_config=log_config)