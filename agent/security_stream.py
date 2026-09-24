"""Bounded, nonblocking event reader for the explicit pinned security profile."""

import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass, field

from agent.errors import AgentError

KEYS = ("input", "output", "reasoning", "cache_read", "cache_write", "total")


@dataclass
class SecurityStream:
    code: int = 0
    reason: str | None = None
    counts: dict = field(default_factory=lambda: dict.fromkeys(KEYS))
    steps: int = 0
    tool_calls: int = 0
    text: str = ""
    timed_out: bool = False


def terminate_group(proc):
    # Always kill the group, including children surviving a completed group leader.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
    proc.wait(timeout=2)


def run_stream(
    cmd,
    cwd,
    *,
    env,
    max_seconds,
    max_tokens,
    max_steps,
    max_tools,
    max_bytes,
    max_context,
    max_output_tokens,
    allowed_tools=frozenset({"read", "glob", "grep", "list"}),
    tick=None,
):
    result = SecurityStream()
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )  # Fixed argv, no shell.
    buffered = b""
    byte_count, starts = 0, 0
    final_reason = None
    seen = {}
    missing = set()

    def fail(code, reason, timed_out=False):
        result.code, result.reason, result.timed_out = code, reason, timed_out

    def consume(raw):
        nonlocal starts, final_reason
        try:
            e = json.loads(raw)
            if not isinstance(e, dict):
                raise TypeError()
            typ, part = e.get("type"), e.get("part", {})
            if not isinstance(part, dict):
                raise TypeError()
            identity = part.get("id")
            if identity:
                key = (typ, identity)
                if key in seen:
                    if seen[key] != raw:
                        raise ValueError()
                    return
                seen[key] = raw
            if typ == "step_start":
                starts += 1
                result.text = ""
                final_reason = None
                if starts > max_steps:
                    fail(124, "Security step ceiling exceeded")
            elif typ == "text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise ValueError()
                result.text += text
            elif typ == "error":
                fail(4, "OpenCode reported a provider or execution error")
            elif typ == "tool_use":
                result.tool_calls += 1
                if (
                    part.get("tool") not in allowed_tools
                    or part.get("state", {}).get("status") != "completed"
                ):
                    fail(4, "OpenCode used a forbidden or unsuccessful tool")
                elif result.tool_calls > max_tools:
                    fail(124, "Security tool ceiling exceeded")
            elif typ == "step_finish":
                result.steps += 1
                t = part.get("tokens", {})
                cache = t.get("cache", {})
                values = {
                    "input": t.get("input"),
                    "output": t.get("output"),
                    "reasoning": t.get("reasoning"),
                    "cache_read": cache.get("read"),
                    "cache_write": cache.get("write"),
                    "total": t.get("total"),
                }
                for key, value in values.items():
                    if value is None:
                        missing.add(key)
                    elif type(value) is not int or not 0 <= value <= 10_000_000:
                        raise ValueError()
                    # Retain captured categories even if another step omits a counter.
                    if value is not None:
                        result.counts[key] = (result.counts[key] or 0) + value
                normalized = sum(v or 0 for k, v in values.items() if k != "total")
                if values["total"] is not None and values["total"] < normalized:
                    # Never publish an internally inconsistent total.
                    missing.add("total")
                    fail(4, "OpenCode usage categories are inconsistent")
                cumulative = max(
                    sum(v or 0 for k, v in result.counts.items() if k != "total"),
                    result.counts["total"] or 0,
                )
                if cumulative > max_tokens:
                    fail(124, "Security cumulative token ceiling exceeded")
                if (
                    sum(values[k] or 0 for k in ("input", "cache_read", "cache_write"))
                    > max_context
                ):
                    fail(124, "Security context ceiling exceeded")
                if (
                    sum(values[k] or 0 for k in ("output", "reasoning"))
                    > max_output_tokens
                ):
                    fail(124, "Security output token ceiling exceeded")
                if result.steps > max_steps:
                    fail(124, "Security step ceiling exceeded")
                final_reason = part.get("reason")
            elif typ not in {"reasoning"}:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            fail(4, "Malformed OpenCode event stream")

    try:
        with selectors.DefaultSelector() as sel:
            for pipe in (proc.stdout, proc.stderr):
                os.set_blocking(pipe.fileno(), False)
                sel.register(pipe, selectors.EVENT_READ)
            while sel.get_map() and not result.code:
                if tick is not None:
                    try:
                        tick()
                    except AgentError as error:
                        fail(error.exit_code, str(error))
                        break
                    except (OSError, ValueError, TypeError, KeyError):
                        fail(4, "Execution boundary callback failed")
                        break
                remaining = max_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    fail(124, "Security wall-clock ceiling exceeded", True)
                    break
                for key, _ in sel.select(min(remaining, 0.1)):
                    data = os.read(key.fileobj.fileno(), 16384)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    byte_count += len(data)
                    if byte_count > max_bytes:
                        fail(124, "Security event/output byte ceiling exceeded")
                        break
                    if key.fileobj is proc.stderr:
                        continue  # Drain but never retain or log provider diagnostics.
                    buffered += data
                    while b"\n" in buffered and not result.code:
                        line, buffered = buffered.split(b"\n", 1)
                        if line.strip():
                            consume(line)
            if buffered.strip() and not result.code:
                consume(buffered)
            if not result.code:
                remaining = max_seconds - (time.monotonic() - started)
                try:
                    rc = proc.wait(timeout=max(0.001, remaining))
                except subprocess.TimeoutExpired:
                    fail(124, "Security wall-clock ceiling exceeded", True)
                else:
                    if rc != 0:
                        fail(4, "OpenCode exited unsuccessfully")
                    elif missing or not result.steps or starts != result.steps:
                        fail(4, "OpenCode usage or step evidence is incomplete")
                    elif final_reason != "stop":
                        fail(4, "OpenCode did not finish normally")
    finally:
        terminate_group(proc)
        proc.stdout.close()
        proc.stderr.close()
    # Partial sums survive; the total is unknown if any step omitted usage.
    if missing:
        result.counts["total"] = None
    return result
