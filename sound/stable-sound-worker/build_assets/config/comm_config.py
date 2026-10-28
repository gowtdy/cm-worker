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
        'modelname': 'small-sfx',
        'modelconfig': '/app/models/stable-audio-3-small-sfx/model_config.json',
        'ckptpath': '/app/models/stable-audio-3-small-sfx/model.safetensors',
        'modelhalf': 'True',
        # Optional override; None => auto-detect beside model_config.json
        't5gemmapath': '/app/models/stable-audio-3-small-sfx/t5gemma-b-b-ul2',
    }
    return model_conf

def get_fasttext_modelpath() :
    return "models/fasttext_models/lid.176.bin"

def get_device() :
    import torch
    if torch.cuda.is_available() :
        device = f"cuda:{gpu_index}"
        print(f"Using GPU device: {device}")
    else :
        device = "cpu"
        print("CUDA not available, using CPU")
    return device

def get_t5modelpath() :
    T5_BASEPATH = "models/google-t5"
    t5_modelpath = {
        "t5-small": f'{T5_BASEPATH}/t5-small',
        "t5-base": f'{T5_BASEPATH}/t5-base',
        "t5-large": f'{T5_BASEPATH}/t5-large',
        "t5-3b": f'{T5_BASEPATH}/t5-3b',
        "t5-11b": f'{T5_BASEPATH}/t5-11b',
        "google/flan-t5-small": f'{T5_BASEPATH}/flan-t5-small',
        "google/flan-t5-base": f'{T5_BASEPATH}/flan-t5-base',
        "google/flan-t5-large": f'{T5_BASEPATH}/flan-t5-large',
        "google/flan-t5-xl": f'{T5_BASEPATH}/flan-t5-xl',
        "google/flan-t5-xxl": f'{T5_BASEPATH}/flan-t5-xxl'
    }
    return t5_modelpath

def get_white_list() :
    white_list = []
    return white_list

def get_remote_soundsave_dir() :
    return "/home/aigc/data/aioutput/sounds/tk"
