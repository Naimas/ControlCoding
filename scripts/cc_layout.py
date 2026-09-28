"""Fixed, opt-in storage routing for adopter projects (stdlib only).

Callers must explicitly identify managed artifacts. Application sources, Git and
host-discovered adapters always remain relative to the original project root.
No reads initialize storage, and invalid metadata never falls back to legacy.
This module is also distributed beside the installed hook scripts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import stat
from contextlib import contextmanager
from contextvars import ContextVar

MARKER = "cc/layout.json"
MARKER_VALUE = {"schema": 1, "layout": "contained"}
MARKER_BYTES = b'{"schema":1,"layout":"contained"}\n'
MAX_MARKER_BYTES = 1024
_preview = ContextVar("cc_layout_preview", default=None)
_recovery = ContextVar("cc_layout_recovery", default=None)


class LayoutError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _ordinary(path, directory):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise LayoutError("unsafe_layout_path")
    return info


def _project(project):
    raw = os.fspath(project)
    if "\x00" in raw or raw.startswith(("\\\\", "//")) or ".." in Path(raw).parts:
        raise LayoutError("unsafe_project_path")
    return Path(os.path.abspath(raw))


def layout_marker_path(project):
    return _project(project) / MARKER


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_marker(path, initial):
    # Reparse-point rejection is enforced at the opened handle on Windows.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise LayoutError("layout_unreadable")
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle(handle)
            raise
    else:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or getattr(before, "st_file_attributes", 0) & 0x400
                or _identity(before) != _identity(initial)):
            raise LayoutError("layout_changed")
        data = stream.read(MAX_MARKER_BYTES + 1)
        if len(data) > MAX_MARKER_BYTES or _identity(before) != _identity(os.fstat(stream.fileno())):
            raise LayoutError("invalid_layout_marker")
    after = _ordinary(path, False)
    if after is None or _identity(before) != _identity(after):
        raise LayoutError("layout_changed")
    return data


def is_contained(project):
    root = _project(project)
    if _preview.get() == str(root):
        return True
    namespace = root / "cc"
    try:
        parent = _ordinary(namespace, True)
        if parent is None:
            return False
        if _recovery.get() != str(root) and any(
            _ordinary(namespace / name, False) is not None
            for name in (".migration.json", ".migration.json.pending")
        ):
            raise LayoutError("layout_recovery_required")
        marker = namespace / "layout.json"
        info = _ordinary(marker, False)
        if info is None:
            return False
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise LayoutError("invalid_layout_marker")
                result[key] = value
            return result
        value = json.loads(_read_marker(marker, info).decode("utf-8"), object_pairs_hook=pairs)
        if (type(value) is not dict or value != MARKER_VALUE
                or type(value.get("schema")) is not int):
            raise LayoutError("invalid_layout_marker")
        current = _ordinary(namespace, True)
        if current is None or (parent.st_dev, parent.st_ino) != (current.st_dev, current.st_ino):
            raise LayoutError("layout_changed")
        return True
    except LayoutError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise LayoutError("invalid_layout_marker") from None


def storage_root(project):
    root = _project(project)
    return root / "cc" if is_contained(root) else root


@contextmanager
def migration_access(project):
    """Internal recovery/status scope. Ordinary product operations stay blocked."""
    token = _recovery.set(str(_project(project)))
    try:
        yield
    finally:
        _recovery.reset(token)


@contextmanager
def preview_contained(project):
    """Trusted planner-only override; never initializes a project or changes cwd.

    The caller must not execute writers in this context. Disk metadata is
    validated first. Context-local scope prevents cross-project/thread leakage.
    """
    root = _project(project)
    is_contained(root)
    token = _preview.set(str(root))
    try:
        yield
    finally:
        _preview.reset(token)


def _relative(value):
    raw = os.fspath(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if (not raw or raw != path.as_posix() or path.is_absolute()
            or any(part in (".", "..") or part.endswith((".", " "))
                   or any(c in part for c in ':\x00<>"|?*') for part in raw.split("/"))):
        raise LayoutError("unsafe_managed_path")
    return raw


def managed_relative(project, logical):
    relative = _relative(logical)
    return "cc/" + relative if is_contained(project) else relative


def managed_path(project, logical, *parts):
    relative = _relative(logical)
    for part in parts:
        relative += "/" + _relative(part)
    root = _project(project)
    contained = is_contained(root)
    path = root / ('cc/' + relative if contained else relative)
    if contained:
        # Callers also validate at publication, but a routed path must never
        # knowingly cross an existing link, reparse point or special object.
        namespace = root / 'cc'
        for parent in reversed(path.parents):
            if parent == namespace or parent.is_relative_to(namespace):
                _ordinary(parent, True)
        try:
            entry = path.lstat()
        except FileNotFoundError:
            pass
        else:
            _ordinary(path, stat.S_ISDIR(entry.st_mode))
    return path


def logical_relative(project, physical_relative):
    relative = _relative(physical_relative)
    return relative[3:] if is_contained(project) and relative.startswith("cc/") else relative


def source_path(project, relative):
    """Resolve established managed source identities, never arbitrary dev/tools paths.

    Knowledge archives retain these logical identifiers across storage migration.
    Ordinary project documents retain their literal project-relative paths.
    """
    relative = _relative(relative)
    owned = relative.split("/")[0] in {".controlcoding", ".controlwork"} or relative in {
        "CONTROLCODING.md", "CONTROLWORK.md"}
    return managed_path(project, relative) if owned else _project(project) / relative
