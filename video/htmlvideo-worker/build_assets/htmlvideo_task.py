#!/usr/bin/env python3

import json
import os
import socket
import time
import random
import gc

import torch

from cmwk_common.util import gen_res_dict
from utility.voice_config import (
    get_device,
    ishalf,
    get_dtype,
    get_file_sync_transport,
    get_quic_client_host,
    get_quic_client_port,
)
from cmwk_common.module_enum import TaskStatus
from cmwk_common.userserver_worker import update_task_status
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

from cmwk_common.log import setup_logger, set_reqid, clear_reqid
from utility.gpu_util import cuda_error_handler

from html_utility.video_constant import LANGUAGE_MAP
from html_utility.intent import INTENT_FILENAME, identify_intent
from html_utility.init_htmlvideo import init_htmlvideo
from html_utility.storyboard import build_storyboard
from html_utility.visual import build_frame_visual
from html_utility.frame_html import build_frame_packets, build_frame_html, build_index_html
from html_utility.audio import tts_generate, sync_duration, caption_generate

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


@celery_app.task(name='task_video_intent')
@cuda_error_handler(logger)
def video_intent(payload):
    uid = payload.get('uid', '')
    email = payload.get('email', '')
    task_id = payload.get('task_id', '') or '0'
    domain = payload.get('domain', '')
    content = payload.get('content', '') or ''
    source = payload.get('source', 'article') or 'article'
    uri = payload.get('uri', '') or ''
    set_reqid(task_id)
    try:
        logger.info(
            f'task_id:{task_id} video_intent start source:{source} uri:{uri}'
        )
        # 与前端 sourceFields / uscm validate 一致：article → content；upload → uri
        if source == 'article':
            if len(content.strip()) == 0:
                ret = 1
                msg = f'content:{content} is empty, task_id:{task_id}, worker:{WORKER_HOSTNAME}!'
                logger.warning(f'msg: {msg}')
                update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
                return gen_res_dict(ret, msg)
        elif source == 'upload':
            if len(uri.strip()) == 0:
                ret = 1
                msg = f'uri:{uri} is empty, task_id:{task_id}, worker:{WORKER_HOSTNAME}!'
                logger.warning(f'msg: {msg}')
                update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
                return gen_res_dict(ret, msg)
        else:
            ret = 1
            msg = f'source:{source} is invalid, task_id:{task_id}, worker:{WORKER_HOSTNAME}!'
            logger.warning(f'msg: {msg}')
            update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
            return gen_res_dict(ret, msg)

        localtime = time.localtime(time.time())
        today = time.strftime(DAY_FORMAT, localtime)
        start_time = time.time()

        rand3 = random.randint(0, 999)
        video_dir = f'/app/videos'
        relative_intent_file = f'{today}/user{uid}/{task_id}_{rand3:03d}_{INTENT_FILENAME}'
        intent_file = os.path.join(video_dir, relative_intent_file)
        if os.getenv('INTENT_MOCK', 'false') == 'true':
            intent = {
                'name': 'Buy, Hold, Sell',
                'message': "Analyzing a stock's fundamentals and market signals tells you whether it's worth buying and when it's time to sell.",
                'angle': "how-to",
                'audience': "Beginner to intermediate individual investors who want a practical framework for buy and sell decisions",
                'summary': "A practical how-to on evaluating stocks: how to judge whether a stock is worth buying, and how to recognize the right time to sell. Aimed at everyday investors, it turns a common question into a clear, actionable decision framework.",
                'customization': "Two-part structure: first, how to analyze whether a stock is worth buying; second, how to determine when to sell."
            }
        else:
            intent = identify_intent(intent_file, content, logger=logger)
        intent['filename'] = relative_intent_file
        intent['content'] = content
        intent_json = json.dumps(intent, ensure_ascii=False)
        cost = time.time() - start_time
        logger.info(
            f'task_id:{task_id} video_intent done name:{intent.get("name")} '
            f'file:{intent_file} cost:{cost:.1f}s'
        )
        update_task_status(
            logger,
            task_id,
            domain,
            TaskStatus.COMPLETE,
            payload_msg='ok',
            generate_content=intent_json,
        )
        return gen_res_dict(0, 'ok', {"intent": intent, "intent_file": intent_file})
    except Exception as e:
        logger.error(f'Error in recognize_intent for task_id:{task_id}, worker:{WORKER_HOSTNAME}: {e}')
        msg = f'Error in recognize_intent for task_id:{task_id}, worker:{WORKER_HOSTNAME}: {e}'
        update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        return gen_res_dict(1, msg)
    finally:
        clear_reqid()

@celery_app.task(name='task_video_create')
@cuda_error_handler(logger)
def video_create(payload):
    uid = payload.get('uid', '')
    email = payload.get('email', '')
    task_id = payload.get('task_id', '') or '0'
    domain = payload.get('domain', '')
    intent_taskid = payload.get('intent_taskid', '')
    content = payload.get('content', '')
    aspect = payload.get('aspect')
    style = payload.get('style')
    duration = payload.get('duration')
    #language = payload.get('language', 'english')
    modelcat = payload.get('modelcat', 'english')
    modelname = payload.get('modelname', '')
    message = payload.get('message', '') 
    angle = payload.get('angle', '')
    audience = payload.get('audience', '')
    name = payload.get('name', '')
    summary = payload.get('summary', '')
    customization = payload.get('customization', '')
    notes = payload.get('notes', '')

    language = LANGUAGE_MAP[modelcat]
    set_reqid(task_id)
    try:
        logger.info(
            f'task_id:{task_id} htmlvideo start aspect:{aspect} style:{style} '
            f'duration:{duration} language:{language} modelcat:{modelcat} modelname:{modelname}'
            f'message:{message} angle:{angle} audience:{audience} name:{name} intent_taskid:{intent_taskid}'
            f'summary:{summary} customization:{customization} notes:{notes}'
        )
        if len(content.strip()) == 0:
            ret = 1
            msg = f'content:{content} is empty, task_id:{task_id}, worker:{WORKER_HOSTNAME}!'
            logger.warning(f'msg: {msg}')
            update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
            return gen_res_dict(ret, msg)

        localtime = time.localtime(time.time())
        today = time.strftime(DAY_FORMAT, localtime)
        datetime_str = time.strftime("%Y%m%d%H%M%S", localtime)
        random_number = random.randint(1, 1000)
        start_time = time.time()
        content_length = len(content)

        video_dir = f'/app/videos'
        #project_dir = f'{video_dir}/{today}/user{uid}/{task_id}'
        project_dir = f'{video_dir}/2026-11-11/userclm17919893695531772/6af47d10f984844e'
        os.makedirs(project_dir, exist_ok=True)
        
        # hyperframes init 要求目标目录为空；BRIEF.md 必须在 init 之后写入
        # aspect 为 landscape/portrait 等，对应 hyperframes --resolution preset
        #step_start = time.time()
        #ret, msg = init_htmlvideo(
        #    logger,
        #    project_dir,
        #    resolution=aspect,
        #    content=content,
        #    message=message,
        #    aspect=aspect,
        #    duration=duration,
        #    language=language,
        #    audience=audience,
        #    angle=angle,
        #    summary=summary,
        #    customization=customization,
        #    notes=notes,
        #    style_preset=style,
        #    task_id=task_id,
        #)
        #if ret != 0:
        #    logger.error(f'task_id:{task_id} Failed to initialize htmlvideo: {msg}')
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #cost_init = time.time() - step_start
        #logger.info(
        #    f'task_id:{task_id} htmlvideo initialized, project_dir:{project_dir} '
        #    f'cost:{cost_init:.1f}s'
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_INITED, payload_msg=TaskStatus.VIDEO_INITED)

        #step_start = time.time()
        #try:
        #    storyboard_path = build_storyboard(project_dir, logger)
        #except Exception as e:
        #    msg = f'Failed to build storyboard: {e}'
        #    logger.error(f'task_id:{task_id} {msg}')
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #cost_storyboard = time.time() - step_start
        #logger.info(
        #    f'task_id:{task_id} storyboard built, storyboard_path:{storyboard_path}, '
        #    f'project_dir:{project_dir} cost:{cost_storyboard:.1f}s'
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_STORIED, payload_msg=TaskStatus.VIDEO_STORIED)

        #step_start = time.time()
        #ret, msg = tts_generate(logger, project_dir, modelcat, modelname, task_id)
        #if ret != 0:
        #    logger.error(f"tts_generate task_id:{task_id} {msg}")
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #
        #cost_audio = time.time() - step_start
        #logger.info(
        #    f"task_id:{task_id}  generated, project_dir:{project_dir} "
        #    f"cost:{cost_tts:.1f}s"
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_TTSED, payload_msg=TaskStatus.VIDEO_TTSED)

        #step_start = time.time()
        #try:
        #    frame_visual_path = build_frame_visual(project_dir, logger)
        #except Exception as e:
        #    msg = f'Failed to build frame visual: {e}'
        #    logger.error(f'task_id:{task_id} {msg}')
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #cost_visual = time.time() - step_start
        #logger.info(
        #    f'task_id:{task_id} frame visual built, frame_visual_path:{frame_visual_path}, '
        #    f'project_dir:{project_dir} cost:{cost_visual:.1f}s'
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_VISUALED, payload_msg=TaskStatus.VIDEO_VISUALED)

        #step_start = time.time()
        #ret, msg = sync_duration(logger, project_dir, task_id)
        #if ret != 0:
        #    logger.error(f"sync_duration task_id:{task_id} {msg}")
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #ret, msg = caption_generate(logger, project_dir, task_id)
        #if ret != 0:
        #    logger.error(f"caption_generate task_id:{task_id} {msg}")
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #cost_caption = time.time() - step_start
        #logger.info(
        #    f"task_id:{task_id} caption generated, project_dir:{project_dir} "
        #    f"cost:{cost_caption:.1f}s"
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_CAPTIONED, payload_msg=TaskStatus.VIDEO_CAPTIONED)

        #step_start = time.time()
        #ret, msg = build_frame_packets(logger, project_dir, task_id)
        #if ret != 0:
        #    logger.error(f"build_frame_packets task_id:{task_id} {msg}")
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #cost_packets = time.time() - step_start
        #logger.info(
        #    f"task_id:{task_id} frame packets built, project_dir:{project_dir} "
        #    f"cost:{cost_packets:.1f}s"
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_FRAME_PACKETED, payload_msg=TaskStatus.VIDEO_FRAME_PACKETED)

        #step_start = time.time()
        #try:
        #    frame_html_path = build_frame_html(project_dir, logger)
        #except Exception as e:
        #    msg = f'Failed to build frame html: {e}'
        #    logger.error(f'task_id:{task_id} {msg}')
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #cost_frame_html = time.time() - step_start
        #logger.info(
        #    f'task_id:{task_id} frame html built, frame_html_path:{frame_html_path}, '
        #    f'project_dir:{project_dir} cost:{cost_frame_html:.1f}s'
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_FRAMED_HTML, payload_msg=TaskStatus.VIDEO_FRAMED_HTML)

        #step_start = time.time()
        #try:
        #    index_html_path = build_index_html(project_dir, logger)
        #except Exception as e:
        #    msg = f'Failed to build index html: {e}'
        #    logger.error(f'task_id:{task_id} {msg}')
        #    update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
        #    return gen_res_dict(1, msg)
        #cost_index_html = time.time() - step_start
        #logger.info(
        #    f'task_id:{task_id} index html built, index_html_path:{index_html_path}, '
        #    f'project_dir:{project_dir} cost:{cost_index_html:.1f}s'
        #)
        #update_task_status(logger, task_id, domain, TaskStatus.VIDEO_INDEXED, payload_msg=TaskStatus.VIDEO_INDEXED)

        output_file = 'fourth-video.mp4'
        output_path = f'{video_dir}/2026-11-08/userclm17919893695531772/6aefce31f79c496b/{output_file}'
        try:
            delivery_transport = resolve_delivery_transport(email)
            proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
            # short_uri_prefix: sync success -> /outdt{prefix}/file; complete only after async sync
            short_uri_prefix = f'/video/{today}/user{uid}'
            remote_target_dir = f'/home/aigc/data/aioutput/video/{today}/user{uid}'
            # TODO: 在 enqueue 前完成 htmlvideo 生成，写入 output_path / output_file

            if not output_path or not output_file:
                msg = (
                    f'htmlvideo output missing before sync, task_id:{task_id}, '
                    f'worker:{WORKER_HOSTNAME}'
                )
                logger.error(msg)
                update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
                return gen_res_dict(1, msg)
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
                update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)

        except Exception as e:
            logger.error(f'Error in gen_htmlvideo for task_id:{task_id}, worker:{WORKER_HOSTNAME}: {e}')
            msg = f'Error in gen_htmlvideo for task_id:{task_id}, worker:{WORKER_HOSTNAME}: {e}'
            update_task_status(logger, task_id, domain, TaskStatus.FAILED, payload_msg=msg)
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

        cost_total = time.time() - start_time
        logger.info(
            f'task_id:{task_id}, htmlvideo finish, sync enqueued, transport:{delivery_transport} '
            f'time[frame_html:{cost_frame_html:.1f}s total:{cost_total:.1f}s]'
            # 全链路恢复后可改为:
            # f'time[init:{cost_init:.1f}s storyboard:{cost_storyboard:.1f}s audio:{cost_audio:.1f}s '
            # f'visual:{cost_visual:.1f}s packets:{cost_packets:.1f}s frame_html:{cost_frame_html:.1f}s '
            # f'total:{cost_total:.1f}s]'
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
        transports["quic"] = QuicHttpTransport(biz_type="video")

    try:
        start_file_delivery(logger, transports)
    except Exception as start_err:
        logger.error(f"start_file_delivery failed: {start_err}")

# 应用启动时初始化 SSH ControlMaster 连接与投递队列
initialize_file_delivery()

