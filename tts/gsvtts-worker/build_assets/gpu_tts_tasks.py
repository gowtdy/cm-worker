#!/usr/bin/env python3

# CUDA调试建议：
# 如果遇到CUDA错误，可以设置以下环境变量来帮助调试：
# export CUDA_LAUNCH_BLOCKING=1  # 同步CUDA操作，便于定位错误
# export TORCH_USE_CUDA_DSA=1    # 启用设备端断言
# export CUDA_VISIBLE_DEVICES=0  # 指定使用的GPU设备

import sys
import os

# 将 voice_train/text 目录添加到 sys.path，使 text 可以作为顶层模块导入
# 这必须在所有其他导入之前执行
_app_dir = os.path.dirname(os.path.abspath(__file__))
_voice_train_dir = os.path.join(_app_dir, 'voice_train')
if _voice_train_dir not in sys.path:
    sys.path.insert(0, _voice_train_dir)

import time
import random
import logging
import re
import torch

import datetime
import queue
import gc
from time import time as ttime
from io import BytesIO
from transformers import BarkModel
from transformers import AutoProcessor
from transformers import set_seed

import numpy as np
import hashlib
import yaml
import hmac

import argparse
import librosa
from pydub import AudioSegment
from langdetect import detect, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

from utility.load_model import load_bert_model, load_ssl_model, DictToAttrRecursive
from utility.util import  (gen_res_dict, get_first, is_whole_sentence,
        split, get_phones_and_bert, merge_short_text_in_array, get_spepc, splits,
        cut5, cut_custom, split_long_sentence, verify_signature, process_text, get_pause_duration,
        get_all_pause_durations)
from utility.lang_util import (language_map, bark_support_langs, bark_model_dict, \
        fish_support_langs)
import scipy.io.wavfile as wavfile
from tools.i18n.i18n import I18nAuto
from tools.llama.load_llama_model import load_llama_model, generate_long
from tools.vqgan.load_vqgan_model import load_vqgan_model, decode_vq_tokens
from voice_train.module.models import SynthesizerTrn
from voice_train.AR.models.t2s_lightning_module import Text2SemanticLightningModule
from voice_train.process_ckpt import load_sovits_new, get_sovits_version_from_path_fast

from fish_speech.text.chn_text_norm.text import Text as ChnNormedText

from utility.voice_config import (
    get_device, ishalf, get_dtype,
    get_file_sync_transport, get_quic_client_host, get_quic_client_port,
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

from voice_train.sv import SV
from config.model_conf import get_model_conf
#from indextts_worker import load_indextts_model, indextts_infer
from utility.log import logset, setup_logger, set_reqid, clear_reqid
from utility.voice_config import get_localnet_video_tts_dir, get_remote_ttssave_dir

# 设置CUDA环境变量以提高稳定性
os.environ.setdefault('CUDA_LAUNCH_BLOCKING', '0')  # 默认异步，提高性能
os.environ.setdefault('TORCH_USE_CUDA_DSA', '0')    # 默认关闭设备端断言
# 设置PyTorch内存分配器以减少内存碎片化
os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')

# 创建日志记录器 - 移到文件前面
LOG_FORMAT = "%(asctime)s-%(filename)s[line:%(lineno)d]-%(levelname)s: %(message)s"

# 自定义过滤器，根据模块名称过滤日志
class ModuleFilter(logging.Filter):
    def __init__(self, module_name):
        super().__init__()
        self.module_name = module_name

    def filter(self, record):
        return record.name == self.module_name

#log_dir = '/app/log'
#os.makedirs(log_dir, exist_ok=True)
# 创建日志记录器
#logger = logging.getLogger()
#logger.setLevel(logging.DEBUG)

# 创建处理器sounds_effect，记录来自 sounds_effect 的日志
#handler = logging.FileHandler('tts_worker_log.txt')
#handler.setLevel(logging.DEBUG)
#handler.addFilter(ModuleFilter('tts_worker'))
#formatter = logging.Formatter(LOG_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
#handler.setFormatter(formatter)

# 将处理器添加到日志记录器
#logger.addHandler(handler)

logger = setup_logger(
    logger_name="tts_worker",
    log_file="/app/log/tts_worker_test.log",
    console_level="DEBUG",
    file_level="DEBUG",
)

cnhubert_base_path = get_model_conf('cnhubert_model_path')
bert_path = get_model_conf('bert_model_path')

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'
wav_output_dir = "output"

# 定义一个默认的比特率（例如：256k）
DEFAULT_BITRATE = "128k"
DEFAULT_FORMAT = "mp3"
MAX_TEXT_LENGTH = 500
MAX_FREE_TRIES = 7

# 当 email 为 VIDEO_SCRIPT_TTS 时， 标示是局域网内为视频生成 tts 任务
VIDEO_SCRIPT_TTS = "video_script_tts"

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
DAY_FORMAT = '%Y-%m-%d'

indextts_model = None

# 安全的CUDA初始化函数
def safe_cuda_init():
    """安全地初始化CUDA环境"""
    try:
        if torch.cuda.is_available():
            # 设置CUDA设备
            torch.cuda.set_device(0)  # 使用第一个GPU
            # 同步CUDA操作
            torch.cuda.synchronize()
            # 清理缓存
            torch.cuda.empty_cache()
            logger.info(f'CUDA initialized successfully. Device: {torch.cuda.get_device_name()}')
            return True
        else:
            logger.warning('CUDA is not available')
            return False
    except Exception as e:
        logger.error(f'Failed to initialize CUDA: {e}')
        return False

def check_cuda_status():
    """检查CUDA状态并记录详细信息"""
    try:
        if not torch.cuda.is_available():
            logger.warning('CUDA is not available')
            return False
            
        # 获取设备信息
        device_name = torch.cuda.get_device_name()
        device_count = torch.cuda.device_count()
        current_device = torch.cuda.current_device()
        
        # 获取内存信息
        total_memory = torch.cuda.get_device_properties(current_device).total_memory
        allocated_memory = torch.cuda.memory_allocated(current_device)
        cached_memory = torch.cuda.memory_reserved(current_device)
        free_memory = total_memory - allocated_memory
        
        logger.info(f'CUDA Status - Device: {device_name}, Count: {device_count}, '
                   f'Current: {current_device}, Total: {total_memory/1024**3:.2f}GB, '
                   f'Allocated: {allocated_memory/1024**3:.2f}GB, '
                   f'Cached: {cached_memory/1024**3:.2f}GB, '
                   f'Free: {free_memory/1024**3:.2f}GB')
        
        return True
    except Exception as e:
        logger.error(f'Error checking CUDA status: {e}')
        return False

# 初始化CUDA
safe_cuda_init()

# 检查CUDA状态
check_cuda_status()

# CUDA错误处理装饰器
def cuda_error_handler(func):
    """装饰器：处理CUDA相关错误"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RuntimeError as e:
            if "CUDA" in str(e) or "device-side assert" in str(e):
                logger.error(f'CUDA error in {func.__name__}: {e}')
                # 尝试恢复CUDA状态
                try:
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                        torch.cuda.empty_cache()
                        gc.collect()
                except Exception as cleanup_error:
                    logger.error(f'Failed to cleanup after CUDA error: {cleanup_error}')
                # 重新抛出异常
                raise
            else:
                raise
        except Exception as e:
            logger.error(f'Unexpected error in {func.__name__}: {e}')
            raise
    return wrapper

def safe_tensor_to_device(tensor, device, dtype=None):
    """安全地将tensor移动到指定设备"""
    try:
        if tensor is None:
            return None
            
        # 检查tensor的有效性
        if torch.isnan(tensor).any() or torch.isinf(tensor).any():
            logger.warning('Tensor contains NaN or Inf values, attempting to fix')
            tensor = torch.nan_to_num(tensor, nan=0.0, posinf=1.0, neginf=-1.0)
        
        # 转换数据类型
        if dtype is not None and tensor.dtype != dtype:
            tensor = tensor.to(dtype)
        
        # 移动到设备
        tensor = tensor.to(device)
        
        return tensor
    except Exception as e:
        logger.error(f'Error moving tensor to device: {e}')
        raise



import nltk
# 设置 NLTK 数据路径
nltk_data_path = os.environ.get('NLTK_DATA_PATH', '/app/models/nltk_data')
if nltk_data_path not in nltk.data.path:
    nltk.data.path.append(nltk_data_path)

# 验证 NLTK 数据路径设置
print(f"NLTK data paths: {nltk.data.path}")
print(f"NLTK_DATA_PATH environment variable: {os.environ.get('NLTK_DATA_PATH', 'Not set')}")


i18n = I18nAuto()

# 设备相关变量定义 - 在导入之后
is_half = ishalf()
device = get_device()
dtype = get_dtype()
version = 'v2'
sv_model = None

def check_and_wait_for_gpu_memory(min_free_gb=2.0, max_wait_seconds=30):
    """检查GPU内存，如果不足则等待并清理
    
    Args:
        min_free_gb: 最小需要的空闲内存（GB）
        max_wait_seconds: 最大等待时间（秒）
    
    Returns:
        bool: 如果内存足够返回True，否则返回False
    """
    if not torch.cuda.is_available():
        return True
    
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        try:
            gpu_memory = torch.cuda.get_device_properties(device).total_memory
            allocated_memory = torch.cuda.memory_allocated(device)
            cached_memory = torch.cuda.memory_reserved(device)
            free_memory = gpu_memory - cached_memory
            
            if free_memory >= min_free_gb * 1024**3:
                logger.debug(f'GPU memory sufficient. Free: {free_memory/1024**3:.2f}GB')
                return True
            else:
                logger.warning(f'GPU memory insufficient, waiting... Free: {free_memory/1024**3:.2f}GB, Required: {min_free_gb:.2f}GB')
                # 尝试清理内存
                torch.cuda.empty_cache()
                gc.collect()
                time.sleep(2)
        except Exception as e:
            logger.warning(f'Error checking GPU memory: {e}')
            time.sleep(1)
    
    # 超时后再次检查
    try:
        gpu_memory = torch.cuda.get_device_properties(device).total_memory
        cached_memory = torch.cuda.memory_reserved(device)
        free_memory = gpu_memory - cached_memory
        return free_memory >= min_free_gb * 1024**3
    except:
        return False

def manage_gpu_memory():
    """管理GPU内存，清理缓存并检查内存状态"""
    if not torch.cuda.is_available():
        return True
        
    try:
        # 检查CUDA设备状态
        # device 可能是字符串 "cuda:0" 或 torch.device 对象
        if isinstance(device, str) and device.startswith('cuda:'):
            try:
                device_index = int(device.split(':')[1])
                if device_index >= torch.cuda.device_count():
                    logger.warning(f'Device {device} is invalid, skipping GPU memory management')
                    return False
                if torch.cuda.current_device() != device_index:
                    torch.cuda.set_device(device_index)
            except (ValueError, IndexError):
                logger.warning(f'Invalid device format {device}, skipping GPU memory management')
                return False
        elif hasattr(device, 'index'):
            if torch.cuda.current_device() != device.index:
                torch.cuda.set_device(device.index)
        
        # 同步CUDA操作，确保所有操作完成
        torch.cuda.synchronize()
        
        # 安全地清理GPU缓存
        try:
            torch.cuda.empty_cache()
        except RuntimeError as e:
            logger.warning(f'Failed to empty CUDA cache: {e}')
            # 如果清理失败，尝试重置设备
            try:
                if isinstance(device, str) and device.startswith('cuda:'):
                    device_obj = torch.device(device)
                else:
                    device_obj = device
                torch.cuda.reset_peak_memory_stats(device_obj)
            except:
                pass
        
        # 强制垃圾回收
        gc.collect()
        
        # 获取内存信息
        try:
            # 获取设备索引
            if isinstance(device, str) and device.startswith('cuda:'):
                device_index = int(device.split(':')[1])
                device_obj = torch.device(device)
            else:
                device_obj = device
                device_index = device_obj.index if hasattr(device_obj, 'index') else 0
            
            gpu_memory = torch.cuda.get_device_properties(device_obj).total_memory
            allocated_memory = torch.cuda.memory_allocated(device_obj)
            cached_memory = torch.cuda.memory_reserved(device_obj)
            free_memory = gpu_memory - allocated_memory
            
            logger.debug(f'GPU Memory Status - Total: {gpu_memory/1024**3:.2f}GB, '
                        f'Allocated: {allocated_memory/1024**3:.2f}GB, '
                        f'Cached: {cached_memory/1024**3:.2f}GB, '
                        f'Free: {free_memory/1024**3:.2f}GB')
            
            # 如果可用内存不足，强制清理
            if free_memory < 1 * 1024**3:  # 小于1GB时清理
                logger.warning(f'GPU memory critically low, forcing cleanup. Free: {free_memory/1024**3:.2f}GB')
                try:
                    torch.cuda.empty_cache()
                    gc.collect()
                except RuntimeError as e:
                    logger.error(f'Failed to cleanup GPU memory: {e}')
                    return False
                return False
            return True
        except RuntimeError as e:
            logger.error(f'Failed to get GPU memory info: {e}')
            return False
            
    except Exception as e:
        logger.error(f'Error in manage_gpu_memory: {e}')
        return False

# 在加载模型之前验证设备
def validate_device():
    """验证设备是否有效，如果无效则抛出异常"""
    if isinstance(device, str) and device.startswith('cuda:'):
        try:
            device_index = int(device.split(':')[1])
            if not torch.cuda.is_available():
                raise RuntimeError(
                    f"CUDA not available, but device is set to {device}. "
                    f"Please use CPU or ensure CUDA is properly configured."
                )
            elif device_index < 0 or device_index >= torch.cuda.device_count():
                raise ValueError(
                    f"Device {device} is invalid. Available GPUs: {torch.cuda.device_count()} "
                    f"(indices: 0-{torch.cuda.device_count()-1}). "
                    f"Please set GPU_INDEX environment variable to a valid GPU index."
                )
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid device format {device}: {e}")
    elif device == "cpu":
        pass  # CPU is always valid
    else:
        raise ValueError(f"Unknown device type {device}. Expected 'cpu' or 'cuda:N' format.")

def reset_cuda_device(device):
    """尝试重置 CUDA 设备状态"""
    try:
        if isinstance(device, str) and device.startswith('cuda:'):
            device_obj = torch.device(device)
            device_index = int(device.split(':')[1])
        else:
            device_obj = device
            device_index = device_obj.index if hasattr(device_obj, 'index') else 0
        
        # 1. 同步所有 CUDA 操作
        torch.cuda.synchronize(device_obj)
        
        # 2. 清理缓存
        torch.cuda.empty_cache()
        
        # 3. 重置内存统计
        torch.cuda.reset_peak_memory_stats(device_obj)
        
        # 4. 强制垃圾回收
        gc.collect()
        
        # 5. 再次清理缓存
        torch.cuda.empty_cache()
        
        logger.info(f'CUDA device {device_index} reset attempted')
        return True
    except Exception as e:
        logger.error(f'Failed to reset CUDA device: {e}')
        return False

def log_gpu_error_details(device, error):
    """记录 GPU 错误的详细信息"""
    try:
        if isinstance(device, str) and device.startswith('cuda:'):
            device_index = int(device.split(':')[1])
        else:
            device_index = device.index if hasattr(device, 'index') else 0
        
        # 获取 GPU 信息
        if torch.cuda.is_available() and device_index < torch.cuda.device_count():
            props = torch.cuda.get_device_properties(device_index)
            logger.error(f'GPU Error Details:')
            logger.error(f'  Device: {props.name} (Index: {device_index})')
            logger.error(f'  Total Memory: {props.total_memory / 1024**3:.2f}GB')
            
            try:
                allocated = torch.cuda.memory_allocated(device_index)
                reserved = torch.cuda.memory_reserved(device_index)
                logger.error(f'  Allocated: {allocated / 1024**3:.2f}GB')
                logger.error(f'  Reserved: {reserved / 1024**3:.2f}GB')
            except:
                pass
        
        logger.error(f'  Error: {error}')
        logger.error(f'  Suggestion: This may require worker restart or GPU driver reset')
        
    except Exception as e:
        logger.error(f'Failed to log GPU error details: {e}')

def check_gpu_health(device, attempt_recovery=True):
    """检查 GPU 健康状态，可尝试恢复
    
    Args:
        device: GPU 设备
        attempt_recovery: 如果检测到错误，是否尝试恢复
    
    Returns:
        bool: GPU 是否健康
    """
    try:
        if not torch.cuda.is_available():
            return True  # CPU 模式总是健康的
        
        # 获取设备对象
        if isinstance(device, str) and device.startswith('cuda:'):
            device_obj = torch.device(device)
            device_index = int(device.split(':')[1])
        else:
            device_obj = device
            device_index = device_obj.index if hasattr(device_obj, 'index') else 0
        
        # 检查设备是否有效
        if device_index >= torch.cuda.device_count():
            logger.error(f'Invalid device index: {device_index}')
            return False
        
        # 设置当前设备
        torch.cuda.set_device(device_index)
        
        # 创建一个小的测试 tensor 来验证 GPU 是否正常工作
        test_tensor = torch.randn(10, 10, device=device_obj)
        result = test_tensor @ test_tensor.T
        torch.cuda.synchronize(device_obj)
        
        # 清理测试 tensor
        del test_tensor, result
        torch.cuda.empty_cache()
        
        return True
        
    except RuntimeError as e:
        error_str = str(e)
        logger.error(f'GPU health check failed: {error_str}')
        
        # 检查是否是 device-side assert 错误
        if "device-side assert" in error_str.lower():
            logger.critical(f'CUDA device-side assert detected on device {device_index}!')
            
            if attempt_recovery:
                logger.warning('Attempting to recover GPU state...')
                # 尝试重置设备
                if reset_cuda_device(device):
                    # 等待一段时间让设备恢复
                    time.sleep(2)
                    # 再次尝试健康检查（不尝试恢复，避免无限循环）
                    try:
                        test_tensor = torch.randn(5, 5, device=device_obj)
                        torch.cuda.synchronize(device_obj)
                        del test_tensor
                        torch.cuda.empty_cache()
                        logger.info('GPU recovery successful')
                        return True
                    except Exception as retry_e:
                        logger.error(f'GPU recovery failed: {retry_e}')
                        logger.critical('GPU may need process restart or driver reset')
                        return False
                else:
                    logger.critical('Failed to reset CUDA device, GPU may be in unrecoverable state')
                    return False
            else:
                logger.critical('GPU in error state, recovery not attempted')
                return False
        else:
            # 其他 CUDA 错误
            if attempt_recovery:
                logger.warning('Attempting to recover from CUDA error...')
                reset_cuda_device(device)
                time.sleep(1)
                # 简单重试
                try:
                    test_tensor = torch.randn(5, 5, device=device_obj)
                    torch.cuda.synchronize(device_obj)
                    del test_tensor
                    torch.cuda.empty_cache()
                    return True
                except:
                    return False
            return False
            
    except Exception as e:
        logger.error(f'Unexpected error in GPU health check: {e}')
        return False

# 验证设备
validate_device()

# 设置GPU内存池限制，使用90%的GPU内存以避免OOM
if torch.cuda.is_available():
    try:
        torch.cuda.set_per_process_memory_fraction(0.9, device=0)
        logger.info('Set GPU memory fraction to 90%')
    except Exception as e:
        logger.warning(f'Failed to set GPU memory fraction: {e}')

bert_model, tokenizer = load_bert_model(bert_path, device, is_half)
ssl_model = load_ssl_model(cnhubert_base_path, device, is_half)

def load_sovits_weights(sovits_path, device, is_half=True):
    global vq_model, hps
    try:
        # 2. 在加载前清理 CUDA 错误状态
        if torch.cuda.is_available():
            try:
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
            except RuntimeError as e:
                logger.warning(f'CUDA sync failed before loading model: {e}')
                # 尝试重置 CUDA 设备状态
                try:
                    if isinstance(device, str) and device.startswith('cuda:'):
                        device_obj = torch.device(device)
                    else:
                        device_obj = device
                    # 清理错误状态
                    torch.cuda.empty_cache()
                    gc.collect()
                except:
                    pass
        
        sybomls_version, model_version, if_lora_v3 = get_sovits_version_from_path_fast(sovits_path)
        dict_s2 = load_sovits_new(sovits_path)
        hps = dict_s2["config"]
        hps = DictToAttrRecursive(hps)
        hps.model.semantic_frame_rate = "25hz"
        if "enc_p.text_embedding.weight" not in dict_s2["weight"]:
            hps.model.version = "v2"  # v3model,v2sybomls
        elif dict_s2["weight"]["enc_p.text_embedding.weight"].shape[0] == 322:
            hps.model.version = "v1"
        else:
            hps.model.version = "v2"
        sybomls_version = hps.model.version
        if "Pro" not in model_version:
            model_version = sybomls_version
        else:
            hps.model.version = model_version
        logger.info(f'sovits model:{sovits_path}, version: {hps.model.version}')
        # 5. 创建模型
        vq_model = SynthesizerTrn(
            hps.data.filter_length // 2 + 1,
            hps.train.segment_size // hps.data.hop_length,
            n_speakers=hps.data.n_speakers,
            **hps.model
        )
        # 后验编码器，只有训练的时候需要，推理的时候不需要。
        if not any("enc_q" in key for key in dict_s2["weight"].keys()):
            try:
                del vq_model.enc_q
            except:
                logger.warning(f'Failed to delete enc_q from vq_model')
        
        # 6. 先加载权重到 CPU，再转换和移动（更安全的方式）
        try:
            # 先尝试加载权重到 CPU 上的模型
            missing_keys, unexpected_keys = vq_model.load_state_dict(dict_s2["weight"], strict=False)
            if missing_keys:
                logger.warning(f'Missing keys when loading sovits weights (showing first 5): {list(missing_keys)[:5]}')
            if unexpected_keys:
                logger.warning(f'Unexpected keys when loading sovits weights (showing first 5): {list(unexpected_keys)[:5]}')
        except Exception as e:
            logger.error(f'Error loading state dict on CPU: {e}')
            raise
        
        # 7. 安全地转换精度和移动到设备
        try:
            if is_half == True:
                # 先转换精度，再移动到设备（分步操作更安全）
                vq_model = vq_model.half()
                
                # 在移动到设备前，检查是否有异常值
                for name, param in vq_model.named_parameters():
                    if torch.isnan(param).any() or torch.isinf(param).any():
                        logger.warning(f'Parameter {name} contains NaN or Inf after half conversion, fixing...')
                        # 修复异常值
                        param.data = torch.nan_to_num(param.data, nan=0.0, posinf=1.0, neginf=-1.0)
                
                # 移动到设备
                vq_model = vq_model.to(device)
            else:
                vq_model = vq_model.to(device)
            
            # 8. 同步 CUDA 操作，确保没有错误
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                
        except RuntimeError as e:
            if "CUDA" in str(e) or "device-side assert" in str(e):
                logger.error(f'CUDA error during model conversion/move: {e}')
                # 尝试清理并重试（使用 float32）
                logger.warning('Retrying with float32 precision instead of float16...')
                try:
                    # 重新创建模型
                    vq_model = SynthesizerTrn(
                        hps.data.filter_length // 2 + 1,
                        hps.train.segment_size // hps.data.hop_length,
                        n_speakers=hps.data.n_speakers,
                        **hps.model
                    )
                    if ("pretrained" not in sovits_path):
                        del vq_model.enc_q
                    vq_model.load_state_dict(dict_s2["weight"], strict=False)
                    vq_model = vq_model.to(device)  # 使用 float32
                    logger.warning('Successfully loaded model with float32 precision instead of float16')
                    # 注意：这里不更新全局 is_half，因为可能只是这个模型有问题
                except Exception as retry_e:
                    logger.error(f'Retry with float32 also failed: {retry_e}')
                    raise
            else:
                raise
        
        vq_model.eval()
        logger.info(f'Successfully loaded sovits weights from {sovits_path}')
        return model_version
        
    except Exception as e:
        logger.error(f'Error loading sovits weights from {sovits_path}: {e}')
        # 清理可能的错误状态
        try:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                gc.collect()
        except:
            pass
        raise

def load_gpt_weights(gpt_path, device, is_half=True):
    global hz, max_sec, gpt_model, config
    try:
        # 2. 在加载前清理 CUDA 错误状态
        if torch.cuda.is_available():
            try:
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
            except RuntimeError as e:
                logger.warning(f'CUDA sync failed before loading GPT model: {e}')
                try:
                    torch.cuda.empty_cache()
                    gc.collect()
                except:
                    pass
        
        hz = 50
        dict_s1 = torch.load(gpt_path, map_location="cpu")
        config = dict_s1["config"]
        max_sec = config["data"]["max_sec"]
        gpt_model = Text2SemanticLightningModule(config, "****", is_train=False)
        
        # 4. 加载权重到 CPU
        missing_keys, unexpected_keys = gpt_model.load_state_dict(dict_s1["weight"], strict=False)
        if missing_keys:
            logger.warning(f'Missing keys when loading gpt weights (showing first 5): {list(missing_keys)[:5]}')
        if unexpected_keys:
            logger.warning(f'Unexpected keys when loading gpt weights (showing first 5): {list(unexpected_keys)[:5]}')
        
        # 5. 安全地转换精度和移动到设备
        try:
            if is_half == True:
                # 先转换精度
                gpt_model = gpt_model.half()
                
                # 检查是否有异常值
                for name, param in gpt_model.named_parameters():
                    if torch.isnan(param).any() or torch.isinf(param).any():
                        logger.warning(f'Parameter {name} contains NaN or Inf after half conversion, fixing...')
                        param.data = torch.nan_to_num(param.data, nan=0.0, posinf=1.0, neginf=-1.0)
                
                # 移动到设备
                gpt_model = gpt_model.to(device)
            else:
                gpt_model = gpt_model.to(device)
            
            # 6. 同步 CUDA 操作
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                
        except RuntimeError as e:
            if "CUDA" in str(e) or "device-side assert" in str(e):
                logger.error(f'CUDA error during GPT model conversion/move: {e}')
                # 尝试清理并重试（使用 float32）
                logger.warning('Retrying GPT model with float32 precision instead of float16...')
                try:
                    # 重新创建模型
                    gpt_model = Text2SemanticLightningModule(config, "****", is_train=False)
                    gpt_model.load_state_dict(dict_s1["weight"], strict=False)
                    gpt_model = gpt_model.to(device)  # 使用 float32
                    logger.warning('Successfully loaded GPT model with float32 precision instead of float16')
                except Exception as retry_e:
                    logger.error(f'Retry GPT model with float32 also failed: {retry_e}')
                    raise
            else:
                raise
        
        gpt_model.eval()
        total = sum([param.nelement() for param in gpt_model.parameters()])
        logger.info(f'GPT model loaded: {total / 1e6:.2f}M parameters')
        #logger.info(f'gweight_path:{gpt_path}')
        
    except Exception as e:
        logger.error(f'Error loading gpt weights from {gpt_path}: {e}')
        # 清理可能的错误状态
        try:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                gc.collect()
        except:
            pass
        raise

def init_sv(device, is_half=True):
    global sv_model
    sv_model = SV(device, is_half)

# fish-speech
llama_checkpoint_path = "models/fish_models/fish-speech-1.5"
#llama_queue = launch_thread_safe_queue(
#    checkpoint_path=llama_checkpoint_path,
#      device=device,
#    precision=torch.half,
#    compile=True,
#)
llama_model, decode_one_token = load_llama_model(llama_checkpoint_path, device, torch.half, compile=True)
vqgan_decoder_path = "models/fish_models/fish-speech-1.5/firefly-gan-vq-fsq-8x1024-21hz-generator.pth"
vqgan_config_name = "firefly_gan_vq"
vqgan_model = load_vqgan_model(vqgan_config_name, vqgan_decoder_path, device=device)

def fish_tts_with_retry(text, modelcat, modelname, max_retries=2):
    """带重试机制的fish_tts函数，处理OOM错误"""
    for attempt in range(max_retries):
        try:
            return fish_tts(text, modelcat, modelname)
        except RuntimeError as e:
            error_str = str(e).lower()
            if ("out of memory" in error_str or "cuda" in error_str) and attempt < max_retries - 1:
                logger.warning(f'OOM error in fish_tts, attempt {attempt + 1}/{max_retries}, cleaning memory...')
                # 清理GPU内存
                try:
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                        torch.cuda.empty_cache()
                        gc.collect()
                        time.sleep(1)  # 等待内存释放
                except Exception as cleanup_error:
                    logger.warning(f'GPU cleanup failed during retry: {cleanup_error}')
                continue
            raise
    raise RuntimeError(f'fish_tts failed after {max_retries} attempts')

@cuda_error_handler
def fish_tts(text, modelcat, modelname):
    logger.debug(f'fish tts, text:{text}, lang:{modelcat}, modelname:{modelname}')
    
    # 在开始前检查并等待GPU内存
    if torch.cuda.is_available():
        if not check_and_wait_for_gpu_memory(min_free_gb=2.0, max_wait_seconds=30):
            logger.error('GPU memory insufficient after waiting, aborting fish_tts')
            raise RuntimeError('GPU memory insufficient for fish_tts generation')
        
        try:
            # 记录内存状态
            gpu_memory = torch.cuda.get_device_properties(device).total_memory
            allocated_memory = torch.cuda.memory_allocated(device)
            cached_memory = torch.cuda.memory_reserved(device)
            free_memory = gpu_memory - cached_memory
            logger.debug(f'GPU memory - Total: {gpu_memory/1024**3:.2f}GB, Allocated: {allocated_memory/1024**3:.2f}GB, Cached: {cached_memory/1024**3:.2f}GB, Free: {free_memory/1024**3:.2f}GB')
        except Exception as e:
            logger.warning(f'GPU memory check failed: {e}')
    
    model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
    refer_npy_path = model_path + '/' + modelname + '_audio_prompt.npy'
    refer_text_path = model_path + '/' + modelname + '_text_prompt.txt'

    # 添加文件存在性检查
    if not os.path.exists(refer_npy_path):
        raise FileNotFoundError(f'Prompt tokens file not found: {refer_npy_path}')
    if not os.path.exists(refer_text_path):
        raise FileNotFoundError(f'Prompt text file not found: {refer_text_path}')

    try:
        # 安全加载numpy数组
        prompt_tokens_np = np.load(refer_npy_path)
        logger.debug(f'Loaded prompt tokens shape: {prompt_tokens_np.shape}, dtype: {prompt_tokens_np.dtype}')
        
        # 检查数据类型和形状
        if prompt_tokens_np.size == 0:
            raise ValueError(f'Prompt tokens file is empty: {refer_npy_path}')
        
        # 转换为tensor并移动到设备
        prompt_tokens = torch.from_numpy(prompt_tokens_np)
        
        # 检查tensor的有效性
        if torch.isnan(prompt_tokens).any() or torch.isinf(prompt_tokens).any():
            raise ValueError(f'Prompt tokens contain NaN or Inf values')
        
        # 确保数据类型正确
        if prompt_tokens.dtype != torch.float32 and prompt_tokens.dtype != torch.float16:
            prompt_tokens = prompt_tokens.float()
        
        # 安全地移动到设备
        prompt_tokens = safe_tensor_to_device(prompt_tokens, device)
        
    except Exception as e:
        logger.error(f'Error loading prompt tokens from {refer_npy_path}: {e}')
        # 如果GPU加载失败，尝试使用CPU
        logger.warning(f'Falling back to CPU for prompt tokens')
        prompt_tokens = torch.from_numpy(np.load(refer_npy_path)).cpu()
    
    with open(refer_text_path, 'r', encoding='utf-8') as f:
        prompt_text = f.read().strip()

    num_samples = 1
    max_new_tokens = 512
    top_p = 0.7
    repetition_penalty = 1.2
    temperature = 0.7
    compile = True
    chunk_length = 300
    iterative_prompt = True
    max_length = 1024

    text = ChnNormedText(text).normalize()

    try:
        generator = generate_long(
            model=llama_model,
            device=device,
            decode_one_token=decode_one_token,
            text=text,
            num_samples=num_samples,
            max_new_tokens=max_new_tokens,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            temperature=temperature,
            compile=compile,
            iterative_prompt=iterative_prompt,
            chunk_length=chunk_length,
            prompt_text=prompt_text,
            prompt_tokens=prompt_tokens,
        )
        segments = []
        for response in generator :
            if response.action != 'next':
                segment = decode_vq_tokens(vqgan_model, response.codes)
                segment = segment.detach().float().cpu().numpy()
                segments.append(segment)
                # 立即清理GPU内存，释放中间tensor
                del segment
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            else :
                break
    except Exception as e:
        logger.error(f'Error in fish_tts generation: {e}')
        # 清理GPU内存，但使用更安全的方式
        try:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                gc.collect()
        except Exception as cleanup_error:
            logger.warning(f'GPU cleanup failed after error: {cleanup_error}')
        raise
    
    # 安全地清理GPU内存
    try:
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()
    except Exception as e:
        logger.warning(f'GPU cleanup failed: {e}')
        
    if len(segments) == 0 :
        logger.error('fish tts generated no audio segments')
        raise RuntimeError('No audio generated')
    audio = np.concatenate(segments, axis=0)
    sample_rate = vqgan_model.spec_transform.sample_rate
    return sample_rate, audio

def only_punc(text):
    return not any(t.isalnum() or t.isalpha() for t in text)

def get_tts_wav(ref_wav_path, prompt_text, prompt_language, text, text_language, how_to_cut='', top_k=20, top_p=0.6, \
        temperature=0.6, ref_free = False, speed=1, version='v2'):
    global hps, gpt_model, sv_model, vq_model, ssl_model, bert_model, sv_model
    logger.info(f'get_tts_wav, ref_wav_path:{ref_wav_path}, prompt_text:{prompt_text}, prompt_language={prompt_language}, \
            text:{text}, text_language:{text_language}, how_to_cut:{how_to_cut}, top_k:{top_k}, top_p:{top_p}, \
            temperature:{temperature}, ref_free:{ref_free}, speed:{speed}, version:{version}')

    # global g2pw
    if prompt_text is None or len(prompt_text) == 0:
        ref_free = True
    t0 = ttime()
    if not ref_free:
        prompt_text = prompt_text.strip("\n")
        if (prompt_text[-1] not in splits): 
            prompt_text += "。" if prompt_language != "en" else "."
        #print(i18n("实际输入的参考文本:"), prompt_text)
    text = text.strip("\n")
    if (text[0] not in splits and len(get_first(text)) < 4):
        text = "。" + text if text_language != "en" else "." + text
    #print(i18n("实际输入的目标文本:"), text)
    
    # 创建不同时长的停顿音频，用于不同标点符号
    # 动态获取所有可能的停顿时长值，避免硬编码
    def create_pause_wav(duration_seconds):
        """创建指定时长的停顿音频"""
        pause_wav = np.zeros(
            int(hps.data.sampling_rate * duration_seconds),
            dtype=np.float16 if is_half == True else np.float32,
        )
        pause_wav_torch = torch.from_numpy(pause_wav)
        if is_half == True:
            pause_wav_torch = pause_wav_torch.half().to(device)
        else:
            pause_wav_torch = pause_wav_torch.to(device)
        return pause_wav_torch
    
    # 动态创建所有可能的停顿时长的停顿音频字典
    # 从配置中获取所有唯一的停顿时长值，避免硬编码
    all_pause_durations = get_all_pause_durations()
    pause_wav_dict = {duration: create_pause_wav(duration) for duration in all_pause_durations}
    
    # 保留原有的 0.3 秒停顿用于参考音频处理（向后兼容）
    zero_wav = np.zeros(
        int(hps.data.sampling_rate * 0.3),
        dtype=np.float16 if is_half == True else np.float32,
    )
    zero_wav_torch = torch.from_numpy(zero_wav)
    if is_half == True:
        zero_wav_torch = zero_wav_torch.half().to(device)
    else:
        zero_wav_torch = zero_wav_torch.to(device)

    prompt = None    
    with torch.no_grad():
        wav16k, sr = librosa.load(ref_wav_path, sr=16000)
        #  暂时先去掉, 后续再优化 TODO:gowtd
        #if (wav16k.shape[0] > 160000 or wav16k.shape[0] < 48000):
        #    raise OSError(i18n("参考音频在3~10秒范围外，请更换！"))
        wav16k = torch.from_numpy(wav16k)
        if is_half == True:
            wav16k = wav16k.half().to(device)
        else:
            wav16k = wav16k.to(device)
        wav16k = torch.cat([wav16k, zero_wav_torch])
        ssl_content = ssl_model.model(wav16k.unsqueeze(0))[
            "last_hidden_state"
        ].transpose(
            1, 2
        )  # .float()
        codes = vq_model.extract_latent(ssl_content)

        prompt_semantic = codes[0, 0]
        prompt = prompt_semantic.unsqueeze(0).to(device)
    t1 = ttime()
    # 按自定义标点切
    text = cut5(text, text_language)
    # 处理TikTok情况
    text = re.sub(r"(TikTok|Tiktok|TIKTOK|tiktok)", "ticktalk", text)
    while "\n\n" in text:
        text = text.replace("\n\n", "\n")
    #print(i18n("实际输入的目标文本(切句后):"), text)
    logger.info(f"get_tts_wav, after cut, text:{text}")
    texts = text.split("\n")
    texts = process_text(texts)
    texts = merge_short_text_in_array(texts, 5)
    #texts = split_long_sentence(texts, 25)
    audio_opt = []
    #logger.debug(f'======== ttw 222, texts:{texts}')
    if not ref_free:
        phones1,bert1,norm_text1=get_phones_and_bert(prompt_text, prompt_language, bert_model, tokenizer, version)
    is_v2pro = version in {'v2Pro', 'v2ProPlus'}
    if is_v2pro and sv_model is None:
        init_sv(device, is_half)
    for text in texts:
        # 简单防止纯符号引发参考音频泄露
        if only_punc(text):
            continue
        # 解决输入目标文本的空行导致报错的问题
        if (len(text.strip()) == 0):
            continue
        if (text[-1] not in splits): text += "。" if text_language != "en" else "."
        # 获取根据标点符号的停顿时长
        pause_duration = get_pause_duration(text)
        phones2,bert2,norm_text2=get_phones_and_bert(text, text_language, bert_model, tokenizer, version)
        if not ref_free:
            bert = torch.cat([bert1, bert2], 1)
            all_phoneme_ids = torch.LongTensor(phones1+phones2).to(device).unsqueeze(0)
            #logger.debug(f'======== ttw 666, all_phoneme_ids len:{all_phoneme_ids.shape}, bert len:{bert.shape}')
        else:
            bert = bert2
            all_phoneme_ids = torch.LongTensor(phones2).to(device).unsqueeze(0)

        logger.info(f"get_tts_wav, text:{text}, norm_text2:{norm_text2}, pause_duration:{pause_duration}")
        bert = bert.to(device).unsqueeze(0)
        all_phoneme_len = torch.tensor([all_phoneme_ids.shape[-1]]).to(device)
        t2 = ttime()
        # gpt model
        with torch.no_grad():
            # pred_semantic = t2s_model.model.infer(
            pred_semantic, idx = gpt_model.model.infer_panel(
                all_phoneme_ids,
                all_phoneme_len,
                None if ref_free else prompt,
                bert,
                # prompt_phone_len=ph_offset,
                top_k=top_k,
                top_p=top_p,
                temperature=temperature,
                early_stop_num=hz * max_sec,
            )
        t3 = ttime()
        # print(pred_semantic.shape,idx)
        pred_semantic = pred_semantic[:, -idx:].unsqueeze(
            0
        )  # .unsqueeze(0)#mq要多unsqueeze一次
        #logger.debug(f'======== ttw 10111, pred_semantic shape: {pred_semantic.shape}')
        spec, audio_tensor = get_spepc(hps, ref_wav_path, dtype, device, is_v2pro)  # .to(device)
        refers = [spec]
        if is_v2pro:
            sv_emb = [sv_model.compute_embedding3(audio_tensor)]
        # 解码音频
        if is_v2pro:
            audio = vq_model.decode(
                pred_semantic,
                torch.LongTensor(phones2).to(device).unsqueeze(0),
                refers,
                speed=speed,
                sv_emb=sv_emb,
            )[0][0]
        else:
            audio = vq_model.decode(
                pred_semantic, torch.LongTensor(phones2).to(device).unsqueeze(0), refers, speed=speed
            )[0][0]
        #wavfile.write(wav_output_dir+'/test.wav', hps.data.sampling_rate, (audio * 32768).astype(np.int16))
        max_audio = torch.abs(audio).max()  # 简单防止16bit爆音
        if max_audio>1.0:
            audio /= max_audio
        audio_opt.append(audio)
        # 根据标点符号类型添加相应时长的停顿
        if pause_duration > 0:
            # 直接使用 pause_duration 作为 key 查找对应的停顿音频
            # 如果 pause_duration 在字典中，直接使用；否则找到最接近的值
            if pause_duration in pause_wav_dict:
                audio_opt.append(pause_wav_dict[pause_duration])
            else:
                # 如果找不到精确匹配，找到最接近的停顿时长
                closest_duration = min(pause_wav_dict.keys(), key=lambda x: abs(x - pause_duration))
                audio_opt.append(pause_wav_dict[closest_duration])
        t4 = ttime()

    #print("%.3f\t%.3f\t%.3f\t%.3f" % (t1 - t0, t2 - t1, t3 - t2, t4 - t3))
    audio_opt = torch.cat(audio_opt, 0)
    audio_opt = audio_opt.cpu().detach().numpy()
    return hps.data.sampling_rate, (audio_opt * 32767).astype(np.int16)


# 设置DetectorFactory的种子，以确保检测结果是可复现的
DetectorFactory.seed = 0
def detect_language(text):
    try :
        lang_code = detect(text)
        return lang_code
    except LangDetectException as e:
        logger.warning(f'text:{text}, detect language error:{e}')
        # detect 失败按照英语处理
        return 'en'

def determine_english_text_language(text_lang):
    if text_lang == 'zh-cn':
        return 'zh'
    elif text_lang in ['ja', 'en']:
        return text_lang
    else:
        return 'auto'

# "all_zh",#全部按中文识别
# "en",#全部按英文识别#######不变
# "all_ja",#全部按日文识别
# "zh",#按中英混合识别####不变
# "ja",#按日英混合识别####不变
# "yue", #按粤英混合识别###不变
# "auto",#多语种启动切分识别语种

def determine_languages(lang, text_lang):

    if lang not in language_map:
        return 'auto', 'auto', 'v1'

    result = language_map[lang]
    prompt_language = result['prompt']
    version = result.get('version', 'v1')

    if lang.startswith('english'):
        text_language = determine_english_text_language(text_lang)
    elif lang == 'japan':
        text_language = 'ja' if text_lang == 'ja' else 'auto'
    elif lang == 'korea':
        text_language = 'ko' if text_lang in ['ko', 'en'] else 'auto'
    else:
        text_language = result.get('text', 'auto')

    return prompt_language, text_language, version

def save_audio_file(sample_rate, audio, output_format, base_output_path, filename_prefix):
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

    logger.info(f'save_audio_file before write, output_path:{output_path}')
    if output_format == "wav":
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


def enqueue_tts_sync(output_path, output_file, short_uri_prefix, remote_target_dir, task_id, domain, email=''):
    """Enqueue async file delivery; returns (ok, error_msg). Does not mark complete."""
    if email == VIDEO_SCRIPT_TTS:
        proxies = []
        delivery_transport = "http"
    else:
        delivery_transport = resolve_delivery_transport(email)
        proxies = get_socat_proxy_configs() if delivery_transport == "rsync" else []
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
        msg = f'enqueue sync failed, queue full or workers down, task_id:{task_id}'
        logger.error(msg)
        update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
        return False, msg
    return True, None

@celery_app.task(name='task_gen_tts')
@cuda_error_handler
def gpu_gen_tts(modelname, text, modelcat='', userid=0, task_id='0', output_format=DEFAULT_FORMAT, modellang='', email='', domain=''):
    set_reqid(task_id)
    try:
        if (len(modelname.strip()) == 0):
            ret = 1
            msg = f'modelname:{modelname} is invalid, task_id:{task_id}!'
            logger.warning(f'msg: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return gen_res_dict(ret, msg)

        if (len(text.strip()) == 0):
            ret = 1
            msg = f'text:{text} is empty, task_id:{task_id}!'
            logger.warning(f'msg: {msg}')
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
            return gen_res_dict(ret, msg)

        lang = "NULL"
        # 接口做过修改
        # tiktokvoice.net 会把语言类型传递在modelcat参数中
        # aivoicelab.net 为了兼容角色模型， 将语言参数参地在modellang中
        if (len(modellang.strip()) != 0):
            lang = modellang
        else :
            if modelcat == 'english_role':
                lang = 'english'
            else :
                lang = modelcat

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
                if not check_and_wait_for_gpu_memory(min_free_gb=2.0, max_wait_seconds=30):
                    logger.warning(f'GPU memory low at task start, attempting cleanup. task_id:{task_id}')
                    manage_gpu_memory()
                    # 再次检查
                    if not check_and_wait_for_gpu_memory(min_free_gb=1.5, max_wait_seconds=10):
                        logger.error(f'GPU memory insufficient for task, task_id:{task_id}')
                        ret = 1
                        msg = f'GPU memory insufficient, please try again later, task_id:{task_id}!'
                        update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                        return gen_res_dict(ret, msg)
        except Exception as e:
            logger.warning(f'GPU memory check failed at task start: {e}')

        try:
            # modcat 变成了语言
            if lang in fish_support_langs :
                model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
                if not os.path.exists(model_path) :
                   ret = 1
                   msg = f'modelname:{modelname} is invalid, task_id:{task_id}!'
                   logger.warning(f'fish tts, model_path:{model_path}, modelname:{modelname}, msg:{msg}')
                   update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                   return gen_res_dict(ret, msg)

                # 使用带重试机制的fish_tts
                sample_rate, audio = fish_tts_with_retry(text, modelcat, modelname, max_retries=2)

                wav_output_path = f'{wav_output_dir}/{today}/user{userid}'
                filename_prefix = f'fis_{modelname}_{datetime_str}_{random_number}'
                #uri_prefix = f'/outimage/wavcl/{today}/user{userid}'
                # short_uri_prefix国内、国外兼容， 同步成功走国外，同步失败走国内, 同步是设置
                # 国内为 f'/outimgage/{enuri_prefix}', 国外为 f'/outdt/{enuri_prefix}', 
                short_uri_prefix = f'/wavcl/{today}/user{userid}'
                if email == VIDEO_SCRIPT_TTS:
                    remote_ttssave_dir = get_localnet_video_tts_dir()
                else:
                    remote_ttssave_dir = get_remote_ttssave_dir()
                remote_target_dir = f'{remote_ttssave_dir}/{today}/user{userid}'

                output_path, output_file = save_audio_file(
                    sample_rate, audio, output_format, wav_output_path, filename_prefix
                )
                ok, sync_msg = enqueue_tts_sync(
                    output_path, output_file, short_uri_prefix, remote_target_dir, task_id, domain, email
                )
                if not ok:
                    return gen_res_dict(1, sync_msg)
            else :
                # gpt-sovits 支持中,日,英, 粤
                # modcat 为 china, china_yue, english, japan 的情况
                model_file_prefix = modelname
                # 公用模型
                model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'

                if not os.path.exists(model_path) :
                   ret = 1
                   msg = f'modelname:{modelname} is invalid, task_id:{task_id}!'
                   logger.warning(f'model_path:{model_path}, modelname:{modelname}')
                   update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                   return gen_res_dict(ret, msg)

                # 在加载模型前检查 GPU 健康状态
                if not check_gpu_health(device, attempt_recovery=True):
                    logger.error(f'GPU health check failed, skipping task for task_id:{task_id}')

                    # 记录详细错误信息
                    log_gpu_error_details(device, "GPU health check failed")

                    ret = 1
                    msg = f'GPU is in an unhealthy state, task_id:{task_id}!'
                    logger.error(f'msg: {msg}')
                    update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                    return gen_res_dict(ret, msg)

                model_desc = f'{model_path}/{model_file_prefix}_desc.txt'
                if not os.path.exists(model_desc):
                    ret = 1
                    msg = f'model_desc not found, task_id:{task_id}!'
                    logger.warning(f'msg: {msg}')
                    update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                    return gen_res_dict(ret, msg)

                load_model_type = "gsv" # 默认使用sovits模型
                with open(model_desc, 'r', encoding='utf-8') as f :
                    model_desc_content = f.readline().strip('\n')
                    logger.debug(f'handle, model_desc_content:{model_desc_content}')
                    items = model_desc_content.split('|')
                    if len(items) >= 8:
                        load_model_type = items[7]
                if load_model_type == "index-tts" : # 使用index-tts模型
                    filename_prefix = f'idx_{modelname}_{datetime_str}_{random_number}'
                    speacker_pt_path = f"{model_path}/{model_file_prefix}_audio_prompt.pt"
                    if not os.path.exists(speacker_pt_path):
                        ret = 1
                        msg = f'speacker_pt_path {speacker_pt_path} not found, task_id:{task_id}!'
                        logger.warning(f'msg: {msg}')
                        update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                        return gen_res_dict(ret, msg)

                    msg = f'index-tts is not supported, task_id:{task_id}!'
                    update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
                    return gen_res_dict(ret, msg)
                    #sample_rate, audio = indextts_infer(logger, indextts_model, speacker_pt_path, text)
                else : # 使用gpt-sovits模型
                    refer_wav_path = f"{model_path}/{model_file_prefix}_audio_prompt.wav"
                    refer_text_path = f"{model_path}/{model_file_prefix}_text_prompt.txt"
                    gpt_weights_path = f"{model_path}/{model_file_prefix}_gpt_weights.ckpt"
                    sovits_weights_path = f"{model_path}/{model_file_prefix}_sovits_weights.pth"
                    prompt_text = ''
                    with open(refer_text_path, 'r', encoding='utf-8') as f :
                        prompt_text = f.read()

                    prompt_language = 'en'
                    text_language = 'en'
                    how_to_cut = ''
                    top_k = 5
                    top_p = 0.4
                    temperature = 0.4
                    ref_free = False
                    speed = 0.95
                    version = "v2"
                    text_lang = detect_language(text)
                    logger.debug(f'handle, lang:{lang}, dectect text lang:{text_lang}')
                    if text_lang is None:
                        text_lang = language_map[lang]
                    else:
                        text_lang = 'auto'
                    logger.debug(f'handle, dectect lang is None, user default lang:{text_lang}')

                    # version 已废弃，直接根据加载的模型确定版本
                    prompt_language, text_language, version = determine_languages(lang, text_lang)

                    #logger.info(f'hanle, sovits_weights_path: {sovits_weights_path}')
                    #logger.info(f'hanle, gpt_weights_path: {gpt_weights_path}')
                    model_version = load_sovits_weights(sovits_weights_path, device, is_half)
                    load_gpt_weights(gpt_weights_path, device, is_half)
                    init_sv(device, is_half)

                    sample_rate, audio = get_tts_wav(refer_wav_path, prompt_text, prompt_language, text, text_language, how_to_cut, top_k, top_p, temperature, ref_free, speed, model_version)
                    filename_prefix = f'gpt_{modelname}_{datetime_str}_{random_number}'

                wav_output_path = wav_output_dir + '/' + today + '/user' + str(userid) 
                #uri_prefix = f'/outimage/wavcl/{today}/user{userid}'
                # short_uri_prefix 国内、国外兼容， 国内为 f'/outimgage/{enuri_prefix}', 国外为 f'/outdt/{enuri_prefix}', 同步成功走国外，同步失败走国内
                short_uri_prefix = f'/wavcl/{today}/user{userid}'
                if email == VIDEO_SCRIPT_TTS:
                    remote_ttssave_dir = get_localnet_video_tts_dir()
                else:
                    remote_ttssave_dir = get_remote_ttssave_dir()
                remote_target_dir = f'{remote_ttssave_dir}/{today}/user{userid}'
                output_path, output_file = save_audio_file(
                    sample_rate, audio, output_format, wav_output_path, filename_prefix
                )
                ok, sync_msg = enqueue_tts_sync(
                    output_path, output_file, short_uri_prefix, remote_target_dir, task_id, domain, email
                )
                if not ok:
                    return gen_res_dict(1, sync_msg)

        except Exception as e:
            logger.error(f'Error in gpu_gen_tts for task_id:{task_id}: {e}')
            msg = f'Error in gpu_gen_tts for task_id:{task_id}: {e}'
            update_task_status(logger, task_id, domain, msg, TaskStatus.FAILED)
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
            f'task_id:{task_id}, inference finish, sync enqueued, time[tts:{cost_tts}] '
            f'sample_rate:{sample_rate}, audio_file:{output_path}'
        )

        result = f"Processed: {task_id}"
        return result
    finally:
        clear_reqid()

## 以下主要提供api服务
common_cloned_models = []
def load_common_cloned_models () :
    global common_cloned_models
    service_models_path = "models/service_model"
    common_models_path = service_models_path + "/common_models/common_models.yaml"
    with open(common_models_path , encoding="UTF-8") as f:
        cfg = f.read()
        models_yaml = yaml.load(cfg, Loader=yaml.FullLoader)
        models_yaml = models_yaml['models']
        for cat in models_yaml :
            cat_models = {}
            catid = cat
            catname = cat
            items = models_yaml[cat]
            model_data = []
            for item in items :
                catid = item['catid']
                catname = item['catname']
                model = {}
                model['model_name'] = item['name']
                model['model_display'] = item['display']
                model['wavplay'] = item['wavplay']
                model_data.append(model)
            model_data.sort(key=lambda x:x['model_display'])
            cat_models['modelcat_name'] = catid
            cat_models['modelcat_display'] = catname
            cat_models['modelcat_data'] = model_data
            common_cloned_models.append(cat_models)
    #        logger.debug(f'task_id:{task_id}, userid:{userid}, cat:{cat} cat_models:{cat_models}')
    #    logger.debug(f'task_id:{task_id}, userid:{userid}, common_models')

def validate_cloned_model(modelcat, modelname):
    """Validate if modelcat exists in common_clone_models"""
    is_valid_model = False
    for cat_model in common_cloned_models:
        if cat_model['modelcat_name'] == modelcat:
            for model in cat_model['modelcat_data']:
                if model['model_name'] == modelname:
                    is_valid_model = True
    return is_valid_model

#def warmup_indextts():
#    """预热index-tts模型，避免第一次调用时的延迟"""
#    global indextts_model, logger
#    try:
#        logger.info("warmup index-tts start")
#        modelcat = "game"
#        modelname = "us-male-phone-guy"
#        model_path = f'models/service_model/common_models/{modelcat}/common_model_{modelname}'
#        speacker_pt_path = f"{model_path}/{modelname}_audio_prompt.pt"
#        if not os.path.exists(speacker_pt_path):
#            logger.warning(f'speacker_pt_path {speacker_pt_path} not found, modelcat:{modelcat}, modelname:{modelname}')
#            return
#        test_text = "Hello, world!"
#        logger.debug(f">> warmup index-tts start, speacker_pt_path: {speacker_pt_path}, test_text: {test_text}")
#        sample_rate, audio = indextts_infer(logger,indextts_model, speacker_pt_path, test_text)
#        logger.debug(f">> warmup index-tts finish, sample_rate: {sample_rate}")
#        logger.info("warmup index-tts finish")
#    except Exception as e:
#        logger.warning(f"warmup index-tts failed: {e}")

def warmup_fish_tts():
    """预热fish_tts模型，避免第一次调用时的延迟"""
    try:
        logger.info("warmup fish_tts start")
        
        # 检查CUDA状态
        if not torch.cuda.is_available():
            logger.warning("CUDA not available, skipping warmup")
            return
            
        # 使用一个简短的测试文本进行预热
        test_text = "Hello, world!"
        test_modelcat = "spain"  # 选择一个常用的模型类别
        test_modelname = "es-male-rafael"  # 需要根据你的实际模型名称调整
        
        fish_tts(test_text, test_modelcat, test_modelname)
        logger.info("warmup fish_tts finish")   

    except Exception as e:
        logger.warning(f"warmup fish_tts failed: {e}")
        # 清理可能的GPU错误状态
        try:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                gc.collect()
        except:
            pass

def initialize_models():
    #global indextts_model
    # 加载index-tts模型
    #indextts_model = load_indextts_model(logger)

    # 预热index-tts
    #print("initialize_models, warmup index-tts start")
    #warmup_indextts()
    #print("initialize_models, warmup index-tts finish")
    # 预热fish_tts,
    print("initialize_models, warmup fish_tts start")
    warmup_fish_tts()
    print("initialize_models, warmup fish_tts finish")

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

