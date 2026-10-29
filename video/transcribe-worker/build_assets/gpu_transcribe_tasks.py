import os
import time

from celery_worker import celery_app
from config.comm_config import get_remote_transcribe_dir, get_localnet_transcribe_dir
from config.model_conf import get_whisper_model_name
from sql.mysql_db import update_ip_tool_record
from utility.module_enum import TaskStatus
from utility.log import setup_logger
from utility.util import gen_reqid
from utility.file_sync_config import get_file_sync_transport
from utility.file_delivery import (
    SyncJob, enqueue_delivery_job, start_file_delivery, resolve_delivery_transport,
)
from utility.socat_rsyc_util import (
    RsyncTransport, get_socat_proxy_configs,
    initialize_ssh_connections_for_proxies,
)
from utility.quic_http_transport import QuicHttpTransport
from utility.samearea_http_transport import SameAreaHttpTransport

from transcriber import get_engine
from transcriber.formatter import format_result

DAY_FORMAT = '%Y-%m-%d'
VIDEO_TTS_TRANSCRIBE = "video_tts_transcribe"

os.makedirs('/app/log', exist_ok=True)

logger = setup_logger(
    logger_name='transcribe-worker',
    log_file='/app/log/transcribe_worker_log.txt',
    console_level='DEBUG',
    file_level='DEBUG',
)

OUTPUT_EXT = {'json': 'json', 'txt': 'txt', 'srt': 'srt', 'vtt': 'vtt', 'ass': 'ass'}


@celery_app.task(name='gpu_transcribe_tasks.transcribe_whisper')
def transcribe_whisper(upfileinfo, language=None, output_format='json', task_id='0',
                       userid=0, first_ip='', email=''):
    start_time = time.time()
    time_str = time.strftime(DAY_FORMAT, time.localtime(start_time))
    reqid = gen_reqid(task_id, start_time)

    try:
        input_path = upfileinfo['relative_path']
        filename_without_ext = upfileinfo['upfilename_without_ext']
        engine = get_engine('whisper')
        result = engine.transcribe(input_path, language=language)
        if not result.segments or not (result.text or '').strip():
            msg = f'empty transcription (no speech detected), task_id:{task_id}'
            logger.warning(msg)
            update_ip_tool_record(task_id, msg, TaskStatus.FAILED)
            return f'transcribe failed, task_id:{task_id}, msg:{msg}'
        content = format_result(result, output_format)
        ext = OUTPUT_EXT.get(output_format, 'json')

        out_dir = f'data/transcribe/{time_str}/user{str(userid)}'
        os.makedirs(out_dir, mode=0o777, exist_ok=True)
        output_file = f'{filename_without_ext}_transcribe.{ext}'
        output_path = os.path.join(out_dir, output_file)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)

        short_uri_prefix = f'/transcribe/{time_str}/user{str(userid)}'
        delivery_transport = "quic"
        proxies = []
        if email == VIDEO_TTS_TRANSCRIBE:
            remote_dir = get_localnet_transcribe_dir()
            delivery_transport = "http"
            proxies = []
        else:
            remote_dir = get_remote_transcribe_dir()
            delivery_transport = resolve_delivery_transport(email)
            proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []

        sync_job = SyncJob(
            source_file=output_path,
            target_dir=f'{remote_dir}/{time_str}/user{str(userid)}',
            task_id=task_id,
            short_uri_prefix=short_uri_prefix,
            output_file=output_file,
            proxies=list(proxies),
            transport=delivery_transport,
        )
        if not enqueue_delivery_job(logger, sync_job):
            msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}'
            logger.error(msg)
            update_ip_tool_record(task_id, msg, TaskStatus.FAILED)
            return f'transcribe failed, task_id:{task_id}, msg:{msg}'

        cost = time.time() - start_time
        logger.info(
            f'reqid:{reqid} transcribe done task_id:{task_id} lang:{language} '
            f'format:{output_format} model:{get_whisper_model_name()} '
            f'cost:{cost:.2f}s output:{output_path} transport:{delivery_transport}'
        )
        return f'TranscribeCompleted: {task_id}'
    except Exception as e:
        logger.error(f'reqid:{reqid} transcribe failed task_id:{task_id}: {e}')
        update_ip_tool_record(task_id, f'transcribe error: {e}', TaskStatus.FAILED)
        return f'transcribe failed, task_id:{task_id}'


def initialize_file_delivery():
    cross_transport = get_file_sync_transport()
    try:
        socat_proxies = get_socat_proxy_configs()
        results = initialize_ssh_connections_for_proxies(logger, socat_proxies)
        logger.info(f'init_socat_summary success={sum(results.values())}/{len(results)}')
    except Exception as e:
        logger.warning(f'init_socat_proxies_fail error={e}')

    transports = {
        "rsync": RsyncTransport(),
        "http": SameAreaHttpTransport(),
    }
    if cross_transport == "quic":
        transports["quic"] = QuicHttpTransport(biz_type="transcribe")
    start_file_delivery(logger, transports)


initialize_file_delivery()
logger.info('preload whisper model into GPU memory')
get_engine('whisper')
logger.info('whisper model ready')