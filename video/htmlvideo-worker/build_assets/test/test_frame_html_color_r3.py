#!/usr/bin/env python3
"""Half-run color-fidelity re-verify: re-dispatch the two slow frames (01, 03)
after the frontmatter-colors inlining fix, so we can confirm the worker now
emits exact frame.md hex values instead of hallucinated approximations.

Bypasses the full 6-frame create_pipeline; drives the two frames directly via
the same run_claude_result(..., skill="frame_html") path the driver uses.
"""
from __future__ import annotations

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/app")
from html_utility import frame_html as fh
from html_utility.claude_cli import run_claude_result

PROJECT = "/app/videos/2026-11-19/userclm17919893695531772/6afea3068088f914"
FRAMES = ["01-not-investing-guessing", "03-three-buy-tests"]


def _logger(log_file: str) -> logging.Logger:
    logger = logging.getLogger("frame-html-color-r3")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s-%(name)s[%(lineno)d]-%(levelname)s: %(message)s"
    )
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh_ = logging.FileHandler(log_file, encoding="utf-8")
    fh_.setFormatter(fmt)
    logger.addHandler(fh_)
    logger.propagate = False
    return logger


def main() -> int:
    logger = _logger("/app/log/frame-html-color-r3.log")
    logger.info("half-run color re-verify START project=%s frames=%s", PROJECT, FRAMES)

    def run_one(fid: str):
        prompt = fh._worker_prompt(
            fid, skeleton_mode=True, project_dir=PROJECT)
        rc, text = run_claude_result(
            PROJECT, prompt, logger,
            extra_env={"CLAUDE_CODE_SUBAGENT_EFFORT": "high"},
            skill="frame_html", label=fid)
        return fid, rc, text

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=len(FRAMES)) as pool:
        for fid, rc, text in pool.map(run_one, FRAMES):
            logger.info("half-run result %s rc=%s tail=%r", fid, rc, (text or "")[-120:])
    logger.info("half-run DONE total=%.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())