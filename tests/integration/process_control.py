from __future__ import annotations

import contextlib
import os
import signal
import subprocess

FORCE_KILL_SIGNAL = getattr(signal, "SIGKILL", 9)


def signal_process_group(process_id: int, signal_number: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(  # type: ignore[attr-defined]
            process_id,
            signal_number,
        )


def run_bounded(
    command: list[str],
    *,
    timeout: float,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        signal_process_group(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            signal_process_group(process.pid, FORCE_KILL_SIGNAL)
            stdout, stderr = process.communicate(timeout=5)
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout,
            stderr=stderr,
        ) from error
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )
