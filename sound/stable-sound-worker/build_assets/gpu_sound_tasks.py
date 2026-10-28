import os
import time
import logging
import json
import gc
import torch
import torchaudio
import httpx

from einops import rearrange
import fasttext

from utility.log import setup_logger
from utility.socat_rsyc_util import (
    RsyncTransport,
    get_socat_proxy_configs,
    initialize_ssh_connections_for_proxies,
)
from utility.file_sync_config import (
    get_file_sync_transport,
    get_quic_client_host,
    get_quic_client_port,
)
from utility.file_delivery import (
    SyncJob,
    enqueue_delivery_job,
    start_file_delivery,
)
from utility.quic_http_transport import QuicHttpTransport
from sql.mysql_db import update_ip_sounds_record
from config.comm_config import get_logdir, get_modelconf, get_device, get_remote_soundsave_dir, get_fasttext_modelpath

from celery_worker import celery_app

from stable_audio_3 import StableAudioModel

# 在模块级别初始化 logger，以便在 initialize_ssh_controlmaster() 中使用
log_dir = get_logdir()
os.makedirs(log_dir, exist_ok=True)
logger = setup_logger(
    logger_name='sound-worker',
    log_file=log_dir + '/sound_worker_log.txt',
    console_level='DEBUG',
    file_level='DEBUG'
)

def load_model():
    global model, fasttext_model
    
    modelconf = get_modelconf()
    logger.debug(f'Loading model from {modelconf["modelname"]}')
    model = StableAudioModel.from_pretrained(
        modelconf['modelname'],
        device=get_device(),
        model_half=modelconf['modelhalf'],
        config_path=modelconf['modelconfig'],
        ckpt_path=modelconf['ckptpath'],
        t5gemma_path=modelconf.get('t5gemmapath'),
    )
    logger.debug(f'Done loading stable audio model')

    logger.debug(f'Loading fasttext model from {get_fasttext_modelpath()}')
    fasttext_model = fasttext.load_model(get_fasttext_modelpath())
    logger.debug(f'Done loading fasttext model')
    return model, fasttext_model

def detect_language(text: str, threshold=0.7, min_len=1):
    if not text or len(text) < min_len:
        return 'unknown'
    try:
        label, prob = fasttext_model.predict(text, k=1)
        if prob[0] > threshold:
            return label[0].replace('__label__', '')
        else:
            return 'unknown'
    except Exception as e:
        logger.warning(f'detect_language error: {e}')
        return 'unknown'

def translate_text(text: str, src_lang: str = '', tgt_lang: str = 'en', logid: str = None) -> str:
    """
    调用 translate 接口进行文本翻译
    
    Args:
        text: 要翻译的文本
        src_lang: 源语言，默认为空字符串
        tgt_lang: 目标语言，默认为 'en'
        logid: 日志ID，可选
    
    Returns:
        翻译后的文本，如果翻译失败则返回原始文本
    """
    try:
        translate_host = os.environ.get('TRANSLATE_HOST', '')
        translate_port = os.environ.get('TRANSLATE_PORT', '5400')
        
        if not translate_host:
            logger.warning('TRANSLATE_HOST not set, skipping translation')
            return text
        
        url = f'http://{translate_host}:{translate_port}/mtapi/translate'
        payload = {
            'text': text,
            'src_lang': src_lang,
            'tgt_lang': tgt_lang
        }
        if logid:
            payload['logid'] = logid
        
        logger.debug(f'Calling translate API: {url}, payload: {payload}')
        
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            # 检查返回码
            ret = result.get('ret', -1)
            if ret != 0:
                logger.warning(f'Translate API returned error: ret={ret}, msg={result.get("msg", "")}')
                return text
            
            translated_text = result.get('translated_text', text)
            logger.debug(f'Translation result: {translated_text}')
            return translated_text
            
    except httpx.HTTPError as e:
        logger.error(f'HTTP error when calling translate API: {e}')
        return text
    except Exception as e:
        logger.error(f'Error when calling translate API: {e}')
        return text

@celery_app.task(name='gpu_sound_tasks.gpu_gen_sound')
def gpu_gen_sound(text: str, length: int, num: int, today: str, datetime: str, 
                      random_number: int, first_ip: str, real_ip: str, email: str, 
                      userid: int, ret: int, cnt: int, task_id: str):

    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
    DAY_FORMAT = '%Y-%m-%d'
    sounds_output_dir = 'output'
 
    start_time = time.time()
    prompt = text
    negative_prompt = "poor quality, distorted, bad audio"
    seconds_start = 0
    seconds_total = 15
    if length < 60 and  length > 0 :
        seconds_total = length
    steps = 8
    cfg_scale = 1.0
    batch_size = 1
    if num < 4 and num > 0 :
        batch_size = num
    seed = -1

    # 增加翻译能力
    need_translate = os.environ.get('NEED_TRANSLATE', 'False')
    logger.debug(f'sound_worker, task_id:{task_id}, need_translate: {need_translate}')
    if need_translate == 'True':
        lang = detect_language(prompt)
        logger.debug(f'sound_worker detect_language, task_id:{task_id}, lang:{lang}, text:{prompt}')
        # 当已经检测出语言，并且不为英语时，进行翻译
        if lang != 'en' and lang != 'unknown':
            prompt = translate_text(prompt, src_lang=lang, tgt_lang='en', logid=task_id)

    logger.info(f'gpu_gen_sound, prompt:{prompt}, negative_prompt:{negative_prompt}, steps:{steps}, \
            cfg_scale:{cfg_scale}, batch_size:{batch_size}, duration:{seconds_total}, seed:{seed}')
    audio = model.generate(
        prompt=prompt,
        negative_prompt=negative_prompt,
        duration=seconds_total,
        steps=steps,
        cfg_scale=cfg_scale,
        seed=seed,
        batch_size=batch_size
    )

    sample_rate = model.model.sample_rate

    audio = rearrange(audio, "b d n -> d (b n)")

    # 添加音频截断逻辑
    peak = torch.max(torch.abs(audio))
    if peak > 0:
       audio = audio / peak
    audio = audio.cpu()
    file_extension = "mp3"
    sounds_output_path = f'{sounds_output_dir}/{today}'
    sounds_file = f'sound_{datetime}_{random_number}.{file_extension}'
    os.makedirs(sounds_output_path, exist_ok=True)
    output_path = os.path.join(sounds_output_path, sounds_file)

    logger.debug(f"Output path: {output_path}")
    torchaudio.save(
        output_path,
        audio,
        sample_rate,
        format=file_extension,
        channels_first=True,
        compression=128000, # 128kbps bitrate
    )

    source_file = output_path
    short_uri_prefix = f'/sounds/tk/{today}'
    remote_soundsave_dir = get_remote_soundsave_dir()
    remote_target_dir = f'{remote_soundsave_dir}/{today}'
    delivery_transport = get_file_sync_transport()
    proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
    sync_job = SyncJob(
        source_file=source_file,
        target_dir=remote_target_dir,
        task_id=task_id,
        short_uri_prefix=short_uri_prefix,
        output_file=sounds_file,
        proxies=list(proxies),
        transport=delivery_transport,
    )
    if not enqueue_delivery_job(logger, sync_job):
        msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}'
        logger.error(msg)
        update_ip_sounds_record(task_id, msg, task_status="failed")

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        gc.collect()

    end_time = time.time()
    cost = int(end_time - start_time)
    logger.info(f"/sapi/gensound, reqid:{task_id}, cost:{cost}, ip:{real_ip} email:{email} text:{text} userid:{userid} ret:{ret} cnt:{cnt} transport:{delivery_transport}")

    result = f"Processed: {task_id}"

    return result

def progress_callback(callback_info):
    global preview_images
    denoised = callback_info["denoised"]
    current_step = callback_info["i"]
    sigma = callback_info["sigma"]

    if (current_step - 1) % preview_every == 0:
        if model.pretransform is not None:
            denoised = model.pretransform.decode(denoised)
        denoised = rearrange(denoised, "b d n -> d (b n)")
        denoised = denoised.clamp(-1, 1).mul(32767).to(torch.int16).cpu()
        audio_spectrogram = audio_spectrogram_image(denoised, sample_rate=sample_rate)
        preview_images.append((audio_spectrogram, f"Step {current_step} sigma={sigma:.3f})"))

# 应用启动时初始化跨区 socat SSH（quic 保底也用）+ 异步投递队列
def initialize_file_delivery():
    """Always warm socat for cross-region (rsync or quic fallback); start delivery queue."""
    cross_transport = get_file_sync_transport()
    # Always warm socat SSH: primary rsync path, or quic-failure fallback.
    try:
        socat_proxies = get_socat_proxy_configs()
        logger.info(f"init_socat_start proxies={len(socat_proxies)}")
        results = initialize_ssh_connections_for_proxies(logger, socat_proxies)
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        logger.info(f"init_socat_summary success={success_count} total={total_count}")
        if success_count < total_count:
            failed_servers = [server_key for server_key, success in results.items() if not success]
            logger.warning(f"init_socat_partial_fail failed={failed_servers}")
    except Exception as e:
        logger.warning(f"init_socat_proxies_fail error={e}")

    if cross_transport == "quic":
        quic_host = get_quic_client_host()
        quic_port = get_quic_client_port()
        logger.info(
            f"init_quic_transport host={quic_host} port={quic_port} "
            f"(socat SSH warmed for quic fallback)"
        )
        if not quic_host or not quic_port:
            logger.error("init_quic_missing_endpoint QUIC_CLIENT_HOST/PORT unset")

    transports = {"rsync": RsyncTransport()}
    if cross_transport == "quic":
        transports["quic"] = QuicHttpTransport(biz_type="sound")

    try:
        start_file_delivery(logger, transports)
    except Exception as start_err:
        logger.error(f"start_file_delivery failed: {start_err}")

# 应用启动时初始化投递队列
initialize_file_delivery()

# 加载模型
load_model()
