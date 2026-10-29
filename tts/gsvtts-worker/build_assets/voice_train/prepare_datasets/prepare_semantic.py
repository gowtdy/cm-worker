import os

import math, traceback
import multiprocessing
import sys, pdb

parent_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(parent_directory)
from random import shuffle
import torch.multiprocessing as mp
from glob import glob
from tqdm import tqdm
import logging, librosa, utils, torch
from module.models import SynthesizerTrn

logging.getLogger("numba").setLevel(logging.WARNING)

def get_semantic(inp_text, exp_name, opt_dir, i_part, all_parts, pretrained_s2G, s2config_path, device, is_half, version) : 
    print(f'inp_text:{inp_text}, exp_name:{exp_name}, opt_dir:{opt_dir}, i_part:{i_part}, all_parts:{all_parts} \
            pretrained_s2G:{pretrained_s2G}, s2config_path:{s2config_path}, device:{device}, is_half:{is_half}')
    hubert_dir = "%s/4-cnhubert" % (opt_dir)
    semantic_path = "%s/6-name2semantic-%s.tsv" % (opt_dir, i_part)
    if os.path.exists(semantic_path) :
        os.remove(semantic_path)
    os.makedirs(opt_dir, exist_ok=True)
    
    if torch.cuda.is_available():
        device = "cuda"
    #elif torch.backends.mps.is_available():
    #    device = "mps"
    else:
        device = "cpu"
    hps = utils.get_hparams_from_file(s2config_path)
    vq_model = SynthesizerTrn(
        hps.data.filter_length // 2 + 1,
        hps.train.segment_size // hps.data.hop_length,
        n_speakers=hps.data.n_speakers,
        version=version,
        **hps.model
    )
    if is_half == True:
        vq_model = vq_model.half().to(device)
    else:
        vq_model = vq_model.to(device)
    vq_model.eval()
    # utils.load_checkpoint(utils.latest_checkpoint_path(hps.s2_ckpt_dir, "G_*.pth"), vq_model, None, True)
    # utils.load_checkpoint(pretrained_s2G, vq_model, None, True)
    print(
        vq_model.load_state_dict(
            torch.load(pretrained_s2G, map_location="cpu", weights_only=False)["weight"], strict=False
        )
    )
    
    def name2go(wav_name, lines):
        hubert_path = "%s/%s.pt" % (hubert_dir, wav_name)
        if os.path.exists(hubert_path) == False:
            return
        ssl_content = torch.load(hubert_path, map_location="cpu")
        if is_half == True:
            ssl_content = ssl_content.half().to(device)
        else:
            ssl_content = ssl_content.to(device)
        codes = vq_model.extract_latent(ssl_content)
        semantic = " ".join([str(i) for i in codes[0, 0, :].tolist()])
        lines.append("%s\t%s" % (wav_name, semantic))
    
    with open(inp_text, "r", encoding="utf8") as f:
        lines = f.read().strip("\n").split("\n")
    
    lines1 = []
    for line in lines[int(i_part) :: int(all_parts)]:
        # print(line)
        try:
            # wav_name,text=line.split("\t")
            wav_name, spk_name, language, text = line.split("|")
            wav_name = os.path.basename(wav_name)
            # name2go(name,lines1)
            name2go(wav_name, lines1)
        except:
            print(line, traceback.format_exc())
    with open(semantic_path, "w", encoding="utf8") as f:
        f.write("\n".join(lines1))

    return 0
