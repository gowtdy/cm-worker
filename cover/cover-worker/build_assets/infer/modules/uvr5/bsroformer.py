# This code is modified from https://github.com/ZFTurbo/
import pdb

import librosa
from tqdm import tqdm
import os
import torch
import numpy as np
import soundfile as sf
import torch.nn as nn

import warnings
warnings.filterwarnings("ignore")

class BsRoformer_Loader:
    def demix_track(self, model, mix, device, is_half):
        C = 352800
        # num_overlap
        N = 1
        fade_size = C // 10
        step = int(C // N)
        border = C - step
        batch_size = 4

        length_init = mix.shape[-1]

        progress_bar = tqdm(total=length_init // step + 1)
        progress_bar.set_description("Processing")

        # Do pad from the beginning and end to account floating window results better
        if length_init > 2 * border and (border > 0):
            mix = nn.functional.pad(mix, (border, border), mode='reflect')

        # Prepare windows arrays (do 1 time for speed up). This trick repairs click problems on the edges of segment
        window_size = C
        fadein = torch.linspace(0, 1, fade_size)
        fadeout = torch.linspace(1, 0, fade_size)
        window_start = torch.ones(window_size)
        window_middle = torch.ones(window_size)
        window_finish = torch.ones(window_size)
        window_start[-fade_size:] *= fadeout # First audio chunk, no fadein
        window_finish[:fade_size] *= fadein # Last audio chunk, no fadeout
        window_middle[-fade_size:] *= fadeout
        window_middle[:fade_size] *= fadein

        with torch.amp.autocast('cuda'):
            with torch.inference_mode():
                req_shape = (1, ) + tuple(mix.shape)

                result = torch.zeros(req_shape, dtype=torch.float32)
                counter = torch.zeros(req_shape, dtype=torch.float32)
                i = 0
                batch_data = []
                batch_locations = []
                while i < mix.shape[1]:
                    part = mix[:, i:i + C].to(device)
                    length = part.shape[-1]
                    if length < C:
                        if length > C // 2 + 1:
                            part = nn.functional.pad(input=part, pad=(0, C - length), mode='reflect')
                        else:
                            part = nn.functional.pad(input=part, pad=(0, C - length, 0, 0), mode='constant', value=0)
                    if(self.is_half==True):
                        part=part.half()
                    batch_data.append(part)
                    batch_locations.append((i, length))
                    i += step
                    progress_bar.update(1)

                    if len(batch_data) >= batch_size or (i >= mix.shape[1]):
                        arr = torch.stack(batch_data, dim=0)
                        # print(23333333,arr.dtype)
                        x = model(arr)

                        window = window_middle
                        if i - step == 0:  # First audio chunk, no fadein
                            window = window_start
                        elif i >= mix.shape[1]:  # Last audio chunk, no fadeout
                            window = window_finish

                        for j in range(len(batch_locations)):
                            start, l = batch_locations[j]
                            result[..., start:start+l] += x[j][..., :l].cpu() * window[..., :l]
                            counter[..., start:start+l] += window[..., :l]

                        batch_data = []
                        batch_locations = []

                estimated_sources = result / counter
                estimated_sources = estimated_sources.cpu().numpy()
                np.nan_to_num(estimated_sources, copy=False, nan=0.0)

                if length_init > 2 * border and (border > 0):
                    # Remove pad
                    estimated_sources = estimated_sources[..., border:-border]

        progress_bar.close()

        return {k: v for k, v in zip(['vocals', 'other'], estimated_sources)}


    def run_folder(self,input, vocal_root, others_root, output_name, format, logger=None):
        # start_time = time.time()
        self.model.eval()
        path = input

        if not os.path.isdir(vocal_root):
            os.mkdir(vocal_root)

        if not os.path.isdir(others_root):
            os.mkdir(others_root)

        try:
            mix, sr = librosa.load(path, sr=44100, mono=False)
        except Exception as e:
            print('Can read track: {}'.format(path))
            print('Error message: {}'.format(str(e)))
            return

        # Convert mono to stereo if needed
        if len(mix.shape) == 1:
            mix = np.stack([mix, mix], axis=0)

        mix_orig = mix.copy()

        mixture = torch.tensor(mix, dtype=torch.float32)
        res = self.demix_track(self.model, mixture, self.device, self.is_half)

        estimates = res['vocals'].T
        
        if format in ["wav", "flac"]:
            sf.write("{}/{}_{}.{}".format(vocal_root, output_name, 'vocal', format), estimates, sr, subtype='FLOAT')
            sf.write("{}/{}_{}.{}".format(others_root, output_name, 'other', format), mix_orig.T - estimates, sr, subtype='FLOAT')
        else:
            path_vocal = "%s/%s_vocal.wav" % (vocal_root, output_name)
            path_other = "%s/%s_other.wav" % (others_root, output_name)
            sf.write(path_vocal, estimates, sr)
            sf.write(path_other, mix_orig.T - estimates, sr)
            opt_path_vocal = path_vocal[:-4] + ".%s" % format
            opt_path_other = path_other[:-4] + ".%s" % format
            if os.path.exists(path_vocal):
                os.system(
                    "ffmpeg -i '%s' -vn '%s' -q:a 2 -y -acodec pcm_s32le -ac 2 -ar 44100 " % (path_vocal, opt_path_vocal)
                )
                if os.path.exists(opt_path_vocal):
                    try:
                        os.remove(path_vocal)
                    except:
                        pass
            if os.path.exists(path_other):
                os.system(
                    "ffmpeg -i '%s' -vn '%s' -q:a 2 -y -acodec pcm_s32le -ac 2 -ar 44100" % (path_other, opt_path_other)
                )
                if os.path.exists(opt_path_other):
                    try:
                        os.remove(path_other)
                    except:
                        pass

        # print("Elapsed time: {:.2f} sec".format(time.time() - start_time))


    def __init__(self, model, device, is_half):
        self.extract_instrumental=True
        self.model = model
        self.device = device
        self.is_half = is_half

    def _path_audio_(self, music_file, vocal_root, ins_root, output_name, format, logger=None):
        self.run_folder(music_file, vocal_root, ins_root, output_name, format, logger)

    def cleanup(self):
        # 清理中间数据，保留模型
        if hasattr(self, 'cache'):
            del self.cache
        if hasattr(self, 'temp_data'):
            del self.temp_data
        torch.cuda.empty_cache()