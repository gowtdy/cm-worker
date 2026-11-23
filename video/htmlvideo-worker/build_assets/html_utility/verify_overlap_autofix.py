# -*- coding: utf-8 -*-
"""Deterministic fix for ``designed_overlay`` content_overlap findings.

``verify-report.mjs`` already triages every ``content_overlap`` error and, on
GATE FAIL, prints one ``VERIFY_FINDINGS_JSON {…}`` line. For rows triaged as
``designed_overlay`` the fix is a fixed attribute, so the harness writes it
itself instead of paying a model turn (2026-11-22 sample 6b02a2d6e070393b:
2.5 min of Read + thinking to add three attributes).

``hyperframes check`` exempts an element when
``element.closest("[data-layout-allow-overlap]")`` matches, so marking the
row's ``mark_side`` element — or its direct class/id-bearing parent for
structural ``X > span:nth-of-type(n)`` selectors — is sufficient.

Only rows with ``autofix_ok is True`` and a ``mark_side`` are written, and
only on that side. Which rows qualify is decided solely by the JS triage
(``verify-check-parse.mjs``); this module keeps no token lists.

Only stdlib: ``html.parser`` builds a light tree with source offsets so the
attribute is inserted into the original bytes without reformatting the file.
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser

FINDINGS_JSON_PREFIX = "VERIFY_FINDINGS_JSON "
ALLOW_ATTR = "data-layout-allow-overlap"
ALLOW_SNIPPET = f'{ALLOW_ATTR}="true"'

_VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
# Never mark these: the whole scene / a whole track layer would be exempted.
_ROOT_TAGS = frozenset({"html", "head", "body", "template"})
_FINDINGS_LINE_RE = re.compile(
    r"^" + re.escape(FINDINGS_JSON_PREFIX) + r"(\{.*\})\s*$", re.M
)
PAYLOAD_VERSION = 1
_MARK_SIDES = ("selector", "container")
# A selector matching more uncovered nodes than this is too broad to blanket-mark.
MAX_MATCHES_PER_ROW = 3


def auto_overlap_enabled() -> bool:
    raw = os.environ.get("HTMLVIDEO_VERIFY_AUTO_OVERLAP", "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def parse_findings_payload(scan_output: str) -> dict | None:
    """Last ``VERIFY_FINDINGS_JSON`` line of the scan, or ``None``."""
    matches = _FINDINGS_LINE_RE.findall(scan_output or "")
    if not matches:
        return None
    try:
        payload = json.loads(matches[-1])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def strip_findings_payload(scan_output: str) -> str:
    return _FINDINGS_LINE_RE.sub("", scan_output or "")


# ── selector parsing ─────────────────────────────────────────────────────


@dataclass
class Compound:
    tag: str = ""
    id: str = ""
    classes: list[str] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)
    nth_of_type: int = 0

    @property
    def anchorable(self) -> bool:
        """Has an author-chosen hook (not just tag / position)."""
        return bool(self.id or self.classes or self.attrs)


_CSS_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?|\\(.)")
_IDENT = r"(?:\\[0-9a-fA-F]{1,6}\s?|\\.|[\w-])+"
_PART_RE = re.compile(
    r"#(?P<id>" + _IDENT + r")"
    r"|\.(?P<cls>" + _IDENT + r")"
    r"|\[(?P<attr>[\w:-]+)(?:=(?P<q>[\"']?)(?P<val>.*?)(?P=q))?\]"
    r"|:nth-of-type\((?P<nth>\d+)\)"
)
_TAG_RE = re.compile(r"^[a-zA-Z][\w-]*")
_HEX_ESCAPE_SPACE_RE = re.compile(r"\\[0-9a-fA-F]{1,6}\s")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")


def _css_unescape(s: str) -> str:
    def repl(m: re.Match) -> str:
        if m.group(1):
            try:
                return chr(int(m.group(1), 16))
            except (ValueError, OverflowError):
                return ""
        return m.group(2)

    return _CSS_ESCAPE_RE.sub(repl, s)


def parse_compound(text: str) -> Compound | None:
    text = text.strip()
    if not text:
        return None
    comp = Compound()
    m = _TAG_RE.match(text)
    pos = 0
    if m:
        comp.tag = m.group(0).lower()
        pos = m.end()
    while pos < len(text):
        m = _PART_RE.match(text, pos)
        if not m:
            return None
        if m.group("id") is not None:
            comp.id = _css_unescape(m.group("id"))
        elif m.group("cls") is not None:
            comp.classes.append(_css_unescape(m.group("cls")))
        elif m.group("attr") is not None:
            comp.attrs[m.group("attr").lower()] = m.group("val") or ""
        else:
            comp.nth_of_type = int(m.group("nth"))
        pos = m.end()
    return comp


def parse_selector(selector: str) -> list[Compound] | None:
    """Child-combinator chains of simple compounds (the CLI's ``selectorFor``
    grammar). Anything else (descendant spaces, pseudo-classes, lists) → None.
    """
    sel = (selector or "").strip()
    if not sel or "," in sel:
        return None
    out: list[Compound] = []
    for part in sel.split(">"):
        part = part.strip()
        bare = _QUOTED_RE.sub("", _HEX_ESCAPE_SPACE_RE.sub("", part))
        if not part or re.search(r"\s", bare):
            return None
        comp = parse_compound(part)
        if comp is None:
            return None
        out.append(comp)
    return out


# ── light HTML tree with source offsets ─────────────────────────────────


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    start: int
    raw: str
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    end: int = -1  # offset just past the closing tag (start+len(raw) for void)

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()


class _TreeBuilder(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self._line_starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self._line_starts.append(i + 1)
        self.source = source
        self.root = Node("#root", {}, -1, "")
        self.nodes: list[Node] = []
        self._stack: list[Node] = [self.root]

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def _add(self, tag: str, attrs, push: bool) -> None:
        raw = self.get_starttag_text() or ""
        start = self._offset()
        if self.source[start:start + len(raw)] != raw:
            start = -1
        node = Node(
            tag.lower(),
            {k.lower(): (v if v is not None else "") for k, v in attrs},
            start,
            raw,
            parent=self._stack[-1],
        )
        node.end = (start + len(raw)) if start >= 0 else -1
        self._stack[-1].children.append(node)
        self.nodes.append(node)
        if push and node.tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_starttag(self, tag, attrs):
        self._add(tag, attrs, push=True)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs, push=False)

    def handle_endtag(self, tag):
        tag = tag.lower()
        offset = self._offset()
        gt = self.source.find(">", offset)
        end = gt + 1 if gt != -1 else offset
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                self._stack[i].end = end
                del self._stack[i:]
                return


def build_tree(source: str) -> _TreeBuilder:
    builder = _TreeBuilder(source)
    builder.feed(source)
    builder.close()
    return builder


def _matches(node: Node, comp: Compound) -> bool:
    if comp.tag and node.tag != comp.tag:
        return False
    if comp.id and node.attrs.get("id") != comp.id:
        return False
    if comp.classes:
        have = node.classes
        if any(c not in have for c in comp.classes):
            return False
    for k, v in comp.attrs.items():
        if node.attrs.get(k) != v:
            return False
    if comp.nth_of_type:
        if node.parent is None:
            return False
        same = [c for c in node.parent.children if c.tag == node.tag]
        if node not in same or same.index(node) + 1 != comp.nth_of_type:
            return False
    return True


def _is_root_like(node: Node) -> bool:
    if node.tag in _ROOT_TAGS:
        return True
    if "data-composition-id" in node.attrs:
        return True
    return "clip" in node.classes


def resolve_target(tree: _TreeBuilder, selector: str) -> tuple[list[Node], str]:
    """Nodes to mark for ``selector`` plus a short log note.

    Marks the last compound when it carries a class / id / attribute, or its
    direct parent when the chain ends in exactly one hook-less child segment
    (``div.f8-ticker > span:nth-of-type(5)`` → the ``div.f8-ticker`` that has
    such a child). Deeper ancestors would exempt too much and are refused, as
    is any scene root / clip layer.
    """
    chain = parse_selector(selector)
    if not chain:
        return [], "unparseable selector"
    anchor = max((i for i, c in enumerate(chain) if c.anchorable), default=-1)
    if anchor < 0:
        return [], "no class/id/attr hook in selector"
    if anchor < len(chain) - 2:
        return [], f"anchor {_fmt(chain[anchor])} more than one level above target"
    current = [n for n in tree.nodes if _matches(n, chain[0])]
    for comp in chain[1:anchor + 1]:
        current = [c for n in current for c in n.children if _matches(c, comp)]
    if anchor == len(chain) - 2:
        current = [n for n in current if any(_matches(c, chain[-1]) for c in n.children)]
    if not current:
        return [], "selector not found in source"
    targets = [n for n in current if not _is_root_like(n)]
    if not targets:
        return [], "only scene root / clip layer matched"
    note = "direct" if anchor == len(chain) - 1 else f"ancestor {_fmt(chain[anchor])}"
    return targets, note


def _fmt(comp: Compound) -> str:
    s = comp.tag
    if comp.id:
        s += "#" + comp.id
    s += "".join("." + c for c in comp.classes)
    s += "".join(f'[{k}="{v}"]' for k, v in comp.attrs.items())
    return s or "*"


def _depth(node: Node) -> int:
    d, cur = 0, node.parent
    while cur is not None:
        d, cur = d + 1, cur.parent
    return d


def insert_allow_overlap(source: str, nodes: list[Node]) -> tuple[str, int]:
    """Insert ``data-layout-allow-overlap="true"`` into each node's start tag.

    Skips nodes already covered by a flagged ancestor: the walk goes up to the
    composition root (``data-composition-id``), which is all of the CLI's
    ``closest()`` this frame file can see.
    """
    todo = {}
    chosen: set[int] = set()

    def covered(node: Node) -> bool:
        cur = node
        while cur is not None:
            if ALLOW_ATTR in cur.attrs or id(cur) in chosen:
                return True
            cur = cur.parent
        return False

    for node in sorted(nodes, key=_depth):
        if covered(node) or node.start < 0 or not node.raw:
            continue
        gt = node.raw.rfind(">")
        if gt < 0:
            continue
        pos = node.start + gt
        if gt > 0 and node.raw[gt - 1] == "/":
            pos -= 1
        todo[pos] = node
        chosen.add(id(node))
    out = source
    for pos in sorted(todo, reverse=True):
        if out[pos - 1].isspace():
            ins = ALLOW_SNIPPET + (" " if out[pos] == "/" else "")
        else:
            ins = " " + ALLOW_SNIPPET + (" " if out[pos] == "/" else "")
        out = out[:pos] + ins + out[pos:]
    return out, len(todo)


# ── project-level entry point ───────────────────────────────────────────


@dataclass
class AutofixResult:
    files_changed: list[str] = field(default_factory=list)
    nodes_marked: int = 0
    handled: int = 0
    unresolved: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.nodes_marked > 0


def _safe_composition_path(project_dir: str, rel: str) -> str | None:
    if not rel or os.path.isabs(rel) or not rel.endswith(".html"):
        return None
    comp_root = os.path.realpath(os.path.join(project_dir, "compositions"))
    abs_path = os.path.realpath(os.path.join(project_dir, rel))
    if not abs_path.startswith(comp_root + os.sep) or not os.path.isfile(abs_path):
        return None
    return abs_path


def row_is_safe(row: dict) -> tuple[bool, str]:
    """Whether a ``designed_overlay`` row may be fixed without a model.

    Requires ``autofix_ok is True`` and ``mark_side`` in selector/container
    (both set by ``findingsPayload``). Rows from older payloads lack them and
    are left for the model; ``reason`` is never consulted.
    """
    if row.get("autofix_ok") is not True:
        return False, f"autofix_ok={row.get('autofix_ok')!r}"
    side = row.get("mark_side")
    if side not in _MARK_SIDES:
        return False, f"mark_side={side!r} (legacy row?)"
    return True, str(row.get("reason") or "")


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_text_atomic(path: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(
        prefix=".autofix-", suffix=".tmp", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resolve_row(tree: _TreeBuilder, row: dict) -> tuple[list[Node], str, str]:
    """Targets on ``mark_side`` only; no fallback to the other side."""
    side = str(row.get("mark_side"))
    sel = str(row.get(side) or "")
    if not sel:
        return [], f"mark_side={side} has no selector", side
    picked, note = resolve_target(tree, sel)
    if not picked:
        note = f"mark_side={side} {sel}: {note}"
    return picked, note, side


def autofix_designed_overlays(
    project_dir: str, scan_output: str, logger: logging.Logger
) -> AutofixResult:
    """Mark every safe, resolvable ``designed_overlay`` row; leave the rest."""
    result = AutofixResult()
    payload = parse_findings_payload(scan_output)
    if payload is None:
        return result
    if payload.get("version") != PAYLOAD_VERSION:
        logger.warning(
            "auto-overlap: findings payload version %r != %d — skipping auto-fix",
            payload.get("version"), PAYLOAD_VERSION,
        )
        return result
    rows = payload.get("designed_overlay") or []
    if not isinstance(rows, list) or not rows:
        return result
    by_file: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ok, why = row_is_safe(row)
        if not ok:
            logger.info(
                "auto-overlap: skip %s %s — %s; left for the model",
                row.get("file", "?"), row.get("selector", ""), why,
            )
            result.unresolved.append(
                f"{row.get('file') or '?'}: {row.get('selector', '')} (not safe: {why})"
            )
            continue
        by_file.setdefault(str(row.get("file") or ""), []).append(row)
    for rel, file_rows in by_file.items():
        abs_path = _safe_composition_path(project_dir, rel)
        if abs_path is None:
            result.unresolved.extend(
                f"{rel or '?'}: {r.get('selector', '')} (file not under compositions/)"
                for r in file_rows
            )
            continue
        source = _read_text(abs_path)
        tree = build_tree(source)
        resolved = []
        for row in file_rows:
            picked, note, side = _resolve_row(tree, row)
            if not picked:
                logger.info(
                    "auto-overlap: unresolved %s %s — %s",
                    rel, row.get("selector", ""), note,
                )
                result.unresolved.append(f"{rel}: {row.get('selector', '')} ({note})")
                continue
            resolved.append((row, picked, note, side))
        # Narrow rows' targets count as cover for broad rows (order-independent).
        narrow = {
            id(n) for _, picked, _, _ in resolved
            if len(picked) <= MAX_MATCHES_PER_ROW for n in picked
        }

        def covered(node: Node) -> bool:
            cur = node
            while cur is not None:
                if ALLOW_ATTR in cur.attrs or id(cur) in narrow:
                    return True
                cur = cur.parent
            return False

        targets: list[Node] = []
        for row, picked, note, side in resolved:
            uncovered = [n for n in picked if not covered(n)]
            if len(uncovered) > MAX_MATCHES_PER_ROW:
                logger.warning(
                    "auto-overlap: %s %s=%s matches %d unflagged nodes (> %d) — "
                    "too broad, left for the model",
                    rel, side, row.get(side), len(uncovered), MAX_MATCHES_PER_ROW,
                )
                result.unresolved.append(
                    f"{rel}: {row.get('selector', '')} ({len(uncovered)} matches)"
                )
                continue
            result.handled += 1
            logger.info(
                "auto-overlap: %s %s=%s → %d node(s) [%s]",
                rel, side, row.get(side), len(picked), note,
            )
            targets.extend(picked)
        new_source, n = insert_allow_overlap(source, targets)
        if n:
            _write_text_atomic(abs_path, new_source)
            result.files_changed.append(rel)
            result.nodes_marked += n
    if result.unresolved:
        logger.info(
            "auto-overlap: %d designed_overlay row(s) left for the model: %s",
            len(result.unresolved), "; ".join(result.unresolved[:6]),
        )
    return result


# ── caption keep-out band: delete the redundant in-frame caption strip ─────

# Runtime caption words (scripts/captions.mjs); same shape as the JS triage's
# CAPTION_WORD_RE. Frame text overlapping them sits in the caption keep-out band.
CAPTION_WORD_RE = re.compile(r"^(?:[a-z][\w-]*)?#caption-word-\d+-\d+$", re.I)

# A frame's own caption strip: a class token that names "caption" / "cap-line" /
# "subtitle" etc. The auto-caption is injected (never in this source), so any
# element here matching this is the frame drawing a second, redundant caption.
_CAPTION_STRIP_TOKEN_RE = re.compile(
    r"caption|cap[-_](line|pill|band|sub|scrim)|subtitle", re.I,
)

_CAPTION_ROOT_TAGS = frozenset({"html", "head", "body", "template"})


def _side_is_caption_word(side: str) -> bool:
    s = (side or "").strip()
    return bool(s) and (CAPTION_WORD_RE.match(s) is not None or "#caption-word" in s)


def _is_caption_root(node: Node) -> bool:
    return node.tag in _CAPTION_ROOT_TAGS or "data-composition-id" in node.attrs


def _is_caption_strip(node: Node) -> bool:
    return any(_CAPTION_STRIP_TOKEN_RE.search(c) for c in node.classes)


def _caption_strip_ancestor(node: Node) -> Node | None:
    """Highest ancestor that is still a frame caption strip, else ``None``."""
    best = None
    cur = node
    while cur is not None and not _is_caption_root(cur):
        if _is_caption_strip(cur):
            best = cur
        cur = cur.parent
    return best


def _delete_caption_spans(
    source: str, builder: "_TreeBuilder", nodes: list["Node"]
) -> tuple[str, int]:
    """Remove caption-strip elements whole (line-aware), preserving tag balance."""
    spans: set[tuple[int, int]] = set()
    for node in nodes:
        if node.start < 0 or node.end < node.start:
            continue
        s = _line_start(builder, node.start)
        if (source[s:node.start]).strip():
            s = node.start
        e = node.end
        if e < len(source) and source[e] == "\n":
            e += 1
        spans.add((s, e))
    if not spans:
        return source, 0
    out = source
    removed = 0
    for s, e in sorted(spans, reverse=True):
        if s < 0 or e > len(out):
            continue
        out = out[:s] + out[e:]
        removed += 1
    return out, removed


def _line_start(builder: "_TreeBuilder", offset: int) -> int:
    starts = builder._line_starts
    i = bisect.bisect_right(starts, offset) - 1
    return starts[i] if i >= 0 else 0


def autofix_caption_keepout(
    project_dir: str, scan_output: str, logger: logging.Logger
) -> AutofixResult:
    """Determinstic fix for real_collision caption-keep-out content_overlap rows.

    When a frame draws its own caption strip that overlaps the runtime
    ``#caption-word-*`` auto-captions, the strip is redundant — delete it. Each
    row's non-caption side is resolved and its highest caption-strip ancestor
    removed; rows without a caption-strip ancestor (or with caption words on both
    sides) are left for the model.
    """
    result = AutofixResult()
    payload = parse_findings_payload(scan_output)
    if payload is None or payload.get("version") != PAYLOAD_VERSION:
        return result
    rows = payload.get("real_collision") or []
    if not isinstance(rows, list) or not rows:
        return result
    by_file: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sel = str(row.get("selector") or "")
        con = str(row.get("container") or "")
        sel_cap = _side_is_caption_word(sel)
        con_cap = _side_is_caption_word(con)
        if sel_cap == con_cap:
            continue  # neither side caption, or both sides caption — not ours
        non_caption = con if sel_cap else sel
        by_file.setdefault(str(row.get("file") or ""), []).append(non_caption)
    for rel, sides in by_file.items():
        abs_path = _safe_composition_path(project_dir, rel)
        if abs_path is None:
            result.unresolved.append(f"{rel or '?'}: (file not under compositions/)")
            continue
        source = _read_text(abs_path)
        tree = build_tree(source)
        to_delete: dict[int, Node] = {}
        for sel in sides:
            picked, note = resolve_target(tree, sel)
            if not picked:
                logger.info("auto-caption: unresolved %s %s — %s", rel, sel, note)
                result.unresolved.append(f"{rel}: {sel} ({note})")
                continue
            for node in picked:
                strip = _caption_strip_ancestor(node)
                if strip is None:
                    logger.info(
                        "auto-caption: %s %s — no caption-strip ancestor", rel, sel
                    )
                    result.unresolved.append(f"{rel}: {sel} (no caption-strip ancestor)")
                    continue
                to_delete[id(strip)] = strip
        if not to_delete:
            continue
        nodes = list(to_delete.values())
        new_source, n = _delete_caption_spans(source, tree, nodes)
        if n:
            _write_text_atomic(abs_path, new_source)
            result.files_changed.append(rel)
            result.nodes_marked += n
            result.handled += len(nodes)
            logger.info(
                "auto-caption: %s deleted %d caption strip(s)", rel, n,
            )
    if result.unresolved:
        logger.info(
            "auto-caption: %d caption-keep-out row(s) left for the model: %s",
            len(result.unresolved), "; ".join(result.unresolved[:6]),
        )
    return result
