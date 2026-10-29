import os
import pdb
import re
import sys

import cn2an
from pypinyin import lazy_pinyin, Style
from pypinyin_g2pw import G2PWPinyin

now_dir = os.getcwd()
sys.path.append(now_dir)
from text.symbols import punctuation
from text.tone_sandhi import ToneSandhi
from text.tone_sandhi_g2pw import ToneSandhiG2pw

from text.zh_normalization.text_normlization import TextNormalizer

normalizer = lambda x: cn2an.transform(x, "an2cn")

current_file_path = os.path.dirname(__file__)
pinyin_to_symbol_map = {
    line.split("\t")[0]: line.strip().split("\t")[1]
    for line in open(os.path.join(current_file_path, "opencpop-strict.txt")).readlines()
}

import jieba_fast.posseg as psg


rep_map = {
    "：": ",",
    "；": ",",
    "，": ",",
    "。": ".",
    "！": "!",
    "？": "?",
    "\n": ".",
    "·": ",",
    "、": ",",
    "...": "…",
    "$": ".",
    "/": ",",
    "—": "-",
    "~": "…",
    "～":"…",
}

tone_modifier = ToneSandhi()
tone_modifier_g2pw = ToneSandhiG2pw()


def replace_punctuation(text):
    text = text.replace("嗯", "恩").replace("呣", "母")
    pattern = re.compile("|".join(re.escape(p) for p in rep_map.keys()))

    replaced_text = pattern.sub(lambda x: rep_map[x.group()], text)

    replaced_text = re.sub(
        r"[^\u4e00-\u9fa5" + "".join(punctuation) + r"]+", "", replaced_text
    )

    return replaced_text


def replace_punctuation_with_en(text):
    text = text.replace("嗯", "恩").replace("呣", "母")
    pattern = re.compile("|".join(re.escape(p) for p in rep_map.keys()))

    replaced_text = pattern.sub(lambda x: rep_map[x.group()], text)

    replaced_text = re.sub(
        r"[^\u4e00-\u9fa5A-Za-z" + "".join(punctuation) + r"]+", "", replaced_text
    )

    return replaced_text


def replace_consecutive_punctuation(text):
    punctuations = ''.join(re.escape(p) for p in punctuation)
    pattern = f'([{punctuations}])([{punctuations}])+'
    result = re.sub(pattern, r'\1', text)
    return result

def g2p(text):
    #print(f'++++ g2p 111, puncation:{punctuation}, text:{text}')
    pattern = r"(?<=[{0}])\s*".format("".join(punctuation))
    #print(f'++++ g2p 222, pattern:{pattern}')
    sentences = [i for i in re.split(pattern, text) if i.strip() != ""]
    #print(f'++++ g2p 3333, sentences:{sentences}')
    phones, word2ph = _g2p(sentences)
    #print(f'++++ g2p 4444, phones:{phones}, word2ph:{word2ph}')
    return phones, word2ph

def g2pw(text, g2pw_model):
    #print(f'++++ g2p 111, puncation:{punctuation}, text:{text}')
    pattern = r"(?<=[{0}])\s*".format("".join(punctuation))
    #print(f'++++ g2p 222, pattern:{pattern}')
    sentences = [i for i in re.split(pattern, text) if i.strip() != ""]
    #print(f'++++ g2p 3333, sentences:{sentences}')
    phones, word2ph = _g2pw(sentences, g2pw_model)
    #print(f'++++ g2p 4444, phones:{phones}, word2ph:{word2ph}')
    return phones, word2ph


def _get_initials_finals(word):
    initials = []
    finals = []
    orig_initials = lazy_pinyin(word, neutral_tone_with_five=True, style=Style.INITIALS)
    orig_finals = lazy_pinyin(
        word, neutral_tone_with_five=True, style=Style.FINALS_TONE3
    )
    for c, v in zip(orig_initials, orig_finals):
        initials.append(c)
        finals.append(v)
    return initials, finals


def _g2p(segments):
    phones_list = []
    word2ph = []
    for seg in segments:
        #print(f'****** _g2p 111, seg:{seg}')
        pinyins = []
        # Replace all English words in the sentence
        seg = re.sub("[a-zA-Z]+", "", seg)
        seg_cut = psg.lcut(seg)
        #print(f'****** _g2p 222, seg_cut:{seg_cut}')
        initials = []
        finals = []
        seg_cut = tone_modifier.pre_merge_for_modify(seg_cut)
        #print(f'****** _g2p 333, seg_cut:{seg_cut}')
        for word, pos in seg_cut:
            #print(f'****** _g2p 444, word:{word}, pos:{pos}')
            if pos == "eng":
                continue
            sub_initials, sub_finals = _get_initials_finals(word)
            #print(f'****** _g2p 555, sub_initials:{sub_initials}, sub_finals:{sub_finals}')
            sub_finals = tone_modifier.modified_tone(word, pos, sub_finals)
            #print(f'****** _g2p 666, sub_finals:{sub_finals}')
            initials.append(sub_initials)
            finals.append(sub_finals)

            # assert len(sub_initials) == len(sub_finals) == len(word)
        #print(f'****** _g2p 777, initials:{initials} finals:{finals}')
        initials = sum(initials, [])
        finals = sum(finals, [])
        #print(f'****** _g2p 888, initials:{initials} finals:{finals}')
        #
        for c, v in zip(initials, finals):
            raw_pinyin = c + v
            #print(f'****** _g2p 999, c:{c} v:{v} raw_pinyin:{raw_pinyin}')
            # NOTE: post process for pypinyin outputs
            # we discriminate i, ii and iii
            if c == v:
                assert c in punctuation
                phone = [c]
                word2ph.append(1)
            else:
                v_without_tone = v[:-1]
                tone = v[-1]

                pinyin = c + v_without_tone
                assert tone in "12345"

                #print(f'****** _g2p 10000, pinyin:{pinyin} v_without_tone:{v_without_tone} tone:{tone}')
                if c:
                    # 多音节
                    v_rep_map = {
                        "uei": "ui",
                        "iou": "iu",
                        "uen": "un",
                    }
                    if v_without_tone in v_rep_map.keys():
                        pinyin = c + v_rep_map[v_without_tone]
                    #print(f'****** _g2p 10011, pinyin:{pinyin} v_without_tone:{v_without_tone} c:{c}')
                else:
                    # 单音节
                    pinyin_rep_map = {
                        "ing": "ying",
                        "i": "yi",
                        "in": "yin",
                        "u": "wu",
                    }
                    if pinyin in pinyin_rep_map.keys():
                        pinyin = pinyin_rep_map[pinyin]
                        #print(f'****** _g2p 10022, pinyin:{pinyin} v_without_tone:{v_without_tone} c:{c}')
                    else:
                        single_rep_map = {
                            "v": "yu",
                            "e": "e",
                            "i": "y",
                            "u": "w",
                        }
                        if pinyin[0] in single_rep_map.keys():
                            pinyin = single_rep_map[pinyin[0]] + pinyin[1:]
                        #print(f'****** _g2p 10033, pinyin:{pinyin} v_without_tone:{v_without_tone} c:{c}')

                assert pinyin in pinyin_to_symbol_map.keys(), (pinyin, seg, raw_pinyin)
                new_c, new_v = pinyin_to_symbol_map[pinyin].split(" ")
                new_v = new_v + tone
                phone = [new_c, new_v]
                #print(f'****** _g2p 10044, pinyin:{pinyin} new_c:{new_c} new_v:{new_v} tone:{tone}')
                word2ph.append(len(phone))

            phones_list += phone
            #print(f'****** _g2p 10055, phones_list:{phones_list} word2ph:{word2ph}')
    return phones_list, word2ph

def _get_initials_finals_g2pw(word, g2pw_model):
    initials = []
    finals = []
    orig_initials = g2pw_model.lazy_pinyin(word, neutral_tone_with_five=True, style=Style.INITIALS)
    orig_finals = g2pw_model.lazy_pinyin(
        word, neutral_tone_with_five=True, style=Style.FINALS_TONE3
    )
    for c, v in zip(orig_initials, orig_finals):
        initials.append(c)
        finals.append(v)
    return initials, finals

def _g2pw(segments, g2pw_model):
    phones_list = []
    word2ph = []
    for seg in segments:
        #print(f'****** _g2p 111, seg:{seg}')
        pinyins = []
        # Replace all English words in the sentence
        seg = re.sub("[a-zA-Z]+", "", seg)
        seg_cut = psg.lcut(seg)
        #print(f'****** _g2p 222, seg_cut:{seg_cut}')
        initials = []
        finals = []
        seg_cut = tone_modifier_g2pw.pre_merge_for_modify(seg_cut, g2pw_model)
        #print(f'****** _g2p 333, seg_cut:{seg_cut}')
        for word, pos in seg_cut:
            #print(f'****** _g2p 444, word:{word}, pos:{pos}')
            if pos == "eng":
                continue
            sub_initials, sub_finals = _get_initials_finals_g2pw(word, g2pw_model)
            #print(f'****** _g2p 555, sub_initials:{sub_initials}, sub_finals:{sub_finals}')
            sub_finals = tone_modifier_g2pw.modified_tone(word, pos, sub_finals)
            #print(f'****** _g2p 666, sub_finals:{sub_finals}')
            initials.append(sub_initials)
            finals.append(sub_finals)

            # assert len(sub_initials) == len(sub_finals) == len(word)
        #print(f'****** _g2p 777, initials:{initials} finals:{finals}')
        initials = sum(initials, [])
        finals = sum(finals, [])
        #print(f'****** _g2p 888, initials:{initials} finals:{finals}')
        #
        for c, v in zip(initials, finals):
            raw_pinyin = c + v
            #print(f'****** _g2p 999, c:{c} v:{v} raw_pinyin:{raw_pinyin}')
            # NOTE: post process for pypinyin outputs
            # we discriminate i, ii and iii
            if c == v:
                assert c in punctuation
                phone = [c]
                word2ph.append(1)
            else:
                v_without_tone = v[:-1]
                tone = v[-1]

                pinyin = c + v_without_tone
                assert tone in "12345"

                #print(f'****** _g2p 10000, pinyin:{pinyin} v_without_tone:{v_without_tone} tone:{tone}')
                if c:
                    # 多音节
                    v_rep_map = {
                        "uei": "ui",
                        "iou": "iu",
                        "uen": "un",
                    }
                    if v_without_tone in v_rep_map.keys():
                        pinyin = c + v_rep_map[v_without_tone]
                    #print(f'****** _g2p 10011, pinyin:{pinyin} v_without_tone:{v_without_tone} c:{c}')
                else:
                    # 单音节
                    pinyin_rep_map = {
                        "ing": "ying",
                        "i": "yi",
                        "in": "yin",
                        "u": "wu",
                    }
                    if pinyin in pinyin_rep_map.keys():
                        pinyin = pinyin_rep_map[pinyin]
                        #print(f'****** _g2p 10022, pinyin:{pinyin} v_without_tone:{v_without_tone} c:{c}')
                    else:
                        single_rep_map = {
                            "v": "yu",
                            "e": "e",
                            "i": "y",
                            "u": "w",
                        }
                        if pinyin[0] in single_rep_map.keys():
                            pinyin = single_rep_map[pinyin[0]] + pinyin[1:]
                        #print(f'****** _g2p 10033, pinyin:{pinyin} v_without_tone:{v_without_tone} c:{c}')

                assert pinyin in pinyin_to_symbol_map.keys(), (pinyin, seg, raw_pinyin)
                new_c, new_v = pinyin_to_symbol_map[pinyin].split(" ")
                new_v = new_v + tone
                phone = [new_c, new_v]
                #print(f'****** _g2p 10044, pinyin:{pinyin} new_c:{new_c} new_v:{new_v} tone:{tone}')
                word2ph.append(len(phone))

            phones_list += phone
            #print(f'****** _g2p 10055, phones_list:{phones_list} word2ph:{word2ph}')
    return phones_list, word2ph

def text_normalize(text):
    # https://github.com/PaddlePaddle/PaddleSpeech/tree/develop/paddlespeech/t2s/frontend/zh_normalization
    tx = TextNormalizer()
    sentences = tx.normalize(text)
    dest_text = ""
    for sentence in sentences:
        dest_text += replace_punctuation(sentence)

    # 避免重复标点引起的参考泄露
    dest_text = replace_consecutive_punctuation(dest_text)
    return dest_text


# 不排除英文的文本格式化
def mix_text_normalize(text):
    # https://github.com/PaddlePaddle/PaddleSpeech/tree/develop/paddlespeech/t2s/frontend/zh_normalization
    tx = TextNormalizer()
    sentences = tx.normalize(text)
    dest_text = ""
    for sentence in sentences:
        dest_text += replace_punctuation_with_en(sentence)

    # 避免重复标点引起的参考泄露
    dest_text = replace_consecutive_punctuation(dest_text)
    return dest_text


if __name__ == "__main__":
    text = "啊——但是《原神》是由,米哈\游自主，研发的一款全.新开放世界.冒险游戏"
    text = "呣呣呣～就是…大人的鼹鼠党吧？"
    text = "你好"
    text = "我们低估了四川，低估了它隐藏在熊猫"
    text = text_normalize(text)
    print(g2p(text))


# # 示例用法
# text = "这是一个示例文本：,你好！这是一个测试..."
# print(g2p_paddle(text))  # 输出: 这是一个示例文本你好这是一个测试
