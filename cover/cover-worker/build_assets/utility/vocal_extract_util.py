from typing import Any
import ffmpeg
import os
import traceback
import logging

from infer.lib.audio import analyze_audio_complexity

def model_extract_vocals(
    model: Any,
    input_wavfile: str,
    middle_vocals_dir: str,
    middle_instrument_dir: str,
    middledata_dir: str,
    output_name: str,
    is_hp3: bool,
    logger: logging.Logger
) -> tuple[int, str]:
    ret = 0
    msg = 'ok'
    need_reformat = 1
    done = 0

    try:
        # 尝试直接处理原始文件
        try:
            info = ffmpeg.probe(input_wavfile, cmd="ffprobe")
            if (info["streams"][0]["channels"] == 2 and
                info["streams"][0]["sample_rate"] == "44100"):
                need_reformat = 0
                model._path_audio_(input_wavfile, middle_vocals_dir, middle_instrument_dir, output_name, 'wav', is_hp3)
                done = 1
        except Exception as e:
            need_reformat = 1
            logger.error(f'Failed to process original file: {str(e)}')
            traceback.print_exc()

        # 如果需要重新格式化
        if need_reformat == 1:
            reformatted_wavfile = f'{middledata_dir}/{output_name}_reformatted.wav'
            logger.debug(f'Reformatting audio: {input_wavfile} -> {reformatted_wavfile}')

            reformat_cmd = f'ffmpeg -i {input_wavfile} -vn -acodec pcm_s32le -ac 2 -ar 44100 {reformatted_wavfile} -y'
            os.system(reformat_cmd)

            if not os.path.exists(reformatted_wavfile):
                raise Exception(f'Error reformatting audio: {reformatted_wavfile} not exists')

            input_wavfile = reformatted_wavfile
            if done == 0:
                model._path_audio_(input_wavfile, middle_vocals_dir, middle_instrument_dir, output_name, 'wav', is_hp3)
    except Exception as e:
        logger.error(f'Error extracting vocals: {str(e)}')
        ret = 1
        msg = f'Error extracting vocals: {str(e)}'
        logger.error(f'msg:{msg}')

    return ret, msg
