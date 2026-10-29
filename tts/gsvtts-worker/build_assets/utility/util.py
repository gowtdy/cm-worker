# -*- coding: utf-8 -*-
import numpy as np
import time
import re
import torch
import LangSegment
import hmac
import json
import hashlib
import librosa
import torchaudio

from transformers import AutoModelForMaskedLM, AutoTokenizer

from voice_train.my_utils import load_audio
from voice_train.text import cleaned_text_to_sequence
from voice_train.text.cleaner import clean_text
from voice_train.text import chinese2 as chinese
from voice_train.module.mel_processing import spectrogram_torch

from utility.voice_config import get_device, ishalf, get_dtype, get_hmac_secret_key

def gen_res_dict(ret: int, msg: str, dt=None):
    d = {}
    if dt:
        d.update(dt)
    d['ret'] = ret
    d['msg'] = msg
    return d

def verify_signature(text: str, timestamp: int) -> str:
    """验证 HMAC 签名
    对text字段进行签名验证，签名格式为：text=<utf8_encoded_text>&timestamp=<timestamp>
    Args:
        text: 需要验证的文本
        timestamp: UTC时间戳
    Returns:
        str: 服务器端计算的签名值
    """
    # 1. 将文本内容转换为 UTF-8 编码的字节数组的十六进制表示
    utf8_text = text.encode('utf-8').hex()
    
    # 2. 组合签名字符串
    message = f"text={utf8_text}&timestamp={timestamp}"
    
    # 3. 计算 HMAC
    expected_signature = hmac.new(
        get_hmac_secret_key().encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return expected_signature

splits = {",", "，", ":", "：", ";", "；", "、", ".", "。", "?", "？", "!", "！", "~", "…"}
punc_pattern = r"[" + "".join(sorted(splits)) + r"]"
sentence_mark = {".", "。", "?", "？", "!", "！", "…"}
sentence_pattern = r"[" + "".join(sorted(sentence_mark)) + r"]"

# 定义不同标点符号的停顿时长（秒）
# 句号、问号、感叹号：0.5秒（500ms）
# 分号、冒号：0.3秒（300ms）
# 逗号、顿号：0.2秒（200ms）
PUNCTUATION_PAUSE_DURATION = {
    # 句号、问号、感叹号 - 长停顿
    ".": 0.5, "。": 0.5,
    "?": 0.5, "？": 0.5,
    "!": 0.5, "！": 0.5,
    "…": 0.5,
    # 分号、冒号 - 中等停顿
    ";": 0.3, "；": 0.3,
    ":": 0.3, "：": 0.3,
    # 逗号、顿号 - 短停顿
    ",": 0.2, "，": 0.2,
    "、": 0.2,
}

def get_first(text):
    text = re.split(punc_pattern, text)[0].strip()
    return text

def is_whole_sentence(text):
    if text[-1] in sentence_mark :
        return True
    return False

def get_pause_duration(text):
    """
    根据文本末尾的标点符号返回停顿时长（秒）
    
    Args:
        text: 文本字符串
        
    Returns:
        float: 停顿时长（秒），如果没有标点符号或不在字典中，返回0
    """
    if not text:
        return 0.0
    last_char = text[-1]
    return PUNCTUATION_PAUSE_DURATION.get(last_char, 0.0)

def get_all_pause_durations():
    """
    获取所有可能的停顿时长值（去重后的唯一值列表）
    
    Returns:
        list: 所有唯一的停顿时长值（秒），按降序排列
    """
    unique_durations = sorted(set(PUNCTUATION_PAUSE_DURATION.values()), reverse=True)
    return unique_durations

def split(todo_text):
    todo_text = todo_text.replace("……", "。").replace("——", "，")
    if todo_text[-1] not in splits:
        todo_text += "。"
    i_split_head = i_split_tail = 0
    len_text = len(todo_text)
    todo_texts = []
    while 1:
        if i_split_head >= len_text:
            break  # 结尾一定有标点，所以直接跳出即可，最后一段在上次已加入
        if todo_text[i_split_head] in splits:
            i_split_head += 1
            todo_texts.append(todo_text[i_split_tail:i_split_head])
            i_split_tail = i_split_head
        else:
            i_split_head += 1
    return todo_texts


def cut5(inp, lang='zh'):
    # if not re.search(r'[^\w\s]', inp[-1]):
    # inp += '。'
    inp = inp.strip("\n")
    #items = re.split(punc_pattern, inp)
    items = re.split(f'({punc_pattern})', inp)
    mergeitems = ["".join(group) for group in zip(items[::2], items[1::2])]
    # 在句子不存在符号或句尾无符号的时候保证文本完整
    if len(items)%2 == 1:
        mergeitems.append(items[-1])
    opt = "\n".join(mergeitems)
    return opt

def cut_custom(inp, lang='zh'):
    # if not re.search(r'[^\w\s]', inp[-1]):
    # inp += '。'
    inp = inp.strip("\n")
    if lang=='en' :
        punds = r'[.?!。]'
    else :
        punds = r'[?!。？！]'
    items = re.split(f'({punds})', inp)
    mergeitems = ["".join(group) for group in zip(items[::2], items[1::2])]
    # 在句子不存在符号或句尾无符号的时候保证文本完整
    if len(items)%2 == 1:
        mergeitems.append(items[-1])
    opt = "\n".join(mergeitems)
    return opt

def process_text(texts):
    _text = []
    for text in texts:
        if text in [None, " ", ""]:
            pass
        else:
            _text.append(text)
    return _text

def clean_text_inf(text, language, version="v2"):
    phones, word2ph, norm_text = clean_text(text, language, version)
    # print(f'clean_text_info 111, text:{text}, language:{language}, version:{version}, phones:{phones}, word2ph:{word2ph}, norm_text:{norm_text}')
    phones = cleaned_text_to_sequence(phones, version)
    #logger.debug(f'****clean_text_info 222, phones:{phones}')
    return phones, word2ph, norm_text

def get_bert_feature(text, word2ph, bert_model, tokenizer):
    device = get_device()
    with torch.no_grad():
        #logger.debug(f'**** get_bert_feature 1111, text len:{len(text)} text:{text}')
        inputs = tokenizer(text, return_tensors="pt")
        #logger.debug(f'**** get_bert_feature 2222, inputs len:{len(inputs)} inputs:{inputs}')
        for i in inputs:
            #logger.debug(f'**** get_bert_feature 22222, i:{i} inputs[i]:{inputs[i]}')
            inputs[i] = inputs[i].to(device)
        res = bert_model(**inputs, output_hidden_states=True)
        res = torch.cat(res["hidden_states"][-3:-2], -1)[0].cpu()[1:-1]
    assert len(word2ph) == len(text)
    phone_level_feature = []
    for i in range(len(word2ph)):
        repeat_feature = res[i].repeat(word2ph[i], 1)
        #logger.debug(f'**** get_bert_feature 3333, i:{i} repeat_feature:{repeat_feature} res[i]:{res[i]}')
        phone_level_feature.append(repeat_feature)
    #logger.debug(f'**** get_bert_feature 44444, inputs len:{len(inputs)} inputs:{inputs}')
    phone_level_feature = torch.cat(phone_level_feature, dim=0)
    return phone_level_feature.T

def get_bert_inf(phones, word2ph, norm_text, language,  bert_model, tokenizer):
    language=language.replace("all_","")
    device = get_device()
    is_half = ishalf()
    if language == "zh":
        bert = get_bert_feature(norm_text, word2ph, bert_model, tokenizer).to(device)#.to(dtype)
    else:
        bert = torch.zeros(
            (1024, len(phones)),
            dtype=torch.float16 if is_half == True else torch.float32,
        ).to(device)

    return bert

def get_phones_and_bert(text,language, bert_model, tokenizer, version='v2', final=False):
    device = get_device()
    is_half = ishalf()
    dtype = get_dtype(is_half=is_half)

    if language in {"en","all_zh","all_ja","all_ko","all_yue"}:
        language = language.replace("all_","")
        if language == "en":

            LangSegment.setfilters(["en"])
            formattext = " ".join(tmp["text"] for tmp in LangSegment.getTexts(text))
        else:
            # 因无法区别中日文汉字,以用户输入为准
            formattext = text
        while "  " in formattext:
            formattext = formattext.replace("  ", " ")
        if language == "zh":
            if re.search(r'[A-Za-z]', formattext):
                formattext = re.sub(r'[a-z]', lambda x: x.group(0).upper(), formattext)
                formattext = chinese.mix_text_normalize(formattext)
                return get_phones_and_bert(formattext,"zh", bert_model, tokenizer, version)
            else:
                phones, word2ph, norm_text = clean_text_inf(formattext, language, version)
                bert = get_bert_feature(norm_text, word2ph, bert_model, tokenizer).to(device)
        elif language == "yue" and re.search(r'[A-Za-z]', formattext):
                formattext = re.sub(r'[a-z]', lambda x: x.group(0).upper(), formattext)
                formattext = chinese.mix_text_normalize(formattext)
                return get_phones_and_bert(formattext,"yue", bert_model, tokenizer, version)
        else:
            phones, word2ph, norm_text = clean_text_inf(formattext, language, version)
            bert = torch.zeros(
                (1024, len(phones)),
                dtype=torch.float16 if is_half == True else torch.float32,
            ).to(device)
    elif language in {"zh", "ja", "ko", "yue", "auto", "auto_yue"}:
        textlist=[]
        langlist=[]
        LangSegment.setfilters(["zh","ja","en","ko"])

        # print(f'get_phones_and_bert 000, text:{text}, language:{language}')
        if language == "auto":
            for tmp in LangSegment.getTexts(text):
                #print(f'get_phones_and_bert 000 - 1, text:{tmp["text"]}, language:{tmp["lang"]}')
                langlist.append(tmp["lang"])
                textlist.append(tmp["text"])
        elif language == "auto_yue":
            for tmp in LangSegment.getTexts(text):
                if tmp["lang"] == "zh":
                    tmp["lang"] = "yue"
                langlist.append(tmp["lang"])
                textlist.append(tmp["text"])
        else:
            for tmp in LangSegment.getTexts(text):
                if tmp["lang"] == "en":
                    langlist.append(tmp["lang"])
                else:
                    # 因无法区别中日韩文汉字,以用户输入为准
                    langlist.append(language)
                textlist.append(tmp["text"])
        # print(f'get_phones_and_bert 111, textlist:{textlist}, langlist:{langlist}')
        phones_list = []
        bert_list = []
        norm_text_list = []
        for i in range(len(textlist)):
            lang = langlist[i]
            phones, word2ph, norm_text = clean_text_inf(textlist[i], lang, version)
            bert = get_bert_inf(phones, word2ph, norm_text, lang,  bert_model, tokenizer)
            phones_list.append(phones)
            norm_text_list.append(norm_text)
            bert_list.append(bert)
        # 如果 bert_list 为空，说明 LangSegment 无法识别文本语言，使用 fallback 处理
        if len(bert_list) == 0:
            # 对于 auto 语言，使用英文作为默认语言；其他情况使用指定语言
            fallback_lang = "en" if language in {"auto", "auto_yue"} else language
            phones, word2ph, norm_text = clean_text_inf(text, fallback_lang, version)
            bert = get_bert_inf(phones, word2ph, norm_text, fallback_lang, bert_model, tokenizer)
            if not final and len(phones) < 6:
                return get_phones_and_bert("." + text, language, bert_model, tokenizer, version, final=True)
            return phones, bert.to(dtype), norm_text
        bert = torch.cat(bert_list, dim=1)
        phones = sum(phones_list, [])
        norm_text = ''.join(norm_text_list)

    if not final and len(phones) < 6:
        return get_phones_and_bert("." + text,language, bert_model, tokenizer, version,final=True)

    return phones,bert.to(dtype),norm_text

def merge_short_text_in_array(texts, threshold):
    if (len(texts)) < 2:
        return texts
    result = []
    text = ""
    for ele in texts:
        text += ele
        if len(text) >= threshold:
            result.append(text)
            text = ""
    if (len(text) > 0):
        if len(result) == 0:
            result.append(text)
        else:
            result[len(result) - 1] += text
    return result

def split_long_sentence(texts, threshold):
    # 定义分隔符
    sub_separators = set(",，:：、")
    result = []

    #print(f"split_long_sentence start, texts:{texts}")
    for sentence in texts:
        # 如果句子长度小于阈值，直接添加
        if len(sentence) <= threshold:
            result.append(sentence)
            continue
            
        # 对超长句子进行切分
        pattern = "[" + "".join(re.escape(sep) for sep in sub_separators) + "]"
        sub_sentences = re.split(pattern, sentence)
        
        # 临时存储当前组合的句子
        current = ""    
        # 处理切分后的子句
        for sub in sub_sentences:
            sub = sub.strip()
            if not sub:  # 跳过空字符串
                continue
                
            # 如果当前组合加上新子句不超过阈值，则继续组合
            if len(current) + len(sub) <= threshold:
                current = (current + "，" + sub).strip("，")
            else:
                # 如果当前组合不为空，添加到结果中
                if current:
                    result.append(current)
                current = sub
        
        # 添加最后一个组合
        if current:
            result.append(current)
    
    print(f"split_long_sentence end, result:{result}")
    return result

#def get_spepc(hps, filename):
#    audio = load_audio(filename, int(hps.data.sampling_rate))
#    audio = torch.FloatTensor(audio)
#    audio_norm = audio
#    audio_norm = audio_norm.unsqueeze(0)
#    spec = spectrogram_torch(
#        audio_norm,
#        hps.data.filter_length,
#        hps.data.sampling_rate,
#        hps.data.hop_length,
#        hps.data.win_length,
#        center=False,
#    )
#    return spec

resample_transform_dict = {}

def resample(audio_tensor, sr0, sr1, device):
    global resample_transform_dict
    key = "%s-%s-%s" % (sr0, sr1, str(device))
    if key not in resample_transform_dict:
        resample_transform_dict[key] = torchaudio.transforms.Resample(sr0, sr1).to(device)
    return resample_transform_dict[key](audio_tensor)

def get_spepc(hps, filename, dtype, device, is_v2pro=False):
    sr1 = int(hps.data.sampling_rate)
    # audio, sr0 = torchaudio.load(filename)
    # 使用 librosa.load() 替代 torchaudio.load()，参考 voice_train_process.py 的用法
    audio_np, sr0 = librosa.load(filename, sr=None)
    # 转换为 torch tensor，librosa 返回的是 [samples] 形状的 numpy 数组
    audio = torch.from_numpy(audio_np).float().unsqueeze(0)  # [1, samples]
    if sr0 != sr1:
        audio = audio.to(device)
        if audio.shape[0] == 2:
            audio = audio.mean(0).unsqueeze(0)
        audio = resample(audio, sr0, sr1, device)
    else:
        audio = audio.to(device)
        if audio.shape[0] == 2:
            audio = audio.mean(0).unsqueeze(0)

    maxx = audio.abs().max()
    if maxx > 1:
        audio /= min(2, maxx)
    spec = spectrogram_torch(
        audio,
        hps.data.filter_length,
        hps.data.sampling_rate,
        hps.data.hop_length,
        hps.data.win_length,
        center=False,
    )
    spec = spec.to(dtype)
    if is_v2pro == True:
        audio = resample(audio, sr1, 16000, device).to(dtype)
    return spec, audio
