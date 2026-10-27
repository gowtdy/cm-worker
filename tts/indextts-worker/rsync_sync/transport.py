"""SSH transport implementations for the rsync sync subsystem.

NativeSSHTransport  — real subprocess-based SSH ControlMaster + rsync
MockSSHTransport   — preset-based fake for unit tests
"""

import os
import shlex
import subprocess
import threading
from typing import Dict, Optional, Set

from .config import SyncConfig
from .exceptions import ConnectionError, TransferError
from .interfaces import SSHTransport
from .models import ProxyTarget, TransferResult
from .util import get_control_path, get_dir_key, get_server_key, parse_server_key


class NativeSSHTransport(SSHTransport):
    """Real SSH transport using subprocess for ssh and rsync.

    Manages SSH ControlMaster connections with per-proxy slot multiplexing.
    All commands use list-form (no shell=True) for safety.

    Thread-safe: uses locks for shared mutable state.
    """

    def __init__(self, config: SyncConfig = None, logger=None):
        """
        Args:
            config: SyncConfig instance (uses defaults if None)
            logger: optional logger; if None, uses the root logger
        """
        self._config = config or SyncConfig()
        self._config.ensure_control_dir()

        import logging
        self._logger = logger or logging.getLogger("root")

        self._initialized_servers: Set[str] = set()
        self._created_remote_dirs: Set[str] = set()
        self._remote_dir_lock = threading.Lock()

    # ---- SSH Transport interface ----

    def initialize_connection(self, proxy: ProxyTarget, slot: int = 0) -> bool:
        """Initialize a single SSH ControlMaster connection."""
        server_key = get_server_key(proxy.user, proxy.host, proxy.port, slot)
        if server_key in self._initialized_servers:
            self._logger.debug(f"init_skip server_key={server_key} reason=already_initialized")
            return True

        control_path = get_control_path(
            proxy.user, proxy.host, proxy.port, slot, config=self._config
        )
        self._logger.info(f"init_start server_key={server_key}")

        ssh_cmd = self._build_ssh_init_cmd(proxy, control_path, slot)
        try:
            process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            _, stderr = process.communicate(timeout=self._config.ssh_init_timeout_sec)
            if process.returncode == 0:
                self._initialized_servers.add(server_key)
                self._logger.info(f"init_ok server_key={server_key}")
                return True
            self._logger.warning(f"init_fail server_key={server_key} stderr={stderr}")
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(timeout=5)
            except Exception:
                pass
            self._logger.error(f"init_fail server_key={server_key} error=timeout")
        except Exception as e:
            self._logger.error(f"init_fail server_key={server_key} error={e}")
        return False

    def cleanup_connection(self, proxy: ProxyTarget, slot: int = 0) -> None:
        """Close a single ControlMaster connection."""
        server_key = get_server_key(proxy.user, proxy.host, proxy.port, slot)
        control_path = get_control_path(
            proxy.user, proxy.host, proxy.port, slot, config=self._config
        )
        if not os.path.exists(control_path):
            self._initialized_servers.discard(server_key)
            return

        self._logger.info(f"cleanup_start server_key={server_key}")
        ssh_cmd = self._build_ssh_exit_cmd(proxy, control_path)
        try:
            subprocess.run(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            self._logger.debug(f"cleanup_ok server_key={server_key}")
        except Exception as e:
            self._logger.warning(f"cleanup_fail server_key={server_key} error={e}")
        self._initialized_servers.discard(server_key)

    def cleanup_all(self) -> None:
        """Close all initialized ControlMaster connections."""
        for server_key in list(self._initialized_servers):
            try:
                user, host, port, slot = parse_server_key(server_key)
                proxy = ProxyTarget(user=user, host=host, port=port)
                self.cleanup_connection(proxy, slot)
            except Exception as e:
                self._logger.warning(f"cleanup_all_fail server_key={server_key} error={e}")

        self._initialized_servers.clear()
        with self._remote_dir_lock:
            self._created_remote_dirs.clear()

    def ensure_remote_dir(
        self, proxy: ProxyTarget, target_dir: str, slot: int = 0
    ) -> bool:
        """Create remote directory via SSH, with stale-socket retry.

        Idempotent: cached per target_dir so mkdir runs at most once
        per process lifetime for each (proxy, dir) pair.
        """
        dir_key = get_dir_key(proxy.user, proxy.host, proxy.port, target_dir)
        with self._remote_dir_lock:
            if dir_key in self._created_remote_dirs:
                return True

        server_key = get_server_key(proxy.user, proxy.host, proxy.port, slot)
        control_path = get_control_path(
            proxy.user, proxy.host, proxy.port, slot, config=self._config
        )

        stderr = ""
        for attempt in range(2):
            ssh_cmd = self._build_ssh_mkdir_cmd(proxy, target_dir, control_path)
            process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                _, stderr = process.communicate(
                    timeout=self._config.ssh_mkdir_timeout_sec
                )
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    _, stderr = process.communicate(timeout=5)
                except Exception:
                    pass
                self._logger.error(
                    f"mkdir_remote_timeout server_key={server_key} "
                    f"target_dir={target_dir} attempt={attempt + 1} stderr={stderr!r}"
                )
                if attempt == 0 and os.path.exists(control_path):
                    try:
                        os.unlink(control_path)
                        self._logger.warning(
                            f"mkdir_remote_removed_stale_control "
                            f"server_key={server_key} path={control_path}"
                        )
                    except OSError as e:
                        self._logger.warning(
                            f"mkdir_remote_unlink_control_fail "
                            f"server_key={server_key} error={e}"
                        )
                    continue
                return False

            if process.returncode != 0:
                self._logger.error(
                    f"mkdir_remote_fail server_key={server_key} "
                    f"target_dir={target_dir} stderr={stderr}"
                )
                return False

            with self._remote_dir_lock:
                self._created_remote_dirs.add(dir_key)
            self._logger.info(
                f"mkdir_remote_ok server_key={server_key} target_dir={target_dir}"
            )
            return True

        return False

    def transfer_file(
        self,
        source_file: str,
        proxy: ProxyTarget,
        target_dir: str,
        slot: int = 0,
    ) -> TransferResult:
        """rsync a single file via the given ControlMaster slot.

        Uses list-form arguments (no shell=True) for safety.
        """
        import os as _os
        import time
        server_key = get_server_key(proxy.user, proxy.host, proxy.port, slot)
        file_size = _os.path.getsize(source_file) if _os.path.exists(source_file) else 0
        phase_start = time.monotonic()

        # Phase 1: ensure remote directory exists
        mkdir_start = time.monotonic()
        if not self.ensure_remote_dir(proxy, target_dir, slot=slot):
            return TransferResult(ok=False, elapsed_sec=0.0, stderr="mkdir failed")
        mkdir_elapsed = time.monotonic() - mkdir_start

        # Phase 2: rsync transfer
        rsync_cmd = self._build_rsync_cmd(source_file, proxy, target_dir, slot)
        cmd_str = " ".join(shlex.quote(a) for a in rsync_cmd)
        self._logger.info(f"rsync_command server_key={server_key} slot={slot} cmd={cmd_str}")

        rsync_start = time.monotonic()
        try:
            process = subprocess.Popen(
                rsync_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                _, stderr = process.communicate(
                    timeout=self._config.rsync_timeout_sec
                )
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    _, stderr = process.communicate(timeout=30)
                except Exception:
                    stderr = ""
                elapsed = time.monotonic() - rsync_start
                self._logger.error(
                    f"rsync_timeout server_key={server_key} slot={slot} "
                    f"target={proxy.host}:{target_dir}/ stderr={stderr!r}"
                )
                return TransferResult(ok=False, elapsed_sec=elapsed, stderr=stderr)

            rsync_elapsed = time.monotonic() - rsync_start
            total_elapsed = time.monotonic() - phase_start

            if process.returncode != 0:
                self._logger.error(
                    f"rsync_fail server_key={server_key} slot={slot} "
                    f"phase_timing mkdir={mkdir_elapsed:.3f}s rsync={rsync_elapsed:.3f}s "
                    f"total={total_elapsed:.3f}s stderr={stderr}"
                )
                return TransferResult(ok=False, elapsed_sec=rsync_elapsed, stderr=stderr)

            speed_mbps = (file_size / (1024 * 1024)) / rsync_elapsed if rsync_elapsed > 0 else 0
            self._logger.info(
                f"rsync_ok server_key={server_key} slot={slot} "
                f"target={proxy.host}:{target_dir}/ "
                f"phase_timing mkdir={mkdir_elapsed:.3f}s rsync={rsync_elapsed:.3f}s "
                f"total={total_elapsed:.3f}s speed={speed_mbps:.3f} MB/s"
            )
            return TransferResult(ok=True, elapsed_sec=rsync_elapsed, stderr="")
        except Exception as e:
            rsync_elapsed = time.monotonic() - rsync_start
            total_elapsed = time.monotonic() - phase_start
            self._logger.error(
                f"rsync_exception server_key={server_key} slot={slot} error={e} "
                f"phase_timing mkdir={mkdir_elapsed:.3f}s rsync={rsync_elapsed:.3f}s "
                f"total={total_elapsed:.3f}s"
            )
            return TransferResult(ok=False, elapsed_sec=rsync_elapsed, stderr=str(e))

    def get_control_path(self, proxy: ProxyTarget, slot: int = 0) -> str:
        """Get the filesystem path for a ControlMaster socket."""
        return get_control_path(
            proxy.user, proxy.host, proxy.port, slot, config=self._config
        )

    # ---- Internal command builders ----

    def _build_ssh_init_cmd(
        self, proxy: ProxyTarget, control_path: str, slot: int
    ) -> list:
        """Build ssh command for ControlMaster initialization."""
        return [
            "ssh",
            "-o", "ControlMaster=yes",
            "-o", f"ControlPath={control_path}",
            "-o", f"ControlPersist={self._config.control_persist}",
            *self._config.ssh_base_opts_list,
            "-p", str(proxy.port),
            proxy.connect_target,
            "echo 'SSH connection test'",
        ]

    def _build_ssh_exit_cmd(self, proxy: ProxyTarget, control_path: str) -> list:
        """Build ssh command for ControlMaster teardown."""
        return [
            "ssh",
            *self._config.ssh_conn_opts_list,
            "-O", "exit",
            "-o", f"ControlPath={control_path}",
            "-p", str(proxy.port),
            proxy.connect_target,
        ]

    def _build_ssh_mkdir_cmd(
        self, proxy: ProxyTarget, target_dir: str, control_path: str
    ) -> list:
        """Build ssh command for remote mkdir."""
        return [
            "ssh",
            "-p", str(proxy.port),
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={control_path}",
            "-o", f"ControlPersist={self._config.control_persist}",
            *self._config.ssh_base_opts_list,
            proxy.connect_target,
            f"mkdir -p {shlex.quote(target_dir)}",
        ]

    def _build_ssh_opts_string(self, proxy: ProxyTarget, slot: int) -> str:
        """Build the SSH options string passed to rsync -e.

        This IS embedded inside an argument to rsync, so it must remain
        a string that ssh parses. We minimize shell injection risk by
        constructing it from known-good values.
        """
        control_path = get_control_path(
            proxy.user, proxy.host, proxy.port, slot, config=self._config
        )
        return (
            f"-p {int(proxy.port)} "
            f"-o ConnectTimeout={self._config.connect_timeout_sec} "
            f"-o ConnectionAttempts={self._config.connection_attempts} "
            f"-o ServerAliveInterval={self._config.server_alive_interval_sec} "
            f"-o ServerAliveCountMax={self._config.server_alive_count_max} "
            f"-o ControlMaster=auto "
            f"-o ControlPath={control_path} "
            f"-o ControlPersist={self._config.control_persist} "
            f"-o StrictHostKeyChecking=no "
            f"-o BatchMode=yes "
            f"-o GSSAPIAuthentication=no "
            f"-o Compression=no"
        )

    def _build_rsync_cmd(
        self, source_file: str, proxy: ProxyTarget, target_dir: str, slot: int
    ) -> list:
        """Build rsync command as a list (no shell=True).

        Construct: rsync [flags] -e "ssh [opts]" source_file user@host:target_dir/
        """
        ssh_opts = self._build_ssh_opts_string(proxy, slot)
        flags = shlex.split(self._config.rsync_flags)
        return [
            "rsync",
            *flags,
            "-e", f"ssh {ssh_opts}",
            source_file,
            f"{proxy.connect_target}:{target_dir}/",
        ]


class MockSSHTransport(SSHTransport):
    """Fake SSH transport for unit testing.

    All operations are preset by the test — no real subprocess, SSH,
    or filesystem calls. Unpreset operations raise AssertionError.

    Usage:
        transport = MockSSHTransport()
        transport.preset_initialize(proxy, slot=0, return_value=True)
        transport.preset_transfer("/tmp/a.wav", proxy, "/out", slot=0,
                                   TransferResult(ok=True, elapsed_sec=0.3))
        transport.preset_ensure_remote_dir(proxy, "/out", slot=0, return_value=True)

        # ... run system under test ...
        transport.verify_all_presets_consumed()  # asserts nothing left un-called
    """

    def __init__(self):
        self._init_responses: Dict[str, bool] = {}
        self._mkdir_responses: Dict[str, bool] = {}
        self._transfer_responses: Dict[str, TransferResult] = {}
        self._initialized: Set[str] = set()
        self._cleaned: Set[str] = set()
        self._calls: list = []

    # ---- Preset API (for test setup) ----

    def preset_initialize(
        self, proxy: ProxyTarget, slot: int = 0, *, return_value: bool = True
    ) -> None:
        """Preset: what initialize_connection() should return."""
        key = get_server_key(proxy.user, proxy.host, proxy.port, slot)
        self._init_responses[key] = return_value

    def preset_ensure_remote_dir(
        self,
        proxy: ProxyTarget,
        target_dir: str,
        slot: int = 0,
        *,
        return_value: bool = True,
    ) -> None:
        """Preset: what ensure_remote_dir() should return."""
        key = get_dir_key(proxy.user, proxy.host, proxy.port, target_dir)
        self._mkdir_responses[key] = return_value

    def preset_transfer(
        self,
        source_file: str,
        proxy: ProxyTarget,
        target_dir: str,
        slot: int = 0,
        *,
        return_value: TransferResult = None,
    ) -> None:
        """Preset: what transfer_file() should return."""
        if return_value is None:
            return_value = TransferResult(ok=True, elapsed_sec=0.1, stderr="")
        key = f"{source_file}|{proxy.server}|{target_dir}|{slot}"
        self._transfer_responses[key] = return_value

    # ---- SSHTransport interface ----

    def initialize_connection(self, proxy: ProxyTarget, slot: int = 0) -> bool:
        key = get_server_key(proxy.user, proxy.host, proxy.port, slot)
        self._calls.append(("initialize_connection", proxy, slot))
        if key not in self._init_responses:
            raise AssertionError(
                f"MockSSHTransport: no preset for initialize_connection {key}"
            )
        result = self._init_responses[key]
        if result:
            self._initialized.add(key)
        return result

    def cleanup_connection(self, proxy: ProxyTarget, slot: int = 0) -> None:
        key = get_server_key(proxy.user, proxy.host, proxy.port, slot)
        self._calls.append(("cleanup_connection", proxy, slot))
        self._cleaned.add(key)
        self._initialized.discard(key)

    def cleanup_all(self) -> None:
        self._calls.append(("cleanup_all",))
        for key in list(self._initialized):
            self._cleaned.add(key)
        self._initialized.clear()

    def ensure_remote_dir(
        self, proxy: ProxyTarget, target_dir: str, slot: int = 0
    ) -> bool:
        key = get_dir_key(proxy.user, proxy.host, proxy.port, target_dir)
        self._calls.append(("ensure_remote_dir", proxy, target_dir, slot))
        if key not in self._mkdir_responses:
            raise AssertionError(
                f"MockSSHTransport: no preset for ensure_remote_dir {key}"
            )
        return self._mkdir_responses[key]

    def transfer_file(
        self,
        source_file: str,
        proxy: ProxyTarget,
        target_dir: str,
        slot: int = 0,
    ) -> TransferResult:
        key = f"{source_file}|{proxy.server}|{target_dir}|{slot}"
        self._calls.append(("transfer_file", source_file, proxy, target_dir, slot))
        if key not in self._transfer_responses:
            raise AssertionError(
                f"MockSSHTransport: no preset for transfer_file {key}"
            )
        return self._transfer_responses[key]

    def get_control_path(self, proxy: ProxyTarget, slot: int = 0) -> str:
        self._calls.append(("get_control_path", proxy, slot))
        return f"/fake/control_{proxy.user}_at_{proxy.host}_{proxy.port}_{slot}"

    # ---- Verification helpers ----

    def verify_all_presets_consumed(self) -> None:
        """Raise AssertionError if any preset was never called."""
        unconsumed_init = set(self._init_responses) - {
            get_server_key(p.user, p.host, p.port, s)
            for (name, *args) in self._calls
            if name == "initialize_connection"
            for (p, s) in [(args[0], args[1] if len(args) > 1 else 0)]
        }
        unconsumed_transfer = set(self._transfer_responses) - {
            f"{sf}|{p.server}|{td}|{s}"
            for (name, *args) in self._calls
            if name == "transfer_file"
            for (sf, p, td, s) in [(args[0], args[1], args[2], args[3] if len(args) > 3 else 0)]
        }
        if unconsumed_init:
            raise AssertionError(f"Unconsumed init presets: {unconsumed_init}")
        if unconsumed_transfer:
            raise AssertionError(f"Unconsumed transfer presets: {unconsumed_transfer}")

    @property
    def call_log(self) -> list:
        """Return the list of all method calls made."""
        return list(self._calls)

    @property
    def initialized_count(self) -> int:
        return len(self._initialized)

    @property
    def cleaned_count(self) -> int:
        return len(self._cleaned)