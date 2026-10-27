# -*- coding: utf-8 -*-
import torch
import os

from utility.file_sync_config import (
    get_file_sync_transport,
    get_quic_client_host,
    get_quic_client_port,
    get_quic_upload_path,
    get_quic_upload_timeout_sec,
)

gpu_index = os.environ.get('GPU_INDEX', 0)
# 确保 gpu_index 是整数
try:
    gpu_index = int(gpu_index)
except (ValueError, TypeError):
    gpu_index = 0

def get_device() :
    import torch
    if torch.cuda.is_available() :
        # 验证 GPU 索引是否有效
        device_count = torch.cuda.device_count()
        if gpu_index < 0 or gpu_index >= device_count:
            # 如果指定的 GPU 索引无效，抛出异常
            raise ValueError(
                f"GPU_INDEX={gpu_index} is invalid. Available GPUs: {device_count} (indices: 0-{device_count-1}). "
                f"Please set GPU_INDEX environment variable to a valid GPU index."
            )
        device = f"cuda:{gpu_index}"
        print(f"get_device: Using GPU: {device}")
    else :
        device = "cpu"
        print(f"get_device: Using CPU")
    return device

def ishalf() :
    return True

def get_dtype(is_half=True) :
    return torch.float16 if is_half == True else torch.float32

def get_hostname() :
    hostname = "http://www.5w5w.cn"
    return hostname

def get_gpu_numbers() :
    gpu_numbers = '1-2'
    return gpu_numbers

def get_hmac_secret_key() :
    return "0x4AAAAAABL7dEKVA5-OlS1S"

def get_remote_ttssave_dir() :
    return "/home/aigc/data/aioutput/wavcl"

def get_localnet_video_tts_dir() :
    return "/home/aigc/data/aioutput/video/tts"

def get_fasttext_modelpath() :
    return "/app/models/fasttext_models/lid.176.bin"

