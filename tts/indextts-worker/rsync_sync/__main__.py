"""Standalone CLI for rsync_sync — run file sync directly for debugging.

Usage:
    # Sync a single file to one proxy
    python -m rsync_sync -s /tmp/audio.wav -t /remote/output -p user@host:2222

    # Sync to multiple proxies
    python -m rsync_sync -s /tmp/audio.wav -t /remote/output \\
        -p user@host1:2222 -p user@host2:2222

    # Load proxies from a config file (one per line: user@host:port)
    python -m rsync_sync -s /tmp/audio.wav -t /remote/output -f /app/data/proxies.txt

    # Sequential mode (easier to debug)
    python -m rsync_sync -s /tmp/audio.wav -t /remote/output -p user@host:2222 --sequential

    # Dry run (validate config and connections only)
    python -m rsync_sync --dry-run -p user@host:2222

    # Keep ControlMaster alive for connection reuse (auto-cleanup after 10 min idle)
    python -m rsync_sync -s /tmp/audio.wav -t /remote/output -p user@host:2222 --keep-control

    # Custom ControlPersist duration (5 minutes)
    python -m rsync_sync -s /tmp/audio.wav -t /remote/output -p user@host:2222 \\
        --keep-control --control-persist 300
"""

import argparse
import logging
import os
import sys
import time
from typing import List

from .callbacks import NoOpCallback
from .config import SyncConfig
from .models import ProxyTarget, SyncJob
from .parsers import load_proxy_config_file, parse_proxy_config
from .sync_engine import SequentialSyncStrategy
from .transport import NativeSSHTransport
from .slot_manager import SlotManager
from . import SyncOrchestrator


def _setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure console logging for standalone use."""
    logger = logging.getLogger("rsync_sync")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    return logger


def _parse_proxy_arg(raw: str) -> ProxyTarget:
    """Parse a user@host:port string into a ProxyTarget."""
    # Strip whitespace
    raw = raw.strip()
    # Split user@host from port
    if ":" not in raw:
        raise ValueError(f"Invalid proxy format: {raw!r} (expected user@host:port)")
    user_host, port_s = raw.rsplit(":", 1)
    if "@" not in user_host:
        raise ValueError(f"Invalid proxy format: {raw!r} (expected user@host:port)")
    user, host = user_host.split("@", 1)
    port = int(port_s)
    if port < 1 or port > 65535:
        raise ValueError(f"Port out of range: {port}")
    return ProxyTarget(user=user, host=host, port=port)


def _collect_proxies(args, logger) -> List[ProxyTarget]:
    """Gather all proxies from CLI args and config files."""
    proxies: List[ProxyTarget] = []

    # From --proxy args
    for raw in args.proxy or []:
        try:
            proxies.append(_parse_proxy_arg(raw))
        except ValueError as e:
            logger.error(f"Invalid proxy: {e}")
            sys.exit(1)

    # From --proxies-file args
    for filepath in args.proxies_file or []:
        try:
            with open(filepath, "r") as f:
                file_proxies = parse_proxy_config(f.read())
                logger.info(f"Loaded {len(file_proxies)} proxies from {filepath}")
                proxies.extend(file_proxies)
        except FileNotFoundError:
            logger.error(f"Proxies file not found: {filepath}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error reading proxies file {filepath}: {e}")
            sys.exit(1)

    # Deduplicate
    seen = set()
    deduped = []
    for p in proxies:
        if p.server not in seen:
            seen.add(p.server)
            deduped.append(p)

    return deduped


def main(argv=None):
    """Standalone CLI entry point."""
    parser = argparse.ArgumentParser(
        description="rsync_sync — sync files to remote servers via rsync+SSH ControlMaster",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m rsync_sync -s /tmp/audio.wav -t /remote/output -p user@host1:2222
  python -m rsync_sync -s /tmp/audio.wav -t /remote/output -f proxies.txt --sequential
  python -m rsync_sync --dry-run -p user@host1:2222 -p user@host2:2222
        """,
    )

    parser.add_argument(
        "-s", "--source-file",
        help="Local file to sync",
    )
    parser.add_argument(
        "-t", "--target-dir",
        help="Remote target directory",
    )
    parser.add_argument(
        "-p", "--proxy",
        action="append",
        metavar="user@host:port",
        help="Remote proxy server (repeatable)",
    )
    parser.add_argument(
        "-f", "--proxies-file",
        action="append",
        metavar="FILE",
        help="File with proxy entries (one user@host:port per line, repeatable)",
    )
    parser.add_argument(
        "--slot-count",
        type=int,
        default=2,
        help="ControlMaster slots per proxy (default: 2)",
    )
    parser.add_argument(
        "--control-dir",
        default="/tmp/ssh_control",
        help="SSH ControlMaster socket directory (default: /tmp/ssh_control)",
    )
    parser.add_argument(
        "--rsync-flags",
        default="-rt --whole-file --partial",
        help="Flags passed to rsync (default: -rt --whole-file --partial)",
    )
    parser.add_argument(
        "--rsync-timeout",
        type=int,
        default=120,
        help="Timeout in seconds for rsync transfer (default: 120)",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        default=False,
        help="Use sequential mode instead of async (easier for debugging)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Initialize connections but do not transfer files",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help="Custom task ID (default: auto-generated)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable debug-level logging",
    )
    parser.add_argument(
        "--keep-control",
        action="store_true",
        default=False,
        help=(
            "Keep ControlMaster connections alive after exit. "
            "SSH will auto-cleanup after --control-persist seconds of idle "
            "(default: 600 = 10 min). Subsequent runs within that window "
            "reuse the existing connection, skipping SSH authentication."
        ),
    )
    parser.add_argument(
        "--control-persist",
        type=int,
        default=600,
        help=(
            "Seconds to keep ControlMaster alive after last use "
            "(only meaningful with --keep-control, default: 600)"
        ),
    )

    args = parser.parse_args(argv)
    logger = _setup_logging(verbose=args.verbose)

    # Validate args
    proxies = _collect_proxies(args, logger)
    if not proxies:
        logger.error("No proxies specified. Use -p or -f.")
        sys.exit(1)

    if not args.dry_run and not args.source_file:
        logger.error("--source-file is required (unless --dry-run).")
        sys.exit(1)

    if not args.dry_run and not args.target_dir:
        logger.error("--target-dir is required (unless --dry-run).")
        sys.exit(1)

    if args.dry_run and not args.source_file:
        args.source_file = "/dev/null"

    if args.dry_run and not args.target_dir:
        args.target_dir = "/tmp"

    # Show what we're about to do
    logger.info("=" * 60)
    logger.info("rsync_sync standalone")
    logger.info(f"  Proxies: {len(proxies)} ({', '.join(p.server for p in proxies)})")
    logger.info(f"  Source:  {args.source_file}")
    logger.info(f"  Target:  {args.target_dir}")
    logger.info(f"  Slots:   {args.slot_count}")
    logger.info(f"  Mode:    {'sequential' if args.sequential else 'async'}")
    logger.info(f"  Dry run: {args.dry_run}")
    logger.info(f"  Keep control: {args.keep_control}")
    if args.keep_control:
        logger.info(f"  ControlPersist: {args.control_persist}s")
    logger.info("=" * 60)

    # Build config
    config_kwargs = dict(
        slot_count=args.slot_count,
        control_dir=args.control_dir,
        rsync_flags=args.rsync_flags,
        rsync_timeout_sec=args.rsync_timeout,
    )
    if args.keep_control:
        config_kwargs["control_persist"] = args.control_persist
    config = SyncConfig(**config_kwargs)
    config.ensure_control_dir()
    logger.info(f"SSH control directory: {config.control_dir}")

    # Create orchestrator
    callback = NoOpCallback()
    transport = NativeSSHTransport(config, logger=logger)
    slot_manager = SlotManager(transport, config.slot_count, logger=logger)

    if args.sequential:
        strategy = SequentialSyncStrategy(
            transport, slot_manager, callback=callback, logger=logger,
        )
    else:
        from .sync_engine import AsyncQueueSyncStrategy
        strategy = AsyncQueueSyncStrategy(
            transport, slot_manager, config, callback=callback, logger=logger,
        )

    orchestrator = SyncOrchestrator(
        config, strategy, transport, callback=callback, logger=logger,
    )

    # Register and initialize
    orchestrator.register_proxies("cli", proxies)
    logger.info(f"Initializing SSH ControlMaster connections...")
    results = orchestrator.initialize(["cli"])

    ok_count = sum(1 for ok in results.values() if ok)
    fail_count = len(results) - ok_count
    logger.info(f"SSH init complete: {ok_count} ok, {fail_count} failed")

    if fail_count > 0:
        failed = [k for k, ok in results.items() if not ok]
        logger.warning(f"Failed connections: {failed}")
        logger.warning("Syncing to failed proxies will be skipped/retried at transfer time.")

    cleanup = not args.keep_control

    if args.dry_run:
        logger.info("Dry run complete — no files transferred.")
        orchestrator.shutdown(cleanup_transport=cleanup)
        return 0

    # Create and enqueue the sync job
    task_id = args.task_id or f"standalone-{int(time.time())}"
    job = SyncJob(
        source_file=args.source_file,
        target_dir=args.target_dir,
        task_id=task_id,
        proxies=proxies,
    )

    logger.info(f"Enqueueing sync job task_id={task_id}...")

    if args.sequential:
        # Sequential: sync() returns the metrics directly
        metrics = strategy.sync(job)
        elapsed = metrics.total_elapsed_sec
        speed = (metrics.file_size_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        logger.info(
            f"Sync complete: {metrics.success_count}/{metrics.total_count} ok, "
            f"elapsed={elapsed:.2f}s, speed={speed:.2f} MB/s"
        )
        if not metrics.all_succeeded:
            logger.error(f"Failed proxies: {metrics.failed_proxies}")
        orchestrator.shutdown(cleanup_transport=cleanup)
        if args.keep_control:
            logger.info(
                f"ControlMaster connections preserved. "
                f"SSH will auto-cleanup after {args.control_persist}s of idle."
            )
    else:
        # Async: enqueue returns bool, workers handle it
        ok = orchestrator.enqueue(job)
        if not ok:
            logger.error("Failed to enqueue sync job")
            orchestrator.shutdown(cleanup_transport=cleanup)
            return 1
        logger.info("Job enqueued. Waiting for workers to finish...")
        # Give async workers time to complete
        orchestrator.shutdown(cleanup_transport=cleanup)
        if args.keep_control:
            logger.info(
                f"ControlMaster connections preserved. "
                f"SSH will auto-cleanup after {args.control_persist}s of idle."
            )
        logger.info("Workers stopped.")

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())