"""Small stdlib-only lockfile helper for adopter scripts."""

import math
import os
import time
from pathlib import Path


DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_LOCK_POLL_SECONDS = 0.05


class LockfileTimeoutError(RuntimeError):
    pass


def _finite_nonnegative(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


class LockfileGuard:
    def __init__(
        self,
        lock_path: Path,
        *,
        timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    ):
        self.lock_path = Path(lock_path)
        self.timeout_seconds = _finite_nonnegative(timeout_seconds, "timeout_seconds")
        self.poll_seconds = _finite_nonnegative(poll_seconds, "poll_seconds")
        self._acquired = False

    def __enter__(self):
        if self._acquired:
            raise RuntimeError("lockfile guard is already acquired")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                try:
                    self._acquired = True
                finally:
                    os.close(descriptor)
                return self
            except FileExistsError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockfileTimeoutError(
                        f"timed out waiting for lock: {self.lock_path}"
                    ) from exc
                if self.poll_seconds > 0:
                    time.sleep(min(self.poll_seconds, remaining))

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
        try:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
        finally:
            self._acquired = False


def adjacent_lockfile_path(resource_path: Path) -> Path:
    resource_path = Path(resource_path)
    return resource_path.with_name(f"{resource_path.name}.lock")


def lockfile_guard(
    lock_path: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
) -> LockfileGuard:
    return LockfileGuard(
        lock_path,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def adjacent_lockfile_guard(
    resource_path: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
) -> LockfileGuard:
    return lockfile_guard(
        adjacent_lockfile_path(resource_path),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
