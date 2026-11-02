"""Helpers to drop temporary load-time tensors and log process memory."""

from __future__ import annotations

import gc
import platform
import resource
import sys
from typing import Any, Optional

import torch


def release_after_load(*objs: Any, empty_cuda: bool = False) -> None:
    """Drop caller-held temporaries and run GC; optionally clear CUDA cache.

    Callers must finish copying weights into the live model before invoking this.
    """
    del objs
    gc.collect()
    if empty_cuda and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _ru_maxrss_to_mb(ru_maxrss: int) -> float:
    # Linux: kilobytes; macOS/BSD: bytes
    if sys.platform == "darwin" or platform.system() == "Darwin":
        return ru_maxrss / (1024 * 1024)
    return ru_maxrss / 1024


def log_host_rss(logger: Optional[Any], tag: str) -> None:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss_mb = _ru_maxrss_to_mb(usage.ru_maxrss)
    msg = f"[mem] {tag} host_maxrss_mb={rss_mb:.1f}"
    if logger is not None:
        logger.debug(msg)
    else:
        print(msg)


def log_cuda_mem(logger: Optional[Any], tag: str, device: Optional[Any] = None) -> None:
    if not torch.cuda.is_available():
        return
    try:
        if device is None:
            device = torch.cuda.current_device()
        allocated = torch.cuda.memory_allocated(device) / (1024 ** 3)
        reserved = torch.cuda.memory_reserved(device) / (1024 ** 3)
        msg = f"[mem] {tag} cuda_allocated_gb={allocated:.2f} cuda_reserved_gb={reserved:.2f}"
        if logger is not None:
            logger.debug(msg)
        else:
            print(msg)
    except Exception as e:
        msg = f"[mem] {tag} cuda_mem_log_failed error={e}"
        if logger is not None:
            logger.warning(msg)
        else:
            print(msg)
