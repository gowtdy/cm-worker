import os
import subprocess

from html_utility.video_constant import ASPECT_MAP, LANGUAGE_MAP

def yaml_quote(value: str) -> str:
    """Double-quote a YAML scalar and escape \\ and \"."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

def create_html_brief(video_dir, message, aspect, duration, language, audience, angle, summary, customization, notes, content, assets=None):
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

    if content:
        lines.append("## user input content")
        lines.append("")
        lines.append(content)
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
    task_id: str = "",
):
    ret = 0
    msg = "ok"
    
    return ret, msg
