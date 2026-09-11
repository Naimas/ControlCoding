"""Small stdlib lockfile helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path

SURFACE_LOCK_RELATIVE_PATH = Path(".controlcoding") / "cc_surface_lock.json"
SURFACE_LOCK_TIMEOUT_SECONDS = 5.0
SURFACE_LOCK_POLL_SECONDS = 0.05


class LockfileTimeoutError(RuntimeError):
    pass


class LockfileGuard:
    def __init__(self, path: Path, timeout_seconds: float, poll_seconds: float):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self._acquired = True
                return self
            except FileExistsError as exc:
                if time.monotonic() >= deadline:
                    raise LockfileTimeoutError(f"timed out waiting for surface lock: {self.path}") from exc
                if self.poll_seconds > 0:
                    time.sleep(self.poll_seconds)

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.release()
        except OSError:
            if exc_type is None:
                raise
        return False

    def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def lockfile_guard(path: Path, timeout_seconds: float, poll_seconds: float) -> LockfileGuard:
    return LockfileGuard(path, timeout_seconds, poll_seconds)


def surface_lockfile_path(project: Path) -> Path:
    lock_path = project / SURFACE_LOCK_RELATIVE_PATH
    return lock_path.with_name(f"{lock_path.name}.lock")


def surface_lock_guard(
    project: Path,
    timeout_seconds: float = SURFACE_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = SURFACE_LOCK_POLL_SECONDS,
) -> LockfileGuard:
    return lockfile_guard(surface_lockfile_path(project), timeout_seconds, poll_seconds)


def surface_command_locked(command=None, *, quiet_kw: str = ""):
    def decorate(target):
        def wrapped(project: Path, *args, **kwargs):
            try:
                with surface_lock_guard(project):
                    return target(project, *args, **kwargs)
            except LockfileTimeoutError as exc:
                quiet_positional = quiet_kw == "quiet" and len(args) >= 2 and bool(args[1])
                if not (quiet_kw and (bool(kwargs.get(quiet_kw)) or quiet_positional)):
                    print(f"Error: {exc}")
                return 1
        return wrapped
    return decorate(command) if command is not None else decorate


def lock_surface_commands(namespace: dict) -> None:
    for name in (
        "cmd_surface_claim",
        "cmd_surface_observe",
        "cmd_surface_request_takeover",
        "cmd_surface_resolve_takeover",
        "cmd_surface_release",
    ):
        namespace[name] = surface_command_locked(namespace[name])
    namespace["cmd_surface_heartbeat"] = surface_command_locked(namespace["cmd_surface_heartbeat"], quiet_kw="quiet")
