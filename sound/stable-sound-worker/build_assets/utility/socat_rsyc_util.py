import logging
import os
import re
import shlex
import subprocess
import threading
import time
import atexit
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class RsyncProxy:
    user: str
    host: str
    port: int

    @property
    def server(self) -> str:
        return f"{self.user}@{self.host}:{self.port}"


_RSYNC_PROXY_LINE_RE = re.compile(
    r"^\s*(?P<user>[^@\s#]+)@(?P<host>[^:\s#]+):(?P<port>\d+)\s*$"
)

SSH_CONTROL_DIR = "/app/data/ssh_control"
SSH_CONTROL_PERSIST = "yes"
try:
    os.makedirs(SSH_CONTROL_DIR, mode=0o755, exist_ok=True)
except OSError:
    pass
_initialized_servers = set()
_created_remote_dirs = set()
_remote_dir_lock = threading.Lock()

SYNC_SLOT_COUNT = 2  # ControlMaster slots per proxy

# Cap connect stalls and half-dead links; rsync still needs a generous upper bound.
_SSH_CONN_OPTS_LIST = [
    "-o", "ConnectTimeout=5",
    "-o", "ConnectionAttempts=1",
    "-o", "ServerAliveInterval=5",
    "-o", "ServerAliveCountMax=2",
]
_SSH_INIT_TIMEOUT_SEC = 10
_SSH_MKDIR_TIMEOUT_SEC = 15
_RSYNC_COMMUNICATE_TIMEOUT_SEC = 120

SOCAT_RSYNC_PROXIES_FILE = "/app/data/socat_rsync_proxies"
LOCAL_RSYNC_PROXIES_FILE = "/app/data/local_rsync_proxies"


def _get_server_key(user: str, host: str, port: int, slot: int = 0) -> str:
    return f"{user}@{host}:{int(port)}#{int(slot)}"


def _parse_server_key(server_key: str):
    """Parse user@host:port#slot into (user, host, port, slot)."""
    base, slot_s = server_key.rsplit("#", 1)
    user_host, port_s = base.rsplit(":", 1)
    user, host = user_host.split("@", 1)
    return user, host, int(port_s), int(slot_s)


def _get_control_path(user: str, host: str, port: int, slot: int = 0) -> str:
    safe_name = (
        f"{user}@{host}:{int(port)}"
        .replace("@", "_at_")
        .replace(":", "_")
        .replace("/", "_")
    )
    return os.path.join(SSH_CONTROL_DIR, f"control_{safe_name}_{int(slot)}")


def parse_rsync_proxy_config(text: str) -> List[RsyncProxy]:
    """
    Parse `data/socat_rsync_proxy` content.

    Supported format (one per line):
      - user@host:port

    Notes:
      - Blank lines are ignored.
      - Lines starting with '#' are ignored.
      - Leading/trailing whitespace is ignored.
    """
    proxies: List[RsyncProxy] = []
    if not text:
        return proxies

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        m = _RSYNC_PROXY_LINE_RE.match(line)
        if not m:
            logging.warning(f"Invalid rsync_proxy line: {line!r}")
            continue

        user = m.group("user").strip()
        host = m.group("host").strip()
        port_s = m.group("port").strip()
        try:
            port = int(port_s)
        except ValueError:
            logging.warning(f"Invalid rsync_proxy port: {port_s!r}")
            continue

        if port < 1 or port > 65535:
            logging.warning(f"Out-of-range rsync_proxy port: {port}")
            continue

        proxies.append(RsyncProxy(user=user, host=host, port=port))

    return proxies


def get_socat_proxy_configs(config_file: str = SOCAT_RSYNC_PROXIES_FILE) -> List[RsyncProxy]:
    """
    Read and parse a rsync proxy config file (default: SOCAT_RSYNC_PROXIES_FILE).

    Returns:
        list[RsyncProxy]: all valid entries

    Raises:
        ValueError: if no valid proxy entries are found
    """
    proxies: List[RsyncProxy] = []
    try:
        if os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8", errors="replace") as f:
                proxies = parse_rsync_proxy_config(f.read())
    except Exception as e:
        logging.warning(f"Error reading {config_file} config: {str(e)}")

    if not proxies or len(proxies) == 0:
        raise ValueError(f"No valid {config_file} config found")

    return proxies


def get_local_proxy_configs() -> List[RsyncProxy]:
    """Read and parse LOCAL_RSYNC_PROXIES_FILE (LAN video-script TTS sync)."""
    return get_socat_proxy_configs(LOCAL_RSYNC_PROXIES_FILE)


def initialize_ssh_connection(logger, user: str, host: str, port: int, slot: int = 0) -> bool:
    """
    Initialize SSH ControlMaster for a single proxy endpoint and slot.
    """
    server_key = _get_server_key(user, host, port, slot)
    if server_key in _initialized_servers:
        logger.debug(f"init_skip server_key={server_key} reason=already_initialized")
        return True

    control_path = _get_control_path(user, host, port, slot)
    connect_server = f"{user}@{host}"
    logger.info(f"init_start server_key={server_key}")

    ssh_cmd = [
        "ssh",
        "-o", "ControlMaster=yes",
        "-o", f"ControlPath={control_path}",
        "-o", f"ControlPersist={SSH_CONTROL_PERSIST}",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "GSSAPIAuthentication=no",
        "-o", "Compression=no",
        *_SSH_CONN_OPTS_LIST,
        "-p", str(int(port)),
        connect_server,
        "echo 'SSH connection test'",
    ]

    try:
        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _, stderr = process.communicate(timeout=_SSH_INIT_TIMEOUT_SEC)
        if process.returncode == 0:
            _initialized_servers.add(server_key)
            logger.info(f"init_ok server_key={server_key}")
            return True
        logger.warning(f"init_fail server_key={server_key} stderr={stderr}")
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=5)
        except Exception:
            pass
        logger.error(f"init_fail server_key={server_key} error=timeout")
    except Exception as e:
        logger.error(f"init_fail server_key={server_key} error={str(e)}")
    return False


def initialize_ssh_connections_for_proxies(logger, proxies: List[RsyncProxy]) -> Dict[str, bool]:
    """
    Initialize SSH ControlMaster connections for all configured proxies (all slots).
    """
    if not proxies:
        raise ValueError("No valid rsync_proxy config found")

    results: Dict[str, bool] = {}
    for proxy in proxies:
        for slot in range(SYNC_SLOT_COUNT):
            server_key = _get_server_key(proxy.user, proxy.host, proxy.port, slot)
            ok = initialize_ssh_connection(logger, proxy.user, proxy.host, proxy.port, slot)
            results[server_key] = ok

    success_count = sum(1 for ok in results.values() if ok)
    total_count = len(results)
    logger.info(f"init_summary success={success_count} total={total_count} slots={SYNC_SLOT_COUNT}")
    return results


def cleanup_ssh_connections(logger):
    """
    Close all initialized SSH ControlMaster connections.
    """
    for server_key in list(_initialized_servers):
        try:
            user, host, port, slot = _parse_server_key(server_key)
            control_path = _get_control_path(user, host, port, slot)
            connect_server = f"{user}@{host}"

            if not os.path.exists(control_path):
                continue

            ssh_cmd = [
                "ssh",
                *_SSH_CONN_OPTS_LIST,
                "-O", "exit",
                "-o", f"ControlPath={control_path}",
                "-p", str(port),
                connect_server,
            ]
            subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            logger.debug(f"cleanup_ok server_key={server_key}")
        except Exception as e:
            logger.warning(f"cleanup_fail server_key={server_key} error={str(e)}")

    _initialized_servers.clear()
    with _remote_dir_lock:
        _created_remote_dirs.clear()


def _ensure_remote_dir(logger, user: str, host: str, port: int, target_dir: str, slot: int = 0) -> bool:
    """
    Ensure remote directory exists once per process/server/target_dir.
    """
    server_key = _get_server_key(user, host, port, slot)
    # Dir existence is remote-side; share cache across slots for the same host:port.
    dir_key = f"{user}@{host}:{int(port)}|{target_dir}"
    with _remote_dir_lock:
        if dir_key in _created_remote_dirs:
            return True

    control_path = _get_control_path(user=user, host=host, port=int(port), slot=slot)
    connect_server = f"{user}@{host}"

    stderr = ""
    for attempt in range(2):
        ssh_cmd = [
            "ssh",
            "-p",
            str(int(port)),
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={control_path}",
            "-o",
            f"ControlPersist={SSH_CONTROL_PERSIST}",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "BatchMode=yes",
            "-o",
            "GSSAPIAuthentication=no",
            "-o",
            "Compression=no",
            *_SSH_CONN_OPTS_LIST,
            connect_server,
            f"mkdir -p {shlex.quote(target_dir)}",
        ]

        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            _, stderr = process.communicate(timeout=_SSH_MKDIR_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                _, stderr = process.communicate(timeout=5)
            except Exception:
                pass
            logger.error(
                f"mkdir_remote_timeout server_key={server_key} target_dir={target_dir} "
                f"attempt={attempt + 1} stderr={stderr!r}"
            )
            if attempt == 0 and os.path.exists(control_path):
                try:
                    os.unlink(control_path)
                    logger.warning(
                        f"mkdir_remote_removed_stale_control server_key={server_key} path={control_path}"
                    )
                except OSError as e:
                    logger.warning(
                        f"mkdir_remote_unlink_control_fail server_key={server_key} error={e}"
                    )
                continue
            return False

        if process.returncode != 0:
            logger.error(f"mkdir_remote_fail server_key={server_key} target_dir={target_dir} stderr={stderr}")
            return False

        with _remote_dir_lock:
            _created_remote_dirs.add(dir_key)
        logger.info(f"mkdir_remote_ok server_key={server_key} target_dir={target_dir}")
        return True

    return False


def sync_files(logger, source_file, target_server, target_dir, user, port, slot: int = 0):
    """
    Sync a file to the target server using the given ControlMaster slot.
    """
    try:
        control_path = _get_control_path(user=user, host=target_server, port=int(port), slot=slot)
        server_key = _get_server_key(user=user, host=target_server, port=int(port), slot=slot)
        connect_server = f"{user}@{target_server}"
        if not _ensure_remote_dir(logger, user, target_server, int(port), target_dir, slot=slot):
            return False
        ssh_opts_str = (
            f"-p {int(port)} "
            f"-o ConnectTimeout=5 "
            f"-o ConnectionAttempts=1 "
            f"-o ServerAliveInterval=5 "
            f"-o ServerAliveCountMax=2 "
            f"-o ControlMaster=auto "
            f"-o ControlPath={control_path} "
            f"-o ControlPersist={SSH_CONTROL_PERSIST} "
            f"-o StrictHostKeyChecking=no "
            f"-o BatchMode=yes "
            f"-o GSSAPIAuthentication=no "
            f"-o Compression=no"
        )

        cmd = (
            f'rsync -rt --whole-file --partial '
            f'-e "ssh {ssh_opts_str}" '
            f'{source_file} {connect_server}:{target_dir}/'
        )
        logger.info(f"rsync_command server_key={server_key} slot={slot} cmd={cmd}")

        process = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        try:
            stdout, stderr = process.communicate(timeout=_RSYNC_COMMUNICATE_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                stdout, stderr = process.communicate(timeout=30)
            except Exception:
                stdout, stderr = "", ""
            logger.error(
                f"rsync_timeout server_key={server_key} slot={slot} "
                f"target={target_server}:{target_dir}/ stderr={stderr!r}"
            )
            return False

        if process.returncode != 0:
            logger.error(f"rsync_fail server_key={server_key} slot={slot} stderr={stderr}")
            return False

        logger.info(f"rsync_ok server_key={server_key} slot={slot} target={target_server}:{target_dir}/")
        return True
    except Exception as e:
        logger.error(f"Exception during rsync: {str(e)}")
        return False


def sync_files_to_socat_proxies(logger, source_file, target_dir, proxies: List[RsyncProxy], slot: int = 0):
    """
    Sync file to all remote proxies using the given ControlMaster slot.
    """
    if not proxies or len(proxies) == 0:
        raise ValueError("No valid rsync_proxy config found")

    ret = True
    results = {}

    file_size = 0
    if os.path.exists(source_file):
        file_size = os.path.getsize(source_file)
    if file_size < 1024:
        size_str = f"{file_size} B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.2f} KB"
    else:
        size_str = f"{file_size / (1024 * 1024):.2f} MB"

    for proxy in proxies:
        start_time = time.time()
        avg_speed_mbps = 0
        logger.info(f"Start rsync to proxy: {proxy.server}, size: {size_str}, slot={slot}")
        sync_ret = sync_files(
            logger, source_file, proxy.host, target_dir, proxy.user, proxy.port, slot=slot
        )
        end_time = time.time()
        elapsed_time = end_time - start_time

        if sync_ret:
            logger.info(f"Successfully synced to {proxy.server} slot={slot}")
            if elapsed_time > 0 and file_size > 0:
                avg_speed_mbps = (file_size / (1024 * 1024)) / elapsed_time
        else:
            logger.error(f"Failed to sync to {proxy.server} slot={slot}")

        results[proxy.server] = {"ok": sync_ret, "elapsed_s": elapsed_time}

        logger.info(
            f"Sync summary: {proxy.server} {sync_ret} | "
            f"slot={slot} | "
            f"Size: {size_str} | "
            f"Total time: {elapsed_time:.2f}s | "
            f"Avg speed: {avg_speed_mbps:.2f} MB/s"
        )

    success_count = sum(1 for r in results.values() if r.get("ok"))
    total_count = len(results)

    if success_count != total_count:
        ret = False
    logger.info(f"Sync summary: {success_count}/{total_count} servers successful slot={slot}")
    return ret


class RsyncTransport:
    """FileTransport: rsync via SSH ControlMaster to job.proxies."""

    def deliver(self, logger, job, slot: int = 0) -> Tuple[bool, str]:
        try:
            ok = sync_files_to_socat_proxies(
                logger, job.source_file, job.target_dir, job.proxies, slot=slot
            )
            if ok:
                return True, ""
            return False, f"sync_files failed task_id={job.task_id}"
        except Exception as e:
            logger.error(f"RsyncTransport.deliver exception task_id={job.task_id}: {e}")
            return False, f"sync_files exception: {e}"


def _atexit_cleanup():
    log = logging.getLogger("root")
    try:
        cleanup_ssh_connections(log)
    except Exception:
        pass


atexit.register(_atexit_cleanup)
