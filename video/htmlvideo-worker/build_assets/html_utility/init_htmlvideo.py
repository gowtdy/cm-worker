import json
import os
import subprocess

from html_utility.video_constant import ASPECT_MAP, LANGUAGE_MAP


def init_htmlvideo(
    logger,
    project_dir,
    *,
    resolution,
    content,
    message,
    aspect,
    duration,
    language,
    audience,
    angle,
    summary,
    customization,
    notes,
    style_preset=None,
    task_id: str = "",
):
    # 下面的初始化顺序不能颠倒
    # 1. hyperframes init 要求目标目录为空；
    # 2. BRIEF.md 必须在 init 之后写入
    # 3. capture 文件必须在 frame.md 文件之前写入
    # 4. frame.md 文件必须在 frame style 文件之前写入
    # hyperframes init 要求目标目录为空；BRIEF.md 必须在 init 之后写入
    ret, msg = init_htmlframe(logger, project_dir, resolution=resolution, task_id=task_id)
    if ret != 0:
        return ret, "Init video failed!"

    # 写 brief文件
    try:
        create_html_brief(
            project_dir, message, aspect, duration, language,
            audience, angle, summary, customization, notes,
            style_preset=style_preset,
        )
    except Exception as e:
        msg = f"Failed to write BRIEF.md: {e}"
        logger.error(f"task_id:{task_id} {msg}")
        return 1, "Init brief failed!"

    # 写 capture文件
    ret, msg = init_capture(logger, project_dir, content, message, summary, task_id=task_id)
    if ret != 0:
        return ret, "Init capture failed!"

    # 写 frame.md 文件
    ret, msg = init_frame_style(
        logger, project_dir, style_preset=style_preset, task_id=task_id,
    )
    if ret != 0:
        return ret, "Init frame style failed!"

    return 0, "ok"


def yaml_quote(value: str) -> str:
    """Double-quote a YAML scalar and escape \\ and \"."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

def create_html_brief(video_dir, message, aspect, duration, language, audience, angle, summary, customization, notes, style_preset, assets=None):
    brief_path = f'{video_dir}/BRIEF.md'
    workflow = 'aiflow-htmlvideo'
    # Who drives execution.  取值为：automation or companion
    flow = 'automation'
    # Whether the live board is used for plan and layout review. 取值为： yes or no
    storyboard = 'no'
    # How later preference and checkpoint gates behave. 取值为： collaborative or autonomous
    mode = 'autonomous'


    lines: list[str] = ["---"]
    lines.append(f"workflow: {workflow}")
    lines.append(f"flow: {flow}")
    lines.append(f"storyboard: {storyboard}")
    lines.append(f"mode: {mode}")
    lines.append(f"message: {yaml_quote(message)}")
    lines.append(f"audience: {yaml_quote(audience)}")
    lines.append(f"aspect: {ASPECT_MAP[aspect]}")
    lines.append(f"language: {language}")
    lines.append(f"length: {duration}s")
    lines.append(f"angle: {yaml_quote(angle)}")
    lines.append(f"style_preset: {style_preset}")
    #lines.append(f"tone: {tone}")
    lines.append("---")

    lines.append("")
    lines.append("## Intent")
    lines.append("")
    lines.append(summary)
    lines.append("")

    #if assets:
    #    lines.append("## Assets")
    #    lines.append("")
    #    for item in assets:
    #        lines.append(f"- {item}")
    #    lines.append("")

    if customization:
        lines.append("## Customizations")
        lines.append("")
        lines.append(customization)
        lines.append("")

    if notes:
        lines.append("## Notes")
        lines.append("")
        lines.append(notes)
        lines.append("")

    brief = '\n'.join(lines)
    with open(brief_path, 'w') as f:
        f.write(brief)

    return brief

def init_htmlframe(
    logger,
    video_dir, 
    resolution: str | None,
    *,
    example: str = "blank",
    video: str | None = None,
    audio: str | None = None,
    skip_transcribe: bool = True,
    whisper_model: str | None = None,
    whisper_language: str | None = None,
    tailwind: bool = False,
    skip_skills: bool = True,
    task_id: str = "",
):
    ret = 0
    msg = "ok"
    cmd = [
        "npx",
        "hyperframes",
        "init",
        str(video_dir),
        "--non-interactive",
        "--example",
        example,
    ]
    if resolution:
        cmd.extend(["--resolution", resolution])
    if video:
        cmd.extend(["--video", video])
    if audio:
        cmd.extend(["--audio", audio])
    if skip_transcribe:
        cmd.append("--skip-transcribe")
    if whisper_model:
        cmd.extend(["--model", whisper_model])
    if whisper_language:
        cmd.extend(["--language", whisper_language])
    if tailwind:
        cmd.append("--tailwind")
    env = os.environ.copy()
    if skip_skills:
        env["HYPERFRAMES_SKIP_SKILLS"] = "1"
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = (
            f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, "
            f"stdout:{proc.stdout}, stderr:{proc.stderr}"
        )
        logger.error(f"task_id:{task_id} Failed to initialize htmlframe: {msg}")

    if proc.stdout:
        logger.info("task_id:%s hyperframes init stdout: %s", task_id, proc.stdout.strip())
        return ret, msg
    return ret, msg

def init_capture(
    logger,
    video_dir,
    content: str,
    message: str,
    summary: str,
    task_id: str = "",
):
    ret = 0
    msg = "ok"
    
    extracted_dir = f'{video_dir}/capture/extracted'
    visible_text_path = f'{extracted_dir}/visible_text.txt'
    tokens_path = f'{extracted_dir}/tokens.json'

    # 创建 extracted 目录并写入 visible_text.txt / tokens.json
    try:
        os.makedirs(extracted_dir, exist_ok=True)
        with open(visible_text_path, 'w', encoding='utf-8') as f:
            f.write(f'{content}\n')

        tokens_str = json.dumps(
            {
                "title": message,
                "description": summary,
                "colors": [],
                "fonts": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        with open(tokens_path, 'w', encoding='utf-8') as f:
            f.write(f'{tokens_str}\n')
    except Exception as e:
        ret = 1
        msg = f"Failed to write capture files: {e}"
        logger.error(f"task_id:{task_id} {msg}")

    return ret, msg

def init_frame_style(
    logger,
    project_dir,
    *,
    style_preset,
    task_id: str = "",
):
    ret = 0
    msg = "ok"

    script_path = f'/app/scripts/build-frame.mjs'
    if not os.path.exists(script_path):
        ret = 1
        msg = f"script_path:{script_path} not found"
        logger.error(f"task_id:{task_id} {msg}")
        return ret, msg

    cmd = [
        "node",
        script_path,
        "--preset",
        style_preset,
        "--videodir",
        str(project_dir),
    ]
    logger.info("+ %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        ret = 1
        msg = f"cmd:{' '.join(cmd)}, returncode:{proc.returncode}, stdout:{proc.stdout}, stderr:{proc.stderr}"
        logger.error(f"task_id:{task_id} {msg}")
        return ret, msg
    return ret, msg
