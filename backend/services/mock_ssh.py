"""
Fake paramiko-style SSH client for tests.

Previously Panel 1 ran entirely on a mocked connect/start/stop path. That mock
is no longer the production path (see ``ssh_client.RpiSshClient``), but a fake
SSH transport is still useful for exercising the real client without a live
RPi. Inject :class:`FakeSSHClient` via ``RpiSshClient(ssh_factory=...)``.
"""


class _FakeChannelFile:
    """Stand-in for paramiko's ChannelFile: iterable by line, plus read()."""

    def __init__(self, lines: list[str]):
        self._lines = list(lines)

    def __iter__(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return "".join(self._lines).encode()


class FakeSSHClient:
    """
    Minimal paramiko.SSHClient replacement.

    - ``connect`` raises ``connect_error`` if set, else records the call.
    - ``exec_command`` answers ``test -f`` with file_exists and ``tail`` with
      ``tail_lines`` (or raises ``tail_error`` to simulate a dropped channel).
    """

    def __init__(
        self,
        tail_lines: list[str] | None = None,
        file_exists: bool = True,
        connect_error: Exception | None = None,
        tail_error: Exception | None = None,
    ):
        self.tail_lines = tail_lines or []
        self.file_exists = file_exists
        self.connect_error = connect_error
        self.tail_error = tail_error
        self.connect_calls = 0
        self.tail_calls = 0
        self.closed = False

    def set_missing_host_key_policy(self, policy):  # pragma: no cover - noop
        pass

    def connect(self, **kwargs):
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    def exec_command(self, command: str):
        if "test -f" in command:
            return None, _FakeChannelFile(["1" if self.file_exists else "0"]), _FakeChannelFile([])
        if "tail" in command:
            self.tail_calls += 1
            if self.tail_error is not None:
                raise self.tail_error
            # Consume-once: a re-exec after the stream drains yields nothing,
            # mirroring tail -F replaying existing content a single time.
            lines, self.tail_lines = self.tail_lines, []
            return None, _FakeChannelFile(lines), _FakeChannelFile([])
        return None, _FakeChannelFile([]), _FakeChannelFile([])

    def close(self):
        self.closed = True
