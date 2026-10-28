import os
import time
import logging
import datetime
import argparse
import json
import httpx
import random
import gc
import psutil
import subprocess
import ffmpeg
import traceback
import hmac
from scipy.io import wavfile
from io import BytesIO
from typing import Any, Tuple
from dotenv import load_dotenv

# 设置CUDA调试环境变量
os.environ['CUDA_LAUNCH_BLOCKING'] = '1'
os.environ['TORCH_USE_CUDA_DSA'] = '1'

from werkzeug.utils import secure_filename

import torch
import torchaudio
import numpy as np

from concurrent.futures import ThreadPoolExecutor, as_completed
import torch.multiprocessing as mp

from utility.log import setup_logger, set_reqid, clear_reqid
from utility.vocal_extract_util import model_extract_vocals
from utility.model_util import load_hubert, load_convert_model, unload_convert_model, load_bsroformer_model, load_deecho_model,  \
     load_hpkaroke_model, load_mdxnetdereverb_model, load_rmvpe_model
from utility.util import gen_res_dict
from utility.module_enum import TaskStatus
from utility.userserver_client import update_task_status
from utility.socat_rsyc_util import (
    get_socat_proxy_configs,
    initialize_ssh_connections_for_proxies,
    RsyncTransport,
)
from utility.file_delivery import SyncJob, enqueue_delivery_job, start_file_delivery
from utility.file_sync_config import (
    get_file_sync_transport,
    get_quic_client_host,
    get_quic_client_port,
)
from utility.quic_http_transport import QuicHttpTransport

from config.comm_config import get_hostname, get_logdir, get_device, get_white_list, get_userupload_dir, \
    get_remote_cover_dir, get_remote_vocal_dir, get_remote_instrument_dir
from infer.modules.uvr5.vr import AudioPre, AudioPreDeEcho
from infer.lib.audio import load_audio, analyze_audio_complexity
from infer.modules.uvr5.bsroformer import BsRoformer_Loader
from infer.modules.uvr5.mdxnet import MDXNetDereverb
from celery_worker import celery_app

load_dotenv()

log_dir = '/app/log'
os.makedirs(log_dir, exist_ok=True)

logger = setup_logger(
    logger_name='cover-worker',
    log_file=log_dir + '/cover_worker_log.txt',
    console_level='DEBUG',
    file_level='DEBUG'
)

model_dir = '/app/models/service_models'
hubert_path = '/app/assets/hubert/hubert_base.pt'

middledata_dir = '/app/data/middle'
finaldata_dir = '/app/data/final'

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'

host = get_hostname()
device = get_device()
is_half = True

hubert_model = load_hubert(hubert_path, device, is_half)

MAX_FREE_TRIES = 5

userupload_dir = get_userupload_dir(domain='aivoicelab.net')

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_AUDIO_TYPES = ['audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/mp3', 'audio/m4a', 'audio/x-m4a',  'audio/mp4', 'audio/flac', 'audio/aac', 'audio/ogg', 'video/mp4', 'video/mov']
ALLOWED_FILE_EXTENSIONS = ['.mp3', '.wav',  '.m4a', '.flac', '.aac', '.ogg', '.mp4', '.mov']

# uvr5 声音处理相关模型
weight_uvr5_root = os.getenv("weight_uvr5_root")
# 1 bsroformer model, 人声、伴奏分离模型
#bsroformer_path = f'{weight_uvr5_root}/model_bs_roformer_ep_317_sdr_12.9755.ckpt'  # 音频极高频时有点问题
bsroformer_path = f'{weight_uvr5_root}/model_bs_roformer_ep_368_sdr_12.9628.ckpt'
bsroformer_model = load_bsroformer_model(bsroformer_path, device, is_half)

# 2 hpkaroke model, 人声、和声分离模型
hpkaroke_path = f'{weight_uvr5_root}/5_HP-Karaoke-UVR.pth'
hpkaroke_model = load_hpkaroke_model(hpkaroke_path, device, is_half)

# 3 mdxnetdereverb model
dereverb_path = f'{weight_uvr5_root}/onnx_dereverb_By_FoxJoy/vocals.onnx'
dereverb_model_infer, dereverb_model_tdfnet = load_mdxnetdereverb_model(dereverb_path, device, is_half)

# 4 deecho model
deecho_path = f'{weight_uvr5_root}/VR-DeEchoAggressive.pth'
deecho_model = load_deecho_model(deecho_path, device, is_half)

# rmvpe model
rmvpe_root = os.getenv("rmvpe_root")
rmvpe_path = f'{rmvpe_root}/rmvpe.pt'
rmvpe_model = load_rmvpe_model(rmvpe_path, device, is_half)

def check_cuda_health(reqid: str = "unknown") -> bool:
    """
    检查CUDA状态健康性
    """
    try:
        if not torch.cuda.is_available():
            logger.warning(f'reqid:{reqid} CUDA not available')
            return False
        
        # 检查CUDA设备
        device_count = torch.cuda.device_count()
        current_device = torch.cuda.current_device()
        device_name = torch.cuda.get_device_name(current_device)
        
        # 检查内存
        memory_allocated = torch.cuda.memory_allocated(current_device)
        memory_reserved = torch.cuda.memory_reserved(current_device)
        max_memory = torch.cuda.max_memory_allocated(current_device)
        
        logger.debug(f'reqid:{reqid} CUDA Health Check - Device: {device_name}, '
                    f'Memory: allocated={memory_allocated/1024**2:.1f}MB, '
                    f'reserved={memory_reserved/1024**2:.1f}MB, '
                    f'max={max_memory/1024**2:.1f}MB')
        
        # 尝试简单的CUDA操作
        test_tensor = torch.randn(10, 10, device=device)
        test_result = test_tensor.sum()
        del test_tensor
        
        torch.cuda.synchronize()
        return True
        
    except Exception as e:
        logger.error(f'reqid:{reqid} CUDA health check failed: {str(e)}')
        return False

def process_audio_stage(
    model, 
    device, 
    input_path: str, 
    output_dir: str, 
    stage_name: str,
    filename_without_ext: str,
    is_half: bool,
    agg: float,
    reqid: str,
    processor_class: type
) -> tuple[int, str, dict]:
    """
    通用的音频处理阶段函数
    """
    ret = 0
    msg = 'ok'
    result_info = {}
    
    # 验证输入文件是否存在
    if not os.path.exists(input_path):
        return 1, f'Input file does not exist: {input_path}', result_info
    
    # 创建输出目录
    vocals_dir = f'{output_dir}/{stage_name}/vocals'
    instrument_dir = f'{output_dir}/{stage_name}/instrument'
    os.makedirs(vocals_dir, exist_ok=True)
    os.makedirs(instrument_dir, exist_ok=True)

    try:
        # 初始化处理器
        if processor_class == BsRoformer_Loader:
            processor = processor_class(model, device, is_half)
        elif processor_class == AudioPreDeEcho:
            processor = processor_class(model, device, agg)
        elif processor_class == AudioPre:
            processor = processor_class(model, device, agg=agg)
        else:
            raise ValueError(f'Unknown processor class: {processor_class}')

        # 确保CUDA同步
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        # 统一使用 _path_audio_ 方法处理音频
        output_name = f'{filename_without_ext}_{stage_name}'
        logger.debug(f'reqid:{reqid} stage:{stage_name} start, input_path:{input_path}, output_name:{output_name}, vocals_dir:{vocals_dir}, instrument_dir:{instrument_dir}')
        processor._path_audio_(
            music_file=input_path,
            vocal_root=vocals_dir,
            ins_root=instrument_dir,
            output_name=output_name,
            format='wav',
            logger=logger
        )
        
        # 确保处理完成后同步
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            
        logger.debug(f'reqid:{reqid} stage:{stage_name} finished, output_name:{output_name}')
        
        # 设置输出路径
        vocals_path = f'{vocals_dir}/{output_name}_vocal.wav'
        instrument_path = f'{instrument_dir}/{output_name}_other.wav'
        
        # 验证输出文件是否成功生成
        #if not os.path.exists(vocals_path):
        #    return 1, f'Vocals output file was not created: {vocals_path}', result_info
        #if not os.path.exists(instrument_path):
        #    return 1, f'Instrument output file was not created: {instrument_path}', result_info
            
        result_info[f'{stage_name}_vocals'] = vocals_path
        result_info[f'{stage_name}_instrument'] = instrument_path

    except Exception as e:
        ret = 1
        msg = f'{stage_name} model extracting vocals failed: {str(e)}'
        logger.error(f'reqid:{reqid} msg: {msg}')
        logger.error(f'reqid:{reqid} Full traceback: {traceback.format_exc()}')
    finally:
        if 'processor' in locals():
            try:
                processor.cleanup()
            except Exception as cleanup_error:
                logger.warning(f'reqid:{reqid} Processor cleanup failed: {cleanup_error}')
        
        # 安全地清理CUDA资源
        try:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except Exception as cuda_cleanup_error:
            logger.warning(f'reqid:{reqid} Stage {stage_name} CUDA cleanup failed: {cuda_cleanup_error}')
        
        # 强制垃圾回收
        gc.collect()

    return ret, msg, result_info

def extract_vocals(upfileinfo: dict, reqid: str) -> tuple[int, str, dict]:
    """
    主处理函数
    """
    ret = 0
    msg = 'ok'
    extractinfo = {}
    timing_stats = {}
    start_time = time.time()

    # 准备基础参数
    work_num = 10
    input_path = upfileinfo['relative_path']
    filename_without_ext = upfileinfo['upfilename_without_ext']
    update_str = upfileinfo['update_str']
    userip = upfileinfo['userip']
    middledata_dir = f'data/middle/{update_str}/{userip}'
    os.makedirs(middledata_dir, exist_ok=True)
    
    # WAV转换
    wav_conversion_start = time.time()
    input_wavfile = f'{middledata_dir}/{filename_without_ext}.wav'
    ret, msg = convert_to_wav(input_path, input_wavfile, work_num, reqid)
    if ret != 0:
        error_msg = f'WAV conversion failed: {msg}. Input file: {input_path}, Output file: {input_wavfile}'
        logger.error(f'reqid:{reqid} {error_msg}')
        # 记录输入文件信息以便调试
        if os.path.exists(input_path):
            file_size = os.path.getsize(input_path)
            logger.error(f'reqid:{reqid} Input file size: {file_size} bytes')
        return ret, error_msg, extractinfo
    timing_stats['wav_conversion'] = time.time() - wav_conversion_start

    # 处理各个阶段
    stages = [
        {
            'name': 'bsroformer',
            'model': bsroformer_model,
            'input_path': input_wavfile,
            'agg': 0.7,
            'processor_class': BsRoformer_Loader
        },
        {
            'name': 'hpkaroke',
            'model': hpkaroke_model,
            'agg': 0.7,
            'processor_class': AudioPre
        },
        {
            'name': 'deecho',
            'model': deecho_model,
            'agg': 0.5,
            'processor_class': AudioPreDeEcho
        }
    ]

    current_input = input_wavfile
    completed_stages = []
    
    for stage in stages:
        stage_start = time.time()
        stage_name = stage["name"]
        
        logger.debug(f'reqid:{reqid} Starting stage: {stage_name}')
        
        ret, msg, stage_result = process_audio_stage(
            model=stage['model'],
            device=device,
            input_path=current_input,
            output_dir=middledata_dir,
            stage_name=stage_name,
            filename_without_ext=filename_without_ext,
            is_half=is_half,
            agg=stage['agg'],
            reqid=reqid,
            processor_class=stage['processor_class']
        )
        
        if ret != 0:
            logger.error(f'reqid:{reqid} Stage {stage_name} failed: {msg}')
            logger.error(f'reqid:{reqid} Completed stages: {completed_stages}')
            logger.error(f'reqid:{reqid} Available keys in extractinfo: {list(extractinfo.keys())}')
            return ret, msg, extractinfo
            
        # 更新下一阶段的输入路径
        current_input = stage_result[f"{stage_name}_vocals"]
        # 更新提取信息
        extractinfo.update(stage_result)
        completed_stages.append(stage_name)
        
        # 先保存处理时间，再使用
        timing_stats[f"{stage_name}_processing"] = time.time() - stage_start
        logger.debug(f'reqid:{reqid} stage:{stage_name} finished, processing time: {timing_stats[f"{stage_name}_processing"]:.2f}s')

    # 清理资源
    try:
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception as cuda_cleanup_error:
        logger.warning(f'reqid:{reqid} extract_vocals CUDA cleanup failed: {cuda_cleanup_error}')
    
    # 强制垃圾回收
    gc.collect()

    # 记录总处理时间和日志
    total_time = time.time() - start_time
    timing_stats['total'] = total_time
    
    # 记录详细的时间统计
    logger.info(
        f"extract_vocals completed, reqid:{reqid}, timings: "
        f"[total:{timing_stats['total']:.2f}s, "
        f"wav_conversion:{timing_stats['wav_conversion']:.2f}s, "
        f"bsroformer:{timing_stats.get('bsroformer_processing', 0):.2f}s, "
        f"hpkaroke:{timing_stats.get('hpkaroke_processing', 0):.2f}s, "
        f"deecho:{timing_stats.get('deecho_processing', 0):.2f}s], "
        f"ip:{userip}, completed_stages:{completed_stages}"
    )
    
    return ret, msg, extractinfo

def extract_instruments(upfileinfo: dict, reqid: str) -> tuple[int, str, dict]:
    """
    主处理函数
    """
    ret = 0
    msg = 'ok'
    extractinfo = {}
    timing_stats = {}
    start_time = time.time()

    # 准备基础参数
    work_num = 10
    input_path = upfileinfo['relative_path']
    filename_without_ext = upfileinfo['upfilename_without_ext']
    update_str = upfileinfo['update_str']
    userip = upfileinfo['userip']
    middledata_dir = f'data/middle/{update_str}/{userip}'
    os.makedirs(middledata_dir, exist_ok=True)
    
    # WAV转换
    wav_conversion_start = time.time()
    input_wavfile = f'{middledata_dir}/{filename_without_ext}.wav'
    ret, msg = convert_to_wav(input_path, input_wavfile, work_num, reqid)
    if ret != 0:
        logger.error(f'reqid:{reqid} WAV conversion failed: {msg}')
        return ret, msg, extractinfo
    timing_stats['wav_conversion'] = time.time() - wav_conversion_start

    # 处理各个阶段
    stages = [
        {
            'name': 'bsroformer',
            'model': bsroformer_model,
            'input_path': input_wavfile,
            'agg': 0.7,
            'processor_class': BsRoformer_Loader
        }
    ]

    current_input = input_wavfile
    completed_stages = []
    
    for stage in stages:
        stage_start = time.time()
        stage_name = stage["name"]
        
        logger.debug(f'reqid:{reqid} Starting stage: {stage_name}')
        
        ret, msg, stage_result = process_audio_stage(
            model=stage['model'],
            device=device,
            input_path=current_input,
            output_dir=middledata_dir,
            stage_name=stage_name,
            filename_without_ext=filename_without_ext,
            is_half=is_half,
            agg=stage['agg'],
            reqid=reqid,
            processor_class=stage['processor_class']
        )
        
        if ret != 0:
            logger.error(f'reqid:{reqid} Stage {stage_name} failed: {msg}')
            logger.error(f'reqid:{reqid} Completed stages: {completed_stages}')
            logger.error(f'reqid:{reqid} Available keys in extractinfo: {list(extractinfo.keys())}')
            return ret, msg, extractinfo
            
        # 更新下一阶段的输入路径
        current_input = stage_result[f"{stage_name}_vocals"]
        # 更新提取信息
        extractinfo.update(stage_result)
        completed_stages.append(stage_name)
        
        # 先保存处理时间，再使用
        timing_stats[f"{stage_name}_processing"] = time.time() - stage_start
        logger.debug(f'reqid:{reqid} stage:{stage_name} finished, processing time: {timing_stats[f"{stage_name}_processing"]:.2f}s')

    # 清理资源
    try:
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception as cuda_cleanup_error:
        logger.warning(f'reqid:{reqid} extract_instruments CUDA cleanup failed: {cuda_cleanup_error}')
    
    # 强制垃圾回收
    gc.collect()

    # 记录总处理时间和日志
    total_time = time.time() - start_time
    timing_stats['total'] = total_time
    
    # 记录详细的时间统计
    logger.info(
        f"extract_instruments completed, reqid:{reqid}, timings: "
        f"[total:{timing_stats['total']:.2f}s, "
        f"wav_conversion:{timing_stats['wav_conversion']:.2f}s, "
        f"bsroformer:{timing_stats.get('bsroformer_processing', 0):.2f}s], "
        f"ip:{userip}, completed_stages:{completed_stages}"
    )
    
    return ret, msg, extractinfo

def split_audio(input_path: str, segment_duration: int = 30) -> tuple[list, int]:
    """将音频分割成固定长度的片段"""
    audio_data, sr = torchaudio.load(input_path)
    segment_length = int(segment_duration * sr)
    segments = torch.split(audio_data, segment_length, dim=1)
    return segments, sr

def process_segment(segment_data, model, processor_func):
    """处理单个音频段"""
    with torch.cuda.device(device):
        return processor_func(model, segment_data)   

def validate_extractinfo(extractinfo: dict, required_keys: list, reqid: str) -> tuple[bool, str]:
    """
    验证 extractinfo 字典是否包含所有必需的键
    """
    missing_keys = []
    for key in required_keys:
        if key not in extractinfo:
            missing_keys.append(key)
        elif not extractinfo[key] or not os.path.exists(extractinfo[key]):
            missing_keys.append(f"{key} (file missing: {extractinfo.get(key, 'None')})")
    
    if missing_keys:
        available_keys = list(extractinfo.keys())
        msg = f"Missing or invalid keys: {missing_keys}, available keys: {available_keys}"
        logger.error(f'reqid:{reqid} {msg}')
        return False, msg
    
    return True, 'ok'

def convert_to_wav(input_path: str, output_path: str, work_num: int, reqid: str) -> tuple[int, str]:
    """WAV转换辅助函数，支持处理损坏或不标准的音频文件"""
    try:
        # 首先检测文件实际格式
        detected_format = None
        force_format = None
        file_ext = os.path.splitext(input_path)[1].lower() if input_path else ''
        
        try:
            probe_cmd = f'ffprobe -v error -show_format -of json {input_path}'
            probe_result = subprocess.run(
                probe_cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=30
            )
            
            if probe_result.returncode == 0:
                try:
                    probe_data = json.loads(probe_result.stdout)
                    format_name = probe_data.get('format', {}).get('format_name', '')
                    if format_name:
                        detected_format = format_name.split(',')[0]  # 取第一个格式
                        logger.info(f'reqid:{reqid} Detected file format: {detected_format}')
                        
                        # 如果检测到格式不匹配，尝试强制指定格式
                        # 注意：如果扩展名是 .mp3 但检测到是 MP4，说明文件实际上是 MP4，应该强制使用 mp4 格式
                        if file_ext:
                            if file_ext == '.mp3' and ('mp4' in detected_format.lower() or 'mov' in detected_format.lower()):
                                logger.warning(f'reqid:{reqid} File extension is .mp3 but detected format is {detected_format}, trying to force MP4 format (file is actually MP4)')
                                force_format = 'mp4'
                            elif file_ext in ['.mp4', '.m4a'] and 'mp3' in detected_format.lower():
                                logger.warning(f'reqid:{reqid} File extension is {file_ext} but detected format is {detected_format}, trying to force MP3 format')
                                force_format = 'mp3'
                except Exception as e:
                    logger.debug(f'reqid:{reqid} Failed to parse probe result: {str(e)}')
        except Exception as e:
            logger.debug(f'reqid:{reqid} Failed to probe file format: {str(e)}')
        
        # 构建格式参数
        format_args = f'-f {force_format} ' if force_format else ''
        
        # 首先尝试使用宽松的解码参数
        towav_cmd = (
            f'ffmpeg -y {format_args}-i {input_path} '
            f'-err_detect ignore_err '  # 忽略解码错误，继续处理
            f'-fflags +genpts '  # 生成时间戳
            f'-ar 44100 -ac 2 -acodec pcm_s32le '
            f'-threads {work_num} '
            f'-loglevel warning '  # 改为 warning 以便看到更多信息
            f'{output_path}'
        )
        
        towav_result = subprocess.run(
            towav_cmd, 
            shell=True, 
            capture_output=True, 
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=300  # 添加超时保护
        )
        
        # 如果第一次转换失败，尝试使用更宽松的参数
        if towav_result.returncode != 0:
            logger.warning(f'reqid:{reqid} First conversion attempt failed, ffmpeg_cmd: {towav_cmd}, trying with more lenient parameters: {towav_result.stderr[:500]}')
            
            # 检测是否是 moov atom 错误
            error_stderr = towav_result.stderr.lower()
            is_moov_error = 'moov atom not found' in error_stderr
            
            # 如果是 moov atom 错误，先尝试特殊处理
            if is_moov_error:
                logger.warning(f'reqid:{reqid} Detected moov atom error, attempting to fix or work around')
                
                # 如果之前没有强制格式，且扩展名是 .mp3，尝试强制使用 mp4 格式（因为 moov atom 是 MP4 特有的）
                if not force_format and file_ext == '.mp3':
                    logger.warning(f'reqid:{reqid} Moov atom error with .mp3 extension, trying to force MP4 format')
                    format_args = '-f mp4 '
                    force_format = 'mp4'
                
                # 尝试1：使用 analyzeduration 和 probesize 参数，允许 FFmpeg 扫描整个文件
                towav_cmd_moov_fix = (
                    f'ffmpeg -y {format_args}-i {input_path} '
                    f'-analyzeduration 2147483647 '  # 最大分析时长，扫描整个文件
                    f'-probesize 2147483647 '  # 最大探测大小，扫描整个文件
                    f'-fflags +genpts+discardcorrupt+igndts+ignidx '  # 忽略索引
                    f'-err_detect ignore_err+aggressive '  # 激进错误处理
                    f'-vn '  # 忽略视频
                    f'-strict -2 '  # 允许非标准格式
                    f'-ar 44100 -ac 2 -acodec pcm_s32le '
                    f'-threads {work_num} '
                    f'-loglevel warning '
                    f'{output_path}'
                )
                
                moov_fix_result = subprocess.run(
                    towav_cmd_moov_fix, 
                    shell=True, 
                    capture_output=True, 
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=300
                )
                
                if moov_fix_result.returncode == 0:
                    if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                        return 0, 'ok'
                
                # 尝试2：使用 qt-faststart 工具修复文件（如果可用）
                logger.warning(f'reqid:{reqid} Trying qt-faststart to fix moov atom')
                fixed_mp4 = f'{input_path}.fixed.mp4'
                qtfaststart_cmd = f'qt-faststart {input_path} {fixed_mp4}'
                qtfaststart_result = subprocess.run(
                    qtfaststart_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=60
                )
                
                if qtfaststart_result.returncode == 0 and os.path.exists(fixed_mp4):
                    try:
                        # 尝试转换修复后的文件
                        towav_cmd_fixed = (
                            f'ffmpeg -y {format_args}-i {fixed_mp4} '
                            f'-vn '
                            f'-ar 44100 -ac 2 -acodec pcm_s32le '
                            f'-threads {work_num} '
                            f'-loglevel warning '
                            f'{output_path}'
                        )
                        
                        fixed_result = subprocess.run(
                            towav_cmd_fixed,
                            shell=True,
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='ignore',
                            timeout=300
                        )
                        
                        # 清理临时文件
                        if os.path.exists(fixed_mp4):
                            try:
                                os.remove(fixed_mp4)
                            except:
                                pass
                        
                        if fixed_result.returncode == 0:
                            if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                                return 0, 'ok'
                    except:
                        # 清理临时文件
                        if os.path.exists(fixed_mp4):
                            try:
                                os.remove(fixed_mp4)
                            except:
                                pass
                
                # 尝试3：使用 FFmpeg 重建索引
                logger.warning(f'reqid:{reqid} Trying to rebuild index with FFmpeg')
                towav_cmd_rebuild = (
                    f'ffmpeg -y -fflags +genpts+discardcorrupt+igndts+ignidx '
                    f'-analyzeduration 2147483647 -probesize 2147483647 '
                    f'{format_args}-i {input_path} '
                    f'-vn '
                    f'-err_detect ignore_err+aggressive '
                    f'-strict -2 '
                    f'-ar 44100 -ac 2 -acodec pcm_s32le '
                    f'-threads {work_num} '
                    f'-loglevel warning '
                    f'{output_path}'
                )
                
                rebuild_result = subprocess.run(
                    towav_cmd_rebuild,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=300
                )
                
                if rebuild_result.returncode == 0:
                    if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                        return 0, 'ok'
            
            # 第二次尝试：使用更宽松的参数，针对AAC解码问题
            towav_cmd_fallback = (
                f'ffmpeg -y {format_args}-i {input_path} '
                f'-vn '  # 忽略视频流，只处理音频
                f'-map 0:a:0 '  # 明确映射第一个音频流
                f'-err_detect ignore_err '
                f'-fflags +genpts+discardcorrupt+igndts '  # 丢弃损坏的帧，忽略时间戳
                f'-strict -2 '  # 允许非标准格式（处理非标准AAC）
                f'-ar 44100 -ac 2 -acodec pcm_s32le '
                f'-threads {work_num} '
                f'-loglevel warning '
                f'{output_path}'
            )
            
            towav_result_fallback = subprocess.run(
                towav_cmd_fallback, 
                shell=True, 
                capture_output=True, 
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=300
            )
            
            # 如果第二次也失败，尝试第三次：使用更激进的方法
            if towav_result_fallback.returncode != 0:
                logger.warning(f'reqid:{reqid} Second conversion attempt failed, ffmpeg_cmd: {towav_cmd_fallback} trying with alternative approach: {towav_result_fallback.stderr[:500]}')
                
                # 第三次尝试：先尝试使用不同的解码器或方法
                # 使用 -f wav 强制输出格式，并尝试跳过有问题的帧
                towav_cmd_third = (
                    f'ffmpeg -y {format_args}-i {input_path} '
                    f'-vn '  # 忽略视频流
                    f'-map 0:a:0 '  # 明确映射第一个音频流
                    f'-err_detect ignore_err '
                    f'-fflags +genpts+discardcorrupt+igndts '
                    f'-strict -2 '  # 允许非标准格式
                    f'-f wav '  # 强制输出格式为 WAV
                    f'-acodec pcm_s32le '
                    f'-ar 44100 -ac 2 '
                    f'-threads {work_num} '
                    f'-loglevel warning '
                    f'{output_path}'
                )
                
                towav_result_third = subprocess.run(
                    towav_cmd_third, 
                    shell=True, 
                    capture_output=True, 
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=300
                )
                
                # 如果第三次也失败，尝试第四次：使用更激进的方法处理损坏的AAC
                if towav_result_third.returncode != 0:
                    logger.warning(f'reqid:{reqid} Third conversion attempt failed, trying with most aggressive approach: {towav_result_third.stderr[:500]}')
                    
                    # 第四次尝试：使用最激进的方法
                    # 1. 尝试使用不同的音频流（如果有多个）
                    # 2. 使用更激进的错误处理
                    # 3. 尝试跳过损坏的帧
                    towav_cmd_fourth = (
                        f'ffmpeg -y {format_args}-i {input_path} '
                        f'-vn '  # 忽略视频流
                        f'-err_detect ignore_err+aggressive '  # 更激进的错误检测
                        f'-fflags +genpts+discardcorrupt+igndts+ignidx '  # 丢弃损坏的帧和索引
                        f'-strict -2 '  # 允许非标准格式
                        f'-f wav '  # 强制输出格式为 WAV
                        f'-acodec pcm_s32le '  # 使用PCM编码
                        f'-ar 44100 -ac 2 '  # 设置采样率和声道
                        f'-threads {work_num} '
                        f'-loglevel error '  # 只显示错误，减少日志噪音
                        f'{output_path}'
                    )
                    
                    towav_result_fourth = subprocess.run(
                        towav_cmd_fourth, 
                        shell=True, 
                        capture_output=True, 
                        text=True,
                        encoding='utf-8',
                        errors='ignore',
                        timeout=300
                    )
                    
                    # 如果第四次也失败，尝试第五次：使用两步转换法
                    if towav_result_fourth.returncode != 0:
                        logger.warning(f'reqid:{reqid} Fourth conversion attempt failed, trying two-step conversion: {towav_result_fourth.stderr[:500]}')
                        
                        # 第五次尝试：两步转换法
                        # 第一步：先提取为原始PCM，绕过AAC解码器
                        intermediate_pcm = f'{output_path}.tmp.pcm'
                        towav_cmd_step1 = (
                            f'ffmpeg -y {format_args}-i {input_path} '
                            f'-vn '
                            f'-err_detect ignore_err+aggressive '
                            f'-fflags +genpts+discardcorrupt+igndts+ignidx '
                            f'-strict -2 '
                            f'-f s32le '  # 直接输出为原始PCM
                            f'-acodec pcm_s32le '
                            f'-ar 44100 -ac 2 '
                            f'-threads {work_num} '
                            f'-loglevel error '
                            f'{intermediate_pcm}'
                        )
                        
                        step1_result = subprocess.run(
                            towav_cmd_step1, 
                            shell=True, 
                            capture_output=True, 
                            text=True,
                            encoding='utf-8',
                            errors='ignore',
                            timeout=300
                        )
                        
                        # 如果第一步成功，进行第二步：将PCM转换为WAV
                        if step1_result.returncode == 0 and os.path.exists(intermediate_pcm):
                            towav_cmd_step2 = (
                                f'ffmpeg -y -f s32le -ar 44100 -ac 2 -i {intermediate_pcm} '
                                f'-f wav '
                                f'-acodec pcm_s32le '
                                f'-ar 44100 -ac 2 '
                                f'{output_path}'
                            )
                            
                            step2_result = subprocess.run(
                                towav_cmd_step2, 
                                shell=True, 
                                capture_output=True, 
                                text=True,
                                encoding='utf-8',
                                errors='ignore',
                                timeout=300
                            )
                            
                            # 清理临时文件
                            if os.path.exists(intermediate_pcm):
                                try:
                                    os.remove(intermediate_pcm)
                                except:
                                    pass
                            
                            if step2_result.returncode == 0:
                                # 验证输出文件
                                if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                                    return 0, 'ok'
                        
                        # 如果所有方法都失败，尝试检查是否有其他音频流
                        logger.warning(f'reqid:{reqid} Trying to find alternative audio streams')
                        probe_cmd = f'ffprobe -v error -select_streams a -show_entries stream=index,codec_name -of json {input_path}'
                        probe_result = subprocess.run(
                            probe_cmd,
                            shell=True,
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='ignore',
                            timeout=30
                        )
                        
                        if probe_result.returncode == 0:
                            try:
                                probe_data = json.loads(probe_result.stdout)
                                streams = probe_data.get('streams', [])
                                if len(streams) > 1:
                                    # 尝试使用第二个音频流
                                    logger.info(f'reqid:{reqid} Found {len(streams)} audio streams, trying stream 1')
                                    towav_cmd_alt_stream = (
                                        f'ffmpeg -y {format_args}-i {input_path} '
                                        f'-vn '
                                        f'-map 0:a:1 '  # 使用第二个音频流
                                        f'-err_detect ignore_err+aggressive '
                                        f'-fflags +genpts+discardcorrupt+igndts+ignidx '
                                        f'-strict -2 '
                                        f'-f wav '
                                        f'-acodec pcm_s32le '
                                        f'-ar 44100 -ac 2 '
                                        f'-threads {work_num} '
                                        f'-loglevel error '
                                        f'{output_path}'
                                    )
                                    
                                    alt_stream_result = subprocess.run(
                                        towav_cmd_alt_stream, 
                                        shell=True, 
                                        capture_output=True, 
                                        text=True,
                                        encoding='utf-8',
                                        errors='ignore',
                                        timeout=300
                                    )
                                    
                                    if alt_stream_result.returncode == 0:
                                        if os.path.exists(output_path) and os.path.getsize(output_path) >= 100:
                                            return 0, 'ok'
                            except:
                                pass
                        
                        # 所有方法都失败
                        return 1, f'Error converting audio to wav (all attempts failed). First error: {towav_result.stderr[:500]}, Second error: {towav_result_fallback.stderr[:500]}, Third error: {towav_result_third.stderr[:500]}, Fourth error: {towav_result_fourth.stderr[:500]}'
        
        # 验证输出文件是否存在且有效
        if not os.path.exists(output_path):
            return 1, f'Output WAV file was not created: {output_path}'
        
        # 检查文件大小（至少应该有一些数据）
        if os.path.getsize(output_path) < 100:  # 小于100字节可能有问题
            return 1, f'Output WAV file is too small (possibly corrupted): {output_path}'
            
        return 0, 'ok'
    except subprocess.TimeoutExpired:
        return 1, f'Error converting audio to wav: Timeout after 5 minutes'
    except Exception as e:
        return 1, f'Error converting audio to wav: {str(e)}'

def convert_wav_to_mp3(input_wav_path: str, output_mp3_path: str, reqid: str, 
                       prefer_vbr: bool = False, bitrate: str = '128k') -> tuple[int, str]:
    """
    将WAV文件转换为MP3文件
    
    Args:
        input_wav_path: 输入WAV文件路径
        output_mp3_path: 输出MP3文件路径
        reqid: 请求ID用于日志
        prefer_vbr: 是否优先使用VBR编码（如果失败会自动回退到CBR）
        bitrate: CBR编码的比特率（默认128k）
    
    Returns:
        (ret, msg): ret=0表示成功，ret=1表示失败
    """
    try:
        # 如果优先使用VBR，先尝试VBR编码
        if prefer_vbr:
            convert_cmd = (
                f'ffmpeg -y -i {input_wav_path} '
                f'-codec:a libmp3lame -qscale:a 2 '
                f'-loglevel error '
                f'{output_mp3_path}'
            )
            
            convert_result = subprocess.run(
                convert_cmd, 
                shell=True, 
                capture_output=True, 
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=300  # 5分钟超时
            )
            
            # 如果VBR成功，直接返回
            if convert_result.returncode == 0:
                logger.debug(f'reqid:{reqid} Successfully converted WAV to MP3 using VBR: {output_mp3_path}')
                return 0, 'ok'
            
            # VBR失败，记录警告并回退到CBR
            logger.warning(f'reqid:{reqid} VBR encoding failed, falling back to CBR: {convert_result.stderr[:200]}')
        
        # 使用CBR编码（更稳定，避免VBR断言问题）
        convert_cmd = (
            f'ffmpeg -y -i {input_wav_path} '
            f'-codec:a libmp3lame -b:a {bitrate} '
            f'-ar 44100 -ac 2 '
            f'-loglevel error '
            f'{output_mp3_path}'
        )
        
        convert_result = subprocess.run(
            convert_cmd, 
            shell=True, 
            capture_output=True, 
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=300  # 5分钟超时
        )
        
        if convert_result.returncode != 0:
            return 1, f'Error converting WAV to MP3: {convert_result.stderr}'
        
        logger.debug(f'reqid:{reqid} Successfully converted WAV to MP3 using CBR: {output_mp3_path}')
        return 0, 'ok'
        
    except subprocess.TimeoutExpired:
        return 1, 'Error converting WAV to MP3: Timeout after 5 minutes'
    except Exception as e:
        return 1, f'Error converting WAV to MP3: {str(e)}'

def process_voice_cover(
    hubert_model,
    input_audio_path: str,
    model_file_path: str,
    model_index_path: str,
    modelgender: str,
    upgender: str,
    device: str,
    is_half: bool,
    rmvpe_model,
    reqid: str
) -> tuple[int, str, tuple[np.ndarray, int]]:
    ret = 0
    msg = 'ok'

    audio_opt = None
    tgt_sr = 44100
    net_g = None
    pipeline = None

    # 检查CUDA健康状态
    if not check_cuda_health(reqid):
        return 1, 'CUDA health check failed', (tgt_sr, None)

    try:
        # 确保CUDA状态清洁
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        
        # 加载声音模型
        logger.info(f'process_voice_cover, reqid:{reqid} model_file_path:{model_file_path} device:{device} is_half:{is_half} rmvpe_model:{rmvpe_model}')
        net_g, pipeline, model_tgt_sr, if_f0, version = load_convert_model(model_file_path, device, is_half, rmvpe_model)

        # 设置处理参数
         #sid 模型名
        sid = 0 # 说话人id，目前都为0
        f0_file = None # F0曲线文件，可选, 一行一个音高, 代替默认F0及升降调， 目前为空
        
        f0method = 'rmvpe' # 选择音高提取算法,输入歌声可用pm提速,harvest低音好但巨慢无比,crepe效果好但吃GPU,rmvpe效果最好且微吃GPU，目前为rmvpe
        #file_index1 = '' # 特征检索库文件路径,为空则使用下拉的选择结果
        #file_index2 = '' # 自动检测index路径,下拉式选择(dropdown)
        index_rate = 0.9  # 检索特征占比
        #model_index_path  对应 file_index(file_index1, file_index2)
        filter_redius = 3 # >=3则使用对harvest音高识别的结果使用中值滤波，数值为滤波半径，使用可以削弱哑音
        resample_sr = 0 # 后处理重采样至最终采样率，0为不进行重采样
        rms_mix_rate = 0.95  # 输入源音量包络替换输出音量包络融合比例，越靠近1越使用输出包络
        protect = 0.33  # 保护清辅音和呼吸声，防止电音撕裂等artifact，拉满0.5不开启，调低加大保护力度但可能降低索引效果

        # 变调处理， 根据上传者性别和声音模型性别，确定变调半音数
        # 上传性别为女，声音模型性别为男，则变调半音数为 -12; 上传性别为男，声音模型性别为女，则变调半音数为 12; 
        f0_up_key = 0 # 对应 vc_transform = 0， 变调半音数(整数, 半音数量, 升八度12降八度-12),  需细化
        if upgender == 'female' and modelgender == 'male':
            f0_up_key = -12
        elif upgender == 'male' and modelgender == 'female':
            f0_up_key = 12
        # 加载并预处理音频
        logger.info(f'process_voice_cover, reqid:{reqid} input_audio_path:{input_audio_path}, tgt_sr:{tgt_sr}, resample_sr:{resample_sr}, rms_mix_rate:{rms_mix_rate}, version:{version}, protect:{protect}, f0_file:{f0_file}, f0_up_key:{f0_up_key}, f0method:{f0method}, index_rate:{index_rate}, filter_redius:{filter_redius}')
        audio = load_audio(input_audio_path, 16000)
        audio_max = np.abs(audio).max() / 0.95
        if audio_max > 1.0:
            audio /= audio_max

        # 确保CUDA同步
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        # 处理音频
        times = [0, 0, 0]
        #logger.info(f'process_voice_cover 11, reqid:{reqid} sid:{sid}, input_audio_path:{input_audio_path}, times:{times}, f0_up_key:{f0_up_key}, f0method:{f0method}')
        #logger.info(f'process_voice_cover 22, model_index_path:{model_index_path}, index_rate:{index_rate}, if_f0:{if_f0}, filter_redius:{filter_redius}')
        #logger.info(f'process_voice_cover 33, tgt_sr:{model_tgt_sr}, resample_sr:{resample_sr}, rms_mix_rate:{rms_mix_rate}, version:{version}, protect:{protect}, f0_file:{f0_file}')
        #logger.info(f'process_voice_cover 44, pipeline:{pipeline}')
        audio_opt = pipeline.pipeline(
            hubert_model, net_g, sid, audio, input_audio_path, times, f0_up_key,
            f0method, model_index_path, index_rate, if_f0, filter_redius,
            model_tgt_sr, resample_sr, rms_mix_rate, version, protect, f0_file
        )

        # 确保处理完成后同步
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        # 更新采样率
        if (resample_sr >= 16000) and (model_tgt_sr != resample_sr):
            tgt_sr = resample_sr
        else:
            tgt_sr = model_tgt_sr

        # 确保音频数据格式正确
        #if isinstance(audio_opt, torch.Tensor):
        #    audio_opt = audio_opt.cpu().numpy()
        #audio_opt = audio_opt.astype(np.float32)

    except Exception as e:
        ret = 1
        msg = f'Error processing voice cover: {str(e)}'
        logger.error(f'reqid:{reqid} msg: {msg}')
        # 记录详细的错误信息
        logger.error(f'reqid:{reqid} Full traceback: {traceback.format_exc()}')
        # 修复：返回格式应该与正常情况一致，而不是返回字符串
        return ret, msg, (tgt_sr, None)
    finally:
        # 安全地卸载模型并释放资源
        try:
            if net_g is not None and pipeline is not None:
                unload_convert_model(net_g, pipeline)
        except Exception as cleanup_error:
            logger.warning(f'reqid:{reqid} Model cleanup failed: {cleanup_error}')

        # 安全地清理CUDA资源
        try:
            if torch.cuda.is_available():
                torch.cuda.synchronize()  # 确保所有CUDA操作完成
                torch.cuda.empty_cache()
        except Exception as cuda_cleanup_error:
            logger.warning(f'reqid:{reqid} CUDA cleanup failed: {cuda_cleanup_error}')
        
        # 强制垃圾回收
        gc.collect()

    return ret, msg, (tgt_sr, audio_opt)

@celery_app.task(name='task_cover.cover')
def gpu_cover(modelcat, modelname, modelgender, upfileinfo, upgender, task_id='0', email='', domain='', lang='english'):                        
    set_reqid(task_id)
    try:
        ret = 0
        msg = 'ok'
        start_time = time.time()
        timing_stats = {}  # 用于记录各阶段耗时
        localtime = time.localtime(start_time)
        today =  time.strftime(DAY_FORMAT, localtime)
        datetime = time.strftime('%Y%m%d_%H%M%S', localtime)
        random_number = random.randint(1, 1000)

        reqid = task_id

        # 检查CUDA健康状态
        if not check_cuda_health(reqid):
            msg = 'CUDA health check failed'
            logger.error(f'task_id:{task_id} email:{email} {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'cover process failed, task_id:{task_id}, msg:CUDA health check failed'

        model_validation_start = time.time()
        model_path = f'{model_dir}/{lang}/{modelcat}/{modelname}'
        model_index_path = f'{model_path}/{modelname}_v2.index'
        model_file_path = f'{model_path}/{modelname}_weights.pth'
        if not (os.path.exists(model_index_path) and os.path.exists(model_file_path)):
            msg = f'model {modelcat}-{modelname} is not exists, task_id:{task_id}'
            logger.warning(f'{model_index_path} or {model_file_path} is not exists, {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'cover process failed, task_id:{task_id}, msg:{msg}'
        timing_stats['model_validation'] = time.time() - model_validation_start

        relative_path = upfileinfo["relative_path"]
        update_str = upfileinfo['update_str']
        file_ext = upfileinfo['file_ext']
        uploadtime = upfileinfo['uploadtime']
        userip = upfileinfo["userip"]
        randomnum = f'{upfileinfo["randomnum"]}{random_number}'

        # extract vocals
        vocals_extraction_start = time.time()
        logger.info(f'*********** reqid:{reqid} extract_vocals start ***********')
        ret, msg, extractinfo = extract_vocals(upfileinfo, reqid)
        if ret != 0:
            logger.error(f'task_id:{task_id} email:{email} extract_vocals failed: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'cover process failed, task_id:{task_id}, msg:{msg}'
        timing_stats['vocals_extraction'] = time.time() - vocals_extraction_start
        logger.info(f'*********** reqid:{reqid} extract_vocals end ***********')

        # 验证 extractinfo 是否包含必需的键
        required_keys = ['deecho_vocals', 'bsroformer_instrument']
        is_valid, validation_msg = validate_extractinfo(extractinfo, required_keys, reqid)
        if not is_valid:
            logger.error(f'task_id:{task_id} email:{email} extractinfo validation failed: {validation_msg}')
            update_task_status(logger, task_id, domain, validation_msg, TaskStatus.FAILED)
            return f'cover process failed, task_id:{task_id}, msg:{validation_msg}'

        # 最终提取出来的人声
        final_vocals_path = extractinfo['deecho_vocals']
        # 各个阶段分理出的伴奏音
        bsroformer_instrument = extractinfo['bsroformer_instrument']

        input_audio_path  = final_vocals_path # 对应input_audio，输入待处理音频文件路径
        try :
            # 处理音频转换
            voice_conversion_start = time.time()
            logger.info(f'reqid:{reqid} process_voice_cover start, input_audio_path:{input_audio_path}, model_file_path:{model_file_path}, model_index_path:{model_index_path}')
            ret, msg, (tgt_sr, audio_opt) = process_voice_cover(
                hubert_model=hubert_model,
                input_audio_path=input_audio_path,
                model_file_path=model_file_path,
                model_index_path=model_index_path,
                modelgender=modelgender,
                upgender=upgender,
                device=device,
                is_half=is_half,
                rmvpe_model=rmvpe_model,
                reqid=reqid
            )
            timing_stats['voice_conversion'] = time.time() - voice_conversion_start

            if ret != 0:
                logger.error(f'task_id:{task_id} email:{email} process_voice_cover failed: {msg}')
                update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                return f'cover process failed, task_id:{task_id}, msg:{msg}';

            # 保存转换后的人声
            converted_vocals_dir = f'data/middle/{update_str}/{userip}'
            converted_vocals_filename = f'{uploadtime}_{userip}_{randomnum}_converted_vocals.wav'
            if not os.path.exists(converted_vocals_dir):
                os.makedirs(converted_vocals_dir, exist_ok=True)
            converted_vocals_path = f'{converted_vocals_dir}/{converted_vocals_filename}'

            wavfile.write(converted_vocals_path, int(tgt_sr), audio_opt)

            # 混合转换后的人声和原始伴奏
            output_audio_dir = f'{finaldata_dir}/{update_str}'
            if not os.path.exists(output_audio_dir):
                os.makedirs(output_audio_dir, exist_ok=True)

            output_audio_filename = f'{uploadtime}_{userip}_{randomnum}_cover.mp3'
            output_audio_path = f'{output_audio_dir}/{output_audio_filename}'

            # 混音处理
            mixing_start = time.time()
            mix_cmd = (f'ffmpeg -y '
                      f'-i {converted_vocals_path} '
                      f'-i {bsroformer_instrument} '
                      f'-filter_complex "[0:a]volume=1.0[v0];'
                      f'[1:a]volume=0.95[v1];'
                      f'[v0][v1]amix=inputs=2:duration=longest,aresample=44100[aout]" '
                      f'-map "[aout]" '
                      f'-codec:a libmp3lame -b:a 128k '
                      f'-ar 44100 -ac 2 '
                      f'{output_audio_path}')

            mix_result = subprocess.run(
                mix_cmd, 
                shell=True, 
                capture_output=True, 
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            timing_stats['audio_mixing'] = time.time() - mixing_start

            if mix_result.returncode != 0:
                ret = 1
                msg = f'Error mixing and encoding audio: {mix_result.stderr}'
                logger.error(f'task_id:{task_id} email:{email} msg: {msg}')
                update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                return f'cover process failed, task_id:{task_id}, msg:{msg}';

            logger.info(f'reqid:{reqid} Successfully created mp3 file: {output_audio_path}')
        except Exception as e:
            ret = 1
            msg = f'Error processing audio: {str(e)}'
            logger.error(f'task_id:{task_id} email:{email} msg: {msg}, error:{e}')
            # 记录详细的错误信息
            logger.error(f'task_id:{task_id} Full traceback: {traceback.format_exc()}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'cover process failed, task_id:{task_id}, msg:{msg}';
        finally:
            # 安全地清理CUDA资源
            try:
                if torch.cuda.is_available():
                    torch.cuda.synchronize()  # 确保所有CUDA操作完成
                    torch.cuda.empty_cache()
            except Exception as cuda_cleanup_error:
                logger.warning(f'task_id:{task_id} CUDA cleanup failed: {cuda_cleanup_error}')

            # 强制垃圾回收
            gc.collect()

        remote_cover_dir = get_remote_cover_dir()
        target_dir = f'{remote_cover_dir}/{update_str}'
        delivery_transport = get_file_sync_transport()
        proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
        sync_job = SyncJob(
            source_file=output_audio_path,
            target_dir=target_dir,
            task_id=task_id,
            short_uri_prefix=f'/final/{update_str}',
            output_file=output_audio_filename,
            proxies=list(proxies),
            transport=delivery_transport,
            biz_type="cover",
            domain=domain,
        )
        enqueue_start = time.time()
        if not enqueue_delivery_job(logger, sync_job):
            sync_msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}'
            logger.error(f'task_id:{task_id} email:{email} {sync_msg}')
            update_task_status(logger, task_id, domain, sync_msg, TaskStatus.FAILED)
            return f'file sync failed, task_id:{task_id}, msg:{sync_msg}'
        timing_stats['file_sync_enqueue'] = time.time() - enqueue_start

        end_time = time.time()
        total_time = end_time - start_time
        timing_stats['total'] = total_time
        logger.info(
            f"/coverapi/cover worker completed (awaiting delivery), reqid:{reqid}, timings: "
            f"[total:{timing_stats['total']:.2f}s, "
            f"request_validation:{timing_stats.get('request_validation', 0):.2f}s, "
            f"model_validation:{timing_stats.get('model_validation', 0):.2f}s, "
            f"url_validation:{timing_stats.get('url_validation', 0):.2f}s, "
            f"vocals_extraction:{timing_stats.get('vocals_extraction', 0):.2f}s, "
            f"voice_conversion:{timing_stats.get('voice_conversion', 0):.2f}s, "
            f"audio_mixing:{timing_stats.get('audio_mixing', 0):.2f}s], "
            f"email:{email} ret:{ret} transport:{delivery_transport}"
        )

        result = f"CoverProcessed: {task_id}"
        return result
    finally:
        clear_reqid()
@celery_app.task(name='task_cover.vocal')
def gpu_vocal(upfileinfo, task_id='0', email='', domain=''):                        
    set_reqid(task_id)
    try:
        ret = 0
        msg = 'ok'
        start_time = time.time()
        timing_stats = {}  # 用于记录各阶段耗时
        localtime = time.localtime(start_time)
        today =  time.strftime(DAY_FORMAT, localtime)
        datetime = time.strftime('%Y%m%d_%H%M%S', localtime)
        random_number = random.randint(1, 1000)

        reqid = task_id
        relative_path = upfileinfo["relative_path"]
        update_str = upfileinfo['update_str']
        file_ext = upfileinfo['file_ext']
        uploadtime = upfileinfo['uploadtime']
        userip = upfileinfo["userip"]
        randomnum = f'{upfileinfo["randomnum"]}{random_number}'

        # extract vocals
        vocals_extraction_start = time.time()
        logger.info(f'*********** reqid:{reqid} extract_vocals start ***********')
        ret, msg, extractinfo = extract_vocals(upfileinfo, reqid)
        if ret != 0:
            logger.error(f'task_id:{task_id} email:{email} extract_vocals failed: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'vocal process failed, task_id:{task_id}, msg:{msg}';
        timing_stats['vocals_extraction'] = time.time() - vocals_extraction_start
        logger.info(f'*********** reqid:{reqid} extract_vocals end ***********')

        # 最终提取出来的人声
        wav_vocal_path = extractinfo['deecho_vocals']

         # 创建保存MP3文件的目录
        sepvocal_dir = f'data/sepvocal/{update_str}'
        os.makedirs(sepvocal_dir, exist_ok=True)

        # 转换WAV到MP3
        final_vocal_path = f'{sepvocal_dir}/{uploadtime}_{userip}_{randomnum}_vocal.mp3'

        try:
            ret, msg = convert_wav_to_mp3(wav_vocal_path, final_vocal_path, reqid, prefer_vbr=False)
            if ret != 0:
                logger.error(f'task_id:{task_id} email:{email}, msg: {msg}')
                update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                return f'vocal process failed, task_id:{task_id}, msg:{msg}';

            logger.info(f'task_id:{task_id} email:{email} Successfully converted WAV to MP3: {final_vocal_path}')
        except Exception as e:
            ret = 1
            msg = f'Error converting WAV to MP3: {str(e)}'
            logger.error(f'task_id:{task_id} email:{email}, msg: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'vocal process failed, task_id:{task_id}, msg:{msg}';

        remote_vocal_dir = get_remote_vocal_dir()
        target_dir = f'{remote_vocal_dir}/{update_str}'
        vocal_filename = f'{uploadtime}_{userip}_{randomnum}_vocal.mp3'
        delivery_transport = get_file_sync_transport()
        proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
        sync_job = SyncJob(
            source_file=final_vocal_path,
            target_dir=target_dir,
            task_id=task_id,
            short_uri_prefix=f'/sepvocal/{update_str}',
            output_file=vocal_filename,
            proxies=list(proxies),
            transport=delivery_transport,
            biz_type="extract_vocal",
            domain=domain,
        )
        if not enqueue_delivery_job(logger, sync_job):
            sync_msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}'
            logger.error(f'task_id:{task_id} email:{email} {sync_msg}')
            update_task_status(logger, task_id, domain, sync_msg, TaskStatus.FAILED)
            return f'file sync failed, task_id:{task_id}, msg:{sync_msg}'
        logger.info(
            f'task_id:{task_id} vocal sync enqueued transport={delivery_transport}'
        )

        result = f"Vocal Processed: {task_id}"
        return result
    finally:
        clear_reqid()
@celery_app.task(name='task_cover.instrument')
def gpu_instrument(upfileinfo, task_id='0', email='', domain=''):                        
    set_reqid(task_id)
    try:
        ret = 0
        msg = 'ok'
        start_time = time.time()
        timing_stats = {}  # 用于记录各阶段耗时
        localtime = time.localtime(start_time)
        today =  time.strftime(DAY_FORMAT, localtime)
        datetime = time.strftime('%Y%m%d_%H%M%S', localtime)
        random_number = random.randint(1, 1000)    

        reqid = task_id
        relative_path = upfileinfo["relative_path"]
        update_str = upfileinfo['update_str']
        file_ext = upfileinfo['file_ext']
        uploadtime = upfileinfo['uploadtime']
        userip = upfileinfo["userip"]
        randomnum = f'{upfileinfo["randomnum"]}{random_number}'

         # extract vocals
        instruments_extraction_start = time.time()
        logger.info(f'*********** task_id:{task_id} email:{email} extract_instruments, upfileinfo:{upfileinfo} ***********')
        ret, msg, extractinfo = extract_instruments(upfileinfo, reqid)
        timing_stats['instruments_extraction'] = time.time() - instruments_extraction_start
        logger.info(f'*********** task_id:{task_id} email:{email} extract_instruments end ***********')
        # 示例：data/uploads/2025-02-22/202502221755042_83.229.17.48_lastnight-morgan-male.mp3
        if ret != 0:
            logger.error(f'task_id:{task_id} email:{email}, extract_instruments failed: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'instrument process failed, task_id:{task_id}, msg:{msg}';

        wav_vocal_path = extractinfo['bsroformer_instrument']

        # 创建保存MP3文件的目录
        sepinstrum_dir = f'data/sepinstrum/{update_str}'
        os.makedirs(sepinstrum_dir, exist_ok=True)

        # 转换WAV到MP3
        final_instrum_path = f'{sepinstrum_dir}/{uploadtime}_{userip}_{randomnum}_instrum.mp3'

        try:
            ret, msg = convert_wav_to_mp3(wav_vocal_path, final_instrum_path, reqid, prefer_vbr=False)
            if ret != 0:
                logger.error(f'task_id:{task_id} email:{email}, msg: {msg}')
                update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                return f'instrument process failed, task_id:{task_id}, msg:{msg}';

            logger.info(f'task_id:{task_id} email:{email} Successfully converted WAV to MP3: {final_instrum_path}')
        except Exception as e:
            ret = 1
            msg = f'Error converting WAV to MP3: {str(e)}'
            logger.error(f'task_id:{task_id} email:{email}, msg: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return f'instrument process failed, task_id:{task_id}, msg:{msg}';

        remote_instrument_dir = get_remote_instrument_dir()
        target_dir = f'{remote_instrument_dir}/{update_str}'
        instrum_filename = f'{uploadtime}_{userip}_{randomnum}_instrum.mp3'
        delivery_transport = get_file_sync_transport()
        proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
        sync_job = SyncJob(
            source_file=final_instrum_path,
            target_dir=target_dir,
            task_id=task_id,
            short_uri_prefix=f'/sepinstrum/{update_str}',
            output_file=instrum_filename,
            proxies=list(proxies),
            transport=delivery_transport,
            biz_type="extract_instrument",
            domain=domain,
        )
        if not enqueue_delivery_job(logger, sync_job):
            sync_msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}'
            logger.error(f'task_id:{task_id} email:{email} {sync_msg}')
            update_task_status(logger, task_id, domain, sync_msg, TaskStatus.FAILED)
            return f'file sync failed, task_id:{task_id}, msg:{sync_msg}'
        logger.info(
            f'task_id:{task_id} instrument sync enqueued transport={delivery_transport}'
        )

        result = f"Instrument Processed: {task_id}"
        return result
    finally:
        clear_reqid()
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
        transports["quic"] = QuicHttpTransport(biz_type="cover")

    try:
        start_file_delivery(logger, transports)
    except Exception as start_err:
        logger.error(f"start_file_delivery failed: {start_err}")


initialize_file_delivery()