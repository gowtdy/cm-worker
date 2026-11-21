# Frame dispatch — deterministic driver contract

The aiflow-build-frame-html stage has **no orchestrator agent**. Dispatch,
wait, selfcheck, and re-dispatch are implemented by `html_utility/frame_html.py`
using `claude_cli.run_claude_result` per frame. This file pins the semantics.

## The contract

- **DISPATCH(role_docs, frame_id)** — one `claude -p` process per frame. The
  prompt **inlines** the merged `.dispatch.md` body (do not Read the path), the
  skeleton fact, and the terminal reply protocol. Bash is denied; the driver
  owns `frame-selfcheck`.
- **Parallel fan-out** — workers are independent; concurrency =
  `HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY` (default 16), then queued waves.
- **WAIT / GATE** — after each worker return the driver runs
  `node frame-selfcheck.mjs compositions/frames/<frame_id>.html`. Completion =
  rc 0 ∧ `done OK <frame_id>` ∧ selfcheck PASS ∧ file on disk. Wall-clock cap
  per worker = the `frame_html` profile timeout (1500 s default).
- **Re-dispatch** — same prompt + failure / selfcheck findings, batch-parallel,
  bounded by `HTMLVIDEO_FRAME_HTML_MAX_ATTEMPTS` (default 3). Never unbounded.

## Rollback

`HTMLVIDEO_FRAME_HTML_DRIVER=0` routes back to the legacy orchestrator skill
run; `HTMLVIDEO_FRAME_HTML_SKELETON=0` disables skeleton mode.
