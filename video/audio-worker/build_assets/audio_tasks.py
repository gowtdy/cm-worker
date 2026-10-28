import os
import time
import random
from celery_worker import celery_app
from config.comm_config import get_remote_sepaudio_dir
from sql.mysql_db import update_ip_tool_record
from utility.module_enum import TaskStatus
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
from utility.util import gen_reqid, format_timing_stats
from utility.audio_util import convert_to_mp3

DAY_FORMAT = '%Y-%m-%d'

log_dir = '/app/log'
os.makedirs(log_dir, exist_ok=True)

logger = setup_logger(
    logger_name='audio-worker',
    log_file=log_dir + '/audio_worker_log.txt',
    console_level='DEBUG',
    file_level='DEBUG'
)

app = celery_app  # for `celery -A audio_tasks worker`

def split_audio(upfileinfo: dict, reqid: str) -> tuple[int, str, dict]:
    """
    主处理函数
    """
    ret = 0
    msg = 'ok'
    extractinfo = {}
    timing_stats = {}
    start_time = time.time()

    # 准备基础参数
    dir_setup_start = time.time()
    work_num = 5
    input_path = upfileinfo['relative_path']
    filename_without_ext = upfileinfo['upfilename_without_ext']
    update_str = upfileinfo['update_str']
    userip = upfileinfo['userip']
    finaldata_dir = f'data/sepaudio/{update_str}/{userip}'
    os.makedirs(finaldata_dir, exist_ok=True)
    timing_stats['dir_setup'] = time.time() - dir_setup_start
    
    # mp3转换
    mp3_conversion_start = time.time()
    final_mp3file = f'{finaldata_dir}/{filename_without_ext}_audio.mp3'
    ret, msg = convert_to_mp3(input_path, final_mp3file, work_num, reqid, logger)
    if ret != 0:
        return ret, msg, extractinfo
    timing_stats['mp3_conversion'] = time.time() - mp3_conversion_start

    # 记录总处理时间和日志
    total_time = time.time() - start_time
    timing_stats['total'] = total_time
    
    extractinfo['audio_file'] = final_mp3file

    # 记录详细的时间统计
    timing_str = format_timing_stats(timing_stats)
    logger.info(
        f"extract_audio completed, reqid:{reqid}, timing_stats:{timing_str}, ip:{userip}"
    )
    
    return ret, msg, extractinfo

@celery_app.task(name='audio_tasks.extract_audio')
def extract_audio(upfileinfo, task_id='0', first_ip='', email=''):   
    ret = 0
    msg = 'ok'
    start_time = time.time()
    timing_stats = {}  # 用于记录各阶段耗时
    localtime = time.localtime(start_time)
    today =  time.strftime(DAY_FORMAT, localtime)
    datetime = time.strftime('%Y%m%d_%H%M%S', localtime)
    random_number = random.randint(1, 1000)

    reqid = gen_reqid(task_id, start_time)
    relative_path = upfileinfo["relative_path"]
    update_str = upfileinfo['update_str']
    file_ext = upfileinfo['file_ext']
    uploadtime = upfileinfo['uploadtime']
    userip = upfileinfo["userip"]
    randomnum = f'{upfileinfo["randomnum"]}{random_number}'

    # extract audio
    audio_extraction_start = time.time()
    logger.info(f'*********** reqid:{reqid} extract_audio start ***********')
    ret, msg, extractinfo = split_audio(upfileinfo, reqid)
    if ret != 0:
        logger.error(f'task_id:{task_id} first_ip:{first_ip} email:{email} extract_audio failed: {msg}')
        update_ip_tool_record(task_id, '', TaskStatus.FAILED)
        return f'extract audio failed, task_id:{task_id}, msg:{msg}'
    timing_stats['audio_extraction'] = time.time() - audio_extraction_start
    logger.info(f'*********** reqid:{reqid} extract_audio end ***********')

    audio_file = extractinfo['audio_file']
    audio_basename = os.path.basename(audio_file)
    short_uri_prefix = f'/sepaudio/{update_str}/{userip}'
    remote_sepaudio_dir = get_remote_sepaudio_dir()
    target_dir = f'{remote_sepaudio_dir}/{update_str}/{userip}'
    delivery_transport = get_file_sync_transport()
    proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
    sync_job = SyncJob(
        source_file=audio_file,
        target_dir=target_dir,
        task_id=task_id,
        short_uri_prefix=short_uri_prefix,
        output_file=audio_basename,
        proxies=list(proxies),
        transport=delivery_transport,
    )
    enqueue_start = time.time()
    if not enqueue_delivery_job(logger, sync_job):
        sync_msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}'
        logger.error(f'task_id:{task_id} first_ip:{first_ip} email:{email} {sync_msg}')
        update_ip_tool_record(task_id, sync_msg, TaskStatus.FAILED)
        return f'file sync failed, task_id:{task_id}, msg:{sync_msg}'
    timing_stats['file_sync_enqueue'] = time.time() - enqueue_start
    logger.info(
        f'*********** reqid:{reqid} file sync enqueued transport={delivery_transport} ***********'
    )

    # 记录总处理时间和日志（投递完成由 file_delivery 回调写库）
    total_time = time.time() - start_time
    timing_stats['total'] = total_time
    timing_str = format_timing_stats(timing_stats)
    logger.info(
        f'*********** reqid:{reqid} extract audio completed (awaiting delivery), '
        f'timing_stats:{timing_str}, ip:{userip} transport:{delivery_transport} ***********'
    )

    result = f"ExtractAudioCompleted: {task_id}, ip:{first_ip}, email:{email}"
    return result

def initialize_file_delivery():
    """Always warm socat for cross-region (rsync or quic fallback); start delivery queue."""
    cross_transport = get_file_sync_transport()
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
        transports["quic"] = QuicHttpTransport(biz_type="extract_audio")

    try:
        start_file_delivery(logger, transports)
    except Exception as start_err:
        logger.error(f"start_file_delivery failed: {start_err}")

initialize_file_delivery()
