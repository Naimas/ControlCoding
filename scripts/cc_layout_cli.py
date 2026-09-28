"""Explicit contained storage initialization and offline, backed-up migration.

Migration never guesses ownership of application documents. Include selections
are reviewed as CC-owned artifacts; all changed bytes remain in the external
backup. The journal permits resuming publication/cleanup after interruption.
"""
from __future__ import annotations

from contextlib import ExitStack, nullcontext
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat

from cc_layout import (LayoutError, MARKER_BYTES, is_contained, _ordinary,
                       _relative, preview_contained, migration_access)

JOURNAL = "cc/.migration.json"
PENDING_JOURNAL = JOURNAL + ".pending"
ALLOWED = {".controlcoding", ".controlwork", "CONTROLCODING.md", "CONTROLWORK.md",
           "STATUS.md", "STATUS_HISTORY.md", "ROADMAP.md", "BUGS.md", "PROJECT.md", "dev", "devlog", "knowledge",
           "docs", "wiki", ".obsidian", ".bridge", "screenshots",
           "hooks", "tools", "design", "criteria", "contracts", "project-definition",
           "planning", "implementation", "acceptance", "ACCEPTANCE_CRITERIA.md",
           "controlcoding.verification.json", "controlcoding.invariants.json",
           "controlcoding.architecture.json", "controlcoding.fitness.json"}
MAX_FILES = 20000
MAX_TOTAL = 8 * 1024 ** 3


def _selection(name):
    name = _relative(name)
    return name.split('/')[0] in ALLOWED or (name.endswith('/.feature-lock.json') and
        all(not p.startswith('.') and p != 'cc' for p in name.split('/')[:-1]))


def _destination(name):
    if name.endswith('/.feature-lock.json') and name.split('/')[0] not in ALLOWED:
        return '.controlcoding/module-locks/' + name
    return name


def _root(value):
    from cc_setup_service import _root as validate
    root = validate(str(value))
    for parent in (*reversed(root.parents), root):
        if _ordinary(parent, True) is None:
            raise LayoutError("missing_project")
    return root


def _encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _ordinary_chain(path, directory=False):
    for parent in reversed(path.parents):
        _ordinary(parent, True)
    return _ordinary(path, directory)


def _file(path, owned_stream=None):
    before = _ordinary_chain(path)
    if before is None:
        raise LayoutError("migration_input_missing")
    digest = hashlib.sha256()
    with nullcontext(owned_stream) if owned_stream is not None else path.open("rb") as stream:
        stream.seek(0)
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise LayoutError("migration_input_changed")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    after = _ordinary(path, False)
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if after is None or fields(before) != fields(after):
        raise LayoutError("migration_input_changed")
    return {"sha256": digest.hexdigest(), "bytes": before.st_size,
            "mode": stat.S_IMODE(before.st_mode)}


def _inventory(root, selections):
    result, directories, total = {}, [], 0
    pending = [root / name for name in selections]
    while pending:
        path = pending.pop()
        name = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            _ordinary_chain(path, True)
            directories.append(name)
            pending.extend(sorted(path.iterdir()))
        else:
            result[name] = _file(path)
            total += result[name]["bytes"]
        if len(result) + len(directories) + len(pending) > MAX_FILES or total > MAX_TOTAL:
            raise LayoutError("migration_limit")
    return dict(sorted(result.items())), sorted(directories)


def status(project):
    root = _root(project)
    with migration_access(root):
        contained = is_contained(root)
    return {"layout": "contained" if contained else "legacy", "project": str(root),
            "storage": str(root / "cc" if contained else root),
            "marker": "cc/layout.json" if contained else None,
            "recovery_required": (root / JOURNAL).exists() or (root / PENDING_JOURNAL).exists(),
            "external_integrations": ["host instruction/configuration adapters", "Git hooks",
                                      ".gitignore", "optional editor tasks"]}


def initialization_plan(project):
    root = _root(project)
    result = status(root)
    blockers = []
    if result["layout"] == "contained":
        blockers.append("Contained storage is already active.")
    elif (root / "cc").exists():
        blockers.append("Existing cc/ is not adopted. Inspect it or recover the pending migration.")
    legacy = [n for n in (".controlcoding", ".controlwork", "CONTROLCODING.md", "CONTROLWORK.md") if (root / n).exists()]
    if legacy:
        blockers.append("Existing CC material requires migration: " + ", ".join(legacy))
    return {**result, "blockers": blockers, "creates": ["cc/", "cc/layout.json"]}


def initialize(project):
    root = _root(project)
    plan = initialization_plan(root)
    if plan["blockers"]:
        raise LayoutError("layout_initialization_conflict")
    # Both operations are create-only. A competing namespace is never adopted.
    (root / "cc").mkdir()
    with (root / "cc/layout.json").open("xb") as stream:
        stream.write(MARKER_BYTES)
        stream.flush()
        os.fsync(stream.fileno())
    return status(root)


def _backup_path(root, value):
    if not value:
        raise LayoutError("external_backup_required")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise LayoutError("unsafe_backup_path")
    path = Path(os.path.abspath(path))
    if path.is_relative_to(root) or root.is_relative_to(path):
        raise LayoutError("backup_overlaps_project")
    for parent in (*reversed(path.parents), path):
        _ordinary(parent, True)
    return path


def _adapter_changes(root, selected, files):
    """Exact generated adapters only; custom/foreign bytes remain untouched."""
    import cc
    outputs, before, blockers = {}, {}, []
    hooks = any(name.startswith('hooks/') for name in files)
    for name, builder in (("pre-commit", cc._build_repo_precommit_hook_script),
                          ("post-commit", cc._build_repo_postcommit_hook_script)):
        rel = ".git/hooks/" + name
        path = root / rel
        if not path.exists() or not hooks:
            continue
        _ordinary(path, False)
        fitness = root / "tools/fitness_check.py" if "tools/fitness_check.py" in files else None
        old = builder(root / "hooks", fitness) if name == "pre-commit" else builder(root / "hooks")
        new = builder(root / "cc/hooks", root / "cc/tools/fitness_check.py" if fitness else None) if name == "pre-commit" else builder(root / "cc/hooks")
        actual = path.read_bytes()
        if actual.decode("utf-8").replace("\r\n", "\n") == old:
            outputs[rel] = new.encode()
            before[rel] = _file(path)
        elif b"ControlCoding" in actual:
            blockers.append("Customized CC Git hook requires explicit reconciliation: " + rel)
    if "CONTROLCODING.md" in selected:
        source = (root / "CONTROLCODING.md").read_text(encoding="utf-8")
        for host in ("codex_cli", "claude_code", "cursor", "windsurf", "cline", "gemini_cli"):
            old, spec = cc._render_expected_host_context(root, host, root / "CONTROLCODING.md", source, "CONTROLCODING.md")
            if old is None:
                continue
            path = root / spec["path"]
            if not path.exists():
                continue
            _ordinary(path, False)
            data = path.read_bytes()
            if data.decode("utf-8").replace("\r\n", "\n") != old.replace("\r\n", "\n"):
                if b"controlcoding" in data.lower():
                    blockers.append("Customized context adapter requires reconciliation: " + spec["path"])
                continue
            with preview_contained(root):
                new, _ = cc._render_expected_host_context(root, host, root / "cc/CONTROLCODING.md", source, "cc/CONTROLCODING.md")
            outputs[spec["path"]] = new.encode()
            before[spec["path"]] = _file(path)
    # Host-specific hook settings and editor adapters may contain absolute paths.
    # Only known moved absolute path prefixes change; every byte delta is backed
    # up and listed in the reviewed migration plan.
    for rel in (".claude/settings.local.json", ".vscode/tasks.json", ".gitignore"):
        path = root / rel
        if not path.exists():
            if rel == '.gitignore':
                with preview_contained(root):
                    outputs[rel] = cc._build_gitignore_block(project_root=root).encode()
                before[rel] = None
            continue
        _ordinary(path, False)
        data = path.read_bytes()
        if rel == ".gitignore":
            config_file = root / '.controlcoding/cc_config.json'
            config = json.loads(config_file.read_bytes()) if config_file.exists() else {}
            old = cc._build_gitignore_block(documentation_mode=config.get('documentation_mode', 'managed'),
                cc_artifact_mode=config.get('cc_artifact_mode', 'local_only'), project_root=root)
            with preview_contained(root):
                replacement = cc._build_gitignore_block(project_root=root)
            new = None
            for newline in ('\r\n', '\n'):
                block = old.replace('\n', newline).encode()
                if data.count(block) == 1:
                    new = data.replace(block, replacement.replace('\n', newline).encode())
                    break
            if new is None:
                if cc.GITIGNORE_MARKER_START.encode() in data:
                    blockers.append('Customized CC ignore block requires reconciliation: .gitignore')
                    continue
                new = data + (b'' if data.endswith(b'\n') else b'\n') + replacement.encode()
        else:
            new = _relocate_text(root, data, selected)
            if rel == '.claude/settings.local.json':
                new = _mcp_settings(new, selected)
        if new != data:
            outputs[rel] = new
            before[rel] = _file(path)
    return outputs, before, blockers


def _relocate_text(root, data, selected):
    value = data.decode("utf-8")
    for name in sorted(selected, key=len, reverse=True):
        for old, new in ((str(root / name), str(root / "cc" / _destination(name))),
                         ((root / name).as_posix(), (root / "cc" / _destination(name)).as_posix()),
                         ('${workspaceFolder}/' + name, '${workspaceFolder}/cc/' + _destination(name)),
                         ('{project}/' + name, '{project}/cc/' + _destination(name))):
            for origin, target in ((old.replace('\\', '\\\\'), new.replace('\\', '\\\\')), (old, new)):
                # Match a complete selected path or a descendant, never a
                # similarly named application path such as tools/application.py.
                pattern = r'(?<![\w.-])' + re.escape(origin) + r'(?=$|[/\\\s"\x27])'
                value = re.sub(pattern, lambda _match: target, value)
    return value.encode()


def _launcher_manifest(data):
    """Only this generated manifest owns relative launcher/document pointers."""
    value = json.loads(data)
    def path(item):
        normalized = item.replace('\\', '/') if isinstance(item, str) else item
        return 'cc/' + normalized if isinstance(normalized, str) and normalized.startswith('.controlcoding/') else item
    if not isinstance(value, dict):
        raise LayoutError('invalid_launcher_manifest')
    for name in ('primaryEntryPoint', 'publicAssistant'):
        if isinstance(value.get(name), dict):
            value[name] = {key: path(item) for key, item in value[name].items()}
    for name in ('supportEntryPoints', 'generatedFiles'):
        if isinstance(value.get(name), list):
            value[name] = [path(item) for item in value[name]]
    if isinstance(value.get('editorTasks'), dict) and 'templatePath' in value['editorTasks']:
        value['editorTasks']['templatePath'] = path(value['editorTasks']['templatePath'])
    if 'instructionFilePath' in value:
        value['instructionFilePath'] = path(value['instructionFilePath'])
    return _encoded(value)


def _mcp_settings(data, selected):
    """Relocate declared MCP script/bridge locators, not arbitrary JSON strings."""
    value = json.loads(data)
    if not isinstance(value, dict):
        return data
    def path(item):
        if not isinstance(item, str):
            return item
        normalized = item.replace('\\', '/')
        return 'cc/' + _destination(normalized) if any(
            normalized == name or normalized.startswith(name + '/') for name in selected
        ) else item
    servers = value.get('mcpServers', {})
    if isinstance(servers, dict):
        for server in servers.values():
            if not isinstance(server, dict):
                continue
            if isinstance(server.get('args'), list):
                server['args'] = [path(item) for item in server['args']]
            if isinstance(server.get('env'), dict) and 'BRIDGE_DIR' in server['env']:
                server['env']['BRIDGE_DIR'] = path(server['env']['BRIDGE_DIR'])
    return _encoded(value) if value != json.loads(data) else data


def _document_reference_conflicts(root, files):
    """Refuse known inline links that relocation would retarget or break.

    Human documents and their evidence hashes are never silently rewritten.
    Root-relative links remain root-relative; links between moved docs retain
    their meaning. Unresolved links are not asserted to become valid.
    """
    from cc_documentation_observer import references
    issues = []
    for name, info in files.items():
        if not name.lower().endswith('.md'):
            continue
        if info['bytes'] > 2 * 1024 * 1024:
            issues.append('Review oversized document references before migration: ' + name)
            continue
        try:
            text = (root / name).read_text(encoding='utf-8')
        except UnicodeError:
            issues.append('Review non-UTF-8 document before migration: ' + name)
            continue
        # Include Markdown images in the same bounded path-only analysis.
        text = text.replace('![', '[')
        old = list(references(text, name))
        new = list(references(text, 'cc/' + _destination(name)))
        if len(old) != len(new):
            issues.append('Reconcile document links that cross the project boundary: ' + name)
            continue
        for original, relocated in zip(old, new):
            expected = 'cc/' + _destination(original) if original in files else original
            if expected != relocated and (root / original).exists():
                issues.append('Reconcile relative document reference before migration: ' + name + ' -> ' + original)
    return sorted(set(issues))


def migration_plan(project, include, backup):
    root = _root(project)
    if is_contained(root) or (root / "cc").exists():
        raise LayoutError("layout_migration_conflict")
    selections = sorted(set(include or []))
    if not selections or any(not _selection(n) for n in selections):
        raise LayoutError("explicit_owned_selection_required")
    if any(a != b and b.startswith(a + '/') for a in selections for b in selections):
        raise LayoutError('overlapping_migration_selection')
    missing = [n for n in selections if not (root / n).exists()]
    if missing:
        raise LayoutError("migration_selection_missing")
    backup_path = _backup_path(root, backup)
    if backup_path.exists():
        raise LayoutError("backup_already_exists")
    files, directories = _inventory(root, selections)
    outputs, adapters, blockers = _adapter_changes(root, selections, files)
    blockers.extend(_document_reference_conflicts(root, files))
    # Inline feature declarations are integration metadata, not application
    # source. Require their explicit inclusion before activating containment.
    import cc
    import importlib.util
    specification = importlib.util.spec_from_file_location('_cc_layout_feature_owner', cc.HOOKS_DIR / 'feature_lock.py')
    owner = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(owner)
    for item in owner.find_all_lock_files(root):
        relative = Path(item).relative_to(root).as_posix()
        if relative not in files:
            blockers.append('Include inline module metadata: ' + relative)
    # Unselected canonical state would leave two unrelated control planes.
    for name in (".controlcoding", ".controlwork", "CONTROLCODING.md", "CONTROLWORK.md"):
        if (root / name).exists() and name not in selections:
            blockers.append("Include the existing managed surface: " + name)
    helper_sources = _helper_sources()
    for name in helper_sources:
        if (root / name).exists() and name not in files:
            blockers.append('Reconcile or explicitly include the installed helper: ' + name)
    busy = [p for p in files if p.endswith(("-wal", "-shm", "-journal")) or
            (p.endswith(".lock") and p not in (".controlcoding/knowledge/writer.lock", ".controlcoding/knowledge/worker.lock"))]
    blockers.extend("Close writers and inspect pending lock/SQLite state: " + p for p in busy)
    refreshed = {name: cc_source for name in files if name.startswith("hooks/")
                 if (cc_source := _hook_source(name)) is not None}
    converted = {}
    for name in files:
        if name in refreshed:
            converted[name] = refreshed[name].read_bytes()
        elif name.startswith('tools/') and name.count('/') == 1:
            for source in (cc.SCRIPT_DIR / Path(name).name, cc.TEMPLATES_DIR / 'scripts' / Path(name).name):
                if source.is_file():
                    converted[name] = source.read_bytes()
                    break
        elif (name.startswith(".controlcoding/") or name.startswith('controlcoding.')) and name.endswith(".json") and files[name]["bytes"] <= 2 * 1024 * 1024:
            original = (root / name).read_bytes()
            new = _relocate_text(root, original, selections)
            if name == '.controlcoding/launchers/manifest.json':
                new = _launcher_manifest(new)
            elif name == '.controlcoding/settings.json':
                new = _mcp_settings(new, selections)
            if new != original:
                converted[name] = new
    # The resolver is required by the newly deployed hooks/tools.
    if any(n.startswith('hooks/') for n in files):
        converted["hooks/cc_layout.py"] = Path(__file__).with_name("cc_layout.py").read_bytes()
    if any(n.startswith(('tools/', 'hooks/')) for n in files):
        converted["tools/cc_layout.py"] = Path(__file__).with_name("cc_layout.py").read_bytes()
        converted['tools/control_plane_utils.py'] = (cc.SCRIPTS_DIR / 'control_plane_utils.py').read_bytes()
    _complete_helpers(converted, helper_sources)
    info = root.stat()
    plan = {"schema": 1, "project": str(root), "root_identity": [info.st_dev, info.st_ino],
            "backup": str(backup_path), "include": selections,
            "files": files, "destinations": {p: _destination(p) for p in files},
            "directories": directories, "adapters": adapters,
            "adapter_outputs": {p: _hash(v) for p, v in outputs.items()},
            "refresh": {p: _hash(v) for p, v in converted.items()}, "blockers": blockers,
            "notice": "Selected paths are explicitly declared CC-owned. Retire active sessions before migration. Original bytes remain in the external backup; derived evidence must be refreshed."}
    plan["approval_id"] = _hash(_encoded(plan))
    return plan, outputs, converted


def _helper_sources():
    """Finite trusted companion catalog, independent of adopter file contents."""
    import cc
    return {**{'tools/' + p.name: p for p in cc.SCRIPTS_DIR.glob('*.py')},
            'tools/fitness_check.py': cc.SCRIPT_DIR / 'fitness_check.py',
            'tools/cc_layout.py': Path(__file__).with_name('cc_layout.py'),
            **{'hooks/' + name: cc._init_hook_source(name) for name in cc.INIT_HOOKS}}


def _complete_helpers(converted, sources):
    """Refresh selected packs and their local Python dependency closure.

    Only trusted bundled sources are parsed; application code is never scanned
    for executable dependencies or overwritten outside the selected namespace.
    """
    import cc
    packs = [{prefix + p.name for prefix, paths in pack.items() for p in paths}
             for pack in cc.PACK_FILES.values()]
    pending, seen = list(set(converted) & set(sources)), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        converted[name] = sources[name].read_bytes()
        dependencies = set()
        for pack in packs:
            if name in pack:
                dependencies.update(pack & sources.keys())
        for node in ast.walk(ast.parse(converted[name])):
            modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                       else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in modules:
                basename = module.split('.')[0] + '.py'
                for prefix in (name.split('/')[0] + '/', 'tools/'):
                    if prefix + basename in sources:
                        dependencies.add(prefix + basename)
                        break
        pending.extend(dependencies - seen)


def _hook_source(relative):
    import cc
    name = relative.removeprefix("hooks/")
    return cc._init_hook_source(name) if name in cc.INIT_HOOKS else None


def _json_write(path, value):
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("xb") as stream:
        stream.write(_encoded(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def _copy(source, target, expected, owned_stream=None):
    _ordinary_chain(target.parent, True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with (nullcontext(owned_stream) if owned_stream is not None else source.open("rb")) as src, target.open("xb") as dst:
        src.seek(0)
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush(); os.fsync(dst.fileno())
    os.chmod(target, expected["mode"])
    if _file(target) != expected or _file(source, owned_stream) != expected:
        raise LayoutError("migration_copy_changed")


def _require_closed_database(path):
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000 | 0x40000000, 0, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise LayoutError('migration_database_in_use')
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle(handle)


def migrate(project, include, backup, approval):
    root = _root(project)
    plan, adapters, converted = migration_plan(root, include, backup)
    if plan["approval_id"] != approval:
        raise LayoutError("preview_mismatch")
    if plan["blockers"]:
        raise LayoutError("migration_blocked")
    backup_root = Path(plan["backup"])
    with ExitStack() as stack:
        leases = {}
        current, dirs = _inventory(root, plan["include"])
        if current != plan["files"] or dirs != plan["directories"]:
            raise LayoutError("migration_input_changed")
        # Exclusive SQLite transactions make the byte-preserving offline backup
        # coherent. Pre-existing WAL/SHM are refused above; a closed WAL-mode
        # database is allowed. Our read-only transaction may create temporary
        # empty sidecars, which SQLite removes when its final handle closes.
        for name in plan["files"]:
            if name.endswith((".db", ".sqlite", ".sqlite3")):
                # SQLite can leave an idle connection open outside a SQL
                # transaction. Refuse it before staging/publishing anything.
                _require_closed_database(root / name)
                connection = sqlite3.connect((root / name).as_uri() + "?mode=rw", uri=True, timeout=0)
                stack.callback(connection.close)
                connection.execute("BEGIN EXCLUSIVE")
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise LayoutError("invalid_migration_database")
            elif name in (".controlcoding/knowledge/writer.lock", ".controlcoding/knowledge/worker.lock"):
                stream = stack.enter_context((root / name).open("r+b"))
                leases[name] = stream
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup_root.mkdir(parents=False)
        for name, expected in {**plan["files"], **plan["adapters"]}.items():
            if expected is not None:
                _copy(root / name, backup_root / "original" / name, expected, leases.get(name))
        _json_write(backup_root / "manifest.json", plan)
        (root / "cc").mkdir()
        journal = {"schema": 1, "stage": "copying", "plan": plan, "outputs": {}, "adapter_done": []}
        _json_write(root / JOURNAL, journal)
        for name in plan["directories"]:
            (root / "cc" / name).mkdir(parents=True, exist_ok=True)
        for name, expected in plan["files"].items():
            _copy(root / name, root / "cc" / plan['destinations'][name], expected, leases.get(name))
        for name, data in converted.items():
            target = root / "cc" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for name in {*plan['destinations'].values(), *converted}:
            journal["outputs"][name] = _file(root / "cc" / name)
        for name, data in adapters.items():
            target = backup_root / "adapter-next" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        journal["stage"] = "prepared"
        _json_write(root / JOURNAL, journal)
    # Writer DB handles must close before source cleanup on Windows. Every
    # source is rechecked again and changed files are never removed.
    return recover(root, apply=True)


def recover(project, *, apply=False, rollback=False):
    with migration_access(project):
        return _recover(project, apply=apply, rollback=rollback)


def _recover(project, *, apply=False, rollback=False):
    root = _root(project)
    path = root / JOURNAL
    pending = root / PENDING_JOURNAL
    if _ordinary(pending, False) is not None:
        # A partial atomic write is not authority to publish or remove anything.
        # Preserve both versions and report a stable, inspectable recovery state.
        if apply:
            raise LayoutError('migration_journal_write_incomplete')
        return {'stage': 'journal-write-incomplete', 'can_resume': False,
                'manual_recovery_required': True, 'journal': JOURNAL,
                'pending_journal': PENDING_JOURNAL,
                'guidance': 'Preserve both journal files and the external backup. Inspect the backup manifest before manually recovering; no files have been changed by this inspection.'}
    _ordinary(path, False)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise LayoutError("invalid_migration_journal")
    journal = json.loads(path.read_bytes())
    plan = journal["plan"]
    info = root.stat()
    backup = _backup_path(root, plan["backup"])
    if (plan.get("project") != str(root) or plan.get("root_identity") != [info.st_dev, info.st_ino]
            or plan["include"] != sorted(set(plan["include"])) or any(not _selection(n) for n in plan["include"])):
        raise LayoutError("invalid_migration_journal")
    helper_names = set(_helper_sources())
    def selected(name):
        _relative(name)
        return (any(name == item or name.startswith(item + "/") for item in plan["include"])
                or name in helper_names and
                   any(n.startswith(('hooks/', 'tools/')) for n in plan['files']))
    destinations = {_destination(p) for p in plan['files']}
    expected_outputs = destinations | set(plan['refresh'])
    actual_outputs = set(journal['outputs'])
    output_inventory_valid = (actual_outputs <= expected_outputs if journal.get('stage') == 'copying'
                              else actual_outputs == expected_outputs)
    if (plan.get('destinations') != {p: _destination(p) for p in plan['files']}
            or any(not selected(n) for n in [*plan["files"], *plan["directories"], *plan["refresh"]])
            or not output_inventory_valid
            or set(plan["adapters"]) != set(plan["adapter_outputs"])
            or set(plan["adapters"]) - {".git/hooks/pre-commit", ".git/hooks/post-commit", ".gitignore",
                ".claude/settings.local.json", ".vscode/tasks.json", "AGENTS.md", "CLAUDE.md", "GEMINI.md",
                ".clinerules", ".cursor/rules/project.mdc", ".windsurfrules"}):
        raise LayoutError("invalid_migration_journal")
    _ordinary_chain(backup / 'manifest.json')
    manifest = json.loads((backup / "manifest.json").read_bytes())
    if manifest != plan or _hash(_encoded({k:v for k,v in plan.items() if k != "approval_id"})) != plan["approval_id"]:
        raise LayoutError("backup_verification_failed")
    if not apply:
        return {"stage": journal["stage"], "backup": str(backup), "can_resume": journal["stage"] in ("prepared", "published", "cleanup")}
    if journal["stage"] not in ("prepared", "published", "cleanup"):
        raise LayoutError("migration_copy_incomplete")
    for name, expected in {**plan["files"], **plan["adapters"]}.items():
        _relative(name)
        if expected is not None and _file(backup / "original" / name) != expected:
            raise LayoutError("backup_verification_failed")
    for name, expected in journal["outputs"].items():
        _relative(name)
        if _file(root / "cc" / name) != expected:
            raise LayoutError("migration_destination_changed")
    for name, expected in plan["files"].items():
        if (root / name).exists() and _file(root / name) != expected:
            raise LayoutError("migration_input_changed")
    if rollback:
        # Rollback is only available while every staged/published byte still
        # matches this journal. A used or independently edited archive is never
        # discarded, and unrelated additions prevent cleanup.
        expected_names = {"cc/" + n for n in journal["outputs"]} | {JOURNAL}
        if (root / "cc/layout.json").exists():
            if (root / "cc/layout.json").read_bytes() != MARKER_BYTES:
                raise LayoutError("migration_destination_changed")
            expected_names.add("cc/layout.json")
        current_files, current_dirs = _inventory(root, ["cc"])
        if set(current_files) != expected_names:
            raise LayoutError("migration_destination_changed")
        for name, expected in plan["adapters"].items():
            current = _file(root / name) if (root / name).exists() else None
            if current != expected and (current is None or current["sha256"] != plan["adapter_outputs"][name]):
                raise LayoutError("migration_adapter_changed")
        for name in sorted(plan['directories'], key=lambda n: n.count('/')):
            directory = root / name
            _ordinary_chain(directory, True)
            directory.mkdir(parents=True, exist_ok=True)
        for name, expected in plan["files"].items():
            if not (root / name).exists():
                _copy(backup / "original" / name, root / name, expected)
        for name, expected in plan["adapters"].items():
            if expected is None:
                (root / name).unlink(missing_ok=True)
            else:
                (root / name).write_bytes((backup / "original" / name).read_bytes())
        for name in expected_names - {JOURNAL}:
            (root / name).unlink()
        path.unlink()
        for name in sorted(current_dirs, key=lambda n: n.count("/"), reverse=True):
            (root / name).rmdir()
        return {"layout": "legacy", "rolled_back": True, "backup": str(backup)}
    for name, expected in plan["adapters"].items():
        pending = backup / "adapter-next" / name
        _ordinary_chain(pending)
        data = pending.read_bytes()
        if _hash(data) != plan["adapter_outputs"][name]:
            raise LayoutError("backup_verification_failed")
        current = _file(root / name) if (root / name).exists() else None
        if current is None or current["sha256"] != _hash(data):
            if current != expected:
                raise LayoutError("migration_adapter_changed")
            with (root / name).open('xb' if expected is None else 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
        journal["adapter_done"] = sorted(set(journal["adapter_done"]) | {name})
        _json_write(path, journal)
    marker = root / "cc/layout.json"
    if not marker.exists():
        with marker.open("xb") as stream:
            stream.write(MARKER_BYTES); stream.flush(); os.fsync(stream.fileno())
    if not is_contained(root):
        raise LayoutError("invalid_layout_marker")
    journal["stage"] = "published"
    _json_write(path, journal)
    for name, expected in plan["files"].items():
        source = root / name
        if source.exists():
            if _file(source) != expected:
                raise LayoutError("migration_input_changed")
            source.unlink()
    for name in sorted(plan["directories"], key=lambda n: n.count("/"), reverse=True):
        directory = root / _relative(name)
        if directory.exists():
            _ordinary(directory, True)
            directory.rmdir()  # Never recursive; foreign additions are preserved.
    journal["stage"] = "complete"
    _json_write(backup / "result.json", journal)
    path.unlink()
    return {**status(root), "backup": str(backup), "migrated_files": len(plan["files"]),
            "evidence": "Refresh derived indexes and rerun verification before claiming freshness."}


def add_parser(subparsers):
    parser = subparsers.add_parser("layout", help="Inspect or explicitly contain CC-owned storage")
    parser.add_argument("action", choices=("status", "init", "preview", "migrate", "recover"))
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--backup", default="")
    parser.add_argument("--approval", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true", help="Recover unchanged staged bytes back to the legacy installation")
    return parser


def command(args):
    try:
        if args.action == "status":
            result = status(args.project_root)
        elif args.action == "init":
            result = initialize(args.project_root) if args.apply else initialization_plan(args.project_root)
        elif args.action == "recover":
            result = recover(args.project_root, apply=args.apply, rollback=args.rollback)
        elif args.action == "migrate" and args.apply:
            result = migrate(args.project_root, args.include, args.backup, args.approval)
        else:
            result = migration_plan(args.project_root, args.include, args.backup)[0]
        print(json.dumps(result, indent=2)); return 0
    except (ValueError, OSError, KeyError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "code": getattr(error, "code", "layout_operation_failed")})); return 1
