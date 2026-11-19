#!/usr/bin/env python3
"""
Test harness for ``step_init`` (hyperframes init + BRIEF + capture + frame.md).

Creates a new HyperFrames project in an empty directory. Does not clone and
does not require ``--videodir`` to exist: ``hyperframes init`` needs an empty
directory, and ``video_create`` creates that directory with ``os.makedirs``
immediately before ``step_init``. This script does the same, after rejecting a
non-empty path. ``--preflight-only`` checks inputs and imports, then exits
without creating the directory.

Side effects (when not ``--preflight-only``): creates ``--videodir`` and writes
``hyperframes.json``, ``BRIEF.md``, ``frame.md``, and
``capture/extracted/{visible-text.txt,tokens.json}``. ``update_task_status`` is
stubbed unless ``--live-status``.

Default ``--videodir`` is ``/app/videos/harness/{YYYY-MM-DD}/init-{YYYYmmddHHMMSS}``.
When ``--content`` and ``--content-file`` are both omitted, fields are seeded
from ``--seed-from`` (default: the 2026-11-19 failed project):
``capture/extracted/visible-text.txt``, ``tokens.json`` title/description, and
``BRIEF.md`` frontmatter (line split, no YAML parser).

Deploy path: Dockerfile copies build_assets/ → /app/, so these harnesses live
at /app/test/ (run inside the htmlvideo worker container).

Usage:
  python3 /app/test/test_step_init.py --preflight-only --content "Hello"
  python3 /app/test/test_step_init.py --content-file /tmp/content.txt
  python3 /app/test/test_step_init.py --seed-from /app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914

Full pipeline chain (container). After init, copy project= from the log into P.
storyboard / tts default to --clone; use --in-place so later steps see the same tree.
  # optional smoke: python3 /app/test/<script>.py --preflight-only ...
  python3 /app/test/test_step_init.py \\
    --seed-from /app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914
  # or: --content-file /tmp/content.txt / --content "..."
  P=/app/videos/harness/YYYY-MM-DD/init-...   # replace with logged project=
  python3 /app/test/test_step_storyboard.py --videodir "$P" --in-place
  python3 /app/test/test_parallel_tts_visual.py --videodir "$P" --in-place \\
    --modelcat english --modelname "<voice>"
  python3 /app/test/test_step_sync_duration.py --videodir "$P"
  python3 /app/test/test_parallel_caption_frames.py --videodir "$P"
  python3 /app/test/test_step_verify.py --videodir "$P"
  python3 /app/test/test_step_render.py --videodir "$P"
  # exit 0 per step before continuing; --live-status --domain <d> for real status POSTs
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import (  # noqa: E402
    APP_ROOT,
    FAILED_PROJECT,
    add_common_args,
    check_bins,
    get_logger,
    log_env,
    log_step_result,
    stub_update_status,
)

BUILD_FRAME = "/app/scripts/build-frame.mjs"
PRESET_ROOT = Path("/app/skills/hyperframes-creative/frame-presets")
_INIT_OUTPUTS = (
    "hyperframes.json",
    "BRIEF.md",
    "frame.md",
    "capture/extracted/visible-text.txt",
    "capture/extracted/tokens.json",
)

# Used only when /app and this repo's html_utility cannot be imported.
# Keys match html_utility.video_constant.
_ASPECT_FALLBACK = {
    "landscape": "1920x1080",
    "portrait": "1080x1920",
    "square": "1080x1080",
    "landscape-4k": "3840x2160",
    "portrait-4k": "2160x3840",
    "square-4k": "2160x2160",
}
_LANGUAGE_FALLBACK = {
    "english": "en",
    "china": "zh",
    "japan": "ja",
    "spanish": "es",
    "arab": "ar",
}


def default_init_videodir() -> str:
    now = time.localtime()
    today = time.strftime("%Y-%m-%d", now)
    stamp = time.strftime("%Y%m%d%H%M%S", now)
    return f"/app/videos/harness/{today}/init-{stamp}"


def load_maps() -> tuple[dict[str, str], dict[str, str]]:
    """Prefer ``/app`` maps, then this repo, then the fallback copies."""
    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    if APP_ROOT not in sys.path:
        sys.path.insert(0, APP_ROOT)
    try:
        from html_utility.video_constant import ASPECT_MAP, LANGUAGE_MAP

        return dict(ASPECT_MAP), dict(LANGUAGE_MAP)
    except Exception:
        return dict(_ASPECT_FALLBACK), dict(_LANGUAGE_FALLBACK)


def _unquote_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        body = value[1:-1]
        out: list[str] = []
        index = 0
        while index < len(body):
            if body[index] == "\\" and index + 1 < len(body):
                out.append(body[index + 1])
                index += 2
                continue
            out.append(body[index])
            index += 1
        return "".join(out)
    return value


def _parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    found: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        if not key or key.startswith("-"):
            continue
        found[key] = _unquote_scalar(raw.strip())
    return found


def _parse_length(raw: str) -> int | None:
    text = raw.strip()
    if text.endswith(("s", "S")):
        text = text[:-1].strip()
    if text.isdigit():
        return int(text)
    return None


def _read_seed(
    seed_dir: Path,
    aspect_map: dict[str, str],
    language_map: dict[str, str],
    logger,
) -> dict[str, object] | None:
    seed_dir = seed_dir.expanduser().resolve()
    if not seed_dir.is_dir():
        logger.error("seed-from is not a directory: %s", seed_dir)
        return None
    seed: dict[str, object] = {}
    visible = seed_dir / "capture" / "extracted" / "visible-text.txt"
    if visible.is_file():
        seed["content"] = visible.read_text(encoding="utf-8").strip()
    else:
        logger.warning("seed visible-text.txt missing: %s", visible)
    tokens_path = seed_dir / "capture" / "extracted" / "tokens.json"
    tokens: dict = {}
    if tokens_path.is_file():
        try:
            loaded = json.loads(tokens_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("seed tokens.json invalid: %s", exc)
            loaded = {}
        if isinstance(loaded, dict):
            tokens = loaded
        else:
            logger.warning("seed tokens.json is not an object: %s", tokens_path)
    else:
        logger.warning("seed tokens.json missing: %s", tokens_path)
    frontmatter: dict[str, str] = {}
    brief = seed_dir / "BRIEF.md"
    if brief.is_file():
        frontmatter = _parse_frontmatter(brief.read_text(encoding="utf-8"))
    else:
        logger.warning("seed BRIEF.md missing: %s", brief)

    title = str(tokens.get("title") or "").strip()
    description = str(tokens.get("description") or "").strip()
    if title:
        seed["message"] = title
    elif frontmatter.get("message"):
        seed["message"] = frontmatter["message"]
    if description:
        seed["summary"] = description
    elif frontmatter.get("summary"):
        seed["summary"] = frontmatter["summary"]
    for key in ("audience", "angle", "customization", "notes"):
        if frontmatter.get(key):
            seed[key] = frontmatter[key]
    if frontmatter.get("style_preset"):
        seed["style"] = frontmatter["style_preset"]
    aspect_raw = frontmatter.get("aspect", "")
    aspect_key = next((key for key, value in aspect_map.items() if value == aspect_raw), None)
    if aspect_key:
        seed["aspect"] = aspect_key
    elif aspect_raw:
        logger.warning("seed BRIEF aspect %r is not in ASPECT_MAP", aspect_raw)
    language_raw = frontmatter.get("language", "")
    modelcat = next(
        (key for key, value in language_map.items() if value == language_raw), None
    )
    if modelcat:
        seed["modelcat"] = modelcat
    elif language_raw:
        logger.warning("seed BRIEF language %r is not in LANGUAGE_MAP", language_raw)
    if frontmatter.get("length"):
        length = _parse_length(frontmatter["length"])
        if length is None:
            logger.warning("seed BRIEF length %r is not a number of seconds", frontmatter["length"])
        else:
            seed["duration"] = length
    logger.info(
        "seeded from %s fields=%s",
        seed_dir,
        ",".join(sorted(seed)) or "<none>",
    )
    return seed


def _pick(explicit, seed: dict[str, object], key: str, default):
    if explicit is not None:
        return explicit
    if key in seed:
        return seed[key]
    return default


def parse_args(argv=None):
    aspect_map, language_map = load_maps()
    parser = argparse.ArgumentParser(
        description=(
            "Run step_init in a new empty directory "
            "(hyperframes init, BRIEF.md, capture files, frame.md)."
        )
    )
    add_common_args(parser, default_init_videodir())
    parser.add_argument("--content", default=None, help="spoken/source text written to visible-text.txt")
    parser.add_argument(
        "--content-file",
        type=Path,
        default=None,
        help="read content from this file (ignored when --content is set)",
    )
    parser.add_argument("--message", default=None)
    parser.add_argument("--summary", default=None)
    parser.add_argument("--audience", default=None)
    parser.add_argument("--angle", default=None)
    parser.add_argument("--customization", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--aspect",
        default=None,
        choices=tuple(aspect_map),
        help="ASPECT_MAP key; also passed as hyperframes --resolution (default: landscape)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="seconds; BRIEF length is {duration}s (default: 30)",
    )
    parser.add_argument(
        "--modelcat",
        default=None,
        choices=tuple(language_map),
        help="LANGUAGE_MAP key; step_init receives the mapped language code (default: english)",
    )
    parser.add_argument(
        "--style",
        default=None,
        help="frame preset name (default: capsule)",
    )
    parser.add_argument(
        "--seed-from",
        type=Path,
        default=Path(FAILED_PROJECT),
        help="project to read when --content/--content-file are omitted",
    )
    args = parser.parse_args(argv)
    return args, aspect_map, language_map


def _resolve_inputs(args, aspect_map, language_map, logger):
    seed: dict[str, object] = {}
    if args.content is not None:
        content = args.content
        if args.content_file is not None:
            logger.info("--content set; ignoring --content-file %s", args.content_file)
    elif args.content_file is not None:
        content_path = args.content_file.expanduser()
        if not content_path.is_file():
            logger.error("content-file is not a file: %s", content_path)
            return None
        content = content_path.read_text(encoding="utf-8")
    else:
        loaded = _read_seed(args.seed_from, aspect_map, language_map, logger)
        if loaded is None:
            return None
        seed = loaded
        content = str(seed.get("content", ""))
    resolved = {
        "content": content,
        "message": str(_pick(args.message, seed, "message", "")),
        "summary": str(_pick(args.summary, seed, "summary", "")),
        "audience": str(_pick(args.audience, seed, "audience", "")),
        "angle": str(_pick(args.angle, seed, "angle", "")),
        "customization": str(_pick(args.customization, seed, "customization", "")),
        "notes": str(_pick(args.notes, seed, "notes", "")),
        "aspect": str(_pick(args.aspect, seed, "aspect", "landscape")),
        "duration": int(_pick(args.duration, seed, "duration", 30)),
        "modelcat": str(_pick(args.modelcat, seed, "modelcat", "english")),
        "style": str(_pick(args.style, seed, "style", "capsule")),
    }
    if resolved["modelcat"] not in language_map:
        logger.error("modelcat %s is not in LANGUAGE_MAP", resolved["modelcat"])
        return None
    if resolved["aspect"] not in aspect_map:
        logger.error("aspect %s is not in ASPECT_MAP", resolved["aspect"])
        return None
    resolved["language"] = language_map[resolved["modelcat"]]
    return resolved


def _reject_nonempty(project_dir: Path, logger) -> int:
    if not project_dir.exists():
        return 0
    if not project_dir.is_dir():
        logger.error("videodir exists and is not a directory: %s", project_dir)
        return 1
    try:
        extra = next(project_dir.iterdir(), None)
    except OSError as exc:
        logger.error("videodir cannot be listed: %s (%s)", project_dir, exc)
        return 1
    if extra is not None:
        logger.error(
            "videodir exists and is not empty "
            "(hyperframes init requires an empty directory): %s",
            project_dir,
        )
        return 1
    return 0


def _preflight(project_dir: Path, inputs: dict, logger) -> int:
    if _reject_nonempty(project_dir, logger) != 0:
        return 1
    if not str(inputs["content"]).strip():
        logger.error("preflight failed — content is empty")
        return 1
    missing = check_bins(["node", "npx", BUILD_FRAME], logger)
    preset = PRESET_ROOT / inputs["style"] / "FRAME.md"
    if not preset.is_file():
        logger.error("preflight failed — frame preset FRAME.md missing: %s", preset)
        missing.append(str(preset))
    if missing:
        return 1
    logger.info(
        "preflight ok — empty dir, content, node/npx, build-frame.mjs, preset %s",
        inputs["style"],
    )
    return 0


def main(argv=None) -> int:
    args, aspect_map, language_map = parse_args(argv)
    logger = get_logger("test_step_init", args.log_file)
    project_dir = args.videodir.expanduser().resolve()
    task_id = args.task_id or project_dir.name
    inputs = _resolve_inputs(args, aspect_map, language_map, logger)
    if inputs is None:
        return 1
    if _preflight(project_dir, inputs, logger) != 0:
        return 1

    try:
        sys.path.insert(0, APP_ROOT)
        from html_utility.create_pipeline import step_init
    except Exception as exc:
        logger.error("import of step_init failed: %s", exc)
        return 1

    stub_update_status(stub=not args.live_status, logger=logger)
    log_env(
        logger,
        {
            "aspect": inputs["aspect"],
            "resolution": inputs["aspect"],
            "duration": inputs["duration"],
            "modelcat": inputs["modelcat"],
            "language": inputs["language"],
            "style": inputs["style"],
        },
    )
    if args.preflight_only:
        logger.info("preflight-only: not creating %s and not running step_init", project_dir)
        return 0

    # init_htmlvideo does not create the project root. video_create does
    # os.makedirs immediately before step_init; hyperframes init then requires
    # that directory to be empty. Match video_create.
    project_dir.mkdir(parents=True, exist_ok=True)
    logger.info("project dir ready (empty): %s", project_dir)

    logger.info(
        "step_init START task_id=%s project=%s aspect=%s style=%s duration=%s language=%s",
        task_id,
        project_dir,
        inputs["aspect"],
        inputs["style"],
        inputs["duration"],
        inputs["language"],
    )
    started = time.time()
    try:
        result = step_init(
            logger,
            str(project_dir),
            resolution=inputs["aspect"],
            content=inputs["content"],
            message=inputs["message"],
            aspect=inputs["aspect"],
            duration=inputs["duration"],
            language=inputs["language"],
            audience=inputs["audience"],
            angle=inputs["angle"],
            summary=inputs["summary"],
            customization=inputs["customization"],
            notes=inputs["notes"],
            style_preset=inputs["style"],
            task_id=task_id,
        )
    except Exception as exc:
        logger.error("step_init raised: %s", exc)
        return 1
    elapsed = time.time() - started
    log_step_result(logger, "init", result)
    missing_outputs = [rel for rel in _INIT_OUTPUTS if not (project_dir / rel).is_file()]
    if missing_outputs:
        logger.error("step_init outputs missing: %s", ", ".join(missing_outputs))
    ok = result.ret == 0 and not missing_outputs
    logger.info("step_init %s total=%.1fs", "PASS" if ok else "FAIL", elapsed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
