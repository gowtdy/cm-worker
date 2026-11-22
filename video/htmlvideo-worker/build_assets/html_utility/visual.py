# -*- coding: utf-8 -*-
"""Frame visual design helpers for htmlvideo worker.

Deterministic fan-out (mirrors frame_html.py): a single "design contract"
session produces the video-wide `## Video direction` + per-frame blueprint
assignment, then N parallel workers each append ONE frame's shot-sequence lines
(blueprint/focal/roles/sfx/Adapt/Scene) to its OWN fragment file. Python re-
assembles STORYBOARD.md, so the narrative fields are preserved byte-exact by
construction (the model never re-types them). Rollback: HTMLVIDEO_FRAME_VISUAL_DRIVER=0.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from html_utility.claude_cli import _env_positive_int, run_claude, run_claude_result

STORYBOARD_FILENAME = "STORYBOARD.md"
FRAME_FILENAME = "frame.md"
HYPERFRAMES_JSON = "hyperframes.json"
VIDEO_DIRECTION_HEADING = "## Video direction"

VISUAL_DRIVER_ENV = "HTMLVIDEO_FRAME_VISUAL_DRIVER"
VISUAL_CONCURRENCY_ENV = "HTMLVIDEO_FRAME_VISUAL_MAX_CONCURRENCY"
VISUAL_REPAIR_ROUNDS_ENV = "HTMLVIDEO_FRAME_VISUAL_REPAIR_ROUNDS"
VISUAL_DESIGN_THINKING_ENV = "HTMLVIDEO_FRAME_VISUAL_DESIGN_THINKING_TOKENS"
VISUAL_BLUEPRINT_MAX_CHARS_ENV = "HTMLVIDEO_FRAME_VISUAL_BLUEPRINT_MAX_CHARS"
DEFAULT_MAX_WORKERS = 16
MAX_RETRY_ROUNDS = 1
DEFAULT_REPAIR_ROUNDS = 2
# Design sees every frame and writes the video-wide contract, so it keeps a
# wider budget than the per-frame workers (profile default 24000).
DEFAULT_DESIGN_THINKING_TOKENS = 32000
DEFAULT_BLUEPRINT_MAX_CHARS = 5000

VISUAL_DIR = os.path.join(".hyperframes", "visual")
DESIGN_FILE = os.path.join(VISUAL_DIR, "_design.md")
ANIMATION_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "skills", "hyperframes-animation"))
BLUEPRINTS_DIR = os.path.join(ANIMATION_DIR, "blueprints")

_SPLIT_RE = re.compile(r"(?=^## Frame\s+\d+)", re.M)
_FRAME_HEAD_RE = re.compile(r"^## Frame\s+(\d+)", re.M)
# Workers sometimes emit `- Scene 1`, `* Scene 1`, `**Scene 1 (…):**` or an
# indented copy of the prompt example; accept them, then normalize to the bare
# `Scene N (…)` form storyboard-gate.mjs parses.
_SCENES_RE = re.compile(r"^\s*(?:[-*]+\s+)?(?:\*\*\s*)?Scene\s+\d+", re.M)
_SCENE_LINE_RE = re.compile(
    r"^[ \t]*((?:[-*]+[ \t]+)?(?:\*\*[ \t]*)?)(Scene[ \t]+\d+.*)$", re.M)
_INDENTED_FIELD_RE = re.compile(
    r"^[ \t]+(-[ \t]*(?:blueprint|focal|roles|sfx):|(?:Adapt|Reproduce):)", re.M)
_FOCAL_RE = re.compile(r"^\s*-\s*focal:", re.M)
_ROLES_RE = re.compile(r"^\s*-\s*roles:", re.M)
_WINDOW_RE = re.compile(
    r"^(Scene\s+\d+\s*\(\s*)(\d+(?:\.\d+)?)\s*s?\s*[–—-]\s*(\d+(?:\.\d+)?)\s*s?\s*\)",
    re.M)
# storyboard-gate.mjs windowRe — a window it cannot parse is rewritten canonically.
_GATE_WINDOW_RE = re.compile(
    r"^Scene\s+\d+\s*\(\s*([\d.]+)s?\s*[–—-]\s*([\d.]+)s?\s*\)")
_DURATION_RE = re.compile(r"^-\s*duration:\s*([\d.]+)\s*s?", re.M)
_WINDOW_EPS = 0.05
GATE_COVERAGE = 0.9
GATE_OVERRUN = 0.2
_DONE_RE = re.compile(r"\bdone\s+OK\s+(\d+)\b")
_ASSIGN_LINE_RE = re.compile(r"^F(\d+)\s*=\s*([\w-]+)(.*)$", re.M)
# One rewrite after Phase A when any assigned blueprint is outside the catalog.
MAX_DESIGN_ASSIGN_REPAIRS = 1
# `compose` = intentional no-shape; workers invent without an inlined blueprint body.
COMPOSE_BLUEPRINT = "compose"


def _env_on(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


def _split_storyboard(text: str):
    """Return (preamble, blocks{num:block}, order[num...]) from STORYBOARD text.

    The preamble is everything before the first `## Frame N` (the YAML front
    matter; on an outline board it has no `## Video direction` yet). Blocks are
    keyed by frame number, order preserves document order.
    """
    parts = _SPLIT_RE.split(text)
    preamble = parts[0].rstrip()
    blocks: dict[str, str] = {}
    order: list[str] = []
    for part in parts[1:]:
        m = _FRAME_HEAD_RE.match(part)
        if not m:
            continue
        blocks[m.group(1)] = part.strip()
        order.append(m.group(1))
    return preamble, blocks, order


def _frame_md_tokens(frame_md: str) -> str:
    """Extract the frame.md frontmatter `colors:` + `typography:` blocks (the exact
    hex/font VALUES). Mirrors frame-packets-core.mjs `frontmatterBlock`: the values
    live in YAML frontmatter, while the `## Colors` / `## Typography` narrative
    sections only reference them symbolically ({colors.cream}) — so a No-tools
    design phase needs them inlined or it hallucinates the palette."""
    if not frame_md:
        return ""
    m = re.search(r"^---\r?\n([\s\S]*?)\r?\n---", frame_md, re.M)
    if not m:
        return ""
    lines = m.group(1).split("\n")
    _top_key = re.compile(r"^[A-Za-z][\w .-]*:")
    out = []
    for key in ("colors", "typography"):
        idx = next((i for i, l in enumerate(lines) if l.startswith(key + ":")), None)
        if idx is None:
            continue
        block = [lines[idx]]
        for j in range(idx + 1, len(lines)):
            line = lines[j]
            if line.strip() and not line[:1].isspace() and _top_key.match(line):
                break
            block.append(line)
        out.append("\n".join(block).rstrip())
    return "\n\n".join(out).strip()


def _blueprint_catalog() -> frozenset[str]:
    """Shot-shape ids on disk under `hyperframes-animation/blueprints/*.md`."""
    ids: set[str] = set()
    try:
        for name in os.listdir(BLUEPRINTS_DIR):
            if name.endswith(".md") and not name.startswith("."):
                ids.add(name[:-3])
    except OSError:
        pass
    return frozenset(ids)


def _type_token_names(tokens: str) -> list[str]:
    """Typography slot + color names from inlined frame.md tokens (NOT blueprints)."""
    if not tokens:
        return []
    names: list[str] = []
    section = None
    for raw in tokens.split("\n"):
        s = raw.strip()
        if s == "colors:":
            section = "colors"
            continue
        if s == "typography:":
            section = "typography"
            continue
        if section in ("colors", "typography"):
            m = re.match(r"^([\w-]+)\s*:", s)
            if m:
                names.append(m.group(1))
    # stable unique, keep order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _catalog_prompt_block(catalog: frozenset[str], banned_tokens: list[str] | None = None) -> str:
    """Tell Phase A which ids are legal shot shapes — and which type tokens are not."""
    ids = sorted(catalog)
    lines = [
        "Valid blueprint ids (pick ONLY from this list, or "
        f"`{COMPOSE_BLUEPRINT}` when no catalog shape fits):",
    ]
    lines.extend(f"- {i}" for i in ids)
    lines.append(f"- {COMPOSE_BLUEPRINT}")
    banned = [t for t in (banned_tokens or []) if t and t not in catalog and t != COMPOSE_BLUEPRINT]
    if banned:
        lines.append(
            "Do NOT use frame.md typography/color token names as blueprint ids — "
            "those are palette/type slots, not shot shapes. Banned examples: "
            + ", ".join(f"`{t}`" for t in banned)
            + "."
        )
    else:
        lines.append(
            "Do NOT use frame.md typography slot names (e.g. `display`, `headline`, "
            "`stat-number`, `card-headline`, `closing-display`, `orbit-numeral`) or "
            "color names as blueprint ids — those are type/palette tokens, not shot shapes."
        )
    return "\n".join(lines) + "\n"


def _block_field(block: str, name: str) -> str:
    m = re.search(rf"^-\s*{name}:\s*(.*)$", block or "", re.M)
    return m.group(1).strip() if m else ""


def _first_sentence(text: str, limit: int = 120) -> str:
    s = text.strip().strip('"“”').strip()
    m = re.match(r"(.+?[.!?])(?:\s|$)", s)
    if m:
        s = m.group(1)
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _frame_summary(frame_num: str, block: str) -> dict[str, str]:
    """Design-phase digest of one outline frame block (read-only facts)."""
    head = (block or "").split("\n", 1)[0]
    m = re.match(r"^## Frame\s+\d+\s*(?:[—–:-]+\s*)?(.*)$", head)
    return {
        "num": frame_num,
        "title": (m.group(1).strip() if m else ""),
        "beat": _block_field(block, "beat"),
        "duration": _block_field(block, "duration"),
        "transition_in": _block_field(block, "transition_in"),
        "scene": _block_field(block, "scene"),
        "voiceover": _first_sentence(_block_field(block, "voiceover")),
    }


def _frame_summaries(blocks: dict[str, str], order: list[str]) -> list[dict[str, str]]:
    return [_frame_summary(fn, blocks[fn]) for fn in order]


_STAGE_STOPWORDS = frozenset(
    "the and with that this then from into onto over under while each their "
    "your they them its it's are was were been being have has had will would "
    "just only also more most very beside below above across after before "
    "single first second third canvas frame".split()
)
_SAME_STAGE_CUE_RE = re.compile(
    r"\b(same|continu(?:es|ing|ed)|stays on|remains|carried over)\b", re.I)


def _stage_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'-]{3,}", (text or "").lower())
            if w not in _STAGE_STOPWORDS}


def _parse_seconds(raw: str) -> float | None:
    m = re.match(r"\s*([\d.]+)", raw or "")
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def _shared_stage_pairs(summaries: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    """Adjacent frames that look like one continuing stage: the scene says
    "same …", or scene vocabulary overlaps heavily (looser when durations also
    match within 10%). A hint for the design phase, never a hard rule."""
    pairs: list[tuple[str, str, str]] = []
    for prev, cur in zip(summaries, summaries[1:]):
        cue = _SAME_STAGE_CUE_RE.search(cur.get("scene", ""))
        if cue:
            pairs.append((prev["num"], cur["num"], f'scene says "{cue.group(1).lower()}"'))
            continue
        a, b = _stage_words(prev.get("scene", "")), _stage_words(cur.get("scene", ""))
        if not a or not b:
            continue
        jac = len(a & b) / len(a | b)
        da, db = _parse_seconds(prev.get("duration", "")), _parse_seconds(cur.get("duration", ""))
        similar_dur = bool(da and db and abs(da - db) / max(da, db) <= 0.1)
        if jac >= 0.3 or (jac >= 0.2 and similar_dur):
            shared = ", ".join(sorted(a & b)[:4])
            pairs.append((prev["num"], cur["num"], f"scene overlap {jac:.2f} ({shared})"))
    return pairs


def _frame_summary_block(summaries: list[dict[str, str]]) -> str:
    if not summaries:
        return ""
    lines = ["── per-frame summary (read-only facts from the outline) ──"]
    for s in summaries:
        parts = [f"F{s['num']}" + (f" — {s['title']}" if s.get("title") else "")]
        if s.get("beat"):
            parts.append(f"beat: {s['beat']}")
        if s.get("duration"):
            parts.append(s["duration"])
        if s.get("transition_in"):
            parts.append(f"transition_in: {s['transition_in']}")
        if s.get("voiceover"):
            parts.append(f'vo: "{s["voiceover"]}"')
        lines.append(" · ".join(parts))
    pairs = _shared_stage_pairs(summaries)
    if pairs:
        lines.append(
            "Shared-stage hints (adjacent frames that look like ONE continuing stage — "
            "keep the stage surface/camera/focal anchor continuous across the cut):")
        lines.extend(f"- F{a}→F{b}: {why}" for a, b, why in pairs)
    else:
        lines.append("Shared-stage hints: none detected.")
    return "\n".join(lines) + "\n"


def _build_design_prompt(
    preamble: str,
    frame_nums: list[str],
    tokens: str = "",
    catalog: frozenset[str] | None = None,
    summaries: list[dict[str, str]] | None = None,
) -> str:
    joined = ", ".join(frame_nums)
    intro = (
        "The frame.md design tokens are inlined below and the harness ALREADY wrote the "
        "`## Video direction` palette & type — do NOT read frame.md, do NOT restate "
        "palette/type, and do NOT emit a `## Video direction` header." if tokens else
        "Read frame.md (design truth: palette/type/tokens) and the outline below."
    )
    cat = catalog if catalog is not None else _blueprint_catalog()
    catalog_block = _catalog_prompt_block(cat, _type_token_names(tokens))
    summary_block = _frame_summary_block(summaries or [])
    return (
        "You are the GLOBAL design phase only of the aiflow-build-frame-visual "
        "contract (everything you need is inlined here — do not invoke any skill). "
        + intro + "\n"
        + (f"{tokens}\n" if tokens else "")
        + "Produce ONLY the creative body then the assignment, in this order:\n\n"
        "**Video direction — creative body** (plain prose with **bold** labels, NOT "
        "`##` headings — it joins the pre-written palette/type under the existing "
        "`## Video direction` heading): color role-mapping (which candy = which "
        "emphasis), motion grammar, rhythm/held-frames (name the breather vs "
        "reveal-cascade frame numbers), caption band, negative list, "
        "**Locked transitions** (one line, every frame in order, e.g. "
        "`F1 cut → F2 push-slide RIGHT → …` — copy each frame's existing "
        "`transition_in` verbatim, never rename it; the shot sequences land them), and "
        "**Shared stage** (which adjacent frames share one stage and what persists "
        "across the cut; `none` if none — the per-frame summary below flags candidates).\n\n"
        "## Blueprint assignment\n"
        f"<one line per frame, frames are: {joined}. Format: `F<num> = <blueprint> "
        "(Adapt|Reproduce)` — `<blueprint>` MUST be a valid catalog id or "
        f"`{COMPOSE_BLUEPRINT}`>\n\n"
        + catalog_block
        + "\n"
        "Output this as your FINAL message text (no tools, no Write, no per-frame Scene "
        "lines — the per-frame workers do those). flow is automation → autonomous.\n\n"
        + (summary_block + "\n" if summary_block else "")
        + "── outline preamble ──\n"
        + preamble
        + "\n── end outline ──\n"
    )


def _split_design(design: str):
    """Split the Phase-A contract on `## Blueprint assignment` → (direction, assignment)."""
    idx = design.find("## Blueprint assignment")
    if idx == -1:
        return design, ""
    return design[:idx].strip(), design[idx:].strip()


def _frame_blueprint(assignment: str, frame_num: str) -> str:
    """The frame's blueprint id from its `F<num> = <id> (…)` assignment line."""
    m = re.search(rf"^F{frame_num}\s*=\s*([\w-]+)", assignment, re.M)
    return m.group(1) if m else ""


def _frame_assignment_line(assignment: str, frame_num: str) -> str:
    m = re.search(rf"^F{frame_num}\s*=.*$", assignment, re.M)
    return m.group(0) if m else ""


def _is_catalog_blueprint(blueprint_id: str, catalog: frozenset[str] | None = None) -> bool:
    """True when the id is `compose` or an on-disk blueprint stem."""
    if not blueprint_id:
        return False
    if blueprint_id.lower() == COMPOSE_BLUEPRINT:
        return True
    cat = catalog if catalog is not None else _blueprint_catalog()
    return blueprint_id in cat


def _assignment_catalog_misses(
    assignment: str,
    frame_nums: list[str],
    catalog: frozenset[str] | None = None,
) -> list[tuple[str, str]]:
    """Return [(frame_num, bad_id_or_empty), ...] for frames with non-catalog blueprints."""
    cat = catalog if catalog is not None else _blueprint_catalog()
    misses: list[tuple[str, str]] = []
    for fn in frame_nums:
        bp = _frame_blueprint(assignment, fn)
        if not _is_catalog_blueprint(bp, cat):
            misses.append((fn, bp))
    return misses


def _build_assignment_rewrite_prompt(
    design: str,
    misses: list[tuple[str, str]],
    frame_nums: list[str],
    catalog: frozenset[str] | None = None,
    banned_tokens: list[str] | None = None,
) -> str:
    """Phase-A repair: rewrite ONLY the blueprint assignment lines to catalog ids."""
    cat = catalog if catalog is not None else _blueprint_catalog()
    bad = ", ".join(
        f"F{fn}={bp or '∅'}" for fn, bp in misses
    )
    joined = ", ".join(frame_nums)
    return (
        "You are fixing the GLOBAL design-phase blueprint assignment ONLY. The creative "
        "Video direction body is already accepted — do NOT rewrite it.\n\n"
        f"These assignments are INVALID (not in the shot-shape catalog): {bad}. "
        "Typography/color token names (display, headline, orbit-numeral, …) are NOT "
        "blueprints.\n\n"
        + _catalog_prompt_block(cat, banned_tokens)
        + "\nEmit ONLY a `## Blueprint assignment` block with one line per frame "
        f"({joined}), format `F<num> = <blueprint> (Adapt|Reproduce)`, every "
        f"`<blueprint>` a catalog id or `{COMPOSE_BLUEPRINT}`. No Scene lines, no tools.\n\n"
        "── current contract (for context) ──\n"
        + design
        + "\n── end contract ──\n"
    )


def _splice_assignment(design: str, rewrite_text: str) -> str:
    """Replace the design's `## Blueprint assignment` section with the rewrite's."""
    new_assign = rewrite_text.strip()
    idx = new_assign.find("## Blueprint assignment")
    if idx != -1:
        new_assign = new_assign[idx:].strip()
    elif _ASSIGN_LINE_RE.search(new_assign):
        new_assign = "## Blueprint assignment\n\n" + new_assign
    else:
        return design  # unusable rewrite — keep prior
    direction, _old = _split_design(design)
    if not direction:
        return new_assign
    return direction + "\n\n" + new_assign


_BP_SECTION_RE = re.compile(r"^(?=#|\*\*[^*\n]+\*\*)", re.M)
# Kept first when a blueprint is over budget: the shape's signature move and
# its boundaries. roles-served / rule-mapping / camera-modifier go first.
_BP_PRIORITY = ("intent", "shot structure", "motion vocabulary", "duration",
                "signature", "boundar", "avoid", "anti")


def _bp_section_label(section: str) -> str:
    m = re.match(r"^\*\*([^*\n]+)\*\*", section)
    return m.group(1).strip().lower() if m else ""


def _truncate_blueprint(body: str, limit: int = DEFAULT_BLUEPRINT_MAX_CHARS) -> str:
    """Fit a blueprint into ~``limit`` chars: keep the title + priority sections
    (intent / shot structure / motion vocabulary / duration …) in document
    order, cut the last one that does not fit at a line boundary, drop the
    rest, and say so with a `(truncated …)` footer."""
    if len(body) <= limit:
        return body
    sections = [s.rstrip() for s in _BP_SECTION_RE.split(body) if s.strip()]

    def rank(i: int) -> int:
        if i == 0:
            return 0
        label = _bp_section_label(sections[i])
        for k, key in enumerate(_BP_PRIORITY):
            if key in label:
                return 1 + k
        return 100 + i

    kept: dict[int, str] = {}
    budget = limit
    ranked = sorted(range(len(sections)), key=rank)
    priority = [i for i in ranked if rank(i) < 100]
    # 1) whole priority sections that fit; 2) cut the oversized ones (usually
    # the shot structure) into what is left; 3) fill with the rest.
    for i in priority:
        if len(sections[i]) + 2 <= budget:
            kept[i] = sections[i]
            budget -= len(sections[i]) + 2
    for i in priority:
        if i in kept or budget <= 400:
            continue
        cut = sections[i][: budget - 40]
        cut = cut[: cut.rfind("\n")] if "\n" in cut else cut
        if cut.count("```") % 2:
            cut += "\n```"
        kept[i] = cut + "\n… (truncated)"
        budget -= len(kept[i]) + 2
    for i in ranked:
        if i not in kept and len(sections[i]) + 2 <= budget:
            kept[i] = sections[i]
            budget -= len(sections[i]) + 2
    dropped = [_bp_section_label(sections[i]) or f"#{i}"
               for i in range(len(sections)) if i not in kept]
    out = "\n\n".join(kept[i] for i in sorted(kept))
    return (
        out + f"\n\n(truncated: blueprint {len(body)} → {len(out)} chars"
        + (f"; dropped: {', '.join(dropped)}" if dropped else "") + ")"
    )


def _blueprint_body(blueprint_id: str, logger: logging.Logger | None = None) -> str:
    """Inline the frame's blueprint body (removes a worker Read); empty for `compose`.

    Logs a warning when the id is neither `compose` nor an on-disk catalog file —
    callers should have validated the assignment first; this is defense-in-depth.
    """
    if not blueprint_id or blueprint_id.lower() == COMPOSE_BLUEPRINT:
        return ""
    path = os.path.join(BLUEPRINTS_DIR, f"{blueprint_id}.md")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            body = f.read().strip()
        limit = _env_positive_int(
            VISUAL_BLUEPRINT_MAX_CHARS_ENV, DEFAULT_BLUEPRINT_MAX_CHARS,
            logger or logging.getLogger(__name__))
        return _truncate_blueprint(body, limit)
    warn = logger.warning if logger else logging.getLogger(__name__).warning
    warn(
        "visual blueprint miss id=%s path=%s — worker gets empty inline "
        "(assign a catalog id or compose)",
        blueprint_id,
        path,
    )
    return ""


def _build_worker_prompt(
    frame_num: str,
    block: str,
    design: str,
    blueprint_body: str = "",
) -> str:
    bp_section = (
        "\n[SELECTED BLUEPRINT — instantiate this shape, keep its signature move]\n"
        + blueprint_body
        if blueprint_body else ""
    )
    rules_dir = os.path.join(ANIMATION_DIR, "rules")
    # Order = shared prefix first (prompt cache): fixed frame-agnostic
    # instructions → design contract → blueprint → this frame's block.
    return (
        "You are ONE per-frame worker for the visual design phase of the "
        "aiflow-build-frame-visual contract (inlined here — do not invoke any skill). "
        "Your single job is to design the shot sequence of the frame named in [YOUR "
        "FRAME] at the end and output ONLY the lines to APPEND after that frame's "
        "existing block — never re-type any existing field.\n\n"
        "Your inputs are FULLY inlined below — the design contract, your blueprint, "
        "and your frame block. Do NOT read the SKILL reference files (visual-design.md / "
        "motion-language.md / blueprints-index.md / rules-index.md), do NOT search the "
        "filesystem (no `ls` / `find` / `grep`), and do NOT run any script or gate. If a "
        "motion you cite needs its mechanics, open the ONE file at "
        f"`{rules_dir}/<id>.md` only, then keep going.\n\n"
        "Output, in this exact order, as your FINAL message text (lines start at "
        "column 0; no bullet or bold before `Scene`):\n"
        "done OK <frame number>\n"
        "- blueprint: <blueprint (Adapt|Reproduce)>        (from the assignment line)\n"
        "- focal: <the invented focal element>\n"
        "- roles: <focal = foreground · ... · ... = background wallpaper>\n"
        "- sfx: <soft sfx names, comma-separated>\n"
        "<an 'Adapt:'/'Reproduce:' line only when the blueprint needs instantiation>\n"
        "Scene 1 (0.0–Xs): <layout + motion, inline vocab, from-state-to-reveal>\n"
        "Scene 2 (...): ...  (≥2 Scenes, monotonic non-overlapping windows, coverage "
        "≥90% of duration, first starts at 0.0 — voiceover-paced, never front-loaded)\n\n"
        "Do NOT emit the `- scene`, `- voiceover`, `- duration`, `- transition_in`, "
        "`- status`, `- src`, `- type`, `- persuasion`, or `- beat` lines — the harness "
        "keeps those byte-exact. Do NOT Write or edit any file, do NOT redesign other "
        "frames. Use valid motion/blueprint vocabulary only.\n"
        "Keep thinking short: do NOT draft the full answer or run a line-by-line "
        "negative-list self-check in your thinking — the harness validates the format "
        "and the Scene windows.\n\n"
        "[DESIGN CONTRACT]\n"
        + design
        + bp_section
        + f"\n\n[CONTEXT — Frame {frame_num}, read-only, do not echo it back]\n"
        + block
        + f"\n\n[YOUR FRAME] Frame {frame_num} — start your reply with "
        f"`done OK {frame_num}`.\n"
    )


def _tokens_to_prose(tokens: str) -> str:
    """Turn the YAML-ish frame.md tokens (`colors:`/`typography:`) into readable
    prose — the downstream workers parse prose far better than raw YAML."""
    colors = []          # [(name, value)]
    fonts = {}           # family -> [slot + size]
    section = None
    for raw in tokens.split("\n"):
        s = raw.strip()
        if s == "colors:":
            section = "colors"
            continue
        if s == "typography:":
            section = "typography"
            continue
        if section == "colors":
            m = re.match(r'^([\w-]+):\s*"([^"]+)"', s)
            if m:
                colors.append((m.group(1), m.group(2)))
        elif section == "typography":
            m = re.match(r'^([\w-]+):\s*(.*)$', s)
            if not m:
                continue
            fam = re.search(r'fontFamily:\s*"([^"]+)"', m.group(2))
            size = re.search(r'(?:cqw|px):\s*([\d.]+)', m.group(2))
            if fam:
                unit = "cqw" if "cqw" in m.group(2) else "px"
                sz = f" {size.group(1)}{unit}" if size else ""
                fonts.setdefault(fam.group(1), []).append(f"{m.group(1)}{sz}".strip())
    lines = []
    if colors:
        lines.append("Colors: " + " · ".join(f"{n} `{v}`" for n, v in colors))
    for fam, slots in fonts.items():
        lines.append(f"Type {fam}: " + " / ".join(slots))
    return (
        "**Palette & type (verbatim from frame.md — authoritative, do not restate)**\n"
        + "\n".join(lines)
    )


def _scripted_direction_head(tokens: str) -> str:
    """The deterministic half of `## Video direction`: the palette & type are a
    verbatim restatement of frame.md, so the harness writes them — not the model
    (原则:确定性的事脚本做,创意的事 LLM 做). Rendered as readable prose, not raw
    YAML, because the per-frame workers consume this."""
    body = _tokens_to_prose(tokens) + "\n\n" if tokens else ""
    return VIDEO_DIRECTION_HEADING + "\n\n" + body


def _validate_design_contract(design_text: str) -> str:
    """The model's half of the contract: creative body + `## Blueprint assignment`.

    The `## Video direction` header + palette/type are scripted (see
    `_scripted_direction_head`); the model supplies the creative body + assignment.
    """
    text = design_text.strip()
    if "## Blueprint assignment" not in text:
        raise RuntimeError("design contract missing ## Blueprint assignment")
    return text


def _diagnose_worker_reply(frame_num: str, text: str) -> str | None:
    """Why a worker reply fails its completion gate; None when it passes."""
    if not text or not text.strip():
        return "empty reply"
    if not _SCENES_RE.search(text):
        return "missing `Scene N (a–b s): …` lines"
    missing = [name for name, rx in (("focal", _FOCAL_RE), ("roles", _ROLES_RE))
               if not rx.search(text)]
    if missing:
        return "missing " + " / ".join(f"`- {m}:`" for m in missing) + " line"
    done = _DONE_RE.search(text)
    if not done:
        return f"missing `done OK {frame_num}` marker"
    if done.group(1) != frame_num:
        return f"`done OK {done.group(1)}` does not match frame {frame_num}"
    return None


def _normalize_scene_lines(text: str) -> str:
    """Strip list/bold prefixes and indentation so every Scene line is the bare
    `Scene N (…): …` form, and un-indent the `- focal:`-style field lines."""
    def fix(m: re.Match) -> str:
        prefix, rest = m.group(1), m.group(2)
        if "**" in prefix:
            rest = rest.replace("**", "", 1)
        return rest.rstrip()
    text = _SCENE_LINE_RE.sub(fix, text)
    return _INDENTED_FIELD_RE.sub(r"\1", text)


def _parse_worker_additions(frame_num: str, text: str) -> str | None:
    """Validate a worker reply and return the normalized additions block
    (without the `done OK` marker). None when the reply failed its completion
    gate — `_diagnose_worker_reply` says why."""
    if _diagnose_worker_reply(frame_num, text) is not None:
        return None
    # Strip the `done OK <num>` marker line; the rest is the additions.
    body = _DONE_RE.sub("", text, count=1).strip()
    body = re.sub(r"\A[ \t]*\n", "", body)
    return _normalize_scene_lines(body).strip() or None


def _fmt_seconds(x: float) -> str:
    s = f"{x:.3f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _frame_duration(block: str) -> float | None:
    m = _DURATION_RE.search(block or "")
    if not m:
        return None
    try:
        dur = float(m.group(1))
    except ValueError:
        return None
    return dur if dur > 0 else None


def _normalize_scene_windows(
    text: str, duration: float | None = None
) -> tuple[str, list[str]]:
    """Deterministically fix the Scene-window arithmetic storyboard-gate.mjs
    checks, so a pure timing slip never costs an LLM repair round.

    - first window starts at 0.0;
    - a window overlapping the previous one starts at the previous end (left
      alone when that would invert it — the gate/repair still catches it);
    - with a known duration, the last window is extended to the duration when
      coverage < 90%, or pulled back to it when it overruns by > 0.2s.
    Returns (text, [human-readable fix, …]); text is unchanged when no fix applies.
    """
    matches = list(_WINDOW_RE.finditer(text))
    if not matches:
        return text, []
    wins = [[float(m.group(2)), float(m.group(3))] for m in matches]
    fixes: list[str] = []
    if abs(wins[0][0]) > 1e-9:
        fixes.append(f"Scene 1 start {_fmt_seconds(wins[0][0])}→0.0")
        wins[0][0] = 0.0
    prev_end = wins[0][1]
    for i in range(1, len(wins)):
        a, b = wins[i]
        if a < prev_end - 1e-9 and prev_end < b:
            fixes.append(f"Scene {i + 1} start {_fmt_seconds(a)}→{_fmt_seconds(prev_end)} (overlap)")
            wins[i][0] = prev_end
        prev_end = max(prev_end, wins[i][1])
    if duration:
        last = wins[-1]
        if prev_end < GATE_COVERAGE * duration and last[1] == prev_end:
            fixes.append(f"Scene {len(wins)} end {_fmt_seconds(last[1])}→{_fmt_seconds(duration)} (coverage)")
            last[1] = duration
        elif last[1] > duration + GATE_OVERRUN and last[0] < duration:
            fixes.append(f"Scene {len(wins)} end {_fmt_seconds(last[1])}→{_fmt_seconds(duration)} (overrun)")
            last[1] = duration

    out: list[str] = []
    pos = 0
    rewrote_format = False
    for m, (a, b) in zip(matches, wins):
        changed = (a != float(m.group(2)) or b != float(m.group(3)))
        gate_ok = _GATE_WINDOW_RE.match(m.group(0)) is not None
        if not changed and gate_ok:
            continue
        rewrote_format = rewrote_format or (not changed and not gate_ok)
        out.append(text[pos:m.start()])
        out.append(f"{m.group(1)}{_fmt_seconds(a)}–{_fmt_seconds(b)}s)")
        pos = m.end()
    if not out:
        return text, fixes
    out.append(text[pos:])
    if rewrote_format:
        fixes.append("window format canonicalized")
    return "".join(out), fixes


def _dispatch_note(rc: int, text: str, frame_num: str = "") -> str:
    if rc == 124:
        return "prior attempt hit its wall-clock timeout"
    if rc != 0:
        return f"prior attempt exited non-zero (rc={rc})"
    reason = _diagnose_worker_reply(frame_num, text) if frame_num else None
    tail = (text or "").strip()[-200:]
    return (
        "(prior attempt produced no valid additions"
        + (f" — {reason}" if reason else "")
        + "; last words: " + (tail or "∅") + ")"
    )


def _visual_repair_prompt(gate_msg: str) -> str:
    """Fix prompt for a gate failure: the model edits the merged STORYBOARD.md's
    shot sequences in place (the driver re-runs the gate itself afterward)."""
    return (
        "You are repairing a HyperFrames visual design. The storyboard-gate failed "
        "after the visual merge with:\n"
        f"{(gate_msg or '')[-3000:]}\n"
        "Make the CHEAPEST safe edit(s) to STORYBOARD.md's shot sequences that fix "
        "the reported finding(s). Do NOT change narrative fields (scene/voiceover/"
        "duration/status/src/transition_in/type/beat). Stop after the edit — the "
        "pipeline re-runs the gate itself."
    )


def _legacy_build_frame_visual(project_dir: str, logger: logging.Logger) -> str:
    """Original single-session flow (rollback when HTMLVIDEO_FRAME_VISUAL_DRIVER=0)."""
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    prompt = (
        "/aiflow-build-frame-visual\n\n"
        "Work in this HyperFrames project directory. hyperframes.json, frame.md, and an "
        "outline-stage STORYBOARD.md already exist — do not re-init, do not invent a "
        "storyboard, do not write HTML, do not run audio, assemble index, or render.\n\n"
        "Follow the aiflow-build-frame-visual skill: enrich STORYBOARD.md with time-coded "
        "shot sequences and a ## Video direction block (skip sketch; autonomous). Do not "
        "mark frames animated; do not write compositions/frames/*.html.\n\n"
        "flow is automation → autonomous: proceed without waiting for sketch confirmation. "
        "Stop when the skill gate passes (shot sequences + ## Video direction).\n"
    )
    rc = run_claude(project_dir, prompt, logger, skill="visual_legacy")
    if rc != 0:
        raise RuntimeError(f"claude build-frame-visual failed rc:{rc}")
    return storyboard_path


def _fragment_path(visual_dir: str, frame_num: str) -> str:
    return os.path.join(visual_dir, f"F{frame_num}.md")


def _write_fragment(visual_dir: str, frame_num: str, addition: str) -> str:
    os.makedirs(visual_dir, exist_ok=True)
    path = _fragment_path(visual_dir, frame_num)
    with open(path, "w", encoding="utf-8") as f:
        f.write(addition.rstrip() + "\n")
    return path


def _read_fragment(visual_dir: str, frame_num: str) -> str | None:
    try:
        with open(_fragment_path(visual_dir, frame_num), encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return text or None


def _run_frame_workers(
    project_dir: str,
    blocks: dict[str, str],
    order: list[str],
    design: str,
    visual_dir: str,
    max_workers: int,
    logger: logging.Logger,
) -> dict[str, str]:
    """Fan out one worker per frame; a failed frame is re-dispatched the moment
    it fails (no whole-round barrier), up to MAX_RETRY_ROUNDS extra attempts.
    Each accepted addition is window-normalized and written to F{n}.md."""
    log = logger.info if logger else (lambda *a, **k: None)
    # Input reduction (mirrors frame-html P4): give each worker only the shared
    # Video direction + ITS OWN blueprint assignment line (drop the other frames'
    # lines), and inline its blueprint body so it does not Read the blueprint file.
    direction, assignment = _split_design(design)

    def run_one(fn: str, attempt: int, note: str | None):
        bp = _frame_blueprint(assignment, fn)
        bp_body = _blueprint_body(bp, logger)
        line = _frame_assignment_line(assignment, fn) or f"F{fn} = {COMPOSE_BLUEPRINT}"
        trimmed = f"{direction}\n\n## Blueprint assignment\n{line}"
        prompt = _build_worker_prompt(fn, blocks[fn], trimmed, bp_body)
        if note:
            prompt += f"\n[RE-DISPATCH] {note} — output the additions now."
        t0 = time.monotonic()
        rc, reply = run_claude_result(
            project_dir, prompt, logger, skill="visual", label=f"F{fn}")
        wall = time.monotonic() - t0
        addition = _parse_worker_additions(fn, reply)
        log("visual worker F%s attempt=%s wall=%.1fs rc=%s out=%d chars ok=%s",
            fn, attempt, wall, rc, len(reply or ""), addition is not None)
        return rc, reply, addition

    additions: dict[str, str] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        running = {pool.submit(run_one, fn, 0, None): (fn, 0) for fn in order}
        while running:
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for fut in done:
                fn, attempt = running.pop(fut)
                rc, reply, addition = fut.result()
                if addition is not None:
                    addition, fixes = _normalize_scene_windows(
                        addition, _frame_duration(blocks[fn]))
                    if fixes:
                        log("visual F%s scene windows fixed: %s", fn, "; ".join(fixes))
                    additions[fn] = addition
                    _write_fragment(visual_dir, fn, addition)
                    log("worker ok F%s (attempt %s)", fn, attempt)
                    continue
                note = _dispatch_note(rc, reply, fn)
                log("worker fail F%s rc=%s (attempt %s): %s", fn, rc, attempt, note)
                if attempt < MAX_RETRY_ROUNDS:
                    running[pool.submit(run_one, fn, attempt + 1, note)] = (fn, attempt + 1)
                else:
                    failed.append(fn)
    if failed:
        raise RuntimeError(
            "frames still missing visual design after "
            f"{1 + MAX_RETRY_ROUNDS} attempts: {sorted(failed, key=int)}")
    return additions


def _build_frame_visual_driver(project_dir: str, logger: logging.Logger) -> str:
    log = logger.info if logger else (lambda *a, **k: None)
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    with open(storyboard_path, "r", encoding="utf-8") as f:
        text = f.read()
    preamble, blocks, order = _split_storyboard(text)
    if not order:
        raise RuntimeError("STORYBOARD.md has no ## Frame N blocks")

    # ── Phase A: global design contract (one session + assignment gate) ───
    log("visual driver: design-contract phase (%d frames)", len(order))
    frame_md = ""
    try:
        with open(os.path.join(project_dir, FRAME_FILENAME), "r", encoding="utf-8") as f:
            frame_md = f.read()
    except OSError:
        pass
    tokens = _frame_md_tokens(frame_md)
    catalog = _blueprint_catalog()
    banned_tokens = _type_token_names(tokens)
    if not catalog:
        raise RuntimeError(
            f"blueprint catalog empty under {BLUEPRINTS_DIR} — cannot validate assignments"
        )
    design_prompt = _build_design_prompt(
        preamble, order, tokens, catalog, _frame_summaries(blocks, order))
    design_thinking = str(_env_positive_int(
        VISUAL_DESIGN_THINKING_ENV, DEFAULT_DESIGN_THINKING_TOKENS, logger))
    t0 = time.monotonic()
    drc, design_text = run_claude_result(
        project_dir, design_prompt, logger, skill="visual", label="design",
        thinking_default=design_thinking,
    )
    log("visual design wall=%.1fs rc=%s out=%d chars",
        time.monotonic() - t0, drc, len(design_text or ""))
    if drc != 0:
        raise RuntimeError(f"claude visual design-contract failed rc:{drc}")
    design = _scripted_direction_head(tokens) + "\n" + _validate_design_contract(design_text)

    # Catalog gate: every F<n> must name a real shot shape (or compose). Type
    # tokens (display/headline/…) were a common miss that left Phase B workers
    # with an empty inline and marathon thinking — rewrite assignment only.
    for assign_round in range(1 + MAX_DESIGN_ASSIGN_REPAIRS):
        _direction, assignment = _split_design(design)
        misses = _assignment_catalog_misses(assignment, order, catalog)
        if not misses:
            break
        bad = ", ".join(f"F{fn}={bp or '∅'}" for fn, bp in misses)
        warn = logger.warning if logger else logging.getLogger(__name__).warning
        warn(
            "visual design assignment outside catalog (round %s/%s): %s",
            assign_round + 1,
            1 + MAX_DESIGN_ASSIGN_REPAIRS,
            bad,
        )
        if assign_round >= MAX_DESIGN_ASSIGN_REPAIRS:
            raise RuntimeError(
                "design blueprint assignment still outside catalog after "
                f"{1 + MAX_DESIGN_ASSIGN_REPAIRS} attempt(s): {bad}"
            )
        rewrite_prompt = _build_assignment_rewrite_prompt(
            design, misses, order, catalog, banned_tokens
        )
        rrc, rewrite_text = run_claude_result(
            project_dir, rewrite_prompt, logger, skill="visual", label="design-bp-fix",
            thinking_default=design_thinking,
        )
        if rrc != 0:
            raise RuntimeError(
                f"claude visual design assignment rewrite failed rc:{rrc} (bad: {bad})"
            )
        design = _splice_assignment(design, rewrite_text)
        if "## Blueprint assignment" not in design:
            raise RuntimeError(
                "design assignment rewrite lost ## Blueprint assignment "
                f"(bad: {bad})"
            )

    visual_dir = os.path.join(project_dir, VISUAL_DIR)
    os.makedirs(visual_dir, exist_ok=True)
    with open(os.path.join(project_dir, DESIGN_FILE), "w", encoding="utf-8") as f:
        f.write(design + "\n")
    log("visual driver: design contract ready (%d chars)", len(design))

    # ── Phase B: parallel per-frame workers ───────────────────────────────
    max_workers = _env_positive_int(
        VISUAL_CONCURRENCY_ENV, DEFAULT_MAX_WORKERS, logger)
    # Fragments from an earlier run were built against a different design
    # contract — never let the merge pick them up.
    for stale in glob.glob(os.path.join(visual_dir, "F*.md")):
        try:
            os.remove(stale)
        except OSError:
            pass
    additions = _run_frame_workers(
        project_dir, blocks, order, design, visual_dir, max_workers, logger)

    # ── Merge (deterministic; narrative fields never re-typed) ────────────
    parts = [preamble, "", design, ""]
    for fn in order:
        parts.append(blocks[fn])
        parts.append("")
        parts.append(_read_fragment(visual_dir, fn) or additions[fn])
        parts.append("")
    assembled = "\n".join(parts).strip() + "\n"
    with open(storyboard_path, "w", encoding="utf-8") as f:
        f.write(assembled)
    log("visual driver: merged STORYBOARD (%d chars)", len(assembled))

    # ── Gate: storyboard-gate.mjs (deterministic, ~1s) + configurable repair ──
    max_repair = _env_positive_int(
        VISUAL_REPAIR_ROUNDS_ENV, DEFAULT_REPAIR_ROUNDS, logger)

    def run_gate():
        return subprocess.run(
            ["node", "/app/scripts/storyboard-gate.mjs", "--videodir", project_dir],
            capture_output=True, text=True, timeout=120,
        )

    gate_msg = ""
    for round_no in range(1 + max_repair):
        gate = run_gate()
        if gate.returncode == 0:
            return storyboard_path
        gate_msg = (gate.stdout or "") + (gate.stderr or "")
        if round_no < max_repair:
            log("visual driver: gate FAILED, repair round %d/%d", round_no + 1, max_repair)
            run_claude(project_dir, _visual_repair_prompt(gate_msg), logger, skill="repair")
    raise RuntimeError(
        f"storyboard-gate failed after {max_repair} repair round(s):\n{gate_msg}")


def _validate_frame_visual_outputs(project_dir: str) -> str:
    """Post-check the final STORYBOARD carries the video-wide direction block."""
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(storyboard_path) or os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md missing/empty after visual: {storyboard_path}")
    with open(storyboard_path, "r", encoding="utf-8") as f:
        content = f.read()
    if VIDEO_DIRECTION_HEADING not in content:
        raise ValueError(
            f"STORYBOARD.md missing {VIDEO_DIRECTION_HEADING!r} after visual: {storyboard_path}"
        )
    return storyboard_path


def build_frame_visual(
    project_dir: str,
    logger: logging.Logger,
) -> str:
    """Add time-coded shot sequences + `## Video direction` to STORYBOARD.md.

    Two paths (env-gated):
      - ``HTMLVIDEO_FRAME_VISUAL_DRIVER=0`` → legacy single-session flow.
      - otherwise (default) → deterministic fan-out: one design-contract
        session + N parallel per-frame workers + byte-exact merge + gate.

    ``project_dir`` must have ``hyperframes.json``, ``frame.md``, and an
    outline-stage ``STORYBOARD.md``. Raises on missing prerequisites, CLI
    failure, or gate failure. Returns the path to ``STORYBOARD.md``.
    """
    hyperframes_path = os.path.join(project_dir, HYPERFRAMES_JSON)
    frame_path = os.path.join(project_dir, FRAME_FILENAME)
    storyboard_path = os.path.join(project_dir, STORYBOARD_FILENAME)
    if not os.path.isfile(hyperframes_path):
        raise FileNotFoundError(f"hyperframes.json missing: {hyperframes_path}")
    if not os.path.isfile(frame_path):
        raise FileNotFoundError(f"frame.md missing: {frame_path}")
    if not os.path.isfile(storyboard_path):
        raise FileNotFoundError(f"STORYBOARD.md missing: {storyboard_path}")
    if os.path.getsize(storyboard_path) == 0:
        raise ValueError(f"STORYBOARD.md is empty: {storyboard_path}")
    with open(storyboard_path, "r", encoding="utf-8") as f:
        content = f.read()
    if VIDEO_DIRECTION_HEADING in content:
        raise ValueError(
            f"STORYBOARD.md already has {VIDEO_DIRECTION_HEADING!r}: {storyboard_path} "
            "(design already consumed — do not redesign)"
        )

    if _env_on(VISUAL_DRIVER_ENV):
        _build_frame_visual_driver(project_dir, logger)
    else:
        _legacy_build_frame_visual(project_dir, logger)
    return _validate_frame_visual_outputs(project_dir)