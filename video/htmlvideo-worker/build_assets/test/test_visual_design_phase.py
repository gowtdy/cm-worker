#!/usr/bin/env python3
"""Run ONLY Phase A (design-contract) of the frame-visual driver to measure the
visual.py tightening (inline frame.md tokens + no `Read frame.md`)."""
from __future__ import annotations

import logging
import sys
import time

sys.path.insert(0, "/app")
from html_utility import visual as vis
from html_utility.claude_cli import run_claude_result

PROJECT = "/app/tmp-hf-visual-fixture"


def _logger() -> logging.Logger:
    logger = logging.getLogger("visual-phase-a")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s-%(name)s[%(lineno)d]-%(levelname)s: %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler("/app/log/visual-phase-a.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger


def main() -> int:
    logger = _logger()
    with open(PROJECT + "/STORYBOARD.md", encoding="utf-8") as f:
        sb = f.read()
    with open(PROJECT + "/frame.md", encoding="utf-8") as f:
        frame_md = f.read()
    preamble, blocks, order = vis._split_storyboard(sb)
    tokens = vis._frame_md_tokens(frame_md)
    logger.info("Phase A: %d frames, inlined tokens %d chars", len(order), len(tokens))

    prompt = vis._build_design_prompt(preamble, order, tokens)
    t0 = time.time()
    rc, text = run_claude_result(PROJECT, prompt, logger, skill="visual", label="design")
    dt = time.time() - t0
    logger.info("Phase A done rc=%s in %.1fs (text %d chars)", rc, dt, len(text or ""))
    try:
        design = vis._scripted_direction_head(tokens) + "\n" + vis._validate_design_contract(text)
        logger.info(
            "_design.md: %d chars, has Video direction=%s, has Blueprint=%s",
            len(design),
            "## Video direction" in design,
            "## Blueprint assignment" in design,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("split failed: %s", exc)
        return 1
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())