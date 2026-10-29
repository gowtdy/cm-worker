from transcriber.base import TranscribeResult

FORMATS = {"json", "txt", "srt", "vtt", "ass"}


def format_result(result: TranscribeResult, fmt: str) -> str:
    key = (fmt or "json").strip().lower()
    if key not in FORMATS:
        raise ValueError(f"unsupported transcribe format: {fmt}")
    if key == "json":
        return result.to_json()
    if key == "txt":
        return result.text
    if key == "srt":
        return _to_srt(result)
    if key == "vtt":
        return _to_vtt(result)
    return _to_ass(result)


def _clock(seconds: float) -> tuple:
    ms = max(0, int(round(float(seconds) * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return h, m, s, ms


def _srt_time(t: float) -> str:
    h, m, s, ms = _clock(t)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(t: float) -> str:
    h, m, s, ms = _clock(t)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _ass_time(t: float) -> str:
    h, m, s, ms = _clock(t)
    cs = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _to_srt(result: TranscribeResult) -> str:
    blocks = []
    for i, seg in enumerate(result.segments, start=1):
        blocks.append(f"{i}\n{_srt_time(seg.start)} --> {_srt_time(seg.end)}\n{seg.text}")
    return "\n\n".join(blocks) + "\n"


def _to_vtt(result: TranscribeResult) -> str:
    blocks = []
    for i, seg in enumerate(result.segments, start=1):
        blocks.append(f"{i}\n{_vtt_time(seg.start)} --> {_vtt_time(seg.end)}\n{seg.text}")
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1920\n"
    "PlayResY: 1080\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _to_ass(result: TranscribeResult) -> str:
    lines = [ASS_HEADER.rstrip("\n")]
    for seg in result.segments:
        text = seg.text.replace("\\", "\\\\").replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_ass_time(seg.start)},{_ass_time(seg.end)},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"