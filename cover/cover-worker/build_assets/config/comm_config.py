# -*- coding: utf-8 -*-
import os
gpu_index = os.environ.get('GPU_INDEX', 0)

def get_hostname() :
    hostname = "https://tiktokvoice.net"
    # hostname = "http://223.72.18.241:8181"
    return hostname

def get_logdir() :
    logdir = './log'
    return logdir

def get_modelconf() :
    model_conf = {
        'modelconfig': 'models/stable-audio-open-1.0/model_config.json',
        'ckptpath': 'models/stable-audio-open-1.0/model.safetensors',
        'modelhalf': 'True'
    }
    return model_conf

def get_device() :
    import torch
    if torch.cuda.is_available() :
        device = f'cuda:{gpu_index}'
    else :
        device = "cpu"
    return device

def get_white_list() :
    white_list = []
    return white_list

def get_hmac_secret_key() :
    return "l4AnBbVDNIardm33PyFP0Ki29uLAn9a6xrzNL/6FPuQ="

def get_url_secret_key() :
    return "8pf4i9NOKl4o8dhiI-qoy6xzx9T514s6KmcR8kK1dZQ"

def get_userupload_dir(domain='aivoicelab.net') :
    userupload_root = '/home/aigc/data/userupload'
    if domain == 'aivoicelab.net' :
        userupload_dir = f'{userupload_root}/aivoicelab'
    elif domain == 'tiktokvoice.net' :
        userupload_dir = f'{userupload_root}/tiktokvoice'
    else :
        userupload_dir = f'{userupload_root}/default'
    os.makedirs(userupload_dir, exist_ok=True)
    return userupload_dir

def get_remote_cover_dir() :
    return "/home/aigc/data/aioutput/cover/aivoicelab/final"

def get_remote_vocal_dir() :
    return "/home/aigc/data/aioutput/cover/commshare/final/sepvocal"

def get_remote_instrument_dir() :
    return "/home/aigc/data/aioutput/cover/commshare/final/sepinstrum"
