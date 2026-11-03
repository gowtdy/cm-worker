#!/usr/bin/env python3

import os
import socket
import time
import random
import gc

import torch

from utility.util import gen_res_dict
from utility.voice_config import (
    get_device,
    ishalf,
    get_dtype,
    get_file_sync_transport,
    get_quic_client_host,
    get_quic_client_port,
)
from utility.module_enum import TaskStatus
from utility.userserver_client import update_task_status
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

from celery_worker import celery_app

from utility.log import setup_logger, set_reqid, clear_reqid
from utility.gpu_util import cuda_error_handler

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
    logger_name="htmlvideo_worker",
    log_file="/app/log/htmlvideo_worker.log",
    console_level="DEBUG",
    file_level="DEBUG",
)

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'


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


@celery_app.task(name='task_gen_htmlvideo')
@cuda_error_handler(logger)
def gen_htmlvideo(uid='', email='', task_id='', domain='', content=''):
    set_reqid(task_id)
    try:
        if (len(content.strip()) == 0):
            ret = 1
            msg = f'content:{content} is empty, task_id:{task_id}, worker:{WORKER_HOSTNAME}!'
            logger.warning(f'msg: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return gen_res_dict(ret, msg)

        localtime = time.localtime(time.time())
        today = time.strftime(DAY_FORMAT, localtime)
        datetime_str = time.strftime("%Y%m%d%H%M%S", localtime)
        random_number = random.randint(1, 1000)
        start_time = time.time()
        content_length = len(content)

        try:

            delivery_transport = resolve_delivery_transport(email)
            proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
            remote_target_dir = f'/home/aigc/data/aioutput/htmlvideos/{today}/user{userid}'
            sync_job = SyncJob(
                source_file=output_path,
                target_dir=remote_target_dir,
                task_id=task_id,
                short_uri_prefix=short_uri_prefix,
                output_file=output_file,
                proxies=list(proxies),
                transport=delivery_transport,
                domain=domain,
            )
            if not enqueue_delivery_job(logger, sync_job):
                msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}, worker:{WORKER_HOSTNAME}'
                logger.error(msg)
                update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)

        except Exception as e:
            logger.error(f'Error in gpu_gen_tts for task_id:{task_id}, worker:{WORKER_HOSTNAME}: {e}')
            msg = f'Error in gpu_gen_tts for task_id:{task_id}, worker:{WORKER_HOSTNAME}: {e}'
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            try:
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                    torch.cuda.empty_cache()
                    gc.collect()
            except Exception as cleanup_error:
                logger.warning(f'GPU cleanup failed in exception handler: {cleanup_error}')
            return gen_res_dict(1, msg)

        # 任务结束：仅回收本轮推理显存碎片，不负责释放常驻模型主机内存
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
            f'task_id:{task_id}, htmlvideo finish, sync enqueued, transport:{delivery_transport} time[tts:{cost_tts}] '
        )

        result = f"Processed: {task_id}"
        return result
    finally:
        clear_reqid()

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

