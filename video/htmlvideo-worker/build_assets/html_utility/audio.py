import os
import subprocess
from common.cmwk_common.module_enum import TaskStatus
from common.cmwk_common.module_utils import update_task_status

def video_audio(
    logger,
    project_dir,
    modelcat,
    modelname,
    task_id: str = "",
):
    ret = 0
    msg = "ok"

    ret, msg = tts_generate(logger, project_dir, modelcat, modelname, task_id)
    if ret != 0:
        logger.error(f"tts_generate task_id:{task_id} {msg}")
        return ret, "TTS generate failed!"
    update_task_status(logger, task_id, domain, TaskStatus.VIDEO_TTSED, payload_msg=TaskStatus.VIDEO_TTSED)

    ret, msg = sync_duration(logger, project_dir, task_id)
    if ret != 0:
        logger.error(f"sync_duration task_id:{task_id} {msg}")
        return ret, "Sync duration failed!"

    ret, msg = caption_generate(logger, project_dir, task_id)
    if ret != 0:
        logger.error(f"caption_generate task_id:{task_id} {msg}")
        return ret, "Caption generate failed!"
    update_task_status(logger, task_id, domain, TaskStatus.VIDEO_CAPTIONED, payload_msg=TaskStatus.VIDEO_CAPTIONED)

    return ret, msg


def tts_generate(
    logger,
    project_dir,
    modelcat,
    modelname,
    task_id: str = "",
):
    ret = 0
    msg = "ok"

    script_path = f'/app/scripts/audio.mjs'
    if not os.path.exists(script_path):
        ret = 1
        msg = f"script_path:{script_path} not found"
        logger.error(f"tts_generate task_id:{task_id} {msg}")
        return ret, msg

    voice = f'{modelcat}|{modelname}'
    cmd = [
        "node",
        script_path,
        "--videodir",
        str(project_dir),
        "--voice",
        voice,
    ]
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, stdout:{proc.stdout}, stderr:{proc.stderr}"
        logger.error(f"tts_generate task_id:{task_id} {msg}")
        return ret, msg
    return ret, msg


def sync_duration(
    logger,
    project_dir,
    task_id: str = "",
):
    ret = 0
    msg = "ok"

    script_path = f'/app/scripts/audio.mjs'
    if not os.path.exists(script_path):
        ret = 1
        msg = f"script_path:{script_path} not found"
        logger.error(f"sync_duration task_id:{task_id} {msg}")
        return ret, msg

    cmd = [
        "node",
        script_path,
        "sync-durations",
        "--videodir",
        str(project_dir),
        "--audio-meta",
        f"{project_dir}/audio_meta.json",
        "--storyboard",
        f"{project_dir}/STORYBOARD.md",
    ]
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, stdout:{proc.stdout}, stderr:{proc.stderr}"
        logger.error(f"sync_duration task_id:{task_id} {msg}")
        return ret, msg
    return ret, msg


def caption_generate(
    logger,
    project_dir,
    task_id: str = "",
):
    ret = 0
    msg = "ok"

    script_path = f'/app/scripts/captions.mjs'
    if not os.path.exists(script_path):
        ret = 1
        msg = f"script_path:{script_path} not found"
        logger.error(f"caption_generate task_id:{task_id} {msg}")
        return ret, msg

    cmd = [
        "node",
        script_path,
        "build",
        "--videodir",
        str(project_dir),
        "--storyboard",
        f"{project_dir}/STORYBOARD.md",
        "--audio-meta",
        f"{project_dir}/audio_meta.json",
        "--out",
        f"{project_dir}/caption_groups.json",
    ]
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, stdout:{proc.stdout}, stderr:{proc.stderr}"
        logger.error(f"caption_generate task_id:{task_id} {msg}")
        return ret, msg
    return ret, msg
