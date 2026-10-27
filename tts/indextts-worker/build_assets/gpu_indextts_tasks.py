#!/usr/bin/env python3

import sys
import os
import socket

import time
import random
import torch

import gc
from io import BytesIO

from pydub import AudioSegment

from utility.util import  gen_res_dict
import scipy.io.wavfile as wavfile

from sql.mysql_db import add_ip_use_record, get_ip_use_count, add_api_user_record, \
        get_api_user_by_email, update_api_user_by_email, get_email_use_count, \
            update_ip_use_record, update_api_user_record

from utility.voice_config import (
    get_device, ishalf, get_dtype, get_fasttext_modelpath,
    get_file_sync_transport, get_quic_client_host, get_quic_client_port,
)
from utility.module_enum import TaskStatus
from utility.socat_rsyc_util import (
    RsyncTransport,
    get_socat_proxy_configs,
    initialize_ssh_connections_for_proxies,
)
from utility.file_delivery import (
    SyncJob,
    enqueue_delivery_job,
    resolve_delivery_transport,
    start_file_delivery,
)
from utility.quic_http_transport import QuicHttpTransport
from utility.samearea_http_transport import SameAreaHttpTransport
from utility.fasttext_lang import load_fasttext_model, resolve_text_lang

from celery_worker import celery_app

from indextts_worker import (
    load_indextts_model,
    indextts_infer,
    emo_type_to_vector,
    normalize_emo_type,
    parse_emo_alpha,
)
from utility.log import logset, setup_logger
from utility.voice_config import get_remote_ttssave_dir, get_localnet_video_tts_dir
from utility.gpu_util import cuda_error_handler, check_and_wait_for_gpu_memory, manage_gpu_memory, \
    check_gpu_health, log_gpu_error_details
# CUDA调试建议：
# 如果遇到CUDA错误，可以设置以下环境变量来帮助调试：
# export CUDA_LAUNCH_BLOCKING=1  # 同步CUDA操作，便于定位错误
# export TORCH_USE_CUDA_DSA=1    # 启用设备端断言
# export CUDA_VISIBLE_DEVICES=0  # 指定使用的GPU设备
# 设置CUDA环境变量以提高稳定性
os.environ.setdefault('CUDA_LAUNCH_BLOCKING', '0')  # 默认异步，提高性能
os.environ.setdefault('TORCH_USE_CUDA_DSA', '0')    # 默认关闭设备端断言
# 设置PyTorch内存分配器以减少内存碎片化
os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')

logger = setup_logger(
    logger_name="indextts_worker",
    log_file="/app/log/indextts_worker_test.log",
    console_level="DEBUG",
    file_level="DEBUG",
)

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'
wav_output_dir = "output"

# 定义一个默认的比特率（例如：256k）
DEFAULT_BITRATE = "128k"
DEFAULT_FORMAT = "mp3"
MAX_TEXT_LENGTH = 700
MAX_FREE_TRIES = 7
DEFAULT_DURATION_FACTOR = 1.05
DURATION_FACTOR_MIN = 0.5
DURATION_FACTOR_MAX = 2.0
SPEED_MIN = 0.5
SPEED_MAX = 2.0
VOLUME_DB_MIN = -6.0
VOLUME_DB_MAX = 6.0

# 当 email 为 VIDEO_SCRIPT_TTS 时， 标示是局域网内为视频生成 tts 任务
VIDEO_SCRIPT_TTS = "video_script_tts"

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'

indextts_model = None
WORKER_HOSTNAME = socket.gethostname()

# 设备相关变量定义 - 在导入之后
is_half = ishalf()
device = get_device()
dtype = get_dtype()


def parse_speed(speed):
    """Normalize speed to [0.5, 2.0]: invalid -> 1.0, below min -> 0.5, above max -> 2.0."""
    if speed is None or speed == "":
        return 1.0
    try:
        value = float(speed)
    except (TypeError, ValueError):
        logger.warning(f"invalid speed {speed!r}, fallback to 1.0")
        return 1.0
    if value < SPEED_MIN:
        logger.warning(f"speed {value} below {SPEED_MIN}, clamp to {SPEED_MIN}")
        return SPEED_MIN
    if value > SPEED_MAX:
        logger.warning(f"speed {value} above {SPEED_MAX}, clamp to {SPEED_MAX}")
        return SPEED_MAX
    return value


def parse_volume_db(volume_db):
    """Normalize volume_db to [-6.0, 6.0]: invalid -> 0.0, below min -> -6.0, above max -> 6.0."""
    if volume_db is None or volume_db == "":
        return 0.0
    try:
        value = float(volume_db)
    except (TypeError, ValueError):
        logger.warning(f"invalid volume_db {volume_db!r}, fallback to 0.0")
        return 0.0
    if value < VOLUME_DB_MIN:
        logger.warning(f"volume_db {value} below {VOLUME_DB_MIN}, clamp to {VOLUME_DB_MIN}")
        return VOLUME_DB_MIN
    if value > VOLUME_DB_MAX:
        logger.warning(f"volume_db {value} above {VOLUME_DB_MAX}, clamp to {VOLUME_DB_MAX}")
        return VOLUME_DB_MAX
    return value


def speed_to_duration_factor(speed):
    """Map speaking speed to IndexTTS duration_factor; speed=1 -> DEFAULT_DURATION_FACTOR."""
    return max(DURATION_FACTOR_MIN, min(DURATION_FACTOR_MAX, DEFAULT_DURATION_FACTOR / speed))


def save_audio_file(sample_rate, audio, output_format, base_output_path, filename_prefix, volume_db=0.0):
    """
    Write inferred audio to local disk only. Cross-region sync is enqueued separately.

    Returns:
        tuple: (output_path, output_file)
    """
    os.makedirs(base_output_path, mode=0o777, exist_ok=True)
    os.chmod(base_output_path, 0o777)

    file_extension = "wav" if output_format == "wav" else "mp3"
    output_file = f'{filename_prefix}.{file_extension}'
    output_path = os.path.join(base_output_path, output_file)

    wav_data = BytesIO()
    wavfile.write(wav_data, sample_rate, audio)
    wav_data.seek(0)

    logger.info(f'save_audio_file before write, output_path:{output_path}, volume_db:{volume_db}')
    if volume_db != 0.0:
        audio_segment = AudioSegment.from_wav(wav_data)
        audio_segment = audio_segment.apply_gain(volume_db)
        if output_format == "wav":
            audio_segment.export(output_path, format="wav")
        elif "user" in filename_prefix:
            audio_segment.export(output_path, format="mp3", codec="libmp3lame", bitrate=DEFAULT_BITRATE)
        else:
            audio_segment.export(output_path, format="mp3", bitrate=DEFAULT_BITRATE)
    elif output_format == "wav":
        with open(output_path, "wb") as f:
            f.write(wav_data.getvalue())
    else:  # mp3
        audio_segment = AudioSegment.from_wav(wav_data)
        if "user" in filename_prefix:
            audio_segment.export(output_path, format="mp3", codec="libmp3lame", bitrate=DEFAULT_BITRATE)
        else:
            audio_segment.export(output_path, format="mp3", bitrate=DEFAULT_BITRATE)

    logger.info(f'save_audio_file after write, output_path:{output_path}, output_file:{output_file}')
    return output_path, output_file

@celery_app.task(name='task_gen_indextts')
@cuda_error_handler(logger)
def gpu_gen_tts(modelname, text, modelcat='', userid=0, t=1, reqid='0', output_format=DEFAULT_FORMAT, modellang='en', email='',
                emo_type='default', emo_alpha=1.0, speed=1.0, volume_db=0.0):
    isEapi = str(reqid).endswith('.eapi')
    task_id = reqid
    if isEapi:
        task_id = str(reqid).removesuffix('.eapi')

    if (len(modelname.strip()) == 0):
        ret = 1
        msg = f'modelname:{modelname} is invalid, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
        logger.warning(f'msg: {msg}')
        update_ip_use_record(task_id, msg, TaskStatus.FAILED)
        return gen_res_dict(ret, msg)

    if (len(text.strip()) == 0):
        ret = 1
        msg = f'text:{text} is empty, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
        logger.warning(f'msg: {msg}')
        update_ip_use_record(task_id, msg, TaskStatus.FAILED)
        return gen_res_dict(ret, msg)

    try:
        emo_type = normalize_emo_type(emo_type)
        emo_alpha = parse_emo_alpha(emo_alpha, logger=logger)
        emo_vector = emo_type_to_vector(emo_type)
        speed = parse_speed(speed)
        volume_db = parse_volume_db(volume_db)
        duration_factor = speed_to_duration_factor(speed)
    except ValueError as e:
        ret = 1
        msg = f'{e}, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
        logger.warning(f'msg: {msg}')
        if isEapi:
            update_api_user_record(task_id, msg, TaskStatus.FAILED)
        else:
            update_ip_use_record(task_id, msg, TaskStatus.FAILED)
        return gen_res_dict(ret, msg)

    localtime = time.localtime(time.time())
    today = time.strftime(DAY_FORMAT, localtime)
    datetime_str = time.strftime("%Y%m%d%H%M%S", localtime)
    random_number = random.randint(1, 1000)
    start_time = time.time()
    textlen = len(text)

    # 任务开始时安全地管理GPU内存
    try:
        if torch.cuda.is_available():
            # 检查并等待GPU内存
            if not check_and_wait_for_gpu_memory(logger, device, min_free_gb=2.0, max_wait_seconds=30):
                logger.warning(f'GPU memory low at task start, attempting cleanup. reqid:{reqid}')
                manage_gpu_memory(logger, device)
                # 再次检查
                if not check_and_wait_for_gpu_memory(logger, device, min_free_gb=1.5, max_wait_seconds=10):
                    logger.error(f'GPU memory insufficient for task, reqid:{reqid}')
                    ret = 1
                    msg = f'GPU memory insufficient, please try again later, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
                    update_ip_use_record(task_id, msg, TaskStatus.FAILED)
                    return gen_res_dict(ret, msg)
    except Exception as e:
        logger.warning(f'GPU memory check failed at task start: {e}')

    try:
        model_file_prefix = modelname
        if t == 1 :
            # 公用模型
            model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
        elif t == 2 :
            # 用户私有模型
            model_path = f'models/service_model/user_models/user{userid}/model_user{userid}_{modelname}'
            model_file_prefix = f'user{userid}_{modelname}'
        else :
            ret = 1
            msg = f't:{t} is invalid, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
            logger.warning(f'msg: {msg}')
            update_ip_use_record(task_id, msg, TaskStatus.FAILED)
            return gen_res_dict(ret, msg)

        if not os.path.exists(model_path) :
            ret = 1
            msg = f'modelname:{modelname} is invalid, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
            logger.warning(f'model_path:{model_path}, modelname:{modelname}, worker:{WORKER_HOSTNAME}')
            update_ip_use_record(task_id, msg, TaskStatus.FAILED)
            return gen_res_dict(ret, msg)

        # 在加载模型前检查 GPU 健康状态
        if not check_gpu_health(logger, device, attempt_recovery=True):
            logger.error(f'GPU health check failed, skipping task for reqid:{reqid}')
                
            # 记录详细错误信息
            log_gpu_error_details(logger, device, "GPU health check failed")
                
            ret = 1
            msg = f'GPU is in an unhealthy state, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
            logger.error(f'msg: {msg}')
            update_ip_use_record(task_id, msg, TaskStatus.FAILED)
            return gen_res_dict(ret, msg)
        
        filename_prefix = f'idx_{modelname}_{datetime_str}_{random_number}'
        speacker_pt_path = f"{model_path}/{model_file_prefix}_audio_prompt.pt"
        if not os.path.exists(speacker_pt_path):
            ret = 1
            msg = f'speacker_pt_path {speacker_pt_path} not found, reqid:{reqid}, worker:{WORKER_HOSTNAME}!'
            logger.warning(f'msg: {msg}')
            update_ip_use_record(task_id, msg, TaskStatus.FAILED)
            return gen_res_dict(ret, msg)

        text_lang = resolve_text_lang(text, fallback_lang=modellang, logger=logger)
        logger.debug(
            f'reqid:{reqid}, resolve_text_lang text_lang:{text_lang}, modellang:{modellang}, '
            f'emo_type:{emo_type}, emo_alpha:{emo_alpha}, emo_vector:{emo_vector}, '
            f'speed:{speed}, duration_factor:{duration_factor}, volume_db:{volume_db}'
        )
        sample_rate, audio = indextts_infer(
            logger, indextts_model, speacker_pt_path, text, text_lang=text_lang, reqid=task_id,
            duration_factor=duration_factor,
            emo_vector=emo_vector, emo_alpha=emo_alpha,
        )

        wav_output_path = wav_output_dir + '/' + today + '/user' + str(userid) 
        # short_uri_prefix: sync success -> /outdt{prefix}/file; complete only after async sync
        short_uri_prefix = f'/wavcl/{today}/user{userid}'

        output_path, output_file = save_audio_file(
            sample_rate, audio, output_format, wav_output_path, filename_prefix,
            volume_db=volume_db,
        )

        if email == VIDEO_SCRIPT_TTS:
            remote_ttssave_dir = get_localnet_video_tts_dir()
            proxies = []
            delivery_transport = "http"
        else:
            remote_ttssave_dir = get_remote_ttssave_dir()
            delivery_transport = resolve_delivery_transport(email)
            proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
        remote_target_dir = f'{remote_ttssave_dir}/{today}/user{userid}'
        sync_job = SyncJob(
            source_file=output_path,
            target_dir=remote_target_dir,
            task_id=task_id,
            is_eapi=isEapi,
            short_uri_prefix=short_uri_prefix,
            output_file=output_file,
            proxies=list(proxies),
            transport=delivery_transport,
        )
        if not enqueue_delivery_job(logger, sync_job):
            msg = f'enqueue sync failed, queue full or workers down, reqid:{reqid}, worker:{WORKER_HOSTNAME}'
            logger.error(msg)
            if isEapi:
                update_api_user_record(task_id, msg, TaskStatus.FAILED)
            else:
                update_ip_use_record(task_id, msg, TaskStatus.FAILED)

    except Exception as e:
        logger.error(f'Error in gpu_gen_tts for reqid:{reqid}, worker:{WORKER_HOSTNAME}: {e}')
        msg = f'Error in gpu_gen_tts for reqid:{reqid}, worker:{WORKER_HOSTNAME}: {e}'
        if isEapi:
            update_api_user_record(task_id, msg, TaskStatus.FAILED)
        else:
            update_ip_use_record(task_id, msg, TaskStatus.FAILED)
        try:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                gc.collect()
        except Exception as cleanup_error:
            logger.warning(f'GPU cleanup failed in exception handler: {cleanup_error}')
        return gen_res_dict(1, msg)

    # 任务结束时安全地清理GPU内存（不等待跨境 sync）
    try:
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()
    except Exception as e:
        logger.warning(f'GPU cleanup failed at task end: {e}')

    tts_time = time.time()
    cost_tts = tts_time - start_time
    logger.info(
        f'reqid:{reqid}, inference finish, sync enqueued, transport:{delivery_transport} time[tts:{cost_tts}] '
        f'sample_rate:{sample_rate}, audio_file:{output_path}, '
        f'speed:{speed}, duration_factor:{duration_factor}, volume_db:{volume_db}'
    )

    result = f"Processed: {task_id}"
    return result

def warmup_indextts():
    """预热index-tts模型，避免第一次调用时的延迟；覆盖默认路径与情绪向量路径。"""
    global indextts_model, logger
    try:
        logger.info("warmup index-tts start")
        modelcat = "celebrity"
        modelname = "us-male-bad-bunny"
        model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
        speacker_pt_path = f"{model_path}/{modelname}_audio_prompt.pt"
        if not os.path.exists(speacker_pt_path):
            logger.warning(f'speacker_pt_path {speacker_pt_path} not found, modelcat:{modelcat}, modelname:{modelname}')
            return
        test_text = "Hello, world!"
        logger.debug(f">> warmup index-tts default path, speacker_pt_path: {speacker_pt_path}, test_text: {test_text}")
        sample_rate, audio = indextts_infer(logger, indextts_model, speacker_pt_path, test_text)
        logger.debug(f">> warmup index-tts default path finish, sample_rate: {sample_rate}")

        emo_vector = emo_type_to_vector("sad")
        logger.debug(
            f">> warmup index-tts emo path, emo_type=sad, emo_vector={emo_vector}, "
            f"speacker_pt_path: {speacker_pt_path}"
        )
        sample_rate, audio = indextts_infer(
            logger, indextts_model, speacker_pt_path, test_text,
            emo_vector=emo_vector, emo_alpha=1.0,
        )
        logger.debug(f">> warmup index-tts emo path finish, sample_rate: {sample_rate}")
        logger.info("warmup index-tts finish (default + emo_type=sad OK)")
    except Exception as e:
        logger.warning(f"warmup index-tts failed: {e}")

def initialize_models():
    global indextts_model
    # 加载语言检测模型（失败不影响 TTS 主流程，将回退 modellang）
    try:
        load_fasttext_model(get_fasttext_modelpath(), logger=logger)
    except Exception as e:
        logger.warning(f'load_fasttext_model failed: {e}')

    # 加载index-tts模型（启动时预加载 emo_matrix / spk_matrix）
    indextts_model = load_indextts_model(logger, load_emotion_model=True)

    # 预热index-tts
    print("initialize_models, warmup index-tts start")
    warmup_indextts()
    print("initialize_models, warmup index-tts finish")

# 应用启动时初始化跨区 socat SSH（quic 保底也用）+ 局域网 HTTP + 异步投递队列
def initialize_file_delivery():
    """Warm socat for cross-region rsync/quic fallback; LAN VIDEO uses HTTP."""
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

    transports = {
        "rsync": RsyncTransport(),
        "http": SameAreaHttpTransport(),
    }
    if cross_transport == "quic":
        transports["quic"] = QuicHttpTransport(biz_type="tts")

    try:
        start_file_delivery(logger, transports)
    except Exception as start_err:
        logger.error(f"start_file_delivery failed: {start_err}")

# 应用启动时初始化 SSH ControlMaster 连接与投递队列
initialize_file_delivery()
# 在文件末尾调用
initialize_models()

