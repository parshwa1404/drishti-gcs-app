"""
Real SSH wiring for Panel 1 (Logging Control).

``RpiSshClient`` holds one persistent SSH connection to the RPi and tails the
active session's ``timestamps.csv`` with ``tail -F``, invoking a callback per
parsed per-frame record. It reconnects with exponential backoff on link loss
and reports a ``waiting for logger`` state while the CSV does not yet exist.

The blocking ``tail_timestamps`` loop is meant to run in a daemon thread; the
router reads the latest record from shared state for the SSE status stream.
"""

import logging
import os
import shlex
import threading
from pathlib import Path
from stat import S_ISDIR as _S_ISDIR

import paramiko
import yaml

from services.timestamps_csv import parse_timestamps_line

log = logging.getLogger("drishti.ssh")

# connection_status strings surfaced to the Panel 1 badge
STATUS_CONNECTED = "connected"
STATUS_RECONNECTING = "reconnecting"
STATUS_WAITING = "waiting for logger"
STATUS_ERROR = "error"
STATUS_DISCONNECTED = "disconnected"

_ENV_OVERRIDES = {
    "hostname": "DRISHTI_RPI_HOST",
    "username": "DRISHTI_RPI_USER",
    "key_path": "DRISHTI_RPI_KEY_PATH",
    "session_dir": "DRISHTI_RPI_SESSION_DIR",
}


def load_rpi_config(path: str | None = None) -> dict:
    """
    Load ``config/rpi.yaml`` and apply ``DRISHTI_RPI_*`` env overrides.

    Env vars take precedence over yaml so a field laptop can be pointed at a
    fresh RPi without editing files.
    """
    cfg_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config" / "rpi.yaml"
    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path) as fh:
            data = yaml.safe_load(fh) or {}

    for key, env in _ENV_OVERRIDES.items():
        val = os.getenv(env)
        if val:
            data[key] = val
    return data


def _default_ssh_factory() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def _sftp_get_recursive(sftp, remote_path: str, local_path: str) -> None:
    """Recursively download remote_path into local_path via an open SFTP channel."""
    local = Path(local_path)
    local.mkdir(parents=True, exist_ok=True)
    for entry in sftp.listdir_attr(remote_path):
        r_item = f"{remote_path.rstrip('/')}/{entry.filename}"
        l_item = local / entry.filename
        if _S_ISDIR(entry.st_mode):
            _sftp_get_recursive(sftp, r_item, str(l_item))
        else:
            sftp.get(r_item, str(l_item))


class RpiSshClient:
    """Persistent SSH tail of the RPi logger's per-frame timestamps.csv."""

    def __init__(
        self,
        hostname: str,
        username: str,
        key_path: str = '',
        password: str | None = None,
        session_dir: str = '~/drishti_sessions',
        reconnect_max_backoff_s: float = 30,
        connect_timeout_s: float = 10,
        poll_interval_s: float = 2.0,
        ssh_factory=None,
        sleep_fn=None,
    ):
        self.hostname = hostname
        self.username = username
        self.key_path = key_path
        self.password = password
        # Full per-session directory: <base>/<session_name>. timestamps.csv lives here.
        self.session_dir = session_dir
        self.reconnect_max_backoff_s = reconnect_max_backoff_s
        self.connect_timeout_s = connect_timeout_s
        self.poll_interval_s = poll_interval_s
        self._ssh_factory = ssh_factory or _default_ssh_factory
        # Default sleep is interruptible by disconnect(); tests inject their own.
        self._sleep = sleep_fn or self._stop_wait

        self._client = None
        self._connected = False
        self._stop = threading.Event()
        self.connection_status = STATUS_DISCONNECTED
        self.error_count = 0

    # ─── connection lifecycle ────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _stop_wait(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def connect(self) -> None:
        """Open the SSH connection. Raises paramiko/OSError on failure."""
        client = self._ssh_factory()
        key_filename = os.path.expanduser(self.key_path) if self.key_path else None

        kwargs: dict = dict(hostname=self.hostname, username=self.username, timeout=self.connect_timeout_s)
        if key_filename:
            kwargs['key_filename'] = key_filename
            kwargs['look_for_keys'] = False
            kwargs['allow_agent'] = False
        elif self.password:
            kwargs['password'] = self.password
            kwargs['look_for_keys'] = False
            kwargs['allow_agent'] = False
        # else: default paramiko key discovery (agent + ~/.ssh)

        client.connect(**kwargs)
        self._client = client
        self._connected = True
        self.connection_status = STATUS_CONNECTED
        # Never log key contents or the resolved key path.
        log.info("SSH connected to %s as %s", self.hostname, self.username)

    def disconnect(self) -> None:
        self._stop.set()
        self._connected = False
        self.connection_status = STATUS_DISCONNECTED
        if self._client is not None:
            try:
                self._client.close()
            except OSError:
                pass
            self._client = None

    # ─── SFTP ────────────────────────────────────────────────────────────────

    def download_session_dir(self, remote_dir: str, local_dir: str) -> None:
        """Download remote_dir (and all subdirs/files) to local_dir via SFTP.

        Expands ~ on the remote side first because SFTP doesn't run a shell.
        """
        # Expand ~ on the remote side (shlex.quote prevents shell tilde expansion).
        _, stdout, _ = self._client.exec_command(f"echo {remote_dir}")
        actual_remote = stdout.read().decode("utf-8", errors="replace").strip() or remote_dir

        sftp = self._client.open_sftp()
        try:
            _sftp_get_recursive(sftp, actual_remote, local_dir)
        finally:
            sftp.close()

    # ─── tailing ─────────────────────────────────────────────────────────────

    def _timestamps_path(self) -> str:
        return f"{self.session_dir.rstrip('/')}/timestamps.csv"

    def _remote_file_exists(self, path: str) -> bool:
        cmd = f"test -f {shlex.quote(path)} && echo 1 || echo 0"
        _stdin, stdout, _stderr = self._client.exec_command(cmd)
        return stdout.read().decode("utf-8", errors="replace").strip() == "1"

    def tail_timestamps(self, callback) -> None:
        """
        Blocking loop (run in a daemon thread): tail the active session's
        timestamps.csv and invoke ``callback(record)`` per parsed line.

        Surfaces ``waiting for logger`` until the CSV exists and reconnects with
        exponential backoff (1, 2, 4 … capped at ``reconnect_max_backoff_s``) on
        any SSH/socket error.
        """
        backoff = 1.0
        while not self._stop.is_set():
            try:
                if not self._connected:
                    self.connection_status = STATUS_RECONNECTING
                    self.connect()
                    backoff = 1.0

                path = self._timestamps_path()
                if not self._remote_file_exists(path):
                    self.connection_status = STATUS_WAITING
                    self._sleep(self.poll_interval_s)
                    continue

                self.connection_status = STATUS_CONNECTED
                _stdin, stdout, _stderr = self._client.exec_command(
                    f"tail -F -n +1 {shlex.quote(path)}"
                )
                for raw in stdout:
                    if self._stop.is_set():
                        break
                    rec = parse_timestamps_line(raw)
                    if rec is None:
                        self.error_count += 1
                        continue
                    callback(rec)
                # Stream ended (channel closed) — re-evaluate after a short beat.
                self._sleep(self.poll_interval_s)

            except (paramiko.SSHException, OSError, EOFError) as exc:
                self._connected = False
                self.connection_status = STATUS_RECONNECTING
                log.warning(
                    "SSH tail error (%s); retrying in %ss", type(exc).__name__, backoff
                )
                self._sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max_backoff_s)

    def get_logger_stdout(self, callback) -> None:
        """
        Optional second stream: tail the logger's stdout log if present.

        Mirrors ``tail_timestamps`` but for ``<session_dir>/logger.log`` and
        passes raw lines through. Best-effort; used for operator visibility, not
        parsed for structured fields.
        """
        path = f"{self.session_dir.rstrip('/')}/logger.log"
        backoff = 1.0
        while not self._stop.is_set():
            try:
                if not self._connected:
                    self.connect()
                    backoff = 1.0
                if not self._remote_file_exists(path):
                    self._sleep(self.poll_interval_s)
                    continue
                _stdin, stdout, _stderr = self._client.exec_command(
                    f"tail -F -n 0 {shlex.quote(path)}"
                )
                for raw in stdout:
                    if self._stop.is_set():
                        break
                    callback(raw.rstrip("\n"))
                self._sleep(self.poll_interval_s)
            except (paramiko.SSHException, OSError, EOFError):
                self._connected = False
                self._sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max_backoff_s)
