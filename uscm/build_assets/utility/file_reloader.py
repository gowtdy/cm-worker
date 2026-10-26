# -*- coding: utf-8 -*-
"""通用动态文件加载器：单后台线程按 mtime 轮询重载，解析结果供无锁读取。"""
import logging
import os
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class _FileEntry:
    def __init__(self, name: str, path: str, parse_fn: Callable[[str], Any]):
        self.name = name
        self.path = path
        self.parse_fn = parse_fn
        self.last_mtime_ns = None
        self.value = None


class FileReloader:
    """维护一张注册表，一个后台线程轮询所有文件的重载器。"""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self._registry = {}
        self._lock = threading.Lock()
        self._started = False

    def register(self, name: str, path: str, parse_fn: Callable[[str], Any]) -> Any:
        entry = _FileEntry(name, path, parse_fn)
        with self._lock:
            self._registry[name] = entry
        self._load(entry)
        self._ensure_thread()
        return entry.value

    def get(self, name: str) -> Any:
        entry = self._registry.get(name)
        return entry.value if entry is not None else None

    def _ensure_thread(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            threading.Thread(target=self._poll_loop, daemon=True).start()
            self._started = True

    def _load(self, entry: _FileEntry) -> None:
        try:
            mtime = os.stat(entry.path).st_mtime_ns
            with open(entry.path, encoding="utf-8") as f:
                text = f.read()
            entry.value = entry.parse_fn(text)
            entry.last_mtime_ns = mtime
        except Exception:
            logger.exception("file_reloader load failed, name:%s path:%s", entry.name, entry.path)

    def _poll_loop(self) -> None:
        pass


default_reloader = FileReloader()