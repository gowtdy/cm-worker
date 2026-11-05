# Celery payload + cmwk_common Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans. Spec: `docs/superpowers/specs/2026-11-05-celery-task-payload-design.md`

**Goal:** Unify Celery delay to `payload=` and extract `common/cmwk_common` with rsync inject; no shim.

**Architecture:** Shared package at repo root; build scripts sync into `build_assets/cmwk_common`; workers/`uscm` import `cmwk_common.*`; tasks accept only `payload`.

## Tasks

1. Add `common/cmwk_common` + `sync_cmwk_common.sh` + gitignore
2. Patch all `build-*-image.sh` / `build-audio-worker.sh` to sync before docker build
3. Rewrite imports; delete migrated utility files; keep biz-only util remnants
4. Change all Celery task signatures to `def xxx(payload)`
5. uscm: `build_payload` + `dispatch`; task_req = json.dumps(payload); htmlvideo full fields
