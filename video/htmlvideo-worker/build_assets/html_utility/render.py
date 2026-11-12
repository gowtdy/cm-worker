import os
import subprocess
import logging


def render_video(
    logger: logging.Logger,
    project_dir: str,
    video_name: str,
    task_id: str = "",
):
    ret = 0
    msg = "ok"

    output_path = f"{project_dir}/{video_name}"
    cmd = [
        "npx",
        "hyperframes",
        "render",
        str(project_dir),
        "--quality",
        "high",
        "--output",
        output_path,
    ]
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, stdout:{proc.stdout}, stderr:{proc.stderr}"
        logger.error(f"render_video task_id:{task_id} {msg}")
        return ret, msg
    return ret, msg
