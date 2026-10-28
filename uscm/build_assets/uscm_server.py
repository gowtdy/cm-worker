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

from utility.util import gen_reqid, gen_res_dict, map_lang_abbrev
from utility.request_util import get_real_ip, validate_request, validate_signature, validate_text_length, \
    validate_gender, validate_audio_url
from utility.module_enum import ModuleType
from utility.userserver_client import post_generate_add
from utility.allused_voicemodels import register_allused_voicemodels, get_common_model
from utility.voice_settings import parse_emo_params, parse_speed_volume_params
from config.comm_config import DEFAULT_USER_FIRST_IP

from tasks_interface import gpu_gen_tts, gpu_gen_indextts

app = FastAPI()

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'
DEFAULT_FORMAT = "mp3"
sounds_output_dir = 'output'

cover_model_dir = '/app/models/cover_service_models'

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
    userid = json_post_raw.get("userid", 0)
    reqid = gen_reqid(userid, tm)
    localtime = time.localtime(tm)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime("%Y%m%d_%H%M%S", localtime)
    cnt = 0
    
    email = json_post_raw.get("email", '')
    domain = json_post_raw.get("domain", '')
    # 用户是否订阅,有4个取值, 0 未订阅, 1 已订阅, 2 虽订阅,字符已用完,
    subscript = json_post_raw.get("subscript", 0)

    output_format = json_post_raw.get("output_format", DEFAULT_FORMAT)

    text = json_post_raw.get("text", '')
    # 验证文本长度
    text_validation = validate_text_length(text, MAX_TTS_TEXT_LENGTH, reqid, logger)
    if text_validation['ret'] != 0:
        logger.info(f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid},' \
            f'email:{email}, subscript:{subscript}, refer:{referer}, textlen:{len(text)} exceeds limit!')
        return text_validation
    
    textlen = len(text)  # 重新定义 textlen 用于后续逻辑
    
    ret, msg = validate_signature(logger, timestamp, signature, text, datetime, reqid, first_ip, real_ip, userid, email, ModuleType.TTS)
    if ret != 0 :
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} userid:{userid} email:{email} validate_signature failed: {msg}')
        return gen_res_dict(ret, msg)

    ret, msg = validate_request(logger, real_ip, referer, reqid, email, subscript, today, userid, ModuleType.TTS, domain, request)
    if ret != 0 :
        logger.info(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} email:{email} userid:{userid} subscript:{subscript} ret:{ret}, msg:{msg}')
        return gen_res_dict(ret, msg)

    modelcat = json_post_raw.get("modelcat", '')
    modelname = json_post_raw.get("modelname", '')
    modellang = json_post_raw.get("modellang", 'english')

    model_entry = get_common_model(modelcat, modelname)
    if not model_entry:
        ret = 1
        msg = f'modelname:{modelname} is invalid, reqid:{reqid}!'
        logger.warning(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} userid:{userid} email:{email} modelcat:{modelcat}, modelname:{modelname}, msg:{msg}')
        return gen_res_dict(ret, msg)

    #add_ip_use_record(first_ip, today, text, reqid, '/api/genaudio', modelcat, modelname, textlen, email, real_ip)

    # 判断调用哪一个任务（来自 allused_voicemodels 字典）
    modellang = model_entry.get("modellang") or modellang
    tts_modeltype = model_entry.get("model_type") or "gsv"  # 默认 sovits；gsv 与 fish-speed 共用 task
    tts_modelversion = model_entry.get("model_typeversion") or ""
    logger.debug(
        f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} handle, '
        f'modellang:{modellang} model_type:{tts_modeltype} model_typeversion:{tts_modelversion}'
    )
    generate_content = f'{modelcat}|{modelname}|{text}'
    emo_type, emo_alpha, speed, volume_db = 'default', 1.0, 1.0, 0.0
    if tts_modeltype == "index-tts":
        ok, emo_type, emo_alpha, emo_err = parse_emo_params(json_post_raw, logger)
        if not ok:
            logger.warning(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} parse_emo_params failed: {emo_err}')
            return gen_res_dict(1, emo_err)
        ok2, speed, volume_db, sv_err = parse_speed_volume_params(json_post_raw, logger)
        if not ok2:
            logger.warning(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} parse_speed_volume_params failed: {sv_err}')
            return gen_res_dict(1, sv_err)

    task_cmd = '/api/genaudio'
    task_req = json.dumps({
        "first_ip": first_ip,
        "userid": userid,
        "email": email,
        "emo_type": emo_type,
        "emo_alpha": emo_alpha,
        "speed": speed,
        "volume_db": volume_db,
        "output_format": output_format,
    }, ensure_ascii=False)
    ret, msg = post_generate_add(
        logger, reqid, first_ip, email, userid, domain, generate_content, real_ip, ModuleType.TTS,
        task_cmd=task_cmd, task_req=task_req,
    )
    if ret != 0:
        logger.error(f'reqid:{reqid} first_ip:{first_ip} real_ip:{real_ip} userid:{userid} email:{email} post_generate_add failed: {msg}')
        return gen_res_dict(ret, msg)

    if tts_modeltype == "index-tts":
        gpu_gen_indextts.delay(
            modelname, text, modelcat, userid, reqid, output_format, modellang, email,
            domain=domain, emo_type=emo_type, emo_alpha=emo_alpha, speed=speed, volume_db=volume_db,
            )
    else:
        modellang = map_lang_abbrev(modellang, modellang)
        gpu_gen_tts.delay(modelname, text, modelcat, userid, reqid, output_format, modellang, email)

    endtm = time.time()
    cost = int(endtm - tm)

    res = {
        "ret": 0,
        "msg": 'ok',
        "reqid": reqid
    }
    
    logger.info(
        f'reqid:{reqid}, first_ip:{first_ip}, real_ip:{real_ip}, userid:{userid}, '
        f'modelcat:{modelcat}, modellang:{modellang} email:{email}, subscript:{subscript}, '
        f'cost:{cost}, modelname:{modelname}, refer:{referer}, today req cnt:{cnt}, '
        f'ret:{ret}'
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