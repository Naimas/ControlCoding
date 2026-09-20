"""Bounded, metadata-only command execution for local evidence (stdlib only)."""

from __future__ import annotations

import os
import subprocess
import time

OUTPUT_LIMIT = 16 * 1024 * 1024
DRAIN_SECONDS = 0.5


def _available(stream) -> int:
    if os.name != "nt":
        return 65536
    import ctypes
    import msvcrt
    from ctypes import wintypes

    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
    peek.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not peek(msvcrt.get_osfhandle(stream.fileno()), None, 0, None,
                ctypes.byref(count), None):
        if ctypes.get_last_error() in (109, 232):  # broken/closing pipe
            return -1
        raise OSError("pipe inspection failed")
    return count.value


def run_command(argv, *, cwd, timeout, env=None, output_limit=OUTPUT_LIMIT,
                capture_limit=0, pass_fds=()):
    """Drain both binary pipes without communicate(), shell or reader threads.

    capture_limit is private to bounded Git inventory reads. Suite callers leave
    it at zero. A descendant retaining a pipe cannot extend cleanup indefinitely.
    Only the owned child is killed; this is not a process-tree sandbox.
    """
    started = time.monotonic()
    sizes = {"stdout": 0, "stderr": 0}
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    proc = None
    error = None
    code = None
    try:
        options = {"pass_fds": pass_fds} if os.name != "nt" else {}
        proc = subprocess.Popen(argv, cwd=cwd, env=env, shell=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=0, **options)
        streams = {"stdout": proc.stdout, "stderr": proc.stderr}
        if os.name != "nt":
            for stream in streams.values():
                os.set_blocking(stream.fileno(), False)
        exited_at = None
        while streams:
            now = time.monotonic()
            if now - started >= timeout:
                error = "timeout"
                break
            code = proc.poll()
            if code is not None:
                exited_at = exited_at or now
                if now - exited_at > DRAIN_SECONDS:
                    error = "pipe_timeout"
                    break
            progressed = False
            for name, stream in list(streams.items()):
                available = _available(stream)
                if available == 0:
                    continue
                if available < 0:
                    del streams[name]
                    continue
                try:
                    data = os.read(stream.fileno(), min(65536, available,
                                   max(1, output_limit - sum(sizes.values()) + 1)))
                except BlockingIOError:
                    continue
                if not data:
                    del streams[name]
                    continue
                progressed = True
                sizes[name] += len(data)
                remaining = capture_limit - sum(map(len, captured.values()))
                if remaining > 0:
                    captured[name].extend(data[:remaining])
                if sum(sizes.values()) > output_limit:
                    error = "output_limit"
                    break
            if error:
                break
            if not progressed:
                time.sleep(0.01)
        if not error:
            try:
                code = proc.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                error = "timeout"
    except KeyboardInterrupt:
        error = "interrupted"
    except (OSError, ValueError, TypeError):
        error = "spawn_error" if proc is None else "capture_error"
    finally:
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=DRAIN_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    error = error or "cleanup_error"
            code = proc.poll()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
    result = {
        "status": "incomplete" if error else ("passed" if code == 0 else "failed"),
        "returnCode": code,
        "durationMs": int((time.monotonic() - started) * 1000),
        "error": error,
        "stdoutTail": "", "stderrTail": "",
        "output": {"policy": "metadata-only-v1", "limitBytes": output_limit,
                   "stdoutBytes": sizes["stdout"], "stderrBytes": sizes["stderr"],
                   "discardedBytes": sum(sizes.values()), "textOmitted": True},
    }
    if capture_limit:
        result["_captured"] = {key: bytes(value) for key, value in captured.items()}
        result["_captureComplete"] = sum(sizes.values()) <= capture_limit
    return result
