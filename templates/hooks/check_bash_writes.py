#!/usr/bin/env python3
"""Report protected working-tree paths after a Bash event, without recovery.

This optional Claude Code PostToolUse hook observes protected raw worktree/index
mismatches and present untracked entries after a Bash event.
They may predate the event or belong to another actor. The hook cannot attribute
them to this command and never restores/deletes files or changes the Git index.

Exit 0 always: the tool has already run. Warnings use systemMessage for the user
and PostToolUse additionalContext for the agent. Optional hook logging remains
best-effort; an incomplete inspection is reported rather than treated as clean.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_utils import control_plane_path, find_project_root, normalize_protected_zones
except ImportError:
    from templates.hooks.hook_utils import control_plane_path, find_project_root, normalize_protected_zones

try:
    from feature_lock import check_module_perimeter
    HAS_FEATURE_LOCK = True
except ImportError:
    try:
        from templates.hooks.feature_lock import check_module_perimeter
        HAS_FEATURE_LOCK = True
    except ImportError:
        HAS_FEATURE_LOCK = False
        import sys as _sys
        print("[check_bash_writes] feature_lock.py not found - module perimeter checks disabled",
              file=_sys.stderr)


def _find_project_root():
    """Walk up from this file's directory looking for project root markers."""
    return find_project_root(__file__)


def _load_deny_zones(project_root):
    """Load DENY-level zones from cc_config.json or return empty list."""
    config_path = control_plane_path(project_root, "cc_config.json")
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return [
            z["path"] for z in normalize_protected_zones(data.get("protected_zones", []))
            if z.get("level", "warn") == "deny"
        ]

    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _is_protected(file_path, deny_zones):
    """Check if a file path falls within any DENY zone."""
    norm = file_path.replace("\\", "/")
    for zone in deny_zones:
        zone_clean = zone.rstrip("/")
        # Check if file is inside the zone directory or matches the zone file
        if norm.startswith(zone_clean + "/") or norm == zone_clean:
            return True
        # Also check if any path segment matches (for relative paths)
        parts = norm.split("/")
        zone_parts = zone_clean.split("/")
        for i in range(len(parts) - len(zone_parts) + 1):
            if parts[i:i + len(zone_parts)] == zone_parts:
                return True
    return False


class _InspectionError(RuntimeError):
    """The working-tree inspection could not be completed."""

    def __init__(self, message, *, code="inspection"):
        super().__init__(message)
        self.code = code


class _ReadBudget:
    """Charge every returned content byte to one event-wide budget."""

    def __init__(self, limit):
        self.limit = limit
        self.consumed = 0

    @property
    def remaining(self):
        return max(0, self.limit - self.consumed)

    def charge(self, count):
        self.consumed += count
        if self.consumed > self.limit:
            raise AssertionError("content read exceeded its reserved budget")


_GIT_TIMEOUT_SECONDS = 10
_MAX_TRACKED_FILE_BYTES = 8 * 1024 * 1024
_MAX_TRACKED_TOTAL_BYTES = 32 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


def _entry_parts(file_path):
    """Return Git path components that cannot escape or re-root traversal."""
    pure_path = PurePosixPath(file_path)
    parts = pure_path.parts
    if (
        not parts
        or pure_path.is_absolute()
        or any(part in ("", ".", "..") for part in parts)
        or (os.name == "nt" and any("\\" in part or ":" in part for part in parts))
    ):
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: unsafe Git path",
            code="confinement",
        )
    return parts


def _inspection_limit(file_path):
    return _InspectionError(
        f"tracked content {json.dumps(file_path)}: inspection byte limit exceeded",
        code="budget",
    )


def _read_regular_file(descriptor, file_path, object_id, opened, budget):
    """Hash exactly the opened size while charging each successful read."""
    size = opened.st_size
    if size > _MAX_TRACKED_FILE_BYTES or size > budget.remaining:
        raise _inspection_limit(file_path)

    digest = _blob_hasher(object_id, size)
    bytes_read = 0
    while bytes_read < size:
        request = min(_READ_CHUNK_BYTES, size - bytes_read, budget.remaining)
        if request <= 0:
            raise _inspection_limit(file_path)
        chunk = os.read(descriptor, request)
        budget.charge(len(chunk))
        if not chunk:
            break
        bytes_read += len(chunk)
        digest.update(chunk)
    return digest, bytes_read


def _posix_parent_descriptor(project_root, file_path):
    """Open an anchored, no-follow directory chain for a Git path."""
    parts = _entry_parts(file_path)
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in required) or not os.supports_dir_fd:
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: confined inspection is "
            "unavailable on this platform",
            code="unsupported",
        )
    if os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd:
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: confined inspection is "
            "unavailable on this platform",
            code="unsupported",
        )

    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.dup(project_root.descriptor)
        for component in parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    except (OSError, RuntimeError) as exc:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: cannot traverse confined path "
            f"({type(exc).__name__})",
            code="confinement",
        ) from exc
    return descriptor, parts[-1]


def _windows_handle_api():
    """Create configured Win32 handle functions without third-party code."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    final_name = kernel32.GetFinalPathNameByHandleW
    final_name.argtypes = (
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
    )
    final_name.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return create_file, final_name, close_handle


def _windows_final_path(final_name, handle):
    length = final_name(handle, None, 0, 0)
    if not length:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(length + 1)
    result = final_name(handle, buffer, len(buffer), 0)
    if not result or result >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    path = buffer.value
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normcase(os.path.normpath(path))


def _windows_directory_attributes(handle):
    class AttributeTagInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    get_info.restype = wintypes.BOOL
    info = AttributeTagInfo()
    if not get_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.attributes


class _InspectionRoot:
    """Own one root boundary from before index listing through the last entry."""

    def __init__(self, project_root):
        self.path = Path(os.path.abspath(project_root))
        self.descriptor = None
        self.handles = []
        self.git_cwd = str(self.path)

    def __fspath__(self):
        return self.git_cwd

    def __str__(self):
        return self.git_cwd

    def __enter__(self):
        try:
            if os.name == "nt":
                self._open_windows()
            else:
                self._open_posix()
        except (OSError, AttributeError, RuntimeError) as exc:
            self.close()
            raise _InspectionError(
                "tracked root: stable root acquisition failed "
                f"({type(exc).__name__}: {exc})", code="confinement",
            ) from exc
        return self

    def _open_windows(self):
        create_file, _final_name, self._close_handle = _windows_handle_api()
        # Lock every component, including ancestors: locking only the root
        # would still allow an ancestor junction/pathname to be substituted.
        for directory in (*reversed(self.path.parents), self.path):
            handle = create_file(
                str(directory), 0x00000001, 0x00000001 | 0x00000002, None,
                3, 0x02000000 | 0x00200000, None,
            )  # LIST_DIRECTORY; no SHARE_DELETE; BACKUP_SEMANTICS | OPEN_REPARSE_POINT
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handles.append(handle)
            attributes = _windows_directory_attributes(handle)
            if not attributes & 0x00000010 or attributes & 0x00000400:
                raise _InspectionError(
                    "root component is not a plain directory", code="confinement",
                )

    def _open_posix(self):
        required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
        if (
            any(not hasattr(os, name) for name in required)
            or os.open not in os.supports_dir_fd
            or os.stat not in os.supports_dir_fd
        ):
            raise _InspectionError("directory-relative inspection unavailable")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        self.descriptor = os.open(self.path.anchor, flags)
        for component in self.path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=self.descriptor)
            os.close(self.descriptor)
            self.descriptor = next_descriptor
        # Git must enter the same directory object, not reopen self.path.
        # pass_fds keeps this alias usable in the child through chdir/exec.
        opened = os.fstat(self.descriptor)
        for prefix in ("/proc/self/fd", "/dev/fd"):
            alias = f"{prefix}/{self.descriptor}"
            try:
                alias_state = os.stat(alias)
            except OSError:
                continue
            if (alias_state.st_dev, alias_state.st_ino) == (opened.st_dev, opened.st_ino):
                self.git_cwd = alias
                return
        raise _InspectionError("directory-descriptor Git cwd is unavailable")

    def close(self):
        if self.descriptor is not None:
            descriptor, self.descriptor = self.descriptor, None
            os.close(descriptor)
        while self.handles:
            self._close_handle(self.handles.pop())

    def __exit__(self, *_exc):
        self.close()


def _windows_project_final_path(project_root):
    return _windows_final_path(_windows_handle_api()[1], project_root.handles[-1])


def _windows_descriptor_is_confined(descriptor, project_root):
    _create_file, final_name, _close_handle = _windows_handle_api()
    target = _windows_final_path(final_name, msvcrt.get_osfhandle(descriptor))
    root = _windows_project_final_path(project_root)
    try:
        return os.path.commonpath((root, target)) == root
    except ValueError:
        return False


def _git_records(project_root, arguments, label):
    """Read NUL-delimited records from an index/listing-only Git command."""
    try:
        options = {}
        if isinstance(project_root, _InspectionRoot) and os.name != "nt":
            options["pass_fds"] = (project_root.descriptor,)
        result = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=", *arguments],
            cwd=str(project_root),
            capture_output=True, timeout=_GIT_TIMEOUT_SECONDS, **options,
        )
    except subprocess.TimeoutExpired as exc:
        raise _InspectionError(f"{label}: Git timed out") from exc
    except FileNotFoundError as exc:
        raise _InspectionError(f"{label}: Git is unavailable") from exc
    except OSError as exc:
        raise _InspectionError(f"{label}: Git could not start ({type(exc).__name__})") from exc
    if result.returncode != 0:
        raise _InspectionError(f"{label}: Git exited with status {result.returncode}")
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise _InspectionError(f"{label}: incomplete Git path output")
    return [record for record in result.stdout.split(b"\0") if record]


def _git_files(project_root, arguments, label):
    """Decode NUL-delimited Git path records without stripping path bytes."""
    return [
        os.fsdecode(path)
        for path in _git_records(project_root, arguments, label)
    ]


def _git_index_entries(project_root):
    """Enumerate index entries without refreshing the index or reading content."""
    records = _git_records(
        project_root, ["ls-files", "--stage", "-v", "-z", "--"],
        "tracked index",
    )
    entries = []
    for record in records:
        try:
            header, raw_path = record.split(b"\t", 1)
            tag, mode, object_id, stage = header.split()
            tag_text = tag.decode("ascii")
            mode_text = mode.decode("ascii")
            object_text = object_id.decode("ascii")
            stage_number = int(stage.decode("ascii"))
            int(mode_text, 8)
            int(object_text, 16)
        except (UnicodeDecodeError, ValueError) as exc:
            raise _InspectionError("tracked index: malformed Git index output") from exc
        if (
            len(tag_text) != 1
            or len(object_text) not in (40, 64)
            or stage_number not in (0, 1, 2, 3)
            or not raw_path
        ):
            raise _InspectionError("tracked index: malformed Git index output")
        entries.append(
            (tag_text, mode_text, object_text.lower(), stage_number, os.fsdecode(raw_path))
        )
    return entries


def _blob_hasher(object_id, size):
    """Create the SHA-1/SHA-256 Git blob digest implied by an index object id."""
    algorithm = "sha1" if len(object_id) == 40 else "sha256"
    digest = hashlib.new(algorithm, usedforsecurity=False)
    digest.update(b"blob " + str(size).encode("ascii") + b"\0")
    return digest


def _same_file_state(left, right, *, compare_ctime=True):
    """Compare fields that reveal replacement or an edit during inspection."""
    fields = ["st_mode", "st_size", "st_mtime_ns", "st_ino", "st_dev"]
    if compare_ctime:
        fields.append("st_ctime_ns")
    return all(
        getattr(left, field) == getattr(right, field)
        for field in fields
    )


def _posix_entry_matches_index(project_root, file_path, mode, object_id, budget):
    """Inspect through an anchored directory-descriptor traversal."""
    try:
        parent_descriptor, leaf = _posix_parent_descriptor(project_root, file_path)
    except _InspectionError:
        raise
    try:
        try:
            before = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False

        if mode == "120000":
            if not stat.S_ISLNK(before.st_mode):
                return False
            # os.readlink() has no caller-supplied byte bound. Do not weaken
            # the event budget for this special index mode.
            raise _InspectionError(
                f"tracked content {json.dumps(file_path)}: bounded symlink "
                "inspection is unavailable on this platform",
                code="unsupported",
            )

        if mode not in ("100644", "100755"):
            raise _InspectionError(
                f"tracked content {json.dumps(file_path)}: unsupported index mode {mode}"
            )
        if not stat.S_ISREG(before.st_mode):
            return False

        flags = (
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(leaf, flags, dir_fd=parent_descriptor)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                return False
            if not _same_file_state(before, opened):
                raise _InspectionError(
                    f"tracked content {json.dumps(file_path)}: path changed during inspection",
                    code="race",
                )
            digest, bytes_read = _read_regular_file(
                descriptor, file_path, object_id, opened, budget,
            )
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            bytes_read != opened.st_size
            or not _same_file_state(opened, after_open)
            or not _same_file_state(before, after_path)
        ):
            raise _InspectionError(
                f"tracked content {json.dumps(file_path)}: path changed during inspection",
                code="race",
            )
        return digest.hexdigest() == object_id
    except _InspectionError:
        raise
    except OSError as exc:
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: confined read failed "
            f"({type(exc).__name__})"
        ) from exc
    finally:
        os.close(parent_descriptor)


def _windows_entry_matches_index(project_root, file_path, mode, object_id, budget):
    """Validate the opened handle's final path before reading on Windows."""
    parts = _entry_parts(file_path)
    full_path = Path(project_root).joinpath(*parts)
    try:
        before = os.lstat(full_path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: cannot inspect path "
            f"({type(exc).__name__})"
        ) from exc

    if mode == "120000":
        if not stat.S_ISLNK(before.st_mode):
            return False
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: confined symlink inspection "
            "is unavailable on Windows",
            code="unsupported",
        )
    if mode not in ("100644", "100755"):
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: unsupported index mode {mode}"
        )
    if not stat.S_ISREG(before.st_mode):
        return False

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = os.open(full_path, flags)
    except OSError as exc:
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: cannot open path "
            f"({type(exc).__name__})"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            return False
        if not _same_file_state(before, opened, compare_ctime=False):
            raise _InspectionError(
                f"tracked content {json.dumps(file_path)}: path changed during inspection",
                code="race",
            )
        try:
            confined = _windows_descriptor_is_confined(descriptor, project_root)
        except (AttributeError, OSError) as exc:
            raise _InspectionError(
                f"tracked content {json.dumps(file_path)}: confined handle "
                f"validation is unavailable ({type(exc).__name__})",
                code="unsupported",
            ) from exc
        if not confined:
            raise _InspectionError(
                f"tracked content {json.dumps(file_path)}: opened path resolves "
                "outside project",
                code="confinement",
            )
        digest, bytes_read = _read_regular_file(
            descriptor, file_path, object_id, opened, budget,
        )
        after_open = os.fstat(descriptor)
    except _InspectionError:
        raise
    except OSError as exc:
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: read failed "
            f"({type(exc).__name__})"
        ) from exc
    finally:
        os.close(descriptor)

    try:
        after_path = os.lstat(full_path)
    except OSError as exc:
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: path changed during inspection",
            code="race",
        ) from exc
    if (
        bytes_read != opened.st_size
        or not _same_file_state(opened, after_open)
        or not _same_file_state(before, after_path, compare_ctime=False)
    ):
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: path changed during inspection",
            code="race",
        )
    return digest.hexdigest() == object_id


def _worktree_entry_matches_index(project_root, file_path, mode, object_id, budget):
    """Compare one entry as raw Git blob bytes without invoking Git conversion."""
    if not isinstance(project_root, _InspectionRoot):
        with _InspectionRoot(project_root) as root:
            return _worktree_entry_matches_index(root, file_path, mode, object_id, budget)
    if mode == "160000":
        raise _InspectionError(
            f"tracked content {json.dumps(file_path)}: protected submodule "
            "content is not inspected",
            code="unsupported",
        )
    before = budget.consumed
    if os.name == "nt":
        matches = _windows_entry_matches_index(
            project_root, file_path, mode, object_id, budget,
        )
    else:
        matches = _posix_entry_matches_index(
            project_root, file_path, mode, object_id, budget,
        )
    return matches, budget.consumed - before


def _git_changed_files(project_root, should_inspect=lambda _path: True):
    """Find protected raw worktree/index mismatches without content filters."""
    if not isinstance(project_root, _InspectionRoot):
        with _InspectionRoot(project_root) as root:
            return _git_changed_files(root, should_inspect)
    grouped = {}
    for entry in _git_index_entries(project_root):
        grouped.setdefault(entry[4], []).append(entry)

    changed = []
    errors = []
    budget = _ReadBudget(_MAX_TRACKED_TOTAL_BYTES)
    for file_path in sorted(grouped):
        if not should_inspect(file_path):
            continue
        entries = grouped[file_path]
        stage_zero = [entry for entry in entries if entry[3] == 0]
        if len(entries) != 1 or len(stage_zero) != 1:
            changed.append(file_path)
            continue
        tag, mode, object_id, _stage, _path = stage_zero[0]
        try:
            parts = _entry_parts(file_path)
        except _InspectionError as exc:
            errors.append(str(exc))
            continue
        full_path = Path(project_root).joinpath(*parts)
        if tag.upper() == "S" and not os.path.lexists(full_path):
            # An absent skip-worktree entry is intentional, not a deletion.
            continue
        try:
            matches, _consumed = _worktree_entry_matches_index(
                project_root, file_path, mode, object_id, budget,
            )
        except _InspectionError as exc:
            errors.append(str(exc))
            continue
        if not matches:
            changed.append(file_path)
    return changed, errors


def _git_untracked_files(project_root):
    """Observe non-ignored untracked paths without content conversion."""
    return _git_files(
        project_root, ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        "untracked paths",
    ), []


def _emit_warning(message):
    """Emit one documented Claude Code warning/context object, without blocking."""
    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        },
    }))


# Optional: import hook_logger if available
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_logger import log_event
    HAS_LOGGER = True
except ImportError:
    HAS_LOGGER = False


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if not isinstance(input_data, dict):
        sys.exit(0)

    # Only process Bash tool calls
    tool_name = input_data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    project_root = _find_project_root()
    deny_zones = _load_deny_zones(project_root)

    # Index listing is separated from bounded raw filesystem comparison so Git
    # never applies a configured content filter while this hook is inspecting.
    reasons = {}

    def protection_reason(file_path):
        if file_path in reasons:
            return reasons[file_path]
        reason = ""
        if _is_protected(file_path, deny_zones):
            reason = "DENY zone"
        elif HAS_FEATURE_LOCK:
            fl_result = check_module_perimeter(file_path, project_root)
            if fl_result["decision"] == "deny":
                reason = "module perimeter: " + fl_result["message"]
        reasons[file_path] = reason
        return reason

    paths = []
    errors = []
    try:
        changed, tracked_errors = _git_changed_files(
            project_root, lambda path: bool(protection_reason(path)),
        )
        paths.extend(changed)
        errors.extend(tracked_errors)
    except _InspectionError as exc:
        errors.append(str(exc))
    try:
        untracked, untracked_errors = _git_untracked_files(project_root)
        paths.extend(untracked)
        errors.extend(untracked_errors)
    except _InspectionError as exc:
        errors.append(str(exc))

    observations = []
    for file_path in sorted(set(paths)):
        reason = protection_reason(file_path)
        if reason:
            observations.append(f"{json.dumps(file_path)} ({reason})")
            if HAS_LOGGER:
                log_event(project_root, "WARN", "bash_worktree_observation", file_path,
                          "Protected path observed; authorship unknown; " + reason)

    messages = []
    if observations:
        messages.append(
            "CONTROL CODING: Protected working-tree paths observed after a Bash event:\n"
            + "\n".join(observations)
            + "\nThese changes may predate this command or belong to another actor. "
            "This hook cannot attribute them to the command and has not restored or "
            "deleted files. A tracked path is listed for a raw worktree/index mismatch, "
            "which can also reflect Git normalization or smudge-filtered content. "
            "Review the listed paths and their diffs with the user before deciding "
            "how to resolve them."
        )
    if errors:
        messages.append(
            "CONTROL CODING: Working-tree inspection incomplete: " + "; ".join(errors)
            + ". Check Git availability and repository access, then inspect again. "
            "No recovery was attempted."
        )
        if HAS_LOGGER:
            log_event(project_root, "ERROR", "bash_worktree_inspection", "", "; ".join(errors))
    if messages:
        _emit_warning("\n".join(messages))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        try:
            _root = _find_project_root()
            if HAS_LOGGER:
                log_event(_root, "ERROR", Path(__file__).name,
                          str(exc), "Hook crashed - failing open")
        except Exception:
            pass
        _emit_warning(
            "CONTROL CODING: Working-tree inspection incomplete: "
            f"hook error ({type(exc).__name__}). No recovery was attempted."
        )
        sys.exit(0)
