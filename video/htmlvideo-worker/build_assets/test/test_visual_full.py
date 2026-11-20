#!/usr/bin/env python3
"""Full frame-visual verify: reset the fixture storyboard to outline, then run
the full visual driver (Phase A + B + merge + gate) to confirm the -50% target."""
from __future__ import annotations

import logging
import sys
import time

sys.path.insert(0, "/app")
from html_utility.storyboard import build_storyboard
from html_utility.visual import build_frame_visual

PROJECT = "/app/tmp-hf-visual-fixture"


def _logger() -> logging.Logger:
    logger = logging.getLogger("visual-full")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s[%(lineno)d] %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler("/app/log/visual-full.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger


def main() -> int:
    logger = _logger()
    logger.info("resetting storyboard to outline stage...")
    sb = build_storyboard(PROJECT, logger)
    logger.info("storyboard reset ok: %s", sb)

    t0 = time.time()
    sb2 = build_frame_visual(PROJECT, logger)
    dt = time.time() - t0
    logger.info("FULL build_frame_visual done in %.1fs -> %s", dt, sb2)
    return 0


if __name__ == "__main__":
    sys.exit(main())