import platform, os
import ffmpeg
import numpy as np
import av
from io import BytesIO
import traceback
import re
import librosa
import soundfile as sf
import logging

logger = logging.getLogger(__name__)

def wav2(i, o, format):
    inp = av.open(i, "rb")
    if format == "m4a":
        format = "mp4"
    out = av.open(o, "wb", format=format)
    if format == "ogg":
        format = "libvorbis"
    if format == "mp4":
        format = "aac"

    ostream = out.add_stream(format)

    for frame in inp.decode(audio=0):
        for p in ostream.encode(frame):
            out.mux(p)

    for p in ostream.encode(None):
        out.mux(p)

    out.close()
    inp.close()


def load_audio(file, sr):
    try:
        # https://github.com/openai/whisper/blob/main/whisper/audio.py#L26
        # This launches a subprocess to decode audio while down-mixing and resampling as necessary.
        # Requires the ffmpeg CLI and `ffmpeg-python` package to be installed.
        if os.path.exists(file) == False:
            raise RuntimeError(
                "You input a wrong audio path that does not exists, please fix it!"
            )
        out, _ = (
            ffmpeg.input(file, threads=0)
            .output("-", format="f32le", acodec="pcm_f32le", ac=1, ar=sr)
            .run(cmd=["ffmpeg", "-nostdin"], capture_stdout=True, capture_stderr=True)
        )
    except Exception as e:
        traceback.print_exc()
        raise RuntimeError(f"Failed to load audio: {e}")

    return np.frombuffer(out, np.float32).flatten()


def analyze_audio_complexity(audio_path):
    """分析音频复杂度并推荐agg值"""
    try:
        # 加载音频
        y, sr = librosa.load(audio_path, sr=None)
        # 计算STFT和梅尔频谱图
        S = np.abs(librosa.stft(y))
        mel_spec = librosa.feature.melspectrogram(S=S, sr=sr)
        # 1 基础特征计算
        duration = librosa.get_duration(y=y, sr=sr) # 计算音频时长
        spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0] #计算频谱质心 - 反映音色的"明亮度"
        rms = librosa.feature.rms(y=y)[0]  # 计算RMS能量 - 反映音量变化
        rms_variation = np.std(rms) / np.mean(rms)
        flatness = librosa.feature.spectral_flatness(y=y)[0] # 计算谱平面度 - 反映音频的复杂度
        
        # 2 计算高频能力比例
        freqs = librosa.fft_frequencies(sr=sr)
        high_freq_mask = freqs > 5000
        high_freq_energy = np.sum(S[high_freq_mask]) / np.sum(S)
        
        # 3. 计算频谱对比度
        spectral_contrast = np.mean(librosa.feature.spectral_contrast(y=y, sr=sr))

        # 4. 计算综合复杂度评分
        complexity_score = (
            np.mean(spectral_centroids) / sr * 0.25 +  # 频谱质心 - 反映音色明亮度
            np.std(rms) * 0.25 +                       # 音量变化 - 反映动态范围
            np.mean(flatness) * 0.2 +                  # 谱平面度 - 反映音色复杂度
            np.std(mel_spec) * 0.2 +                   # 频谱变化 - 反映频率变化
            np.percentile(rms, 95) * 0.1               # 音量峰值 - 反映最大音量
        )

        # 根据音频特征返回优化后的参数
        logger.debug(f'++++ analyze_audio_complexity, audio_path:{audio_path}, duration:{duration}, complexity_score:{complexity_score}, rms_variation:{rms_variation}, sr:{sr}, flatness:{flatness}')
        params = {
            # chunks参数 - 基于音频时长和复杂度
            'chunks': min(48, 32 if duration < 30 else (40 if duration < 120 else 48)),
            # shifts参数 - 基于复杂度评分
            'shifts': min(10, 8 if complexity_score < 0.3 else (10 if complexity_score < 0.6 else 15)),
            # mixing策略 - 针对混响调整
            'mixing': 'min_mag',
            # margin参数 - 基于采样率
            'margin': int(sr * 1.5),
            # 基础agg值 - 用于其他处理
            'base_agg': 0 if complexity_score < 0.3 else (3 if complexity_score < 0.6 else 6),
            
            # 音频特征统计
            'complexity_score': complexity_score,
            'rms_variation': rms_variation,
            'duration': duration,
            'high_freq_energy': high_freq_energy
        }
        
        logger.debug(f"Audio analysis results: {params}")
        return params
            
    except Exception as e:
        logger.error(f"audio analysis error: {str(e)}")
        # 返回默认参数
        return {
            'chunks': 40,
            'shifts': 10,
            'mixing': 'min_mag',
            'margin': 44100, # 2秒 at 44.1kHz
            'base_agg': 3,
            'complexity_score': 0.5,
            'rms_variation': 0.5,
            'duration': 0,
            'high_freq_energy': 0.3
        }

