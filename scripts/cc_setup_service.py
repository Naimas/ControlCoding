"""Bounded, read-only setup observations and canonical minimal-init previews.

See docs/setup-service-contract.md. No transport, apply, or provider operations.
Core is imported lazily; importing this module does not inspect a project.
"""

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import marshal
import os
from pathlib import Path
import stat
import sys


SCHEMA_VERSION = 1
SERVICE_ID = "cc-setup-service/1"
_MIB = 1024 * 1024


class SetupServiceError(Exception):
    """Safe operation failure; raw OS/parser/config exceptions are never returned."""

    def __init__(self, code, source="request"):
        self.code = code
        self.source = source
        super().__init__(f"{code}: {source}")

    def as_dict(self):
        return {"schema_version": SCHEMA_VERSION, "service": SERVICE_ID,
                "status": "error", "error": {"code": self.code, "source": self.source}}


@dataclass(frozen=True)
class ReadPolicy:
    """Callers may lower, but never raise, the service's hard limits."""

    file_bytes: int = _MIB
    target_bytes: int = 8 * _MIB
    trusted_bytes: int = 8 * _MIB
    input_count: int = 256

    def __post_init__(self):
        for name, ceiling in (("file_bytes", _MIB), ("target_bytes", 8 * _MIB),
                              ("trusted_bytes", 8 * _MIB), ("input_count", 256)):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= ceiling:
                raise SetupServiceError("invalid_policy")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _signature(s, directory=False):
    # Windows path stat infers executable bits from .cmd/.exe extensions, while
    # descriptor stat does not. These synthetic bits are not file permissions.
    mode = s.st_mode & ~0o111 if os.name == 'nt' and not directory else s.st_mode
    base = (s.st_dev, s.st_ino, mode)
    # Windows lstat/fstat disagree on ctime (birth vs change time) across these
    # Python versions. Pinned handles, mtime/size and full byte rechecks are used.
    change_time = 0 if os.name == "nt" else s.st_ctime_ns
    return base if directory else (*base, s.st_size, s.st_mtime_ns, change_time)


def _ordinary(s, directory, source):
    if (getattr(s, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))):
        raise SetupServiceError("unsupported_path", source)
    # A multiply-linked file may alias a private file outside the selected root.
    if not directory and s.st_nlink != 1:
        raise SetupServiceError("unsupported_path", source)


def _root(value):
    if type(value) is not str or not value or len(value) > 4096 or "\x00" in value:
        raise SetupServiceError("invalid_root")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or len(path.parts) > 64:
        raise SetupServiceError("invalid_root")
    if os.name == "nt":
        # Exclude UNC, device namespaces, ADS and Win32 alias spellings.
        if (len(path.drive) != 2 or path.drive[1] != ":"
                or any(":" in p or p.endswith((".", " ")) for p in path.parts[1:])):
            raise SetupServiceError("unsupported_path", "root")
    return path


def _windows_open(path, directory):
    """Pin entries without write/delete sharing, including directory reparse edits."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # Open the entry itself, including reparse points; inspect before any read.
    handle = create(str(path), 0x80000000, 1,
                    None, 3, 0x02000000 | 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise


class _Snapshots:
    """Descriptor-backed snapshots; all content reads are allowlisted and bounded."""

    def __init__(self, root, trusted, policy):
        self.root, self.trusted, self.policy = root, trusted, policy
        self.stack = ExitStack()
        self.entries = {}
        self.used = {"target": 0, "trusted": 0}
        self.last_source = "root"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def label(self, path):
        if path == self.root:
            return "root"
        if path.is_relative_to(self.root):
            return "target/" + path.relative_to(self.root).as_posix()
        if path in self.trusted:
            return "trusted/" + self.trusted[path]
        return "ancestor"

    def _open(self, path, directory):
        if os.name == "nt":
            return _windows_open(path, directory)
        if os.name != "posix" or os.open not in os.supports_dir_fd:
            raise SetupServiceError("unsupported_platform")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        parent = self.entries.get(path.parent)
        return os.open(path.name if parent else path, flags,
                       dir_fd=parent[2] if parent else None)

    def _entry(self, path, directory):
        source = self.label(path)
        self.last_source = source
        if path in self.entries:
            old_dir, snapshot, _ = self.entries[path]
            if old_dir != directory:
                raise SetupServiceError("unsupported_path", source)
            return snapshot
        if len(self.entries) >= self.policy.input_count:
            raise SetupServiceError("input_limit", source)
        inspected = False
        try:
            # Parents have already been opened from the volume root downward.
            # lstat avoids opening FIFOs/devices, and fstat closes replacement races.
            parent = self.entries.get(path.parent)
            if parent and parent[1] is None:
                self.entries[path] = (directory, None, None)
                return None
            if os.name == "posix" and parent:
                before = os.stat(path.name, dir_fd=parent[2], follow_symlinks=False)
            else:
                before = path.lstat()
            inspected = True
            _ordinary(before, directory, source)
            fd = self._open(path, directory)
            self.stack.callback(os.close, fd)
            opened = os.fstat(fd)
            _ordinary(opened, directory, source)
            if _signature(before, directory) != _signature(opened, directory):
                raise SetupServiceError("changed_input", source)
            if directory:
                snapshot = _signature(opened, True)
            else:
                bucket = "trusted" if path in self.trusted else "target"
                remaining = getattr(self.policy, bucket + "_bytes") - self.used[bucket]
                limit = min(self.policy.file_bytes, remaining)
                if opened.st_size > limit:
                    raise SetupServiceError("too_large", source)
                chunks, size = [], 0
                while True:
                    chunk = os.read(fd, min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > limit:
                        raise SetupServiceError("too_large", source)
                data = b"".join(chunks)
                after = os.fstat(fd)
                if _signature(opened) != _signature(after) or len(data) != after.st_size:
                    raise SetupServiceError("changed_input", source)
                self.used[bucket] += size
                snapshot = (*_signature(after), data)
            self.entries[path] = (directory, snapshot, fd)
            return snapshot
        except FileNotFoundError:
            if inspected:
                raise SetupServiceError("changed_input", source) from None
            self.entries[path] = (directory, None, None)
            return None
        except SetupServiceError:
            raise
        except OSError:
            raise SetupServiceError("inaccessible", source) from None

    def observe(self, path, observations, *, directory=False):
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise SetupServiceError("unsupported_path", "planner")
        if not path.is_relative_to(self.root) and path not in self.trusted:
            raise SetupServiceError("unsupported_path", "planner")
        for parent in reversed(path.parents):
            self._entry(parent, True)
        snapshot = self._entry(path, directory)
        observations[path] = (directory, snapshot)
        return snapshot

    def recheck(self, observations=None):
        # Rewalk from pinned parents, never traverse a newly introduced link.
        for path, (directory, snapshot, fd) in self.entries.items():
            source = self.label(path)
            try:
                parent = self.entries.get(path.parent)
                if parent and parent[1] is None:
                    continue  # Its missing parent is itself rechecked first.
                if os.name == "posix" and parent:
                    current = os.stat(path.name, dir_fd=parent[2], follow_symlinks=False)
                else:
                    current = path.lstat()
                _ordinary(current, directory, source)
                if snapshot is None or _signature(current, directory) != snapshot[:3 if directory else 6]:
                    raise SetupServiceError("changed_input", source)
                if _signature(os.fstat(fd), directory) != _signature(current, directory):
                    raise SetupServiceError("changed_input", source)
                if not directory:
                    os.lseek(fd, 0, os.SEEK_SET)
                    data = bytearray()
                    expected = snapshot[-1]
                    while len(data) <= len(expected):
                        part = os.read(fd, min(65536, len(expected) + 1 - len(data)))
                        if not part:
                            break
                        data.extend(part)
                    if data != expected or _signature(os.fstat(fd)) != _signature(current):
                        raise SetupServiceError("changed_input", source)
            except FileNotFoundError:
                if snapshot is not None:
                    raise SetupServiceError("changed_input", source) from None
            except OSError:
                raise SetupServiceError("inaccessible", source) from None

    def fingerprints(self):
        result = []
        for path, (directory, snapshot, _) in self.entries.items():
            label = self.label(path)
            if label == "ancestor":
                continue
            item = {"source": label, "kind": "directory" if directory else "file",
                    "state": "absent" if snapshot is None else "present"}
            if snapshot is not None:
                item["identity"] = list(snapshot[:3 if directory else 6])
                if not directory:
                    item.update(bytes=len(snapshot[-1]), sha256=_digest(snapshot[-1]))
            result.append(item)
        return sorted(result, key=lambda item: item["source"])


def _json_object(data, source):
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        raise SetupServiceError("invalid_json", source) from None
    if type(value) is not dict:
        raise SetupServiceError("invalid_type", source)
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 32 or count > 10000:
            raise SetupServiceError("structure_limit", source)
        if type(item) is dict:
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth + 1) for v in item)
    return value


def _request(request):
    if type(request) is not dict:
        raise SetupServiceError("invalid_request")
    allowed = {"schema_version", "operation", "project_root", "central_hooks"}
    if request.keys() - allowed:
        raise SetupServiceError("unknown_control")
    if type(request.get("schema_version")) is not int:
        raise SetupServiceError("invalid_type", "schema_version")
    if request["schema_version"] != SCHEMA_VERSION:
        raise SetupServiceError("unsupported_version")
    if type(request.get("operation")) is not str:
        raise SetupServiceError("invalid_type", "operation")
    if request["operation"] != "minimal_init":
        raise SetupServiceError("unsupported_operation")
    central = request.get("central_hooks", False)
    if type(central) is not bool:
        raise SetupServiceError("invalid_type", "central_hooks")
    if central:
        raise SetupServiceError("unsupported_operation", "central_hooks")
    root = _root(request.get("project_root"))
    if len(json.dumps(request).encode("utf-8")) > _MIB:
        raise SetupServiceError("request_limit")
    return root


def _policy(policy):
    if policy is None:
        return ReadPolicy()
    if type(policy) is not ReadPolicy:
        raise SetupServiceError("invalid_policy")
    return policy


def _core():
    import cc
    return cc


def _trusted(core):
    return {**{core.HOOKS_DIR / n: "templates/hooks/" + n for n in core.INIT_HOOKS},
            core.TEMPLATES_DIR / "CLAUDE.md.template": "templates/CLAUDE.md.template",
            core.SCRIPT_DIR / "fitness_check.py": "scripts/fitness_check.py",
            Path(__file__).absolute(): "scripts/cc_setup_service.py",
            core.SCRIPT_DIR / "cc.py": "scripts/cc.py"}


def _require_root(reader):
    if reader.observe(reader.root, {}, directory=True) is None:
        raise SetupServiceError("missing_root", "root")


def _envelope(root, operation):
    return {"schema_version": SCHEMA_VERSION, "service": SERVICE_ID,
            "operation": operation, "project_root": str(root), "status": "ok",
            "scope": "minimal_init", "apply_supported": False,
            "runtime": {"python": list(sys.version_info[:3]),
                        "core_python_supported": sys.version_info >= (3, 11),
                        "memory": "not_probed", "host_delivery": "unverified"}}


def _planner_identity(core):
    """Identify loaded planner code as well as separately observed source files."""
    names = ("_plan_init", "_validate_init_config", "_validate_init_settings",
             "_resolve_hook_commands", "merge_hooks", "_build_gitignore_block",
             "_build_repo_precommit_hook_script", "_build_repo_postcommit_hook_script",
             "_control_plane_path", "_legacy_control_plane_path", "_control_plane_dir",
             "_legacy_control_plane_dir", "_canonical_context_path", "_legacy_context_path")
    # Format 2 omits reference-sharing flags, which can change as Python interns
    # objects during execution even though the executable code is unchanged.
    return {"code_sha256": _digest(b"".join(marshal.dumps(getattr(core, n).__code__, 2) for n in names)),
            "constants_sha256": _digest(json.dumps({"hooks": core.INIT_HOOKS,
                "settings": core.BASE_SETTINGS, "canonical_dir": core.CONTROL_PLANE_DIRNAME,
                "legacy_dir": core.LEGACY_CONTROL_PLANE_DIRNAME,
                "context": core.CANONICAL_CONTEXT_FILENAME,
                "legacy_context": core.LEGACY_CONTEXT_FILENAME}, sort_keys=True).encode("utf-8"))}


def validate_setup_request(request, *, policy=None):
    """Validate the envelope and existing ordinary root, not plan compatibility."""
    root = _request(request)
    with _Snapshots(root, {}, _policy(policy)) as reader:
        _require_root(reader)
        reader.recheck()
        result = _envelope(root, "validate")
        result["inputs"] = reader.fingerprints()
        return result


def _read_sources(reader, core):
    sources, selected = [], {}
    for name in ("cc_config.json", "settings.json"):
        for folder in (core.CONTROL_PLANE_DIRNAME, core.LEGACY_CONTROL_PLANE_DIRNAME):
            path = reader.root / folder / name
            snap = reader.observe(path, {})
            label = reader.label(path)
            item = {"source": label, "state": "absent", "selected": False}
            if snap is not None:
                value = _json_object(snap[-1], label)
                try:
                    if name == "cc_config.json":
                        core._validate_init_config(value, path)
                    else:
                        core._validate_init_settings(value, path)
                except ValueError:
                    raise SetupServiceError("invalid_config", label) from None
                item["state"] = "valid"
                if name not in selected:
                    item["selected"] = True
                    selected[name] = (label, value)
            sources.append(item)
    for name in (core.CANONICAL_CONTEXT_FILENAME, core.LEGACY_CONTEXT_FILENAME):
        path = reader.root / name
        snap = reader.observe(path, {})
        sources.append({"source": reader.label(path), "state": "absent" if snap is None
                        else "present_unvalidated"})
    return sources, selected


def read_setup_state(project_root, *, policy=None):
    """Read six fixed config/context paths; errors never become empty success."""
    root, core = _root(project_root), _core()
    with _Snapshots(root, {}, _policy(policy)) as reader:
        _require_root(reader)
        sources, selected = _read_sources(reader, core)
        config = selected.get("cc_config.json", (None, {}))[1]
        settings = selected.get("settings.json", (None, {}))[1]
        projected = {key: config[key] for key in
                     ("hooks_location", "documentation_mode", "cc_artifact_mode") if key in config}
        projected["protected_zone_count"] = len(core._normalize_protected_zones(config.get("protected_zones")))
        projected["hook_event_count"] = len(settings.get("hooks", {}))
        projected["mcp_server_count"] = len(settings.get("mcpServers", {}))
        reader.recheck()
        result = _envelope(root, "read")
        result.update(sources=sources, configuration=projected, inputs=reader.fingerprints(),
                      precedence="canonical_then_legacy", freshness="observed_only")
        return result


def preview_minimal_init(request, *, policy=None):
    """Project canonical plan bytes into safe hashes/actions; never call apply."""
    root, core = _request(request), _core()
    trusted = _trusted(core)
    with _Snapshots(root, trusted, _policy(policy)) as reader:
        _require_root(reader)
        _read_sources(reader, core)  # Bound JSON complexity before the canonical parser.
        for path in (Path(__file__).absolute(), core.SCRIPT_DIR / "cc.py"):
            if reader.observe(path, {}) is None:
                raise SetupServiceError("missing_source", reader.label(path))
        identity = _planner_identity(core)
        try:
            plan = core._plan_init(root, observe=reader.observe, recheck=reader.recheck)
        except SetupServiceError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            # Identify a known input mentioned by Core, without exposing exception
            # text. Config checks can happen after another path was observed.
            matches = [p for p in reader.entries if str(p) in str(exc)
                       and reader.label(p) not in {"root", "ancestor"}]
            source = reader.label(max(matches, key=lambda p: len(str(p)))) if matches else reader.last_source
            raise SetupServiceError("plan_conflict", source) from None
        files = []
        generated_bytes = 0
        for path, item in plan["files"].items():
            generated_bytes += len(item["data"])
            if len(item["data"]) > _MIB or generated_bytes > 8 * _MIB:
                raise SetupServiceError("output_limit", reader.label(path))
            # Retained files use observed bytes; canonical item.data can contain a
            # generated alternative that apply deliberately does not publish.
            observed = plan["observations"][path][1]
            actual = observed[-1] if item["action"] == "keep" else item["data"]
            files.append({"path": path.relative_to(root).as_posix(),
                          "action": item["action"], "reason": item["reason"],
                          "sha256": _digest(actual), "bytes": len(actual),
                          "generated_sha256": _digest(item["data"])})
        result = _envelope(root, "preview")
        result.update(planner="cc._plan_init", files=files,
                      planner_identity=identity,
                      trusted_source_root=str(core.REPO_ROOT),
                      directories=[{"path": p.relative_to(root).as_posix(), "action": action}
                                   for p, action in plan["directories"].items()],
                      inputs=reader.fingerprints(), freshness="observed_only")
        result["plan_fingerprint"] = _digest(json.dumps(result, sort_keys=True).encode("utf-8"))
        reader.recheck()
        if _planner_identity(core) != identity:
            raise SetupServiceError("changed_input", "planner")
        return result
