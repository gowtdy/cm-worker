import torch
import gc 
from fairseq import checkpoint_utils

from infer.lib.infer_pack.models import (
    SynthesizerTrnMs256NSFsid,
    SynthesizerTrnMs256NSFsid_nono,
    SynthesizerTrnMs768NSFsid,
    SynthesizerTrnMs768NSFsid_nono,
)
from infer.modules.vc.pipeline import Pipeline
from infer.lib.rmvpe import RMVPE

#from tools.uvr5.mdxnet import MDXNetDereverb
#from tools.uvr5.vr import AudioPre, AudioPreDeEcho
#from tools.uvr5.bsroformer import BsRoformer_Loader

from infer.modules.uvr5.bs_roformer.bs_roformer import BSRoformer
from infer.modules.uvr5.mdxnet import MDXNetDereverb, ConvTDFNetTrim
from infer.modules.uvr5.vr import AudioPre, AudioPreDeEcho
from infer.lib.uvr5_pack.lib_v5 import nets_61968KB as Nets
from infer.lib.uvr5_pack.lib_v5.nets_new import CascadedNet
from infer.lib.uvr5_pack.lib_v5.model_param_init import ModelParameters

from utility.log import setup_logger

logger = setup_logger(logger_name="voice_cover_server", log_file='logs/voice_cover_log.txt', console_level="DEBUG", file_level="DEBUG")

synthesizer_class = {
            ("v1", 1): SynthesizerTrnMs256NSFsid,
            ("v1", 0): SynthesizerTrnMs256NSFsid_nono,
            ("v2", 1): SynthesizerTrnMs768NSFsid,
            ("v2", 0): SynthesizerTrnMs768NSFsid_nono,
        }

def load_hubert(hubert_path, device, is_half):
    models, _, _ = checkpoint_utils.load_model_ensemble_and_task(
        [hubert_path],
        suffix="",
    )
    hubert_model = models[0]
    hubert_model = hubert_model.to(device)
    if is_half:
        hubert_model = hubert_model.half()
    else:
        hubert_model = hubert_model.float()
    return hubert_model.eval()

def device_config(device, is_half) -> tuple:
    if torch.cuda.is_available():
        i_device = int(device.split(":")[-1])
        gpu_name = torch.cuda.get_device_name(i_device)
        gpu_mem = int(
            torch.cuda.get_device_properties(i_device).total_memory
            / 1024
            / 1024
            / 1024
            + 0.4
        )
        if gpu_mem <= 4:
            preprocess_per = 3.0

        if is_half:
            # 6G显存配置
            x_pad = 3
            x_query = 10
            x_center = 60
            x_max = 65
        else:
            # 5G显存配置
            x_pad = 1
            x_query = 6
            x_center = 38
            x_max = 41

        if gpu_mem is not None and gpu_mem <= 4:
            x_pad = 1
            x_query = 5
            x_center = 30
            x_max = 32
        return x_pad, x_query, x_center, x_max

def load_convert_model(model_file_path, device, is_half, rmvpe_model = None):
    #logger.info(f'load_convert_model 11, model_file_path:{model_file_path}, device:{device}, is_half:{is_half}, rmvpe_model:{rmvpe_model}')
    person_model = torch.load(model_file_path, map_location="cpu")
    tgt_sr = person_model["config"][-1]
    person_model["config"][-3] = person_model["weight"]["emb_g.weight"].shape[0] # n_spk
    if_f0 = person_model.get("f0", 1)
    version = person_model.get("version", "v2")

    #logger.info(f'load_convert_model 44, version:{version}, if_f0:{if_f0}, is_half:{is_half}, person_model["config"]:{person_model["config"]}')
    net_g = synthesizer_class.get(
            (version, if_f0), SynthesizerTrnMs768NSFsid
        )(*person_model["config"], is_half=is_half)
    del net_g.enc_q
    net_g.load_state_dict(person_model["weight"], strict=False)
    net_g.eval().to(device)
    if is_half:
        net_g = net_g.half()
    else :
        net_g = net_g.float()

    #logger.info(f'load_convert_model 55, net_g:{net_g}')
    #logger.info(f'version: {version}, if_f0: {if_f0}, device: {device}, is_half: {is_half}, tgt_sr: {tgt_sr}')
    (x_pad, x_query, x_center, x_max) = device_config(device, is_half)
    pipline = Pipeline(tgt_sr, device, is_half, x_pad, x_query, x_center, x_max, rmvpe_model)
    #n_spk = person_model["config"][-3]
    #logger.info(f'load_convert_model 66, net_g:{net_g}')
    #logger.info(f'x_pad: {x_pad}, x_query: {x_query}, x_center: {x_center}, x_max: {x_max}, pipline: {pipline}')
    return net_g, pipline, tgt_sr, if_f0, version

def unload_convert_model(net_g, pipeline):
    try:
        # 删除模型
        if net_g is not None:
            del net_g
        if pipeline is not None:
            del pipeline
        # 清理CUDA缓存
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            gc.collect()
    except Exception as e:
        print(f'Error unloading convert model: {e}')

def load_bsroformer_model(model_path, device, is_half):
    bsroformer_config = {
        "attn_dropout": 0.1,
        "depth": 12,
        "dim": 512,
        "dim_freqs_in": 1025,
        "dim_head": 64,
        "ff_dropout": 0.1,
        "flash_attn": True,
        "freq_transformer_depth": 1,
        "freqs_per_bands":(2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 12, 12, 12, 12, 12, 12, 12, 12, 24, 24, 24, 24, 24, 24, 24, 24, 48, 48, 48, 48, 48, 48, 48, 48, 128, 129),
        "heads": 8,
        "linear_transformer_depth": 0,
        "mask_estimator_depth": 2,
        "multi_stft_hop_size": 147,
        "multi_stft_normalized": False,
        "multi_stft_resolution_loss_weight": 1.0,
        "multi_stft_resolutions_window_sizes":(4096, 2048, 1024, 512, 256),
        "num_stems": 1,
        "stereo": True,
        "stft_hop_length": 441,
        "stft_n_fft": 2048,
        "stft_normalized": False,
        "stft_win_length": 2048,
        "time_transformer_depth": 1,
    }

    model = BSRoformer(
        **dict(bsroformer_config)
    )

    state_dict = torch.load(model_path,map_location="cpu")
    model.load_state_dict(state_dict)
    if(is_half==False):
        model = model.to(device)
    else:
        model = model.half().to(device)
    return model

def load_hpkaroke_model(model_path, device, is_half):
    mp = ModelParameters("infer/lib/uvr5_pack/lib_v5/modelparams/4band_v2.json")
    model = Nets.CascadedASPPNet(mp.param["bins"] * 2)
    cpk = torch.load(model_path, map_location="cpu")
    model.load_state_dict(cpk)
    model.eval()
    if is_half:
        model = model.half().to(device)
    else:
        model = model.to(device)
    return model

def load_mdxnetdereverb_model(model_path, device, is_half):
    import onnxruntime as ort
    from infer.modules.uvr5.mdxnet import ConvTDFNetTrim
    dim_t = 9
    dim_f = 3072
    n_fft = 6144
    model_tdfnet = ConvTDFNetTrim(
        device=device,
        model_name="Conv-TDF",
        target_name="vocals",
        L=11,
        dim_f=dim_f,
        dim_t=dim_t,
        n_fft=n_fft,
    )
    model_infer = ort.InferenceSession(
        model_path,
        providers=[
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ],
    )
    return model_infer, model_tdfnet

def load_deecho_model(model_path, device, is_half):
    mp = ModelParameters("infer/lib/uvr5_pack/lib_v5/modelparams/4band_v3.json")
    nout = 64 if "DeReverb" in model_path else 48
    model = CascadedNet(mp.param["bins"] * 2, nout)
    cpk = torch.load(model_path, map_location="cpu")
    model.load_state_dict(cpk)
    model.eval()
    if is_half:
        model = model.half().to(device)
    else:
        model = model.to(device)
    return model

def load_rmvpe_model(model_path, device, is_half):
    return RMVPE(model_path, is_half=is_half, device=device)