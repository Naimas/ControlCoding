"""Versioned input identity. Endpoint observations, not an atomic snapshot.

No Git diff, content filters, index refresh, network fetch or project imports.
No source contents, environment values or per-file inventory leave this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import configparser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import sys
import tempfile
import time
import uuid

from cc_evidence_process import run_command

POLICY_VERSION = 1
MAX_ENTRIES = 10000
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_SECONDS = 30
MANDATORY = (
    "controlcoding.verification.json", "controlcoding.invariants.json",
    *(f"{root}/{name}" for root in (".controlcoding", ".claude")
      for name in ("verification.json", "capabilities.json", "cc_config.json", "settings.json")),
)
# Always excluded, including tracked runtime outputs. Other private/generated
# exclusions apply only to untracked/archive discovery, not ordinary tracked files.
ALWAYS = {".git", "verification_receipts", "invariant_receipts",
          "verification_tmp", "invariant_tmp"}
GENERATED = {"_work", ".controlwork", ".controlcoding", ".claude", ".codex",
             ".agents", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             ".venv", "venv", "node_modules", "build", "dist", ".tox", ".nox"}
PRIVATE_FILES = {"AGENTS.md", ".env"}


class EvidenceError(Exception):
    """Only stable reason codes are safe to persist."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def parts(relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise EvidenceError("unsafe_path")
    values = relative.split("/")
    if any(not p or p in (".", "..") or "\x00" in p for p in values):
        raise EvidenceError("unsafe_path")
    if any(any(0xD800 <= ord(c) <= 0xDFFF for c in p) for p in values):
        raise EvidenceError("unsupported_name")
    if os.name == "nt" and any(p.endswith((".", " ")) or
            p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                                      *(f"LPT{i}" for i in range(10))} for p in values):
        raise EvidenceError("unsupported_name")
    return values


def _state(info, *, path_comparison=False):
    # Windows path stat and fstat expose different ctime semantics on 3.13.
    # Compare ctime between two handle observations, not across those APIs.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            None if path_comparison and os.name == "nt" else info.st_ctime_ns,
            stat.S_IFMT(info.st_mode), info.st_mode & 0o111)


class Budget:
    def __init__(self, *, total=MAX_TOTAL, per_file=MAX_FILE, entries=MAX_ENTRIES, seconds=MAX_SECONDS):
        self.remaining = total
        self.per_file = per_file
        self.entries = entries
        self.deadline = time.monotonic() + seconds

    def check(self):
        if time.monotonic() > self.deadline:
            raise EvidenceError("snapshot_timeout")

    def entry(self):
        self.check()
        self.entries -= 1
        if self.entries < 0:
            raise EvidenceError("entry_limit")


class SafeRoot:
    """Retain the root and every Windows ancestor; POSIX operations use dir_fd.

    Windows handles disallow deletion/rename and reject reparse components before
    traversal. POSIX reads stay anchored even if a directory is renamed; binding
    changes invalidate the observation. No check-then-open traversal of a link.
    """
    def __init__(self, project):
        self.path = Path(os.path.abspath(project))
        self.handles = []
        self.fd = None

    def _win_open(self, path, directory=True, *, access=None):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        self._close = close
        handle = create(str(path), access if access is not None else (1 if directory else 0x80000000), 3, None, 3,
                        0x00200000 | (0x02000000 if directory else 0), None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        class Tags(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]
        get = kernel.GetFileInformationByHandleEx
        get.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        get.restype = wintypes.BOOL
        tags = Tags()
        if not get(handle, 9, ctypes.byref(tags), ctypes.sizeof(tags)):
            close(handle)
            raise EvidenceError("handle_unavailable")
        if tags.attributes & 0x400 or bool(tags.attributes & 0x10) != directory:
            close(handle)
            raise EvidenceError("unsafe_file_type")
        return handle

    def __enter__(self):
        try:
            if os.name == "nt":
                for directory in (*reversed(self.path.parents), self.path):
                    self.handles.append(self._win_open(directory))
            else:
                if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
                    raise EvidenceError("confinement_unavailable")
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                self.fd = os.open(self.path.anchor, flags)
                for component in self.path.parts[1:]:
                    child = os.open(component, flags, dir_fd=self.fd)
                    os.close(self.fd)
                    self.fd = child
            self.identity = self.path.stat() if os.name == "nt" else os.fstat(self.fd)
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *_):
        while self.handles:
            self._close(self.handles.pop())
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def validate(self):
        # Reopen each parent without following links, then compare the retained
        # root. This never substitutes a new root for the one used to read bytes.
        with SafeRoot(self.path) as other:
            if (other.identity.st_dev, other.identity.st_ino) != (self.identity.st_dev, self.identity.st_ino):
                raise EvidenceError("root_changed")

    @contextmanager
    def directory(self, relative="", *, create=False, exclusive=False):
        """Open confined parents; optionally acquire a new final component only."""
        components = parts(relative) if relative else []
        if exclusive and (not create or not components):
            raise ValueError("exclusive directory requires a nonempty creation path")
        opened = []
        current = self.path if os.name == "nt" else self.fd
        try:
            for index, component in enumerate(components):
                if create:
                    try:
                        if os.name == "nt":
                            (current / component).mkdir()
                        else:
                            os.mkdir(component, dir_fd=current)
                    except FileExistsError:
                        if exclusive and index == len(components) - 1:
                            raise EvidenceError("attempt_directory_exists")
                if os.name == "nt":
                    current = current / component
                    opened.append(self._win_open(current))
                else:
                    current = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=current)
                    opened.append(current)
            yield current
        finally:
            for handle in reversed(opened):
                self._close(handle) if os.name == "nt" else os.close(handle)

    def _stat(self, parent, name):
        return os.lstat(parent / name) if os.name == "nt" else os.stat(name, dir_fd=parent, follow_symlinks=False)

    def read(self, relative, budget, *, missing=False):
        components = parts(relative)
        budget.check()
        try:
            with self.directory("/".join(components[:-1])) as parent:
                before = self._stat(parent, components[-1])
                if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
                    raise EvidenceError("unsafe_file_type")
                if os.name == "nt":
                    import msvcrt
                    handle = self._win_open(parent / components[-1], directory=False)
                    try:
                        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                    except BaseException:
                        self._close(handle)
                        raise
                else:
                    fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    opened = os.fstat(fd)
                    if not stat.S_ISREG(opened.st_mode) or _state(before, path_comparison=True) != _state(opened, path_comparison=True):
                        raise EvidenceError("file_changed")
                    size = opened.st_size
                    if size > budget.per_file or size > budget.remaining:
                        raise EvidenceError("byte_limit")
                    result = bytearray()
                    while len(result) < size:
                        budget.check()
                        data = os.read(fd, min(65536, size - len(result), budget.remaining))
                        budget.remaining -= len(data)  # Never refunded on a later failure.
                        if not data:
                            raise EvidenceError("file_changed")
                        result.extend(data)
                    if _state(opened) != _state(os.fstat(fd)) or _state(before) != _state(self._stat(parent, components[-1])):
                        raise EvidenceError("file_changed")
                    return bytes(result), opened
                finally:
                    os.close(fd)
        except FileNotFoundError:
            if missing:
                return None
            raise

    def listing(self, relative, budget):
        with self.directory(relative) as parent:
            result = []
            with os.scandir(parent) as entries:
                for entry in entries:
                    budget.entry()
                    parts(entry.name)
                    result.append((entry.name, entry.stat(follow_symlinks=False)))
            return result

    def write_atomic(self, relative, value, *, replace_existing=True):
        components = parts(relative)
        data = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
        if len(data) > 2 * 1024 * 1024:
            raise EvidenceError("receipt_size_limit")
        with self.directory("/".join(components[:-1]), create=True) as parent:
            name = ".pending-" + uuid.uuid4().hex
            options = {} if os.name == "nt" else {"dir_fd": parent}
            target = parent / name if os.name == "nt" else name
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600, **options)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.validate()
                if not replace_existing:
                    if os.name == "nt":
                        os.link(target, parent / components[-1])
                    else:
                        os.link(name, components[-1], src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                elif os.name == "nt":
                    os.replace(target, parent / components[-1])
                else:
                    os.replace(name, components[-1], src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(target, **options)
                except FileNotFoundError:
                    pass

    def _win_remove_file(self, parent, name, expected):
        """Delete the validated handle; never chmod/unlink a reopened path."""
        import ctypes
        import msvcrt
        from ctypes import wintypes
        handle = self._win_open(parent / name, directory=False, access=0x10000 | 0x180)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self._close(handle)
            raise
        try:
            observed = os.fstat(fd)
            if _state(observed, path_comparison=True) != _state(expected, path_comparison=True):
                raise EvidenceError("cleanup_entry_changed")
            # Clearing read-only on a shared inode would change a foreign link.
            if observed.st_nlink != 1:
                raise EvidenceError("cleanup_shared_file")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get = kernel.GetFileInformationByHandleEx
            get.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
            get.restype = wintypes.BOOL
            set_info = kernel.SetFileInformationByHandle
            set_info.argtypes = get.argtypes
            set_info.restype = wintypes.BOOL
            class Basic(ctypes.Structure):
                _fields_ = [("creation", ctypes.c_longlong), ("access", ctypes.c_longlong),
                            ("write", ctypes.c_longlong), ("change", ctypes.c_longlong),
                            ("attributes", wintypes.DWORD)]
            basic = Basic()
            if not get(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
                raise ctypes.WinError(ctypes.get_last_error())
            if basic.attributes & 1:
                basic.attributes = (basic.attributes & ~1) or 0x80
                if not set_info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
                    raise ctypes.WinError(ctypes.get_last_error())
            delete = ctypes.c_ubyte(1)
            if not set_info(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            os.close(fd)

    def remove_tree(self, relative, *, expected=None):
        # No link traversal, no permission changes through lexical paths, and
        # no deletion of an observed replacement. Limits remain fail-closed.
        budget = Budget()
        def identity(info):
            return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)
        def remove(directory, expected):
            with self.directory(directory) as held:
                opened = os.stat(held) if os.name == "nt" else os.fstat(held)
                if identity(opened) != identity(expected):
                    raise EvidenceError("cleanup_entry_changed")
                with os.scandir(held) as entries:
                    listing = []
                    for entry in entries:
                        budget.entry()
                        parts(entry.name)
                        listing.append((entry.name, self._stat(held, entry.name)))
                for name, info in listing:
                    budget.check()
                    if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                        raise EvidenceError("cleanup_unsafe_entry")
                    if stat.S_ISDIR(info.st_mode):
                        remove(directory + "/" + name, info)
                    elif stat.S_ISREG(info.st_mode):
                        if os.name == "nt":
                            self._win_remove_file(held, name, info)
                        else:
                            current = self._stat(held, name)
                            if _state(current) != _state(info):
                                raise EvidenceError("cleanup_entry_changed")
                            os.unlink(name, dir_fd=held)
                    else:
                        raise EvidenceError("cleanup_unsafe_entry")
            components = parts(directory)
            with self.directory("/".join(components[:-1])) as parent:
                if identity(self._stat(parent, components[-1])) != identity(expected):
                    raise EvidenceError("cleanup_entry_changed")
                budget.check()
                if os.name == "nt":
                    os.rmdir(parent / components[-1])
                else:
                    os.rmdir(components[-1], dir_fd=parent)
        components = parts(relative)
        with self.directory("/".join(components[:-1])) as parent:
            current = self._stat(parent, components[-1])
        if expected is not None and identity(current) != identity(expected):
            raise EvidenceError("cleanup_entry_changed")
        remove(relative, current)

    def git(self, arguments, *, budget=None):
        if not getattr(self, "_git_projection", False):
            raise EvidenceError("git_projection_required")
        if budget is not None:
            budget.check()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT="0",
                   GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")
        cwd = str(self.path)
        fds = ()
        if os.name != "nt":
            for prefix in ("/proc/self/fd", "/dev/fd"):
                candidate = f"{prefix}/{self.fd}"
                if os.path.exists(candidate):
                    cwd, fds = candidate, (self.fd,)
                    break
            else:
                raise EvidenceError("git_cwd_unavailable")
        result = run_command(["git", "--no-optional-locks", "--no-replace-objects", "--no-lazy-fetch",
                              "-c", "safe.directory=" + str(self.path), "-c", "core.fsmonitor=false",
                              "-c", "core.excludesFile=" + os.devnull, *arguments],
                             cwd=cwd, env=env, pass_fds=fds,
                             timeout=min(10, budget.deadline - time.monotonic()) if budget else 10,
                             output_limit=4 * 1024 * 1024, capture_limit=4 * 1024 * 1024)
        if result["status"] == "incomplete" or not result["_captureComplete"]:
            raise EvidenceError("git_inspection_incomplete")
        return result["returnCode"], result["_captured"]["stdout"]

    @contextmanager
    def git_projection(self, budget):
        """Git sees only private, bounded data, never original repository paths.

        SafeRoot's no-follow reads apply while constructing the projection on
        both platforms. Subsequent original-path replacements cannot redirect
        any subprocess. Ordinary source contents are not copied.
        """
        with tempfile.TemporaryDirectory(prefix="cc-evidence-git-") as temporary:
            target = Path(temporary)
            if target.resolve().is_relative_to(self.path.resolve()):
                raise EvidenceError("git_temporary_root_unsupported")
            (target / ".git/objects").mkdir(parents=True)
            (target / ".git/refs").mkdir()

            def put(path, data):
                parts(path)
                dest = target.joinpath(*path.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)

            def copy_file(path, *, missing=False):
                item = self.read(path, budget, missing=missing)
                if item is not None:
                    put(path, item[0])
                return item

            for forbidden in (".git/commondir", ".git/objects/info/alternates",
                              ".git/objects/info/http-alternates"):
                if self.read(forbidden, budget, missing=True) is not None:
                    raise EvidenceError("git_metadata_unsupported")
            # Never give Git the original config: includes and path-valued
            # options could escape even a perfectly confined metadata copy.
            config = self.read(".git/config", budget, missing=True)
            parsed = configparser.RawConfigParser(strict=False)
            try:
                parsed.read_string(config[0].decode("utf-8") if config else "")
                extensions = dict(parsed.items("extensions")) if parsed.has_section("extensions") else {}
                if set(extensions) - {"objectformat"}:
                    raise EvidenceError("git_metadata_unsupported")
                fmt = extensions.get("objectformat", "sha1").strip('"').lower()
                version = parsed.get("core", "repositoryformatversion", fallback="0")
                ignorecase = parsed.get("core", "ignorecase", fallback="false").lower()
                if fmt not in ("sha1", "sha256") or version not in ("0", "1") or ignorecase not in ("true", "false"):
                    raise EvidenceError("git_metadata_unsupported")
            except (configparser.Error, UnicodeError):
                raise EvidenceError("git_config_unsupported") from None
            generated = f"[core]\nrepositoryformatversion = {1 if fmt == 'sha256' else 0}\nbare = false\nignorecase = {ignorecase}\n"
            if fmt == "sha256":
                generated += "[extensions]\nobjectformat = sha256\n"
            put(".git/config", generated.encode("ascii"))
            for path in ("HEAD", "index", "packed-refs", "shallow", "info/exclude"):
                copy_file(".git/" + path, missing=path != "HEAD")
            for name, info in self.listing(".git", budget):
                if name.startswith("sharedindex."):
                    copy_file(".git/" + name)
            for base in (".git/refs", ".git/objects"):
                pending = [base]
                while pending:
                    directory = pending.pop()
                    if directory.count("/") > 128:
                        raise EvidenceError("depth_limit")
                    for name, info in self.listing(directory, budget):
                        path = directory + "/" + name
                        if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                            raise EvidenceError("git_metadata_unsupported")
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(path)
                        elif stat.S_ISREG(info.st_mode):
                            if path in (".git/objects/info/alternates", ".git/objects/info/http-alternates"):
                                raise EvidenceError("git_metadata_unsupported")
                            copy_file(path)
                        else:
                            raise EvidenceError("git_metadata_unsupported")
            # Build names for Git's local ignore matcher. SafeRoot enumerates
            # every directory; Git never walks the original worktree.
            pending = [""]
            while pending:
                directory = pending.pop()
                if directory.count("/") > 128:
                    raise EvidenceError("depth_limit")
                for name, info in self.listing(directory, budget):
                    path = directory + "/" + name if directory else name
                    if name == ".git" and directory:
                        raise EvidenceError("nested_repository")
                    if excluded(path):
                        continue
                    if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                        raise EvidenceError("unsafe_file_type")
                    if stat.S_ISDIR(info.st_mode):
                        (target / path).mkdir(parents=True, exist_ok=True)
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        if name == ".gitignore":
                            copy_file(path)
                        else:
                            put(path, b"")
                    else:
                        raise EvidenceError("unsafe_file_type")
            self.validate()
            with SafeRoot(target) as projection:
                projection._git_projection = True
                yield projection
            self.validate()


def excluded(path, *, tracked=False):
    values = parts(path)
    if any(p in ALWAYS for p in values):
        return True
    return not tracked and (any(p in GENERATED or p.endswith(".egg-info") for p in values)
                            or values[-1] in PRIVATE_FILES or values[-1].endswith((".pyc", ".pyo")))


def input_policy(*contracts):
    extras, names = set(), set()
    for contract in contracts:
        if "evidenceInputs" not in contract:
            continue
        value = contract["evidenceInputs"]
        if not isinstance(value, dict) or value.get("schemaVersion") != 1 or set(value) - {"schemaVersion", "extraPaths", "environmentNames"}:
            raise EvidenceError("invalid_input_policy")
        paths = value.get("extraPaths", [])
        envs = value.get("environmentNames", [])
        if not isinstance(paths, list) or not isinstance(envs, list) or len(paths) + len(envs) > 128:
            raise EvidenceError("invalid_input_policy")
        for path in paths:
            if any(p in ALWAYS for p in parts(path)) or any(c in path for c in "*?[]"):
                raise EvidenceError("invalid_input_policy")
        if any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", n) for n in envs):
            raise EvidenceError("invalid_input_policy")
        if len(set(paths)) != len(paths) or len(set(envs)) != len(envs):
            raise EvidenceError("invalid_input_policy")
        extras.update(paths)
        names.update(envs)
    return {"schemaVersion": 1, "extraPaths": sorted(extras), "environmentNames": sorted(names)}


def _walk(root, budget, *, engine=False):
    result = set()
    pending = [""]
    while pending:
        directory = pending.pop()
        if directory.count("/") > 128:
            raise EvidenceError("depth_limit")
        for name, info in root.listing(directory, budget):
            path = directory + "/" + name if directory else name
            if excluded(path):
                continue
            if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                raise EvidenceError("unsafe_file_type")
            if stat.S_ISDIR(info.st_mode):
                if any(n == ".git" for n, _ in root.listing(path, budget)):
                    raise EvidenceError("nested_repository")
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                if not engine or path.endswith(".py"):
                    result.add(path)
            else:
                raise EvidenceError("unsafe_file_type")
    return result


def _records(data):
    if data and not data.endswith(b"\0"):
        raise EvidenceError("git_inventory_invalid")
    return data.split(b"\0")[:-1]


def capture_inputs(project, policy, *, budget=None):
    budget = budget or Budget()
    result = {"complete": False, "reasons": [], "scopeKind": "unknown", "scopeVersion": POLICY_VERSION,
              "policyDigest": digest([POLICY_VERSION, policy, sorted(ALWAYS), sorted(GENERATED), sorted(PRIVATE_FILES)]),
              "contentDigest": None, "fileCount": 0, "revision": None, "dirty": None}
    try:
        with SafeRoot(project) as root:
            top = dict(root.listing("", budget))
            indexed, headed = {}, {}
            if ".git" in top:
                # Worktrees/gitfiles and shared object stores are deliberately unsupported.
                if not stat.S_ISDIR(top[".git"].st_mode) or getattr(top[".git"], "st_file_attributes", 0) & 0x400:
                    raise EvidenceError("git_metadata_unsupported")
                with root.git_projection(budget) as projection:
                    def git(args):
                        code, data = projection.git(args, budget=budget)
                        if code:
                            raise EvidenceError("git_inspection_failed")
                        return data
                    fmt = git(["rev-parse", "--show-object-format"]).decode("ascii").strip()
                    if fmt not in ("sha1", "sha256"):
                        raise EvidenceError("git_object_format")
                    code, data = projection.git(["rev-parse", "--verify", "HEAD"], budget=budget)
                    head = data.decode("ascii").strip() if code == 0 else None
                    if head is None:
                        # Only a valid unborn symbolic branch is an empty HEAD.
                        ref = git(["symbolic-ref", "-q", "HEAD"]).decode("ascii").strip()
                        if not ref.startswith("refs/heads/"):
                            raise EvidenceError("git_head_invalid")
                        refcode, _ = projection.git(["show-ref", "--verify", "--quiet", ref], budget=budget)
                        if refcode != 1:
                            raise EvidenceError("git_head_invalid")
                    for record in _records(git(["ls-files", "--stage", "--sparse", "-z"])):
                        meta, name = record.split(b"\t", 1)
                        mode, oid, stage = meta.decode("ascii").split()
                        path = os.fsdecode(name)
                        parts(path)
                        if stage != "0" or mode not in ("100644", "100755") or path in indexed:
                            raise EvidenceError("git_index_unsupported")
                        indexed[path] = [mode, oid]
                    for record in _records(git(["ls-files", "-v", "-z"])):
                        if record[:1].upper() == b"S":
                            raise EvidenceError("git_sparse_unsupported")
                    if head:
                        for record in _records(git(["ls-tree", "-r", "-z", "HEAD"])):
                            meta, name = record.split(b"\t", 1)
                            mode, kind, oid = meta.decode("ascii").split()
                            if kind != "blob" or mode not in ("100644", "100755"):
                                raise EvidenceError("git_tree_unsupported")
                            headed[os.fsdecode(name)] = [mode, oid]
                    untracked = {os.fsdecode(p) for p in _records(git(["ls-files", "--others", "--exclude-standard", "-z"]))}
                paths = {p for p in set(indexed) | set(headed) if not excluded(p, tracked=True)}
                paths.update(p for p in untracked if not excluded(p))
                result.update(scopeKind="git", revision={"head": head, "objectFormat": fmt,
                              "indexDigest": digest(indexed)})
            else:
                paths = _walk(root, budget)
                untracked = set()
                result.update(scopeKind="archive", revision={"notApplicable": True})
            paths.update(MANDATORY)
            paths.update(policy["extraPaths"])
            if len(paths) > MAX_ENTRIES:
                raise EvidenceError("entry_limit")
            folded = [p.casefold() for p in paths]
            if len(set(folded)) != len(folded):
                raise EvidenceError("case_collision")
            hasher = hashlib.sha256()
            raw_dirty, deleted = False, 0
            for path in sorted(paths, key=lambda p: p.encode("utf-8")):
                budget.entry()
                item = root.read(path, budget, missing=True)
                if item is None:
                    marker = [path, "absent"]
                    if path in indexed:
                        raw_dirty = True
                        deleted += 1
                else:
                    data, info = item
                    executable = bool(info.st_mode & 0o111) if os.name != "nt" else (
                        indexed.get(path, ["100644"])[0] == "100755")
                    marker = [path, "file", executable, len(data), hashlib.sha256(data).hexdigest()]
                    if path in indexed:
                        blob = hashlib.new(result["revision"]["objectFormat"])
                        blob.update(f"blob {len(data)}\0".encode())
                        blob.update(data)
                        raw_dirty |= blob.hexdigest() != indexed[path][1] or executable != (indexed[path][0] == "100755")
                encoded = json.dumps(marker, ensure_ascii=True, separators=(",", ":")).encode()
                hasher.update(len(encoded).to_bytes(8, "big"))
                hasher.update(encoded)
            root.validate()
            result.update(complete=True, contentDigest=hasher.hexdigest(), fileCount=len(paths),
                          dirty={"worktreeVsIndex": raw_dirty if indexed or result["scopeKind"] == "git" else None,
                                 "indexVsHead": indexed != headed if result["scopeKind"] == "git" else None,
                                 "untrackedCount": len([p for p in untracked if not excluded(p)]), "deletedCount": deleted})
    except EvidenceError as exc:
        result["reasons"] = [exc.code]
    except (OSError, ValueError, TypeError, UnicodeError):
        result["reasons"] = ["input_inspection_failed"]
    return result


def read_contract(project, kind):
    names = ("controlcoding.verification.json", ".controlcoding/verification.json", ".claude/verification.json") if kind == "verification" else ("controlcoding.invariants.json",)
    with SafeRoot(project) as root:
        budget = Budget(per_file=2 * 1024 * 1024, total=4 * 1024 * 1024)
        for name in names:
            item = root.read(name, budget, missing=True)
            if item is None:
                continue
            raw = item[0]
            value = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(value, dict):
                raise EvidenceError("invalid_contract")
            root.validate()
            return value, {"kind": kind, "path": name, "sha256": hashlib.sha256(raw).hexdigest()}
    return {}, {"kind": kind, "path": names[0], "sha256": None}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def runner_context(engine_dir):
    try:
        with SafeRoot(engine_dir) as root:
            budget = Budget()
            # Source checkout: all owned Python sources. Installed distribution:
            # explicit metadata inventory, never enumerate arbitrary sys.path.
            if root.path.name == "scripts" and (root.path.parent / "pyproject.toml").is_file():
                files = _walk(root, budget, engine=True)
            else:
                distribution = importlib.metadata.distribution("controlcoding")
                files = {str(p).replace("\\", "/") for p in distribution.files or []
                         if str(p).endswith(".py") and (str(p).startswith("cc_") or str(p) == "cc.py")}
                if not {"cc.py", "cc_evidence.py", "cc_evidence_inputs.py", "cc_evidence_process.py"} <= files:
                    raise EvidenceError("engine_layout_unknown")
            records = []
            for path in sorted(files):
                records.append([path, hashlib.sha256(root.read(path, budget)[0]).hexdigest()])
            root.validate()
        dependencies = []
        # pytest and CLI startup can list the same search directory more than
        # once. Count each search location once, retaining distinct locations
        # (including conflicting installed versions) in the dependency inventory.
        search_paths = list(dict.fromkeys(os.path.normcase(os.path.abspath(p or os.curdir))
                                         for p in sys.path))
        for distribution in importlib.metadata.distributions(path=search_paths):
            budget.entry()
            name, version = distribution.metadata.get("Name"), distribution.version
            if not name or not version:
                raise EvidenceError("dependencies_unknown")
            dependencies.append([name.lower().replace("_", "-"), version])
        value = {"policyVersion": POLICY_VERSION, "engineDigest": digest(records),
                 "engineFileCount": len(records), "python": platform.python_version(),
                 "implementation": platform.python_implementation(), "os": platform.system(),
                 "architecture": platform.machine(), "dependenciesDigest": digest(sorted(dependencies)),
                 "dependencyCount": len(dependencies)}
        return {"complete": True, "digest": digest(value), **value, "reasons": []}
    except (OSError, ValueError, TypeError, EvidenceError, importlib.metadata.PackageNotFoundError):
        return {"complete": False, "digest": None, "reasons": ["runner_context_unknown"]}


def command_args(template, project, temp):
    return [os.path.expandvars(arg.replace("{project}", Path(project).as_posix()).replace("{temp}", str(temp).replace("\\", "/")))
            for arg in shlex.split(template)]


def executable_argv(argv, project):
    """Launch the same resolved executable whose location enters the context."""
    if not argv:
        raise EvidenceError("command_empty")
    executable = argv[0]
    if "/" in executable or "\\" in executable:
        executable = str(Path(project) / executable) if not Path(executable).is_absolute() else executable
    search = os.pathsep.join(os.path.abspath(Path(project) / directory)
                             for directory in os.environ.get("PATH", os.defpath).split(os.pathsep))
    resolved = shutil.which(executable, path=search)
    if not resolved:
        raise EvidenceError("executable_unresolved")
    return [os.path.abspath(resolved), *argv[1:]]


def execution_context(project, policy, entries):
    try:
        commands = []
        for entry in entries:
            template_argv = command_args(entry["command"], project, "{temp}")
            try:
                argv = executable_argv(template_argv, project)
            except EvidenceError as exc:
                if exc.code != "executable_unresolved":
                    raise
                # Known absence is measurable. An omitted optional command must
                # not block required checks; selecting it fails before launch.
                commands.append([entry["id"], digest(template_argv), None])
                continue
            commands.append([entry["id"], digest(argv), digest(argv[0])])
        value = {"projectLocationDigest": digest(os.path.normcase(os.path.abspath(project))),
                 "commandsDigest": digest(commands),
                 "environmentDigest": digest({n: os.environ.get(n) for n in policy["environmentNames"]})}
        return {"complete": True, "digest": digest(value), **value, "reasons": []}
    except EvidenceError as exc:
        return {"complete": False, "digest": None, "reasons": [exc.code]}
    except (ValueError, TypeError, OSError):
        return {"complete": False, "digest": None, "reasons": ["execution_context_unknown"]}
