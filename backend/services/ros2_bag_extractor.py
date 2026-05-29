"""
Run extract_ros2_bag.py on a remote host via SSH, then SFTP the output back.

Usage (called from routers/session.py fetch-remote endpoint):
    extract_and_download(ssh_client, remote_bag_dir, local_dest_dir)
"""
import shlex
from pathlib import Path

_SCRIPT_LOCAL = Path(__file__).resolve().parents[1] / "scripts" / "extract_ros2_bag.py"
_SCRIPT_REMOTE = "/tmp/drishti_extract_ros2_bag.py"


def _expand_path(ssh_client, path: str) -> str:
    """Expand ~ on the remote side (shlex.quote would suppress shell tilde expansion)."""
    _, stdout, _ = ssh_client._client.exec_command(f"echo {path}")
    return stdout.read().decode().strip() or path


def is_ros2_bag(ssh_client, remote_dir: str) -> bool:
    """Return True if remote_dir contains a .db3 bag file."""
    abs_dir = _expand_path(ssh_client, remote_dir)
    _, stdout, _ = ssh_client._client.exec_command(
        f"find {shlex.quote(abs_dir)} -maxdepth 1 -name '*.db3' 2>/dev/null | head -1"
    )
    return bool(stdout.read().decode().strip())


def extract_and_download(ssh_client, remote_bag_dir: str, local_dest_dir: str) -> None:
    """
    1. Upload the extractor script to the remote host.
    2. Find the .db3 file, run the extractor (produces frames/ + gps.nmea + timestamps.csv).
    3. SFTP the extracted directory back to local_dest_dir.
    """
    abs_bag_dir = _expand_path(ssh_client, remote_bag_dir)

    sftp = ssh_client._client.open_sftp()
    try:
        sftp.put(str(_SCRIPT_LOCAL), _SCRIPT_REMOTE)
    finally:
        sftp.close()

    # Find the .db3 inside the bag dir
    _, stdout, _ = ssh_client._client.exec_command(
        f"find {shlex.quote(abs_bag_dir)} -maxdepth 1 -name '*.db3' 2>/dev/null | head -1"
    )
    db3_path = stdout.read().decode('utf-8', errors='replace').strip()
    if not db3_path:
        raise ValueError(f"No .db3 file found in {abs_bag_dir}")

    bag_name = abs_bag_dir.rstrip('/').rsplit('/', 1)[-1]
    remote_out = f"/tmp/drishti_extracted_{bag_name}"

    # Run the extractor (blocks until complete)
    cmd = (f"python3 {shlex.quote(_SCRIPT_REMOTE)} "
           f"{shlex.quote(db3_path)} {shlex.quote(remote_out)}")
    _, stdout, stderr = ssh_client._client.exec_command(cmd)
    stdout_text = stdout.read().decode('utf-8', errors='replace')
    stderr_text = stderr.read().decode('utf-8', errors='replace')
    exit_code   = stdout.channel.recv_exit_status()

    if exit_code != 0:
        raise RuntimeError(
            f"Bag extraction failed (exit {exit_code}):\n"
            f"stdout: {stdout_text[:300]}\nstderr: {stderr_text[:300]}"
        )

    # SFTP download the extracted dir
    from services.ssh_client import _sftp_get_recursive
    sftp = ssh_client._client.open_sftp()
    try:
        _sftp_get_recursive(sftp, remote_out, local_dest_dir)
    finally:
        sftp.close()

    # Clean up remote temp dir
    ssh_client._client.exec_command(f"rm -rf {shlex.quote(remote_out)}")
