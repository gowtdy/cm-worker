import os 
import time
from indextts.infer_v2 import IndexTTS2

from utility.util import get_device

def load_indextts_model(logger, model_dir="/app/models/indextts2-model", load_speaker_extractor=False, load_emotion_model=False):
    cfg_path = f"{model_dir}/config.yaml"
    if not os.path.isfile(cfg_path):
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    device = get_device()
    logger.debug(f"Loading IndexTTS2 model from {model_dir} on device {device}")

    logger.debug(">> Loading Index-TTS model...")
    t_load_start = time.perf_counter()
    indextts_model = IndexTTS2(
        model_dir=model_dir,
        cfg_path=cfg_path,
        use_fp16=True,
        device=device,
        use_cuda_kernel=True,
        use_deepspeed=False,
        use_accel=True,
        use_torch_compile=False,
        load_speaker_extractor=load_speaker_extractor,
        load_emotion_model=load_emotion_model,
    )
    t_load_end = time.perf_counter()
    logger.debug(f">> Index-TTS model loaded in {t_load_end - t_load_start:.2f} seconds")
    return indextts_model

def indextts_infer(logger, indextts_model, speaker_pt_path, text):
    
    # 原始推理模式
    #def infer(self, spk_audio_prompt, text, output_path,
    #          emo_audio_prompt=None, emo_alpha=1.0,
    #          emo_vector=None,
    #          use_emo_text=False, emo_text=None, use_random=False, interval_silence=200,
    #          verbose=False, max_text_tokens_per_segment=120, more_segment_before=0,
    #          segment_batch_size=1, **generation_kwargs):
    logger.debug(f">> Index-TTS infer start, speaker_pt_path: {speaker_pt_path}, text: {text}")
    return indextts_model.infer(
        spk_audio_prompt = speaker_pt_path, 
        text = text, 
        segment_batch_size = 2,
        verbose = True,
        logger = logger,
        )

if __name__ == "__main__":
    indextts_model = load_indextts_model()
    indextts_infer(indextts_model, "Hello, how are you?")