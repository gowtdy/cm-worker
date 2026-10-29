# -*- coding: utf-8 -*-

import os

import sys, numpy as np, traceback, pdb
import os.path
from glob import glob
from tqdm import tqdm

parent_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(parent_directory)
from text.cleaner import clean_text
import torch
from transformers import AutoModelForMaskedLM, AutoTokenizer
import numpy as np


from time import time as ttime
import shutil


def my_save(fea,path, i_part):#####fix issue: torch.save doesn't support chinese path
    dir=os.path.dirname(path)
    name=os.path.basename(path)
    # tmp_path="%s/%s%s.pth"%(dir,ttime(),i_part)
    tmp_path="%s%s.pth"%(ttime(),i_part)
    torch.save(fea,tmp_path)
    shutil.move(tmp_path,"%s/%s"%(dir,name))

def get_bert_feature(text, word2ph, tokenizer, bert_model, device):
    with torch.no_grad():
        inputs = tokenizer(text, return_tensors="pt")
        for i in inputs:
            inputs[i] = inputs[i].to(device)
        res = bert_model(**inputs, output_hidden_states=True)
        res = torch.cat(res["hidden_states"][-3:-2], -1)[0].cpu()[1:-1]

    assert len(word2ph) == len(text)
    phone_level_feature = []
    for i in range(len(word2ph)):
        repeat_feature = res[i].repeat(word2ph[i], 1)
        phone_level_feature.append(repeat_feature)

    phone_level_feature = torch.cat(phone_level_feature, dim=0)

    return phone_level_feature.T


def get_text(inp_text, inp_wav_dir, exp_name, opt_dir, i_part, all_parts, bert_pretrained_dir, device, is_half):
    txt_path = "%s/2-name2text-%s.txt" % (opt_dir, i_part)
    print(f'get_text, inp_text:{inp_text}, inp_wav_dir:{inp_wav_dir}, exp_name:{exp_name}, opt_dir:{opt_dir}, \
            i_part:{i_part}, all_parts:{all_parts}, bert_pretrained_dir:{bert_pretrained_dir}, device:{device} \
            is_half:{is_half}')
    if os.path.exists(txt_path) :
        os.remove(txt_path)
    bert_dir = "%s/3-bert" % (opt_dir)
    os.makedirs(opt_dir, exist_ok=True)
    os.makedirs(bert_dir, exist_ok=True)
    if torch.cuda.is_available():
        device = "cuda:0"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    tokenizer = AutoTokenizer.from_pretrained(bert_pretrained_dir)
    bert_model = AutoModelForMaskedLM.from_pretrained(bert_pretrained_dir)
    if is_half == True:
        bert_model = bert_model.half().to(device)
    else:
        bert_model = bert_model.to(device)
    
    
    data = []
    res = []
    with open(inp_text, "r", encoding="utf8") as f:
        lines = f.read().strip("\n").split("\n")

    language_v1_to_language_v2 = {
        "ZH": "zh",
        "zh": "zh",
        "JP": "ja",
        "jp": "ja",
        "JA": "ja",
        "ja": "ja",
        "EN": "en",
        "en": "en",
        "En": "en",
        "KO": "ko",
        "Ko": "ko",
        "ko": "ko",
        "yue": "yue",
        "YUE": "yue",
        "Yue": "yue",
    }
    for line in lines[int(i_part) :: int(all_parts)]:
        try:
            wav_name, spk_name, language, text = line.split("|")
            # todo.append([name,text,"zh"])
            data.append(
                [wav_name, text, language_v1_to_language_v2.get(language, language)]
            )
        except:
            print(line, traceback.format_exc())
    
    for name, text, lan in data:
        try:
            name = os.path.basename(name)
            phones, word2ph, norm_text = clean_text(
                text.replace("%", "-").replace("￥", ","), lan
            )
            path_bert = "%s/%s.pt" % (bert_dir, name)
            if os.path.exists(path_bert) == False and lan == "zh":
                bert_feature = get_bert_feature(norm_text, word2ph, tokenizer, bert_model, device)
                assert bert_feature.shape[-1] == len(phones)
                # torch.save(bert_feature, path_bert)
                my_save(bert_feature, path_bert, i_part)
            phones = " ".join(phones)
            # res.append([name,phones])
            res.append([name, phones, word2ph, norm_text])
        except:
            print(name, text, traceback.format_exc())
    opt = []
    for name, phones, word2ph, norm_text in res:
        opt.append("%s\t%s\t%s\t%s" % (name, phones, word2ph, norm_text))
    with open(txt_path, "w", encoding="utf8") as f:
        f.write("\n".join(opt) + "\n")
