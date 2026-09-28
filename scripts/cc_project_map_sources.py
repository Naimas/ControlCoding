"""Read-only bounded Project Map observations; see docs/project-map-sources.md."""

import ast
from dataclasses import dataclass, fields
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import tomllib

import cc_project_map_model as model
from cc_setup_service import _Snapshots, _ordinary, _root, _signature, SetupServiceError


ADAPTER = "cc-project-map-sources/v1"
_EXCLUDED = frozenset(".git .hg .svn node_modules vendor dist build target __pycache__ venv env coverage logs secrets credentials private memory data storage runtime scratch exports reports _work devlog".split())
_CODE = frozenset(".py .pyi .js .jsx .ts .tsx .mjs .cjs .css .scss .html .go .rs .java .c .cpp .h .hpp .cs .sql".split())
_SECRET_SUFFIX = frozenset(".pem .key .p12 .pfx .db .sqlite .sqlite3 .log .env".split())
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")


class MapSourceError(ValueError):
    """Safe error: no raw OS, parser or source content is included."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SourcePolicy:
    max_entries: int = 5000
    max_depth: int = 24
    file_bytes: int = 1024 * 1024
    target_bytes: int = 32 * 1024 * 1024
    input_count: int = 384
    max_ast_nodes: int = 20000
    max_design_files: int = 32
    seconds: int = 10

    @property
    def trusted_bytes(self):
        return 0  # The observer never reads a trusted source outside its root.


def _policy(value):
    default = SourcePolicy()
    if value is None:
        return default
    if type(value) is not SourcePolicy:
        raise MapSourceError("invalid_policy")
    for field in fields(default):
        n = getattr(value, field.name)
        if type(n) is not int or not 0 < n <= getattr(default, field.name):
            raise MapSourceError("invalid_policy")
    return value


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _relative(value):
    if type(value) is not str or not value or len(value) > 1024:
        raise MapSourceError("invalid_path")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise MapSourceError("invalid_path")
    if any(c in value for c in '\\:*?"<>|') or value.startswith("/"):
        raise MapSourceError("invalid_path")
    for part in value.split("/"):
        if part in ("", ".", "..") or part.endswith((".", " ")):
            raise MapSourceError("invalid_path")
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
            raise MapSourceError("invalid_path")
    return value


def _excluded(relative):
    parts = relative.lower().split("/")
    return any(p.startswith(".") or p in _EXCLUDED or p.startswith(("secret", "credential", "id_rsa", "id_ed25519")) for p in parts) or Path(parts[-1]).suffix in _SECRET_SUFFIX


def _request(request, policy):
    if type(request) is not dict or set(request) != {"project_root", "project_id", "observed_at", "design_paths"}:
        raise MapSourceError("invalid_request")
    try:
        root = _root(request["project_root"])
        for part in root.parts[1:]:
            _relative(part)
        project_id = request["project_id"]
        if type(project_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", project_id):
            raise MapSourceError("invalid_request")
        observed = request["observed_at"]
        if type(observed) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", observed):
            raise MapSourceError("invalid_request")
        datetime.fromisoformat(observed)
    except (SetupServiceError, ValueError, TypeError):
        raise MapSourceError("invalid_request") from None
    design = request["design_paths"]
    if type(design) is not list or len(design) > policy.max_design_files:
        raise MapSourceError("invalid_request")
    for path in design:
        _relative(path)
        if _excluded(path) or Path(path).suffix.lower() not in (".md", ".txt"):
            raise MapSourceError("excluded_design")
    if len(set(design)) != len(design):
        raise MapSourceError("invalid_request")
    # Excluded runtime stores cannot become a fresh project by selecting them as root.
    if _excluded(root.name) or any(part.startswith(".") or part.lower() in {"secrets", "credentials", "private"} for part in root.parts[1:]):
        raise MapSourceError("excluded_root")
    normalized = {"project_root": str(root), "project_id": project_id, "observed_at": observed, "design_paths": sorted(design)}
    return root, normalized


def preview_project_map_scope(request, *, policy=None):
    """Pure scope description. This preview neither grants permission nor reads."""
    policy = _policy(policy)
    _, normalized = _request(request, policy)
    binding = {"request": normalized, "policy": {f.name: getattr(policy, f.name) for f in fields(policy)}, "adapter": ADAPTER,
               "detail_policy": "file-overview-with-bounded-symbols-v2"}
    return {"schema_version": 1, "adapter": ADAPTER,
            "preview_id": _hash(json.dumps(binding, sort_keys=True).encode("utf-8")),
            "design_paths": normalized["design_paths"], "code_extensions": sorted(_CODE),
            "manifests": ["package.json", "pyproject.toml"],
            "excluded_names": sorted(_EXCLUDED), "hidden_paths_excluded": True,
            "sensitive_override_supported": False, "limits": binding["policy"], "detail_policy": binding["detail_policy"]}


class _Inventory:
    def __init__(self, root, policy, reader, tick):
        self.root, self.policy, self.reader, self.tick = root, policy, reader, tick
        self.directories = {}
        self.metadata = {}
        self.count = 0

    def _names(self, path, limit):
        self.tick()
        fd = self.reader.entries[path][2]
        names = []
        with os.scandir(path if os.name == "nt" else fd) as entries:
            for entry in entries:
                self.tick()
                if len(names) >= limit:
                    raise MapSourceError("scope_limit")
                names.append(entry.name)
        return sorted(names)

    def names(self, path):
        names = self._names(path, self.policy.max_entries - self.count)
        self.count += len(names)
        self.directories[path] = names
        return names

    def inspect(self, path):
        self.tick()
        if os.name == "posix":
            return os.stat(path.name, dir_fd=self.reader.entries[path.parent][2], follow_symlinks=False)
        return path.lstat()

    def recheck(self):
        self.tick()
        self.reader.recheck()
        for path, names in self.directories.items():
            if self._names(path, self.policy.max_entries) != names:
                raise MapSourceError("changed_input")
        for path, signature in self.metadata.items():
            current = self.inspect(path)
            _ordinary(current, False, "source")
            if _signature(current) != signature:
                raise MapSourceError("changed_input")
        self.tick()


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def _manifest(data, filename, budget):
    parsed = _json(data.decode("utf-8")) if filename == "package.json" else tomllib.loads(data.decode("utf-8"))
    stack, count, scheduled = [(parsed, 0)], 0, 1
    while stack:
        value, depth = stack.pop()
        count += 1
        if count > budget or depth > 24:
            raise MapSourceError("parse_limit")
        if type(value) is dict:
            scheduled += len(value)
            if scheduled > budget:
                raise MapSourceError("parse_limit")
            stack.extend((v, depth + 1) for v in value.values())
        elif type(value) is list:
            scheduled += len(value)
            if scheduled > budget:
                raise MapSourceError("parse_limit")
            stack.extend((v, depth + 1) for v in value)
    if type(parsed) is not dict:
        raise ValueError("object")
    if filename == "package.json":
        names = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
        if any(type(parsed.get(n, {})) is not dict for n in (*names, "scripts")):
            raise ValueError("shape")
        return {"ecosystem": "javascript", "dependency_declarations": sum(len(parsed.get(n, {})) for n in names), "script_declarations": len(parsed.get("scripts", {}))}
    project = parsed.get("project", {})
    if type(project) is not dict or type(project.get("dependencies", [])) is not list:
        raise ValueError("shape")
    return {"ecosystem": "python", "dependency_declarations": len(project.get("dependencies", [])), "script_declarations": None}


def _python(data, policy, tick):
    # Parsing only: no importlib, compile-to-bytecode, eval or target execution.
    tree = ast.parse(data, filename="<project-map-source>")
    stack, count, symbols, imports, dynamic = [(tree, "", None)], 0, [], set(), False
    occurrences, scheduled = {}, 1
    while stack:
        tick()
        current, qualified, parent = stack.pop()
        count += 1
        if count > policy.max_ast_nodes:
            raise MapSourceError("parse_limit")
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = qualified + "." + current.name if qualified else current.name
            if not _NAME.fullmatch(name) or len(name) > 160:
                raise ValueError("unsupported_identifier")
            occurrences[name] = occurrences.get(name, 0) + 1
            key = name + ":" + str(occurrences[name])
            symbols.append({"key": key, "name": name, "parent": parent, "line": current.lineno,
                            "kind": "class" if isinstance(current, ast.ClassDef) else "function"})
            qualified, parent = name, key
        elif isinstance(current, ast.Import):
            for alias in current.names:
                if len(alias.name) <= 160 and _NAME.fullmatch(alias.name):
                    imports.add((0, alias.name))
        elif isinstance(current, ast.ImportFrom):
            name = current.module or ""
            if len(name) <= 160 and (not name or _NAME.fullmatch(name)):
                imports.add((current.level, name))
        elif isinstance(current, ast.Call):
            called = current.func
            if (isinstance(called, ast.Name) and called.id == "__import__") or (isinstance(called, ast.Attribute) and called.attr == "import_module"):
                dynamic = True
        children = list(ast.iter_child_nodes(current))
        scheduled += len(children)
        if scheduled > policy.max_ast_nodes:
            raise MapSourceError("parse_limit")
        stack.extend((child, qualified, parent) for child in reversed(children))
    return symbols, [{"level": level, "module": name} for level, name in sorted(imports)], dynamic


class _Proposal:
    def __init__(self, project_id, root_id, observed_at):
        self.root_id = root_id
        self.nodes, self.edges, self.sources, self.observations, self.findings = [], [], [], [], []
        self.symbols_enabled = True
        self.bundle = {"schema_version": 1, "mode": "observation", "project_id": project_id,
                       "snapshot_id": "pending", "observed_at": observed_at,
                       "coverage": {"state": "partial", "scope": [], "omissions": [], "reason": "Static inventory; lifecycle, criteria and canonical evidence are not assessed"},
                       "selection": {"kind": "file", "nodes": []}, "nodes": self.nodes,
                       "edges": self.edges, "sources": self.sources, "assessments": [], "constraints": []}
        self.add_node("", "system", "Selected project", None, [], "adapter")

    def identifier(self, category, locator):
        return category + ":" + _hash((self.root_id + "\0" + locator).encode("utf-8"))[:40]

    def add_node(self, locator, kind, title, parent, sources, origin):
        if len(self.nodes) >= 2000 and kind != "symbol" and self.symbols_enabled:
            self.omit_symbols()
        if len(self.nodes) >= 2000:
            raise MapSourceError("scope_limit")
        identifier = self.identifier("node", locator)
        self.nodes.append({"id": identifier, "kind": kind, "title": title[:160], "presence": "observed",
                           "origin": origin, "mapping": "proposed", "sources": sources,
                           "lifecycle": {"owner": "adapter", "state": "unknown"}, "plan": "unknown",
                           "criteria": [], "reason": "Static source observation; mapping and completion require separate review"})
        if parent is not None:
            self.edges.append({"id": self.identifier("edge", locator), "source": parent, "target": identifier,
                               "relation": "contains_structure", "origin": "adapter", "mapping": "proposed", "sources": []})
        if kind == "file":
            self.bundle["selection"]["nodes"].append(identifier)
            self.bundle["coverage"]["scope"].append(identifier)
        return identifier

    def finding(self, code, source):
        self.findings.append({"code": code, "source": source})

    def omit_symbols(self):
        """Keep the complete file inventory; optional details never displace files."""
        self.symbols_enabled = False
        symbols = {n["id"] for n in self.nodes if n["kind"] == "symbol"}
        self.nodes[:] = [n for n in self.nodes if n["id"] not in symbols]
        self.edges[:] = [e for e in self.edges if e["source"] not in symbols and e["target"] not in symbols]
        for observation in self.observations:
            if observation["adapter"] == "python-static":
                observation["details"].update(symbols=[], symbol_detail="omitted")
        self.finding("symbol_detail_omitted", "scope")

    def file(self, relative, data, design, policy, tick):
        source_id = self.identifier("source", relative)
        self.sources.append({"id": source_id, "owner": "design" if design else "code",
                             "locator": {"path": relative, "fragment": None},
                             "identity": "sha256:" + _hash(data) if data is not None else None,
                             "adapter": "cc-project-map-sources-v1", "resolution": "resolved",
                             "reason": "Retained content rechecked" if data is not None else "Metadata only; content not selected",
                             "synthetic": False})
        path = Path(relative)
        parent = self.identifier("node", path.parent.as_posix() if path.parent != Path(".") else "")
        node_id = self.add_node(relative, "file", path.name, parent, [source_id], "design" if design else "code")
        observation = {"source": source_id, "node": node_id, "adapter": "inventory", "details": {"content_read": data is not None}}
        if data is None:
            self.finding("content_not_selected", relative)
        else:
            try:
                if design:
                    text = data.decode("utf-8")
                    observation.update(adapter="design-document", details={"bytes": len(data), "lines": len(text.splitlines()), "headings": sum(bool(re.match(r"^#{1,6} ", line)) for line in text.splitlines())})
                elif path.name.lower() in ("package.json", "pyproject.toml"):
                    observation.update(adapter="manifest", details=_manifest(data, path.name.lower(), policy.max_ast_nodes))
                elif path.suffix.lower() in (".py", ".pyi"):
                    symbols, imports, dynamic = _python(data, policy, tick)
                    if self.symbols_enabled and len(self.nodes) + len(symbols) > 2000:
                        self.omit_symbols()
                    if not self.symbols_enabled:
                        symbols = []
                    parents = {}
                    for symbol in symbols:
                        locator = relative + "#" + symbol["key"]
                        parents[symbol["key"]] = self.add_node(locator, "symbol", symbol["name"], parents.get(symbol["parent"], node_id), [source_id], "code")
                    observation.update(adapter="python-static", details={"symbols": [{"node": parents[s["key"]], "line": s["line"], "kind": s["kind"]} for s in symbols], "imports": imports, "dynamic_import_observed": dynamic, "dependency_resolution": "unsupported"})
                    if not self.symbols_enabled:
                        observation["details"]["symbol_detail"] = "omitted"
                    if dynamic:
                        self.finding("dynamic_import_unresolved", relative)
                else:
                    observation.update(adapter="file-identity", details={"bytes": len(data), "semantic_analysis": "unsupported"})
                    self.finding("semantic_analysis_unsupported", relative)
            except MapSourceError as error:
                if error.code != "parse_limit":
                    raise
                self.finding("parse_limit", relative)
                observation.update(adapter="inventory", details={"content_read": True, "bytes": len(data), "parse": "limit"})
                next(n for n in self.nodes if n["id"] == node_id)["reason"] = "File observed; static parsing budget exceeded. Symbol details and completion remain unverified"
            except (ValueError, SyntaxError, UnicodeError, RecursionError):
                self.finding("parse_unavailable", relative)
                observation.update(adapter="inventory", details={"content_read": True, "parse": "unavailable"})
        self.observations.append(observation)


def observe_project_map(request, *, policy=None, expected_preview=None):
    """Observe one bounded selected root. Never write or execute target content."""
    policy = _policy(policy)
    root, normalized = _request(request, policy)
    preview = preview_project_map_scope(normalized, policy=policy)
    if expected_preview is not None and (type(expected_preview) is not str or expected_preview != preview["preview_id"]):
        raise MapSourceError("preview_mismatch")
    started = time.monotonic()

    def tick():
        if time.monotonic() - started > policy.seconds:
            raise MapSourceError("time_limit")

    try:
        with _Snapshots(root, {}, policy) as reader:
            root_snapshot = reader.observe(root, {}, directory=True)
            if root_snapshot is None:
                raise MapSourceError("root_unavailable")
            root_id = _hash((os.path.normcase(str(root)) + "\0" + repr(root_snapshot)).encode("utf-8"))
            proposal = _Proposal(normalized["project_id"], root_id, normalized["observed_at"])
            inventory = _Inventory(root, policy, reader, tick)
            selected = set(normalized["design_paths"])
            found = set()
            pending = [(root, 0)]
            while pending:
                directory, depth = pending.pop()
                for name in inventory.names(directory):
                    tick()
                    path = directory / name
                    relative = path.relative_to(root).as_posix()
                    try:
                        _relative(relative)
                    except MapSourceError:
                        proposal.finding("unsafe_name_omitted", "scope")
                        continue
                    if _excluded(relative):
                        proposal.finding("excluded_path", "scope")
                        continue
                    info = inventory.inspect(path)
                    is_dir = stat.S_ISDIR(info.st_mode)
                    try:
                        _ordinary(info, is_dir, "source")
                    except SetupServiceError:
                        proposal.finding("unsupported_path", relative)
                        continue
                    if is_dir:
                        if depth + 1 > policy.max_depth:
                            raise MapSourceError("scope_limit")
                        if reader.observe(path, {}, directory=True) is None:
                            raise MapSourceError("changed_input")
                        proposal.add_node(relative, "module", name, proposal.identifier("node", directory.relative_to(root).as_posix() if directory != root else ""), [], "adapter")
                        pending.append((path, depth + 1))
                    else:
                        inventory.metadata[path] = _signature(info)
                        design = relative in selected
                        read_content = design or path.suffix.lower() in _CODE or name.lower() in ("package.json", "pyproject.toml")
                        data = None
                        if read_content:
                            snapshot = reader.observe(path, {})
                            if snapshot is None:
                                raise MapSourceError("changed_input")
                            data = snapshot[-1]
                        if design:
                            found.add(relative)
                        proposal.file(relative, data, design, policy, tick)
            for missing in sorted(selected - found):
                proposal.finding("selected_design_unavailable", missing)
            inventory.recheck()
            # At most one retry, using the same retained/rechecked source snapshot.
            # Only optional symbol detail can be removed; file inventory never truncates.
            for attempt in range(2):
                tick()
                proposal.findings.sort(key=lambda row: (row["code"], row["source"]))
                proposal.bundle["coverage"]["omissions"] = sorted({f["code"] for f in proposal.findings} | {"Canonical lifecycle, verification and controls are not assessed", "Static imports are declarations; dependencies are not resolved"})
                proposal.bundle["snapshot_id"] = "pending"
                snapshot_content = {"bundle": proposal.bundle, "observations": proposal.observations, "findings": proposal.findings}
                proposal.bundle["snapshot_id"] = "snapshot:" + _hash(json.dumps(snapshot_content, sort_keys=True).encode("utf-8"))
                try:
                    projection = model.build_map_projection(proposal.bundle)
                    result = {"schema_version": 1, "adapter": ADAPTER, "canonical": False, "mode": "observation",
                              "root_identity": root_id, "preview_id": preview["preview_id"], "projection": projection,
                              "observations": sorted(proposal.observations, key=lambda row: row["source"]),
                              "findings": proposal.findings, "counts": {"enumerated_entries": inventory.count,
                              "content_bytes": reader.used["target"], "retained_entries": len(reader.entries)}}
                    result = model._bounded_copy(result, model.MapPolicy(), model.MapPolicy().max_output_bytes)
                except model.MapValidationError as error:
                    if attempt or error.code != "limit" or not any(n["kind"] == "symbol" for n in proposal.nodes):
                        raise
                    proposal.omit_symbols()
                    continue
                tick()
                return result
    except MapSourceError:
        raise
    except model.MapValidationError:
        raise MapSourceError("scope_limit") from None
    except SetupServiceError as error:
        code = {"too_large": "scope_limit", "input_limit": "scope_limit", "changed_input": "changed_input", "unsupported_path": "unsupported_path", "unsupported_platform": "unsupported_platform"}.get(error.code, "source_unavailable")
        raise MapSourceError(code) from None
    except (OSError, ValueError, RecursionError, MemoryError):
        raise MapSourceError("source_unavailable") from None
