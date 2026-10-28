import logging
import subprocess
import os
import tempfile

def convert_to_mp3(input_path: str, output_path: str, work_num: int, reqid: str, logger: logging.Logger) -> tuple[int, str]:
    """音频转换为MP3辅助函数，使用多级回退策略处理AAC解码问题"""
    import tempfile
    
    try:
        # 策略1: 直接转换（标准方法）
        cmd1 = (
            f'ffmpeg -y -err_detect ignore_err -fflags +genpts+igndts '
            f'-ignore_unknown -i "{input_path}" '
            f'-ar 44100 -ac 2 -acodec libmp3lame -b:a 192k '
            f'-threads {work_num} '
            f'-loglevel error '
            f'"{output_path}"'
        )
        
        result1 = subprocess.run(
            cmd1, 
            shell=True, 
            capture_output=True, 
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=300
        )
        
        if result1.returncode == 0:
            return 0, 'ok'
        
        # 检查是否是 HE-AAC 频带超限错误
        is_he_aac_error = 'Number of bands' in result1.stderr or 'exceeds limit' in result1.stderr
        
        logger.warning(f'reqid:{reqid} Method 1 failed, trying method 1.5 (HE-AAC decoder). Error: {result1.stderr[:300]}')
        
        # 策略1.5: 如果检测到 HE-AAC 错误，尝试使用支持 HE-AAC 的解码器
        if is_he_aac_error:
            logger.info(f'reqid:{reqid} Detected HE-AAC error, trying HE-AAC compatible decoders')
            
            # 尝试1: 使用更宽松的参数，让 ffmpeg 尝试不同的解码策略
            # 注意：ffmpeg 会自动选择解码器，我们通过参数让它更宽容
            cmd1_5_relaxed = (
                f'ffmpeg -y -strict -2 -err_detect ignore_err '
                f'-fflags +genpts+igndts+discardcorrupt '
                f'-ignore_unknown -i "{input_path}" '
                f'-ar 44100 -ac 2 -acodec libmp3lame -b:a 192k '
                f'-threads {work_num} '
                f'-loglevel error '
                f'"{output_path}"'
            )
            
            result1_5_relaxed = subprocess.run(
                cmd1_5_relaxed,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=300
            )
            
            if result1_5_relaxed.returncode == 0:
                logger.info(f'reqid:{reqid} Method 1.5.1 (relaxed strict mode) succeeded')
                return 0, 'ok'
            
            logger.warning(f'reqid:{reqid} Method 1.5.1 (relaxed strict) failed. Error: {result1_5_relaxed.stderr[:300]}')
            
        
        # 策略2: 使用PCM中间格式绕过AAC解码器问题（使用临时文件，更可靠）
        # 先解码为PCM WAV，再编码为MP3，这样可以绕过AAC解码器的限制
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav2:
            tmp_wav2_path = tmp_wav2.name
        
        try:
            cmd2_1 = (
                f'ffmpeg -y -err_detect ignore_err -fflags +genpts+igndts+discardcorrupt '
                f'-ignore_unknown -i "{input_path}" '
                f'-vn -map 0:a:0? '
                f'-c:a pcm_s16le -ar 44100 -ac 2 '
                f'-f wav '
                f'-loglevel error '
                f'"{tmp_wav2_path}"'
            )
            
            result2_1 = subprocess.run(
                cmd2_1,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=300
            )
            
            if result2_1.returncode == 0:
                # 步骤2.2: 将WAV转换为MP3
                cmd2_2 = (
                    f'ffmpeg -y -i "{tmp_wav2_path}" '
                    f'-acodec libmp3lame -b:a 192k '
                    f'-threads {work_num} '
                    f'-loglevel error '
                    f'"{output_path}"'
                )
                
                result2_2 = subprocess.run(
                    cmd2_2,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=300
                )
                
                if result2_2.returncode == 0:
                    logger.info(f'reqid:{reqid} Method 2 (PCM intermediate) succeeded')
                    return 0, 'ok'
                else:
                    logger.warning(f'reqid:{reqid} Method 2.2 (WAV to MP3) failed. Error: {result2_2.stderr[:300]}')
            else:
                logger.warning(f'reqid:{reqid} Method 2.1 (extract to WAV) failed. Error: {result2_1.stderr[:300]}')
        finally:
            # 清理临时文件
            if os.path.exists(tmp_wav2_path):
                try:
                    os.unlink(tmp_wav2_path)
                except:
                    pass
        
        logger.warning(f'reqid:{reqid} Method 2 failed, trying method 3')
        
        # 策略3: 使用临时WAV文件作为中间格式
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav:
            tmp_wav_path = tmp_wav.name
        
        try:
            # 步骤3.1: 提取音频到WAV（使用最宽松的参数）
            cmd3_1 = (
                f'ffmpeg -y -err_detect ignore_err -fflags +genpts+igndts+discardcorrupt '
                f'-ignore_unknown -i "{input_path}" '
                f'-vn -map 0:a:0? '
                f'-c:a pcm_s16le -ar 44100 -ac 2 '
                f'-f wav '
                f'-loglevel error '
                f'"{tmp_wav_path}"'
            )
            
            result3_1 = subprocess.run(
                cmd3_1,
                shell=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=300
            )
            
            if result3_1.returncode != 0:
                logger.warning(f'reqid:{reqid} Method 3.1 (extract to WAV) failed. Error: {result3_1.stderr[:300]}')
                # 尝试更激进的参数（修复：noaccurate_seek 应该作为独立选项，不能放在 fflags 中）
                cmd3_1_alt = (
                    f'ffmpeg -y -err_detect ignore_err -fflags +genpts+igndts+discardcorrupt '
                    f'-noaccurate_seek -ignore_unknown -i "{input_path}" '
                    f'-vn -map 0:a:0? '
                    f'-c:a pcm_s16le -ar 44100 -ac 2 '
                    f'-f wav '
                    f'-loglevel error '
                    f'"{tmp_wav_path}"'
                )
                
                result3_1_alt = subprocess.run(
                    cmd3_1_alt,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=300
                )
                
                if result3_1_alt.returncode != 0:
                    logger.warning(f'reqid:{reqid} Method 3.1 alt failed, will try method 4. Error: {result3_1_alt.stderr[:300]}')
                    # 不立即返回，继续尝试策略4
                    # 如果文件不存在或为空，标记为失败
                    if not os.path.exists(tmp_wav_path) or os.path.getsize(tmp_wav_path) == 0:
                        logger.debug(f'reqid:{reqid} Method 3.1 alt: WAV file not generated or empty, tmp_wav_path={tmp_wav_path}')
                        tmp_wav_path = None  # 标记为未生成
                else:
                    logger.info(f'reqid:{reqid} Method 3.1 alt (extract to WAV) succeeded')
            
            # 步骤3.2: 将WAV转换为MP3（仅在WAV文件成功生成时执行）
            if tmp_wav_path and os.path.exists(tmp_wav_path) and os.path.getsize(tmp_wav_path) > 0:
                logger.debug(f'reqid:{reqid} Method 3.1: WAV file generated successfully, size={os.path.getsize(tmp_wav_path)} bytes, proceeding to convert to MP3')
                cmd3_2 = (
                    f'ffmpeg -y -i "{tmp_wav_path}" '
                    f'-acodec libmp3lame -b:a 192k '
                    f'-threads {work_num} '
                    f'-loglevel error '
                    f'"{output_path}"'
                )
                
                result3_2 = subprocess.run(
                    cmd3_2,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    timeout=300
                )
                
                if result3_2.returncode == 0:
                    logger.info(f'reqid:{reqid} Method 3 (two-step via WAV) succeeded')
                    return 0, 'ok'
                else:
                    logger.warning(f'reqid:{reqid} Method 3.2 failed. Error: {result3_2.stderr[:300]}')
                    # 不立即返回，继续尝试策略4
                
        finally:
            # 清理临时文件
            try:
                if 'tmp_wav_path' in locals() and tmp_wav_path and os.path.exists(tmp_wav_path):
                    os.unlink(tmp_wav_path)
            except:
                pass
        
    except subprocess.TimeoutExpired:
        return 1, 'Error converting audio to mp3: Conversion timeout (exceeded 300 seconds)'
    except Exception as e:
        return 1, f'Error converting audio to mp3: {str(e)}'