# -*- coding: utf-8 -*-
"""Run ``font-imports.mjs`` for a HyperFrames project.

Never raises. ``logger`` may be None. ``HTMLVIDEO_FONT_IMPORTS`` set to
``0`` / ``false`` / ``off`` / ``no`` skips the script and returns 0.
"""

from __future__ import annotations

import os
import subprocess

_SCRIPT = "/app/scripts/font-imports.mjs"
_OFF = {"0", "false", "off", "no"}


def _log(logger, level: str, msg: str, *args) -> None:
    if logger is None:
        return
    try:
        getattr(logger, level)(msg, *args)
    except Exception:
        return


def run_font_imports(project_dir, logger, scope: str = "all", timeout: int = 60) -> int:
    """Inject marked font ``@import`` / brand ``@font-face``. Returns a process code.

    0 = ok (or skipped). 1 = unresolved families or a caught failure.
    2 = the CLI rejected the invocation. Exceptions become 1.
    """
    try:
        flag = os.environ.get("HTMLVIDEO_FONT_IMPORTS", "").strip().lower()
        if flag in _OFF:
            _log(logger, "info", "font-imports skipped (HTMLVIDEO_FONT_IMPORTS=%s)", flag)
            return 0
        if not project_dir:
            _log(logger, "warning", "font-imports skipped: empty project_dir")
            return 0
        cmd = [
            "node",
            _SCRIPT,
            "--videodir",
            str(project_dir),
            "--scope",
            scope or "all",
        ]
        _log(logger, "info", "+ %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        rc = 1 if proc.returncode is None else int(proc.returncode)
        if out:
            _log(logger, "info", "font-imports: %s", out[-2000:])
        if rc != 0:
            _log(
                logger,
                "warning",
                "font-imports rc=%s stderr=%s",
                rc,
                (err or out)[-2000:],
            )
        return rc
    except Exception as exc:
        _log(logger, "warning", "font-imports failed: %s", exc)
        return 1
