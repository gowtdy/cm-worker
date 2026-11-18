# Frame dispatch — deterministic driver contract

The aiflow-build-frame-html stage has **no orchestrator agent**. Dispatch,
wait, and re-dispatch are implemented by `html_utility/frame_html.py` using
`claude_cli.run_claude_result` per frame. This file pins the semantics.

## The contract

- **DISPATCH(role_docs, frame_id)** — one `claude -p` process per frame. The
  prompt carries the merged dispatch path (`.dispatch.md`), the skeleton fact,
  the selfcheck command, the terminal reply protocol; the full role lives on
  disk as an escape hatch.
- **Parallel fan-out** — workers are independent; concurrency =
  `HTMLVIDEO_FRAME_HTML_MAX_CONCURRENCY` (default 8), then queued waves.
- **WAIT** — completion = rc 0 ∧ `done OK <frame_id>` in the session result
  (skeleton-off mode additionally requires the file). Wall-clock cap per worker
  = the `frame_html` profile timeout (720 s default).
- **Re-dispatch** — same prompt + failure reason, batch-parallel, ≤2 rounds.

## Rollback

`HTMLVIDEO_FRAME_HTML_DRIVER=0` routes back to the legacy orchestrator skill
run; `HTMLVIDEO_FRAME_HTML_SKELETON=0` disables skeleton mode.