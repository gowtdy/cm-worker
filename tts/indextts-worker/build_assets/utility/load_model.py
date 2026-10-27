# -*- coding: utf-8 -*-

import os
import torch
from transformers import AutoModelForMaskedLM, AutoTokenizer

from voice_train.feature_extractor import cnhubert
from voice_train.text.g2pw.g2pw import G2PWPinyin

class DictToAttrRecursive(dict):
    def __init__(self, input_dict):
        super().__init__(input_dict)
        for key, value in input_dict.items():
            if isinstance(value, dict):
                value = DictToAttrRecursive(value)
            self[key] = value
            setattr(self, key, value)

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(f"Attribute {item} not found")

    def __setattr__(self, key, value):
        if isinstance(value, dict):
            value = DictToAttrRecursive(value)
        super(DictToAttrRecursive, self).__setitem__(key, value)
        super().__setattr__(key, value)

    def __delattr__(self, item):
        try:
            del self[item]
        except KeyError:
            raise AttributeError(f"Attribute {item} not found")

def load_bert_model(bert_path, device, is_half=True):
    # 验证设备是否有效
    if isinstance(device, str) and device.startswith('cuda:'):
        try:
            device_index = int(device.split(':')[1])
            if not torch.cuda.is_available():
                raise RuntimeError(f"CUDA not available, but device is set to {device}. Please use CPU or ensure CUDA is properly configured.")
            elif device_index < 0 or device_index >= torch.cuda.device_count():
                raise ValueError(
                    f"Device {device} is invalid. Available GPUs: {torch.cuda.device_count()} (indices: 0-{torch.cuda.device_count()-1}). "
                    f"Please set GPU_INDEX environment variable to a valid GPU index."
                )
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid device format {device}: {e}")
    
    tokenizer = AutoTokenizer.from_pretrained(bert_path)
    bert_model = AutoModelForMaskedLM.from_pretrained(bert_path)
    if is_half == True:
        bert_model = bert_model.half().to(device)
    else:
        bert_model = bert_model.to(device)
    return bert_model, tokenizer

def load_ssl_model(cnhubert_base_path, device, is_half=True):
    # 验证设备是否有效
    if isinstance(device, str) and device.startswith('cuda:'):
        try:
            device_index = int(device.split(':')[1])
            if not torch.cuda.is_available():
                raise RuntimeError(f"CUDA not available, but device is set to {device}. Please use CPU or ensure CUDA is properly configured.")
            elif device_index < 0 or device_index >= torch.cuda.device_count():
                raise ValueError(
                    f"Device {device} is invalid. Available GPUs: {torch.cuda.device_count()} (indices: 0-{torch.cuda.device_count()-1}). "
                    f"Please set GPU_INDEX environment variable to a valid GPU index."
                )
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid device format {device}: {e}")
    cnhubert.cnhubert_base_path = cnhubert_base_path
    ssl_model = cnhubert.get_model()
    if is_half == True:
        ssl_model = ssl_model.half().to(device)
    else:
        ssl_model = ssl_model.to(device)
    return ssl_model

def load_g2pwp_model(g2pwp_modelpath, basebert_path) :
    gpu_index = os.environ.get("GPU_INDEX", "0")
    providers = [("CUDAExecutionProvider", {"device_id": str(gpu_index)})]
    #logger.debug(f'=========== g2pwp_model 11, providers: {providers}')
    g2pw = G2PWPinyin(model_dir=g2pwp_modelpath, model_source=basebert_path, \
                v_to_u=False, neutral_tone_with_five=True, providers=providers)
    return g2pw

