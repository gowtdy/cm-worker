# -*- coding: utf-8 -*-
"""Frame visual design helpers for htmlvideo worker.

Deterministic fan-out (mirrors frame_html.py): a single "design contract"
session produces the video-wide `## Video direction` + per-frame blueprint
assignment, then N parallel workers each append ONE frame's shot-sequence lines
(blueprint/focal/roles/sfx/rules/Adapt/Scene) to its OWN fragment file. Python re-
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
VISUAL_LEDGER_ENV = "HTMLVIDEO_FRAME_VISUAL_LEDGER"
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
RULES_DIR = os.path.join(ANIMATION_DIR, "rules")

_SPLIT_RE = re.compile(r"(?=^## Frame\s+\d+)", re.M)
_FRAME_HEAD_RE = re.compile(r"^## Frame\s+(\d+)", re.M)
# Workers sometimes emit `- Scene 1`, `* Scene 1`, `**Scene 1 (…):**` or an
# indented copy of the prompt example; accept them, then normalize to the bare
# `Scene N (…)` form storyboard-gate.mjs parses.
_SCENES_RE = re.compile(r"^\s*(?:[-*]+\s+)?(?:\*\*\s*)?Scene\s+\d+", re.M)
_SCENE_LINE_RE = re.compile(
    r"^[ \t]*((?:[-*]+[ \t]+)?(?:\*\*[ \t]*)?)(Scene[ \t]+\d+.*)$", re.M)
_INDENTED_FIELD_RE = re.compile(
    r"^[ \t]+(-[ \t]*(?:blueprint|focal|roles|sfx|rules):|(?:Adapt|Reproduce):)", re.M)
_FOCAL_RE = re.compile(r"^\s*-\s*focal:", re.M)
_ROLES_RE = re.compile(r"^\s*-\s*roles:", re.M)
_RULES_LINE_RE = re.compile(r"^[ \t]*-[ \t]*rules:[ \t]*(.*)$", re.M)
_RULE_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
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
_MODE_RE = re.compile(r"\((adapt|reproduce)\)", re.I)
# `- blueprint: <id> …` field line (bullet/bold tolerant); group 1 is the id,
# None on a mode-only line such as `- blueprint: Adapt`.
_BLUEPRINT_FIELD_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]*)?(?:\*\*)?blueprint(?:\*\*)?[ \t]*:(?:\*\*)?[ \t]*"
    r"(?:(?!(?:adapt|reproduce)\b)([\w-]+))?", re.I)
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


def _rule_catalog() -> frozenset[str]:
    """Motion recipe ids on disk under `hyperframes-animation/rules/*.md` — the
    same stems storyboard-gate.mjs and frame-packets `citedRules` accept."""
    ids: set[str] = set()
    try:
        for name in os.listdir(RULES_DIR):
            if name.endswith(".md") and not name.startswith("."):
                ids.add(name[:-3])
    except OSError:
        pass
    return frozenset(ids)


def _blueprint_rule_ids(blueprint_id: str, catalog: frozenset[str] | None = None) -> list[str]:
    """Rule ids named after `→` in the blueprint's `rule mapping` section, in
    document order, filtered to on-disk rules. Reads the FULL file: the inlined
    body is truncated and `rule mapping` is the first section dropped."""
    if not blueprint_id or blueprint_id.lower() == COMPOSE_BLUEPRINT:
        return []
    try:
        with open(os.path.join(BLUEPRINTS_DIR, f"{blueprint_id}.md"), encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return []
    cat = catalog if catalog is not None else _rule_catalog()
    out: list[str] = []
    for section in _BP_SECTION_RE.split(body):
        if "rule mapping" not in section.split("\n", 1)[0].lower():
            continue
        for line in section.split("\n"):
            if "→" not in line:
                continue
            for tok in _RULE_TOKEN_RE.findall(line.split("→", 1)[1]):
                if tok in cat and tok not in out:
                    out.append(tok)
    return out


def _rules_field_ids(text: str) -> list[str] | None:
    """Ids listed on the `- rules:` line (backticks tolerated); None when absent."""
    m = _RULES_LINE_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace("`", " ")
    return [t for t in re.split(r"[,\s]+", raw) if t]


def _normalize_rules_line(text: str, catalog: frozenset[str] | None = None) -> tuple[str, list[str]]:
    """Rewrite `- rules:` to the canonical `- rules: a, b` with only on-disk ids
    (deduped). Returns (text, dropped unknown ids)."""
    ids = _rules_field_ids(text)
    if ids is None:
        return text, []
    cat = catalog if catalog is not None else _rule_catalog()
    known = list(dict.fromkeys(i for i in ids if i in cat))
    dropped = [i for i in ids if i not in cat]
    if not known:
        return text, dropped
    line = "- rules: " + ", ".join(known)
    return _RULES_LINE_RE.sub(lambda _m: line, text, count=1), dropped


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


def _color_token_names(tokens: str) -> list[str]:
    """Color slot names ONLY (not typography) from inlined frame.md tokens — the
    palette a `## Layout ledger` color cell must name (not a hex or made-up hue)."""
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
        if section == "colors":
            m = re.match(r"^([\w-]+)\s*:", s)
            if m:
                names.append(m.group(1))
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


def _frame_blueprint_mode(assignment: str, frame_num: str) -> str:
    """`Adapt` / `Reproduce` from the frame's assignment line, else empty."""
    m = _MODE_RE.search(_frame_assignment_line(assignment, frame_num))
    return m.group(1).capitalize() if m else ""


def _assigned_blueprint_field(blueprint_id: str, mode: str = "") -> str:
    """The exact `- blueprint:` value a worker must emit for its assignment."""
    bp = blueprint_id or COMPOSE_BLUEPRINT
    if bp.lower() == COMPOSE_BLUEPRINT or not mode:
        return bp
    return f"{bp} ({mode})"


def _strip_conflicting_blueprints(
    block: str, assigned_id: str, mode: str | None = None,
) -> tuple[str, list[str]]:
    """Prompt-only copy of a frame block with every `blueprint:` field line that
    disagrees with the assignment removed. Returns (block, dropped ids).

    A line naming the assigned id survives once (its `(Adapt|Reproduce)` is
    rewritten to ``mode`` when given); other ids, mode-only lines and repeats
    are dropped so the worker never sees two competing shapes."""
    want = (assigned_id or COMPOSE_BLUEPRINT).lower()
    kept_one = False
    dropped: list[str] = []
    out: list[str] = []
    for line in (block or "").split("\n"):
        m = _BLUEPRINT_FIELD_RE.match(line)
        if not m:
            out.append(line)
            continue
        bp = m.group(1) or ""
        if bp.lower() != want or kept_one:
            dropped.append(bp or "∅")
            continue
        kept_one = True
        if mode:
            line = _MODE_RE.sub(f"({mode})", line, count=1)
        out.append(line)
    return "\n".join(out), dropped


_FIELD_ANCHOR_RE = re.compile(r"^[ \t]*(?:-[ \t]*focal:|Scene[ \t]+\d+)")


def _canonicalize_frame_blueprint(frame_text: str, canonical: str) -> tuple[str, list[str]]:
    """Leave exactly one `blueprint:` field line in a merged frame section.

    The LAST such line (the worker's, since additions follow the outline) is
    rewritten to ``canonical``; every other one is removed. With none present,
    ``canonical`` goes before the first `- focal:` / `Scene N` line, else at the
    end. Returns (text, ids of removed or rewritten non-canonical lines)."""
    lines = frame_text.split("\n")
    hits = [i for i, line in enumerate(lines) if _BLUEPRINT_FIELD_RE.match(line)]
    if not hits:
        at = next((i for i, line in enumerate(lines) if _FIELD_ANCHOR_RE.match(line)), None)
        if at is None:
            at = len(lines)
            while at and not lines[at - 1].strip():
                at -= 1
        lines.insert(at, canonical)
        return "\n".join(lines), ["∅"]
    keep = hits[-1]
    dropped = [_BLUEPRINT_FIELD_RE.match(lines[i]).group(1) or "∅"
               for i in hits if i != keep or lines[i].strip() != canonical]
    lines[keep] = canonical
    drop = set(hits[:-1])
    return "\n".join(line for i, line in enumerate(lines) if i not in drop), dropped


def _enforce_storyboard_blueprints(text: str, assignment: str) -> tuple[str, dict[str, list[str]]]:
    """Deterministic post-merge / post-repair pass: every `## Frame N` section
    ends with exactly one `- blueprint: <assignment field>` line. Bytes outside
    the blueprint lines are untouched. Returns (text, {frame: changed ids})."""
    parts = _SPLIT_RE.split(text)
    fixes: dict[str, list[str]] = {}
    for idx in range(1, len(parts)):
        m = _FRAME_HEAD_RE.match(parts[idx])
        if not m:
            continue
        fn = m.group(1)
        bp = _frame_blueprint(assignment, fn) or COMPOSE_BLUEPRINT
        canonical = "- blueprint: " + _assigned_blueprint_field(
            bp, _frame_blueprint_mode(assignment, fn))
        parts[idx], changed = _canonicalize_frame_blueprint(parts[idx], canonical)
        if changed:
            fixes[fn] = changed
    return "".join(parts), fixes


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
    candidate_rules: list[str] | None = None,
    rule_catalog: frozenset[str] | None = None,
    assigned_blueprint: str = "",
    require_ledger: bool = False,
    color_catalog: frozenset[str] | None = None,
) -> str:
    bp_section = (
        "\n[SELECTED BLUEPRINT — instantiate this shape, keep its signature move]\n"
        + blueprint_body
        if blueprint_body else ""
    )
    if candidate_rules:
        bp_section += (
            "\n\nCandidate rule ids (from this blueprint's rule mapping — pick the ones "
            "your Scenes actually use): " + ", ".join(candidate_rules))
    rules_dir = RULES_DIR
    rule_ids = sorted(rule_catalog if rule_catalog is not None else _rule_catalog())
    ledger_block = ""
    if require_ledger:
        color_list = ", ".join(sorted(color_catalog)) if color_catalog else ""
        ledger_block = (
            "\n\nLAYOUT+MOTION LEDGER (REQUIRED): after your Scene lines, append TWO "
            "concrete tables so the HTML worker transcribes exact values instead of "
            "re-deriving them. Empty tables are rejected.\n\n"
            "## Layout ledger\n\n"
            "| element | role | place | size | color | order |\n"
            "|---------|------|-------|------|-------|-------|\n"
            "| <element> | <role> | <place zone, or x%/y%> | <cqw, or —> | <palette role> | <int> |\n\n"
            "## Motion ledger\n\n"
            "| scene | element | from → to | ease | dur |\n"
            "|-------|---------|-----------|------|-----|\n"
            "| scene<N> | <element> | <prop 0 → 1 …> | <ease> | <Xs> |\n\n"
            "Constraints: `color` MUST be a palette role from the design contract — never a "
            "hex or a made-up hue; `ease` ONLY `power1-4`/`sine`/`quad`/`cubic`/`quart`/"
            "`quint`/`expo`/`circ`/`back`/`elastic`/`bounce` `.in`/`.out`/`.inOut`; `place`/"
            "`size` are values or named zones — NEVER CSS selectors or GSAP calls (the HTML "
            "worker writes those). One data row per element / per scene motion."
            + (f"\n\nValid color roles: {color_list}." if color_list else "")
        )
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
        "- blueprint: <id (Adapt|Reproduce)>   (copied verbatim from your assignment line)\n"
        "- focal: <the invented focal element>\n"
        "- roles: <focal = foreground · ... · ... = background wallpaper>\n"
        "- sfx: <soft sfx names, comma-separated>\n"
        "- rules: <comma-separated rule ids from hyperframes-animation/rules>\n"
        "<an 'Adapt:'/'Reproduce:' line only when the blueprint needs instantiation>\n"
        "Scene 1 (0.0–Xs): <layout + motion, inline vocab + `rule-id`, from-state-to-reveal>\n"
        "Scene 2 (...): ...  (≥2 Scenes, monotonic non-overlapping windows, coverage "
        "≥90% of duration, first starts at 0.0 — voiceover-paced, never front-loaded)\n\n"
        "BLUEPRINT AUTHORITY: your `F<n> = <id> (Adapt|Reproduce)` line under `## Blueprint "
        "assignment` is FINAL — copy its id and mode into `- blueprint:`. Any other "
        "blueprint id in the frame CONTEXT is a superseded outline leftover: ignore it, and do "
        "NOT compare, weigh, or arbitrate blueprint ids in your thinking. Spend the "
        "thinking on Adapt/Reproduce and the Scenes.\n\n"
        "SHAPE vs MOTION-GRAMMAR TIEBREAK: the design contract's motion grammar (e.g. "
        "\"each new item pushes the previous off-screen\") overrides your blueprint's "
        "default shape (e.g. \"assemble and hold\"). When they clash, follow the motion "
        "grammar and state the adaptation in ONE Adapt sentence — decide once here, do "
        "NOT re-read or re-derive the tension in your thinking.\n\n"
        "`- rules:` is REQUIRED (a reply without it is rejected and re-dispatched): ≥1, "
        "ideally 2–4 ids, plain comma-separated, each an exact rule id from the list "
        "below. Pick the ids for the motions your Scenes actually use — from the "
        "SELECTED BLUEPRINT's rule mapping / candidate list when present; for `compose`, "
        "from the common entrance/reveal rules. Every Scene that names a concrete motion "
        "cites its rule id in backticks right beside it (e.g. \"… types on with a trailing "
        "caret `discrete-text-sequence` + `context-sensitive-cursor` …\") — a motion named "
        "only in plain words (\"types on\", \"pop-stagger\", \"hard-cut\") is not a "
        "citation. Within-frame seam cuts (zoom-through / cut-the-curve / waterfall) are "
        "NOT rule ids: name them in the Scene text only, never in `- rules:`.\n"
        "Valid rule ids: " + ", ".join(rule_ids) + "\n"
        + ledger_block
        + "\n\nDo NOT emit the `- scene`, `- voiceover`, `- duration`, `- transition_in`, "
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
        + (f"Your blueprint line is exactly: `- blueprint: {assigned_blueprint}`.\n"
           if assigned_blueprint else "")
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


def _diagnose_worker_reply(
    frame_num: str, text: str, rule_catalog: frozenset[str] | None = None,
    require_ledger: bool = False, color_catalog: frozenset[str] | None = None,
) -> str | None:
    """Why a worker reply fails its completion gate; None when it passes.

    With ``require_ledger`` (schema-sinking scheme-1), the reply must also carry
    a non-empty ``## Layout ledger`` + ``## Motion ledger`` whose color/ease
    cells name known palette roles / motion-grammar easings."""
    if not text or not text.strip():
        return "empty reply"
    if not _SCENES_RE.search(text):
        return "missing `Scene N (a–b s): …` lines"
    missing = [name for name, rx in (("focal", _FOCAL_RE), ("roles", _ROLES_RE))
               if not rx.search(text)]
    if missing:
        return "missing " + " / ".join(f"`- {m}:`" for m in missing) + " line"
    rules = _rules_field_ids(text)
    if not rules:
        return "missing non-empty `- rules: <rule ids>` line"
    cat = rule_catalog if rule_catalog is not None else _rule_catalog()
    if not any(r in cat for r in rules):
        return ("`- rules:` names no known rule id (got: " + ", ".join(rules)
                + ") — use exact hyperframes-animation/rules stems")
    if require_ledger:
        err = _diagnose_ledger(text, color_catalog)
        if err:
            return err
    done = _DONE_RE.search(text)
    if not done:
        return f"missing `done OK {frame_num}` marker"
    if done.group(1) != frame_num:
        return f"`done OK {done.group(1)}` does not match frame {frame_num}"
    return None


# Motion-grammar ease whitelist — same shape storyboard-gate.mjs EASE_RE accepts,
# so a ledger `ease` cell stays parseable end-to-end.
_EASE_RE = re.compile(
    r"^(power[1-4]|sine|quad|cubic|quart|quint|expo|circ|back|elastic|bounce)"
    r"\.(in|out|inOut)$"
)
_LAYOUT_LEDGER_RE = re.compile(r"^##[ \t]+Layout ledger\b", re.M)
_MOTION_LEDGER_RE = re.compile(r"^##[ \t]+Motion ledger\b", re.M)


def _ledger_section(text: str, name: str) -> str:
    """Body between `## {name}` and the next `## ` heading (or end of text)."""
    m = re.search(rf"^##[ \t]+{re.escape(name)}\b[^\n]*\n([\s\S]*?)(?=^##[ \t]+|\Z)",
                  text, re.M)
    return m.group(1) if m else ""


def _cell_primary_token(cell: str) -> str:
    """First slug token of a table cell (``cream (#F5F5F0)`` → ``cream``)."""
    m = re.search(r"[A-Za-z][\w-]*", cell or "")
    return m.group(0) if m else ""


def _ledger_data_rows(text: str) -> list[list[str]]:
    """Data rows (cells) of a markdown table block; [] when none. Skips the
    header row (first cell `element`/`scene`) and the `|---|` separator row."""
    rows: list[list[str]] = []
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [c.strip() for c in line[1:-1].split("|")]
        if not cells or all(not c for c in cells):
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):  # separator row
            continue
        if cells[0].lower() in ("element", "scene"):  # header row
            continue
        rows.append(cells)
    return rows


def _diagnose_ledger(text: str, color_catalog: frozenset[str] | None = None) -> str | None:
    """Why the `## Layout ledger` / `## Motion ledger` is missing/bad; None OK."""
    if not _LAYOUT_LEDGER_RE.search(text):
        return "missing `## Layout ledger` table"
    if not _MOTION_LEDGER_RE.search(text):
        return "missing `## Motion ledger` table"
    layout_rows = _ledger_data_rows(_ledger_section(text, "Layout ledger"))
    motion_rows = _ledger_data_rows(_ledger_section(text, "Motion ledger"))
    if not layout_rows:
        return "`## Layout ledger` has no data row"
    if not motion_rows:
        return "`## Motion ledger` has no data row"
    if color_catalog is not None:
        for r in layout_rows:
            if len(r) < 5:
                continue
            color = _cell_primary_token(r[4])  # element/role/place/size/color/order
            if color and color not in color_catalog:
                return f"`## Layout ledger` color not a palette role: {color}"
    for r in motion_rows:
        if len(r) < 4:
            continue
        ease = r[3]  # scene/element/from→to/ease/dur
        if ease and ease not in ("—", "-") and not _EASE_RE.match(ease):
            return f"`## Motion ledger` ease not a motion-grammar ease: {ease}"
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


def _parse_worker_additions(
    frame_num: str, text: str, rule_catalog: frozenset[str] | None = None,
    require_ledger: bool = False, color_catalog: frozenset[str] | None = None,
) -> str | None:
    """Validate a worker reply and return the normalized additions block
    (without the `done OK` marker). None when the reply failed its completion
    gate — `_diagnose_worker_reply` says why. Unknown ids on `- rules:` are
    dropped (at least one known id survives, or the reply was rejected)."""
    cat = rule_catalog if rule_catalog is not None else _rule_catalog()
    if _diagnose_worker_reply(frame_num, text, cat, require_ledger, color_catalog) is not None:
        return None
    # Strip the `done OK <num>` marker line; the rest is the additions.
    body = _DONE_RE.sub("", text, count=1).strip()
    body = re.sub(r"\A[ \t]*\n", "", body)
    body = _normalize_scene_lines(body)
    body, dropped = _normalize_rules_line(body, cat)
    if dropped:
        logging.getLogger(__name__).info(
            "visual F%s dropped unknown rule ids: %s", frame_num, ", ".join(dropped))
    return body.strip() or None


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


def _dispatch_note(
    rc: int, text: str, frame_num: str = "",
    require_ledger: bool = False, color_catalog: frozenset[str] | None = None,
) -> str:
    if rc == 124:
        return "prior attempt hit its wall-clock timeout"
    if rc != 0:
        return f"prior attempt exited non-zero (rc={rc})"
    reason = _diagnose_worker_reply(
        frame_num, text, require_ledger=require_ledger,
        color_catalog=color_catalog) if frame_num else None
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
        "duration/status/src/transition_in/type/beat). A `- rules:` finding is fixed "
        f"by adding/correcting that frame's `- rules: id1, id2` line with exact stems of "
        f"`{RULES_DIR}/*.md` matching its Scene motions. Stop after the edit — the "
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
    require_ledger: bool = False,
    color_catalog: frozenset[str] | None = None,
) -> dict[str, str]:
    """Fan out one worker per frame; a failed frame is re-dispatched the moment
    it fails (no whole-round barrier), up to MAX_RETRY_ROUNDS extra attempts.
    Each accepted addition is window-normalized and written to F{n}.md."""
    log = logger.info if logger else (lambda *a, **k: None)
    # Input reduction (mirrors frame-html P4): give each worker only the shared
    # Video direction + ITS OWN blueprint assignment line (drop the other frames'
    # lines), and inline its blueprint body so it does not Read the blueprint file.
    direction, assignment = _split_design(design)
    rule_catalog = _rule_catalog()
    if not rule_catalog:
        raise RuntimeError(
            f"rule catalog empty under {RULES_DIR} — cannot validate `- rules:`")

    # Prompt-only CONTEXT: outline `- blueprint:` candidates that disagree with
    # the assignment are removed; `blocks` itself stays byte-exact for the merge.
    contexts: dict[str, str] = {}
    for fn in order:
        bp = _frame_blueprint(assignment, fn) or COMPOSE_BLUEPRINT
        contexts[fn], dropped = _strip_conflicting_blueprints(
            blocks[fn], bp, _frame_blueprint_mode(assignment, fn))
        if dropped:
            log("visual F%s CONTEXT: dropped outline blueprint(s) %s (assignment=%s)",
                fn, ", ".join(dropped), bp)

    def run_one(fn: str, attempt: int, note: str | None):
        bp = _frame_blueprint(assignment, fn)
        bp_body = _blueprint_body(bp, logger)
        line = _frame_assignment_line(assignment, fn) or f"F{fn} = {COMPOSE_BLUEPRINT}"
        trimmed = f"{direction}\n\n## Blueprint assignment\n{line}"
        prompt = _build_worker_prompt(
            fn, contexts[fn], trimmed, bp_body,
            _blueprint_rule_ids(bp, rule_catalog), rule_catalog,
            _assigned_blueprint_field(bp, _frame_blueprint_mode(assignment, fn)),
            require_ledger=require_ledger, color_catalog=color_catalog)
        if note:
            prompt += f"\n[RE-DISPATCH] {note} — output the additions now."
        t0 = time.monotonic()
        rc, reply = run_claude_result(
            project_dir, prompt, logger, skill="visual", label=f"F{fn}")
        wall = time.monotonic() - t0
        addition = _parse_worker_additions(
            fn, reply, rule_catalog, require_ledger, color_catalog)
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
                note = _dispatch_note(rc, reply, fn, require_ledger, color_catalog)
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
    ledger_on = _env_on(VISUAL_LEDGER_ENV, default=False)
    color_catalog = frozenset(_color_token_names(tokens)) if ledger_on else None
    additions = _run_frame_workers(
        project_dir, blocks, order, design, visual_dir, max_workers, logger,
        require_ledger=ledger_on, color_catalog=color_catalog)

    # ── Merge (deterministic; narrative fields never re-typed) ────────────
    parts = [preamble, "", design, ""]
    for fn in order:
        parts.append(blocks[fn])
        parts.append("")
        parts.append(_read_fragment(visual_dir, fn) or additions[fn])
        parts.append("")
    assembled = "\n".join(parts).strip() + "\n"
    # One authoritative `- blueprint:` per frame: superseded outline lines and
    # off-assignment worker lines must not reach readers that take the first.
    _direction, assignment = _split_design(design)
    assembled, bp_fixes = _enforce_storyboard_blueprints(assembled, assignment)
    for fn, changed in bp_fixes.items():
        log("visual F%s merge: blueprint normalized to assignment (dropped %s)",
            fn, ", ".join(changed))
    with open(storyboard_path, "w", encoding="utf-8") as f:
        f.write(assembled)
    log("visual driver: merged STORYBOARD (%d chars)", len(assembled))

    def reenforce_blueprints() -> None:
        try:
            with open(storyboard_path, "r", encoding="utf-8") as f:
                current = f.read()
        except OSError:
            return
        fixed, fixes = _enforce_storyboard_blueprints(current, assignment)
        if not fixes:
            return
        warn = logger.warning if logger else logging.getLogger(__name__).warning
        warn("visual repair touched blueprint lines; re-normalized %s",
             "; ".join(f"F{fn}: {', '.join(c)}" for fn, c in fixes.items()))
        with open(storyboard_path, "w", encoding="utf-8") as f:
            f.write(fixed)

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
            reenforce_blueprints()
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