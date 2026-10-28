import  os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

#logger = logging.getLogger(__name__)

import librosa
import numpy as np
import soundfile as sf
import torch

from infer.lib.uvr5_pack.lib_v5 import nets_61968KB as Nets
from infer.lib.uvr5_pack.lib_v5 import spec_utils
from infer.lib.uvr5_pack.lib_v5.model_param_init import ModelParameters
from infer.lib.uvr5_pack.lib_v5.nets_new import CascadedNet
from infer.lib.uvr5_pack.utils import inference

class VR:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chunk_size = 48000 * 30  # 30秒一块
        self.batch_size = 4  # 批处理大小
        self.n_workers = 4  # 并行工作进程数
        self.executor = ThreadPoolExecutor(max_workers=4)

    def process_audio_chunk(self, X_spec_m, aggressiveness):
        """处理音频片段的基类方法"""
        # 分块处理
        chunks = []
        for i in range(0, X_spec_m.shape[-1], self.chunk_size):
            chunk = X_spec_m[..., i:i + self.chunk_size]
            if chunk.shape[-1] < self.chunk_size:
                # 填充最后一块
                pad_size = self.chunk_size - chunk.shape[-1]
                chunk = np.pad(chunk, ((0, 0), (0, 0), (0, pad_size)), mode='reflect')
            chunks.append(chunk)

        # 批处理
        results = []
        with torch.no_grad():
            for i in range(0, len(chunks), self.batch_size):
                batch = chunks[i:i + self.batch_size]
                batch = np.stack(batch)
                batch = torch.from_numpy(batch).to(self.device)
                if hasattr(self, 'is_half') and self.is_half:
                    batch = batch.half()

                # 并行处理批次
                pred_batch = []
                for chunk in batch:
                    pred, X_mag, X_phase = inference(
                        chunk, self.device, self.model, aggressiveness, self.data
                    )
                    pred_batch.append((pred, X_mag, X_phase))
                results.extend(pred_batch)

        # 合并结果
        pred_all = []
        X_mag_all = []
        X_phase_all = []
        for pred, X_mag, X_phase in results:
            pred_all.append(pred)
            X_mag_all.append(X_mag)
            X_phase_all.append(X_phase)

        pred = np.concatenate(pred_all, axis=-1)
        X_mag = np.concatenate(X_mag_all, axis=-1)
        X_phase = np.concatenate(X_phase_all, axis=-1)

        # 裁剪到原始大小
        orig_size = X_spec_m.shape[-1]
        pred = pred[..., :orig_size]
        X_mag = X_mag[..., :orig_size]
        X_phase = X_phase[..., :orig_size]

        return pred, X_mag, X_phase

    def _path_audio_(self, music_file, vocal_root=None, ins_root=None, output_name=None, format="flac", logger=None):
        start_time = time.time()
        
        # 确保有logger可用
        if logger is None:
            import logging
            logger = logging.getLogger('VR')
            
        if ins_root is None and vocal_root is None:
            return "No save root."
            
        os.makedirs(vocal_root, exist_ok=True) if vocal_root is not None else None
        os.makedirs(ins_root, exist_ok=True) if ins_root is not None else None

        # 读取和预处理音频
        load_start = time.time()
        X_wave, y_wave, X_spec_s = self.prepare_audio(music_file)
        logger.info(f"VR stage {output_name} load audio time: {time.time() - load_start:.2f}s")
        
        # 合并频谱
        stft_start = time.time()
        X_spec_m = spec_utils.combine_spectrograms(X_spec_s, self.mp)
        logger.info(f"VR stage {output_name} stft time: {time.time() - stft_start:.2f}s")
        
        # 设置处理参数
        aggresive_set = float(self.data["agg"] / 100)
        aggressiveness = {
            "value": aggresive_set,
            "split_bin": self.mp.param["band"][1]["crop_stop"],
        }

        # 处理音频
        infer_start = time.time()
        pred, X_mag, X_phase = self.process_audio_chunk(X_spec_m, aggressiveness)
        logger.info(f"VR stage {output_name} infer time: {time.time() - infer_start:.2f}s")
        
        # 后处理
        post_start = time.time()
        if self.data["postprocess"]:
            pred_inv = np.clip(X_mag - pred, 0, np.inf)
            pred = spec_utils.mask_silence(pred, pred_inv)
        y_spec_m = pred * X_phase
        v_spec_m = X_spec_m - y_spec_m
        logger.info(f"VR stage {output_name} post-process time: {time.time() - post_start:.2f}s")

        # 生成输出频谱
        y_spec_m = pred * X_phase
        v_spec_m = X_spec_m - y_spec_m

        # 保存结果
        save_start = time.time()
        self.save_audio(
            y_spec_m, v_spec_m, 
            vocal_root, ins_root, 
            output_name, format
        )
        logger.info(f"VR stage {output_name} save audio time: {time.time() - save_start:.2f}s")
        
        logger.info(f"VR stage {output_name} total time: {time.time() - start_time:.2f}s")

    def prepare_audio(self, music_file):
        """优化的音频预处理"""
        X_wave, y_wave, X_spec_s = {}, {}, {}
        bands_n = len(self.mp.param["band"])
        
        # 并行加载和处理不同频段
        def process_band(d):
            bp = self.mp.param["band"][d]
            if d == bands_n:
                wave, _ = librosa.load(
                    music_file,
                    sr=bp["sr"],
                    mono=False,
                    dtype=np.float32,
                    res_type=bp["res_type"],
                )
                if wave.ndim == 1:
                    wave = np.asfortranarray([wave, wave])
            else:
                wave = librosa.resample(
                    X_wave[d + 1],
                    orig_sr=self.mp.param["band"][d + 1]["sr"],
                    target_sr=bp["sr"],
                    res_type=bp["res_type"],
                )
            
            spec = spec_utils.wave_to_spectrogram_mt(
                wave,
                bp["hl"],
                bp["n_fft"],
                self.mp.param["mid_side"],
                self.mp.param["mid_side_b2"],
                self.mp.param["reverse"],
            )
            return d, wave, spec

        # 串行处理第一个频段（因为其他频段依赖它）
        d = bands_n
        X_wave[d], _, X_spec_s[d] = process_band(d)[1:]

        # 并行处理其余频段
        with ThreadPoolExecutor(max_workers=self.n_workers) as executor:
            futures = []
            for d in range(bands_n-1, 0, -1):
                futures.append(executor.submit(process_band, d))
            
            for future in as_completed(futures):
                d, wave, spec = future.result()
                X_wave[d] = wave
                X_spec_s[d] = spec

        return X_wave, y_wave, X_spec_s

    def apply_postprocessing(self, pred, X_mag):
        """应用后处理"""
        pred_inv = np.clip(X_mag - pred, 0, np.inf)
        return spec_utils.mask_silence(pred, pred_inv)

    def save_audio(self, y_spec_m, v_spec_m, vocal_root, ins_root, output_name, format):
        """优化后的音频保存主方法"""
        save_start = time.time()
        
        # 预先检查目录权限
        if vocal_root:
            self._prepare_save_directory(vocal_root)
        if ins_root:
            self._prepare_save_directory(ins_root)
            
        # 并行转换频谱到波形
        futures = []
        if vocal_root is not None:
            futures.append(
                self.executor.submit(self._get_spectrogram_wave, y_spec_m, self.mp)
            )
        if ins_root is not None:
            futures.append(
                self.executor.submit(self._get_spectrogram_wave, v_spec_m, self.mp)
            )
            
        # 并行保存文件
        save_futures = []
        for i, future in enumerate(futures):
            wav_data = future.result()
            root_dir = vocal_root if i == 0 else ins_root
            suffix = "vocal" if i == 0 else "other"
            save_futures.append(
                self.executor.submit(
                    self._save_file, 
                    wav_data, 
                    root_dir, 
                    output_name, 
                    suffix, 
                    format
                )
            )
            
        # 等待所有保存完成
        for future in save_futures:
            future.result()
            
        return time.time() - save_start

    def _save_file(self, wav_data, root_dir, output_name, suffix, format):
        """优化的音频保存方法"""
        if format in ["wav", "flac"]:
            # 直接保存为目标格式
            sf.write(
                os.path.join(root_dir, f"{output_name}_{suffix}.{format}"),
                wav_data,
                self.mp.param["sr"],
                subtype='FLOAT'
            )
        else:
            # 使用更高效的FFmpeg参数
            path = os.path.join(root_dir, f"{output_name}_{suffix}.wav")
            sf.write(path, wav_data, self.mp.param["sr"], subtype='FLOAT')
            if os.path.exists(path):
                opt_format_path = path[:-4] + f".{format}"
                os.system(f'ffmpeg -i "{path}" -vn "{opt_format_path}" -q:a 2 -y -acodec pcm_s32le -ac 2 -ar 44100')
                if os.path.exists(opt_format_path):
                    try:
                        os.remove(path)
                    except:
                        pass

    def _prepare_save_directory(self, root_dir):
        """优化目录创建和检查"""
        if not os.path.exists(root_dir):
            os.makedirs(root_dir, mode=0o755, exist_ok=True)
        return os.access(root_dir, os.W_OK)

    def _get_spectrogram_wave(self, spec, mp):
        """缓存频谱到波形的转换结果"""
        return spec_utils.cmb_spectrogram_to_wave(spec, mp)

class AudioPre(VR):
    def __init__(self, model, device, agg, tta=False):
        self.data = {
            # Processing Options
            "postprocess": False,
            "tta": tta,
            # Constants
            "window_size": 512,
            "agg": agg,
            "high_end_process": "mirroring",
        }
        mp = ModelParameters("infer/lib/uvr5_pack/lib_v5/modelparams/4band_v2.json")
        self.mp = mp
        self.model = model
        self.device = device

    def process_audio_chunk(self, X_spec_m, aggressiveness):
        """AudioPre特定的处理逻辑"""
        return super().process_audio_chunk(X_spec_m, aggressiveness)

    def _path_audio_(
        self, music_file, vocal_root=None, ins_root=None, output_name=None, format="flac", logger=None
    ):
        start_time = time.time()
        
        if ins_root is None and vocal_root is None:
            return "No save root."
            
        # 读取和预处理音频
        load_start = time.time()
        X_wave, y_wave, X_spec_s = {}, {}, {}
        bands_n = len(self.mp.param["band"])
        for d in range(bands_n, 0, -1):
            bp = self.mp.param["band"][d]
            if d == bands_n:  # high-end band
                (
                    X_wave[d],
                    _,
                ) = librosa.load(  # 理论上librosa读取可能对某些音频有bug，应该上ffmpeg读取，但是太麻烦了弃坑
                    music_file,
                    sr=bp["sr"],
                    mono=False,
                    dtype=np.float32,
                    res_type=bp["res_type"],
                )
                if X_wave[d].ndim == 1:
                    X_wave[d] = np.asfortranarray([X_wave[d], X_wave[d]])
            else:  # lower bands
                X_wave[d] = librosa.resample(
                    X_wave[d + 1],
                    orig_sr=self.mp.param["band"][d + 1]["sr"],
                    target_sr=bp["sr"],
                    res_type=bp["res_type"],
                )
            # Stft of wave source
            X_spec_s[d] = spec_utils.wave_to_spectrogram_mt(
                X_wave[d],
                bp["hl"],
                bp["n_fft"],
                self.mp.param["mid_side"],
                self.mp.param["mid_side_b2"],
                self.mp.param["reverse"],
            )
            # pdb.set_trace()
            if d == bands_n and self.data["high_end_process"] != "none":
                input_high_end_h = (bp["n_fft"] // 2 - bp["crop_stop"]) + (
                    self.mp.param["pre_filter_stop"] - self.mp.param["pre_filter_start"]
                )
                input_high_end = X_spec_s[d][
                    :, bp["n_fft"] // 2 - input_high_end_h : bp["n_fft"] // 2, :
                ]

        logger.info(f"AudioPre stage {output_name} load audio time: {time.time() - load_start:.2f}s")

        stft_start = time.time()
        X_spec_m = spec_utils.combine_spectrograms(X_spec_s, self.mp)
        logger.info(f"AudioPre stage {output_name} stft time: {time.time() - stft_start:.2f}s")

        infer_start = time.time()
        aggresive_set = float(self.data["agg"] / 100)
        aggressiveness = {
            "value": aggresive_set,
            "split_bin": self.mp.param["band"][1]["crop_stop"],
        }
        with torch.no_grad():
            pred, X_mag, X_phase = inference(
                X_spec_m, self.device, self.model, aggressiveness, self.data
            )
        logger.info(f"AudioPre stage {output_name} infer time: {time.time() - infer_start:.2f}s")

        # Postprocess
        if self.data["postprocess"]:
            pred_inv = np.clip(X_mag - pred, 0, np.inf)
            pred = spec_utils.mask_silence(pred, pred_inv)
        y_spec_m = pred * X_phase
        v_spec_m = X_spec_m - y_spec_m

        save_start = time.time()

        # 伴奏不需要，为了时间，先不保存
        #if ins_root is not None:
        #    if self.data["high_end_process"].startswith("mirroring"):
        #        input_high_end_ = spec_utils.mirroring(
        #            self.data["high_end_process"], y_spec_m, input_high_end, self.mp
        #        )
        #        wav_instrument = spec_utils.cmb_spectrogram_to_wave(
        #            y_spec_m, self.mp, input_high_end_h, input_high_end_
        #        )
        #    else:
        #        wav_instrument = spec_utils.cmb_spectrogram_to_wave(y_spec_m, self.mp)
        #    if format in ["wav", "flac"]:
        #        sf.write(
        #            os.path.join(
        #                ins_root,
        #                "{}_other.{}".format(output_name, format),
        #            ),
        #            #(np.array(wav_instrument) * 32768).astype("int16"),
        #            wav_instrument,
        #            self.mp.param["sr"],
        #            subtype='FLOAT'
        #        )  #

        if vocal_root is not None:
            if self.data["high_end_process"].startswith("mirroring"):
                input_high_end_ = spec_utils.mirroring(
                    self.data["high_end_process"], v_spec_m, input_high_end, self.mp
                )
                logger.info(f"AudioPre stage {output_name} mirroring time: {time.time() - save_start:.2f}s")
                wav_vocals = spec_utils.cmb_spectrogram_to_wave(
                    v_spec_m, self.mp, input_high_end_h, input_high_end_
                )
                logger.info("%s vocals done 1" % output_name)
            else:
                wav_vocals = spec_utils.cmb_spectrogram_to_wave(v_spec_m, self.mp)
                logger.info("%s vocals done 2" % output_name)
            logger.info(f"AudioPre stage {output_name} wav_vocals product time: {time.time() - save_start:.2f}s")

            write_start = time.time()
            if format in ["wav", "flac"]:
                sf.write(
                    os.path.join(
                        vocal_root,
                        "{}_vocal.{}".format(output_name, format),
                    ),
                    #(np.array(wav_vocals) * 32768).astype("int16"),
                    wav_vocals,
                    self.mp.param["sr"],
                    subtype='FLOAT'
                )
            logger.info(f"AudioPre stage {output_name} write audio time: {time.time() - write_start:.2f}s")
        logger.info(f"AudioPre stage {output_name} save audio time: {time.time() - save_start:.2f}s")

        logger.info(f"AudioPre stage {output_name} total time: {time.time() - start_time:.2f}s")

    def cleanup(self):
        # 清理中间数据
        if hasattr(self, 'audio_pad'):
            del self.audio_pad
        if hasattr(self, 'wave'):
            del self.wave
        torch.cuda.empty_cache()


class AudioPreDeEcho(VR):
    def __init__(self, model, device, agg, tta=False):
        self.device = device
        self.data = {
            # Processing Options
            "postprocess": False,
            "tta": tta,
            # Constants
            "window_size": 512,
            "agg": agg,
            "high_end_process": "mirroring",
        }
        mp = ModelParameters("infer/lib/uvr5_pack/lib_v5/modelparams/4band_v3.json")

        self.mp = mp
        self.model = model

    def process_audio_chunk(self, X_spec_m, aggressiveness):
        """AudioPreDeEcho特定的处理逻辑"""
        return super().process_audio_chunk(X_spec_m, aggressiveness)

    def apply_postprocessing(self, pred, X_mag):
        """AudioPreDeEcho特定的后处理逻辑"""
        # 可以在这里添加去回声的特定处理
        return super().apply_postprocessing(pred, X_mag)

    def _path_audio_(
        self, music_file, vocal_root=None, ins_root=None, output_name=None, format="flac", logger=None
    ):
        start_time = time.time()
        
        if ins_root is None and vocal_root is None:
            return "No save root."
            
        # 读取和预处理音频
        load_start = time.time()
        X_wave, y_wave, X_spec_s = {}, {}, {}
        bands_n = len(self.mp.param["band"])
        for d in range(bands_n, 0, -1):
            bp = self.mp.param["band"][d]
            if d == bands_n:  # high-end band
                (
                    X_wave[d],
                    _,
                ) = librosa.load(  # 理论上librosa读取可能对某些音频有bug，应该上ffmpeg读取，但是太麻烦了弃坑
                    music_file,
                    sr=bp["sr"],
                    mono=False,
                    dtype=np.float32,
                    res_type=bp["res_type"],
                )
                if X_wave[d].ndim == 1:
                    X_wave[d] = np.asfortranarray([X_wave[d], X_wave[d]])
            else:  # lower bands
                X_wave[d] = librosa.resample(
                    X_wave[d + 1],
                    orig_sr=self.mp.param["band"][d + 1]["sr"],
                    target_sr=bp["sr"],
                    res_type=bp["res_type"],
                )
            # Stft of wave source
            X_spec_s[d] = spec_utils.wave_to_spectrogram_mt(
                X_wave[d],
                bp["hl"],
                bp["n_fft"],
                self.mp.param["mid_side"],
                self.mp.param["mid_side_b2"],
                self.mp.param["reverse"],
            )
            # pdb.set_trace()
            if d == bands_n and self.data["high_end_process"] != "none":
                input_high_end_h = (bp["n_fft"] // 2 - bp["crop_stop"]) + (
                    self.mp.param["pre_filter_stop"] - self.mp.param["pre_filter_start"]
                )
                input_high_end = X_spec_s[d][
                    :, bp["n_fft"] // 2 - input_high_end_h : bp["n_fft"] // 2, :
                ]

        logger.info(f"AudioPreDeEcho stage {output_name} load audio time: {time.time() - load_start:.2f}s")

        stft_start = time.time()
        X_spec_m = spec_utils.combine_spectrograms(X_spec_s, self.mp)
        logger.info(f"AudioPreDeEcho stage {output_name} stft time: {time.time() - stft_start:.2f}s")

        infer_start = time.time()
        aggresive_set = float(self.data["agg"] / 100)
        aggressiveness = {
            "value": aggresive_set,
            "split_bin": self.mp.param["band"][1]["crop_stop"],
        }
        with torch.no_grad():
            pred, X_mag, X_phase = inference(
                X_spec_m, self.device, self.model, aggressiveness, self.data
            )
        logger.info(f"AudioPreDeEcho stage {output_name} infer time: {time.time() - infer_start:.2f}s")

        # Postprocess
        if self.data["postprocess"]:
            pred_inv = np.clip(X_mag - pred, 0, np.inf)
            pred = spec_utils.mask_silence(pred, pred_inv)
        y_spec_m = pred * X_phase   # 人声
        v_spec_m = X_spec_m - y_spec_m #

        save_start = time.time() 
        #if ins_root is not None:
        #    if self.data["high_end_process"].startswith("mirroring"):
        #        input_high_end_ = spec_utils.mirroring(
        #            self.data["high_end_process"], v_spec_m, input_high_end, self.mp
        #        )
        #        wav_instrument = spec_utils.cmb_spectrogram_to_wave(
        #            v_spec_m, self.mp, input_high_end_h, input_high_end_
        #        )
        #    else:
        #        wav_instrument = spec_utils.cmb_spectrogram_to_wave(v_spec_m, self.mp)
        #    logger.info("%s instruments done" % output_name)
        #    if format in ["wav", "flac"]:
        #        sf.write(
        #            os.path.join(
        #                ins_root,
        #                "{}_other.{}".format(output_name, format),
        #            ),
        #            #(np.array(wav_instrument) * 32768).astype("int16"),
        #            wav_instrument,
        #            self.mp.param["sr"],
        #            subtype='FLOAT'
        #        )

        if vocal_root is not None:
            if self.data["high_end_process"].startswith("mirroring"):
                input_high_end_ = spec_utils.mirroring(
                    self.data["high_end_process"], y_spec_m, input_high_end, self.mp
                )
                logger.info(f"AudioPreDeEcho stage {output_name} mirroring time: {time.time() - save_start:.2f}s")
                wav_vocals = spec_utils.cmb_spectrogram_to_wave(
                    y_spec_m, self.mp, input_high_end_h, input_high_end_
                )
                logger.info("%s vocals done 1" % output_name)
            else:
                wav_vocals = spec_utils.cmb_spectrogram_to_wave(y_spec_m, self.mp)
                logger.info("%s vocals done 2" % output_name)

            logger.info(f"AudioPreDeEcho stage {output_name} wav_vocals product time: {time.time() - save_start:.2f}s")
            write_start = time.time()
            if format in ["wav", "flac"]:
                sf.write(
                    os.path.join(
                        vocal_root,
                        "{}_vocal.{}".format(output_name, format),
                    ),
                    #(np.array(wav_vocals) * 32768).astype("int16"),
                    wav_vocals,
                    self.mp.param["sr"],
                    subtype='FLOAT'
                )
            logger.info(f"AudioPreDeEcho stage {output_name} write audio time: {time.time() - write_start:.2f}s")
        logger.info(f"AudioPreDeEcho stage {output_name} save audio time: {time.time() - save_start:.2f}s")
        
        logger.info(f"AudioPreDeEcho stage {output_name} total time: {time.time() - start_time:.2f}s")

    def cleanup(self):
        # 清理中间数据
        if hasattr(self, 'audio_pad'):
            del self.audio_pad
        if hasattr(self, 'wave'):
            del self.wave
        torch.cuda.empty_cache()

class AudioPreDenoise:
    def __init__(self, agg, model_path, device, is_half, tta=False, agg_level=1):
        self.model_path = model_path
        self.device = device
        self.data = {
            "postprocess": True,
            "tta": tta,
            "window_size": 512,
            "agg": agg,
            "high_end_process": "mirroring",
        }

        #if agg_level == 2:
            # 第二次降噪 - 精细参数
            #self.data.update({
            #    "window_size": 512,   # 较小的窗口，更精细的处理
            #    "threshold": 0.15,    # 较低的阈值，去除更多噪声
            #    "smoothing": 0.5      # 更强的平滑效果
            #})
        #else:
            # 根据降噪级别调整参数
            # 默认以及第一次降噪 - 温和参数,
            #self.data.update({
            #    "window_size": 1024,  # 更大的窗口size
            #    "threshold": 0.2,     # 更高的阈值，保留更多细节
            #    "smoothing": 0.3      # 平滑处理
            #})

        mp = ModelParameters("infer/lib/uvr5_pack/lib_v5/modelparams/4band_v3.json")

        # 根据model_data.json的配置设置参数
        if "DeNoise" in model_path:
            nout = 48  # Noise模型使用48通道
            nout_lstm = 128  # 设置LSTM输出通道
            model = CascadedNet(mp.param["bins"] * 2, nout)
        else:
            nout = 48
            model = CascadedNet(mp.param["bins"] * 2, nout)

        cpk = torch.load(model_path, map_location="cpu")
        model.load_state_dict(cpk)
        model.eval()

        if is_half:
            model = model.half().to(device)
        else:
            model = model.to(device)

        self.mp = mp
        self.model = model

    def _path_audio_(
        self, music_file, vocal_root=None, ins_root=None, format="flac", logger=None
    ):
        if vocal_root is None and ins_root is None:
            return "No save root."
        name = os.path.splitext(os.path.basename(music_file))[0]
        if vocal_root is not None:
            os.makedirs(vocal_root, exist_ok=True)
        if ins_root is not None:
            os.makedirs(ins_root, exist_ok=True)

        X_wave, y_wave, X_spec_s, y_spec_s = {}, {}, {}, {}
        bands_n = len(self.mp.param["band"])

        # 多频段处理
        for d in range(bands_n, 0, -1):
            bp = self.mp.param["band"][d]
            if d == bands_n:  # 高频段
                X_wave[d], _ = librosa.load(
                    music_file,
                    sr=bp["sr"],
                    mono=False,
                    dtype=np.float32,
                    res_type=bp["res_type"],
                )
                if X_wave[d].ndim == 1:
                    X_wave[d] = np.asfortranarray([X_wave[d], X_wave[d]])
            else:  # 低频段
                X_wave[d] = librosa.resample(
                    X_wave[d + 1],
                    orig_sr=self.mp.param["band"][d + 1]["sr"],
                    target_sr=bp["sr"],
                    res_type=bp["res_type"],
                )

            # 转换为频谱
            X_spec_s[d] = spec_utils.wave_to_spectrogram_mt(
                X_wave[d],
                bp["hl"],
                bp["n_fft"],
                self.mp.param["mid_side"],
                self.mp.param["mid_side_b2"],
                self.mp.param["reverse"],
            )

            if d == bands_n and self.data["high_end_process"] != "none":
                input_high_end_h = (bp["n_fft"] // 2 - bp["crop_stop"]) + (
                    self.mp.param["pre_filter_stop"] - self.mp.param["pre_filter_start"]
                )
                input_high_end = X_spec_s[d][
                    :, bp["n_fft"] // 2 - input_high_end_h : bp["n_fft"] // 2, :
                ]

        # 合并频谱
        X_spec_m = spec_utils.combine_spectrograms(X_spec_s, self.mp)

        # 设置降噪强度
        aggresive_set = float(self.data["agg"] / 100)
        aggressiveness = {
            "value": aggresive_set,
            "split_bin": self.mp.param["band"][1]["crop_stop"],
        }

        # 执行降噪
        with torch.no_grad():
            pred, X_mag, X_phase = inference(
                X_spec_m, self.device, self.model, aggressiveness, self.data
            )

        #if self.data["postprocess"]:
        #    pred_inv = np.clip(X_mag - pred, 0, np.inf)
        #    pred = spec_utils.mask_silence(pred, pred_inv)
        # 增强后处理
        if self.data["postprocess"]:
            # 保存原始数据类型
            original_dtype = pred.dtype

            # 应用阈值去噪
            threshold = self.data.get("threshold", 0.2)
            pred = np.where(pred < threshold, 0, pred)

            # 应用平滑处理
            smoothing = self.data.get("smoothing", 0.3)
            from infer.lib.uvr5_pack.lib_v5 import spec_utils as spec_utils_v5
            pred = spec_utils_v5.smooth_spectrum(pred, smoothing)

            # 使用原始信号的相位信息
            pred_inv = np.clip(X_mag - pred, 0, np.inf)
            pred = spec_utils.mask_silence(pred, pred_inv)

            # 频谱减法降噪
            pred = spec_utils_v5.spectral_subtraction(pred, X_mag, self.data["agg"])

            # 确保结果与原始数据类型一致
            pred = pred.astype(original_dtype)

        # 获取降噪后的频谱
        v_spec_m = pred * X_phase # 现在这个实际上是噪声部分
        y_spec_m = X_spec_m - v_spec_m  # 这个实际上是干净的人声部分
        # 保存降噪后的人声
        if vocal_root is not None:
            if self.data["high_end_process"].startswith("mirroring"):
                input_high_end_ = spec_utils.mirroring(
                    self.data["high_end_process"], y_spec_m, input_high_end, self.mp
                )
                wav_vocals = spec_utils.cmb_spectrogram_to_wave(
                    y_spec_m, self.mp, input_high_end_h, input_high_end_
                )
            else:
                wav_vocals = spec_utils.cmb_spectrogram_to_wave(y_spec_m, self.mp)

            logger.info("%s vocals done" % name)

            # 修改这里：使用 "vocal_" 前缀
            if format in ["wav", "flac"]:
                sf.write(
                    os.path.join(
                        vocal_root,
                        "{}_vocal.{}".format(name, format),
                    ),
                    #(np.array(wav_vocals) * 32768).astype("int16"),
                    wav_vocals,
                    self.mp.param["sr"],
                    subtype='FLOAT'
                )
            else:
                path = os.path.join(
                    vocal_root, "{}_vocal.wav".format(name)
                )
                sf.write(
                    path,
                    #(np.array(wav_vocals) * 32768).astype("int16"),
                    wav_vocals,
                    self.mp.param["sr"],
                    subtype='FLOAT'
                )
                if os.path.exists(path):
                    opt_format_path = path[:-4] + ".%s" % format
                    os.system('ffmpeg -i "%s" -vn "%s" -q:a 2 -y -acodec pcm_s32le -ac 2 -ar 44100 ' % (path, opt_format_path))
                    if os.path.exists(opt_format_path):
                        try:
                            os.remove(path)
                        except:
                            pass

        # 保存噪声部分
        if ins_root is not None:
            if self.data["high_end_process"].startswith("mirroring"):
                input_high_end_ = spec_utils.mirroring(
                    self.data["high_end_process"], v_spec_m, input_high_end, self.mp
                )
                wav_noise = spec_utils.cmb_spectrogram_to_wave(
                    v_spec_m, self.mp, input_high_end_h, input_high_end_
                )
            else:
                wav_noise = spec_utils.cmb_spectrogram_to_wave(v_spec_m, self.mp)

            logger.info("%s noise done" % name)

            # 修改这里：使用 "noise_" 前缀
            if format in ["wav", "flac"]:
                sf.write(
                    os.path.join(
                        ins_root,
                        "{}_other.{}".format(name, format),
                    ),
                    #(np.array(wav_noise) * 32768).astype("int16"),
                    wav_noise,
                    self.mp.param["sr"],
                    subtype='FLOAT'
                )
            else:
                path = os.path.join(
                    ins_root, "{}_other.wav".format(name)
                )
                sf.write(
                    path,
                    #(np.array(wav_noise) * 32768).astype("int16"),
                    wav_noise,
                    self.mp.param["sr"],
                    subtype='FLOAT'
                )
                if os.path.exists(path):
                    opt_format_path = path[:-4] + ".%s" % format
                    os.system('ffmpeg -i "%s" -vn "%s" -q:a 2 -y -acodec pcm_s32le -ac 2 -ar 44100 ' % (path, opt_format_path))
                    if os.path.exists(opt_format_path):
                        try:
                            os.remove(path)
                        except:
                            pass

    def cleanup(self):
        """清理模型和GPU内存"""
        try:
            # 检查属性是否存在再进行清理
            if hasattr(self, 'model'):
                # 将模型移到CPU
                self.model = self.model.cpu()
                # 清除模型
                del self.model

            if hasattr(self, 'mp'):
                # 清除其他属性
                del self.mp

            # 清理GPU缓存
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            logger.info("模型资源已成功释放")
        except Exception as e:
            logger.error(f"清理模型资源时发生错误: {str(e)}")
            # 确保即使出错也尝试清理GPU缓存
            if torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except:
                    pass

