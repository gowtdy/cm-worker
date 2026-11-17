import os
import shutil
import subprocess
import logging

# 16 workers 与 V8 堆配合的实测基准见 /app/log/docs/render-259s-analysis.md:
# 软渲染 6 workers 时 capture 占 85%(259.3s);--workers 16 约 1.6x;加 GPU 光栅+NVENC 实测 36.8s(6.9x)
RENDER_WORKERS = "16"
RENDER_NODE_OPTIONS = "--max-old-space-size=8192"
STDOUT_TAIL_CHARS = 4000


def gpu_available() -> bool:
    """NVIDIA GPU 可用性探针:nvidia-smi -L 成功且列出 GPU 即真,否则回退软渲染。"""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        probe = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return probe.returncode == 0 and "GPU" in probe.stdout
    except Exception:
        return False


def build_render_cmd(project_dir: str, output_path: str):
    cmd = [
        "npx",
        "hyperframes",
        "render",
        str(project_dir),
        "--quality",
        "high",
        "--workers",
        RENDER_WORKERS,
    ]
    if gpu_available():
        # --browser-gpu 解除 shader-transitions 的软截图强制,capture 切 beginFrame(215s -> ~15s);
        # --gpu 编码切 NVENC。无 GPU 主机回落纯 --workers 16(标定还可顺带被跳过)。
        cmd += ["--gpu", "--browser-gpu"]
    cmd += ["--output", output_path]
    return cmd


def render_video(
    logger: logging.Logger,
    project_dir: str,
    video_name: str,
    task_id: str = "",
):
    ret = 0
    msg = "ok"

    output_path = os.path.join(project_dir, video_name)
    cmd = build_render_cmd(project_dir, output_path)
    env = {**os.environ, "NODE_OPTIONS": RENDER_NODE_OPTIONS}
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    tail_out = (proc.stdout or "")[-STDOUT_TAIL_CHARS:]
    tail_err = (proc.stderr or "")[-STDOUT_TAIL_CHARS:]
    if proc.returncode != 0:
        ret = 1
        msg = (
            f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, "
            f"stdout:{tail_out}, stderr:{tail_err}"
        )
        logger.error("render_video task_id:%s %s", task_id, msg)
        return ret, msg
    # 成功路径也记录 CLI 摘要(rendered in Xm Y.Zs / workers / GPU 标注),
    # 使 render 阶段可纳入 /app/log/stat/skill-timing.py 的统一统计口径
    summary = [line for line in tail_out.strip().splitlines() if line.strip()][-6:]
    logger.info("render_video task_id:%s ok, cli_tail:\n%s", task_id, "\n".join(summary))
    return ret, msg