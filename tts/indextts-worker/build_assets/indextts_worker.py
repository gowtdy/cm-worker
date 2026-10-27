import os
import time
from indextts.infer_v2_5 import IndexTTS2

from utility.voice_config import get_device

# IndexTTS 8 维情绪顺序 / 中文含义:
#   default=不使用情绪向量
#   happy=喜, angry=怒, sad=哀, afraid=惧,
#   disgusted=厌恶, melancholic=低落, surprised=惊喜, calm=平静
EMO_TYPES = (
    "default", "happy", "angry", "sad", "afraid",
    "disgusted", "melancholic", "surprised", "calm",
)
EMO_TYPE_INDEX = {
    "happy": 0,        # 喜
    "angry": 1,        # 怒
    "sad": 2,          # 哀
    "afraid": 3,       # 惧
    "disgusted": 4,    # 厌恶
    "melancholic": 5,  # 低落
    "surprised": 6,    # 惊喜
    "calm": 7,         # 平静
}
EMO_BASE_STRENGTH = 1.0


def normalize_emo_type(emo_type):
    """Return lowercase emo_type or raise ValueError if invalid."""
    key = (emo_type if emo_type is not None else "default")
    if not isinstance(key, str):
        raise ValueError(f"emo_type must be a string, got {type(key).__name__}")
    key = key.strip().lower()
    if not key:
        key = "default"
    if key not in EMO_TYPES:
        raise ValueError(
            f"emo_type '{emo_type}' is invalid; allowed: {', '.join(EMO_TYPES)}"
        )
    return key


def parse_emo_alpha(emo_alpha, logger=None):
    """Normalize emo_alpha to [0, 1]: invalid -> 1.0, below 0 -> 0.0, above 1 -> 1.0."""
    if emo_alpha is None or emo_alpha == "":
        return 1.0
    try:
        value = float(emo_alpha)
    except (TypeError, ValueError):
        if logger is not None:
            logger.warning(f"invalid emo_alpha {emo_alpha!r}, fallback to 1.0")
        return 1.0
    if value < 0.0:
        if logger is not None:
            logger.warning(f"emo_alpha {value} below 0.0, clamp to 0.0")
        return 0.0
    if value > 1.0:
        if logger is not None:
            logger.warning(f"emo_alpha {value} above 1.0, clamp to 1.0")
        return 1.0
    return value


def emo_type_to_vector(emo_type):
    """Map emo_type to 8-d vector, or None for default (no emotion control)."""
    key = normalize_emo_type(emo_type)
    if key == "default":
        return None
    vec = [0.0] * 8
    vec[EMO_TYPE_INDEX[key]] = EMO_BASE_STRENGTH
    return vec


def load_indextts_model(logger, model_dir="/app/models/indextts-model", load_speaker_extractor=False, load_emotion_model=True):
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
        use_bf16=True,
        device=device,
        use_cuda_kernel=True,
        use_deepspeed=False,
        use_accel=True,
        use_torch_compile=False,
        load_speaker_extractor=load_speaker_extractor,
        load_emotion_model=load_emotion_model,
        timing_run_id='initialize_models'
    )
    t_load_end = time.perf_counter()
    logger.debug(f">> Index-TTS model loaded in {t_load_end - t_load_start:.2f} seconds")
    return indextts_model

def indextts_infer(logger, indextts_model, speaker_pt_path, text, text_lang='en', duration_factor=1.05, reqid='default',
                   emo_vector=None, emo_alpha=1.0):
    if emo_vector is not None:
        emo_vector = indextts_model.normalize_emo_vec(emo_vector, apply_bias=True)
    logger.debug(
        f">> Index-TTS infer start, speaker_pt_path: {speaker_pt_path}, text: {text}, "
        f"duration_factor: {duration_factor}, emo_vector: {emo_vector}, emo_alpha: {emo_alpha}"
    )
    return indextts_model.infer(
        spk_audio_prompt=speaker_pt_path,
        text=text,
        lang=text_lang,
        emo_vector=emo_vector,
        emo_alpha=emo_alpha,
        duration_factor=duration_factor,
        segment_batch_size=2,
        pipeline_overlap=True,
        verbose=True,
        logger=logger,
        timing_run_id=reqid,
        max_mel_tokens=1200,
    )

if __name__ == "__main__":
    class _PrintLogger:
        def debug(self, msg):
            print(msg)

    _logger = _PrintLogger()
    indextts_model = load_indextts_model(_logger)
    indextts_infer(_logger, indextts_model, "Hello, how are you?", "Hello, how are you?")
