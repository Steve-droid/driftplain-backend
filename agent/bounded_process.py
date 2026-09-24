"""Fixed argv, bounded combined output, wall clock and whole process-group cleanup."""

import os
import selectors
import subprocess
import time
from dataclasses import dataclass

from agent.security_stream import terminate_group


@dataclass
class ProcessResult:
    code: int
    output: bytes
    duration_ms: int
    reason: str | None = None


def run_bounded(argv, *, seconds=30, max_bytes=1048576, cwd=None, env=None):
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    chunks = []
    size = 0
    reason = None
    code = 4
    try:
        with selectors.DefaultSelector() as sel:
            for pipe in (proc.stdout, proc.stderr):
                os.set_blocking(pipe.fileno(), False)
                sel.register(pipe, selectors.EVENT_READ)
            while sel.get_map():
                remaining = seconds - (time.monotonic() - started)
                if remaining <= 0:
                    reason = "wall-clock limit exceeded"
                    break
                for key, _ in sel.select(min(0.1, remaining)):
                    data = os.read(key.fileobj.fileno(), 16384)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    size += len(data)
                    if size > max_bytes:
                        reason = "output byte limit exceeded"
                        break
                    chunks.append(data)
                if reason:
                    break
            if not reason:
                try:
                    code = proc.wait(
                        timeout=max(0.001, seconds - (time.monotonic() - started))
                    )
                except subprocess.TimeoutExpired:
                    reason = "wall-clock limit exceeded"
    finally:
        terminate_group(proc)
        proc.stdout.close()
        proc.stderr.close()
    return ProcessResult(
        code, b"".join(chunks), int((time.monotonic() - started) * 1000), reason
    )
