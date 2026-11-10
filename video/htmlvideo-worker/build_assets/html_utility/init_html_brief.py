from html_utility.video_constant import ASPECT_MAP, LANGUAGE_MAP

def yaml_quote(value: str) -> str:
    """Double-quote a YAML scalar and escape \\ and \"."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

def init_html_brief(video_dir, message, aspect, duration, language, audience, angle, summary, customization, notes, content, assets=None):
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
    lines.append(f"angle: {angle}")
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