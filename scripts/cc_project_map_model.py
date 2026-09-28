"""Pure, bounded Project Map projection. See docs/project-map-contract.md.

Caller-supplied observations are not authenticated here. This module neither
reads a project nor selects evidence receipts nor grants permission to act.
"""

from dataclasses import dataclass, fields
from datetime import datetime
import json
import re
from types import MappingProxyType


@dataclass(frozen=True)
class MapPolicy:
    max_input_bytes: int = 2 * 1024 * 1024
    max_output_bytes: int = 768 * 1024
    max_nodes: int = 2000
    max_edges: int = 8000
    max_sources: int = 4000
    max_source_refs: int = 4000
    max_assessments: int = 4000
    max_constraints: int = 4000
    max_depth: int = 24
    max_values: int = 200000
    max_id: int = 128
    max_title: int = 160
    max_path: int = 1024
    max_text: int = 4096


class MapValidationError(ValueError):
    """Stable error code/location, without echoing untrusted input."""

    def __init__(self, code, location="$", message="Invalid map data"):
        self.code = code
        self.location = location
        super().__init__(f"{code} at {location}: {message}")


STRUCTURE = frozenset("system subsystem service application module component file symbol".split())
WORK = frozenset("feature phase task milestone criterion".split())
DELIVERY = frozenset("unknown planned active review accepted aborted".split())
FEATURE_STATES = MappingProxyType({"planned": "planned", "active": "active", "verifying": "active",
                  "passing": "review", "blocked": "active", "completed": "accepted",
                  "aborted": "aborted"})
ORIGINS = frozenset("design code ai user adapter synthetic".split())
MAPPINGS = frozenset("proposed confirmed rejected conflicted".split())
RELATIONS = frozenset("contains_structure contains_work implements depends_on verified_by constrained_by references supersedes".split())
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


def _fail(code, loc="$", message="Invalid map data"):
    raise MapValidationError(code, loc, message)


def _policy(policy):
    if policy is None:
        return MapPolicy()
    if type(policy) is not MapPolicy:
        _fail("policy")
    ceiling = MapPolicy()
    for field in fields(ceiling):
        value = getattr(policy, field.name)
        if type(value) is not int or not 1 <= value <= getattr(ceiling, field.name):
            _fail("policy", "$.policy", "Bounds may only be tightened")
    return policy


def _bounded_copy(value, policy, byte_limit):
    """Iterative preflight before JSON recursion/allocation; counts compact UTF-8."""
    stack = [(value, 1, False)]
    active = set()
    size = 0
    scheduled = 1
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
            continue
        if depth > policy.max_depth:
            _fail("limit", message="Value count or nesting bound exceeded")
        kind = type(item)
        if kind in (dict, list):
            if id(item) in active:
                _fail("cycle", message="Cyclic input container")
            children = len(item) * (2 if kind is dict else 1)
            scheduled += children
            if scheduled > policy.max_values:
                _fail("limit", message="Container bound exceeded")
            active.add(id(item))
            stack.append((item, depth, True))
            size += 2 + max(0, len(item) - 1)
            if kind is dict:
                size += len(item)
                for key, child in item.items():
                    if type(key) is not str:
                        _fail("type", message="Object keys must be strings")
                    stack.append((child, depth + 1, False))
                    stack.append((key, depth + 1, False))
            else:
                stack.extend((child, depth + 1, False) for child in item)
        elif kind is str:
            if len(item) > policy.max_text:
                _fail("limit", message="String bound exceeded")
            try:
                size += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            except UnicodeError:
                _fail("text", message="Invalid Unicode")
        elif item is None or kind is bool:
            size += 4 if item is None or item is True else 5
        elif kind is int:
            if not -(2**63) <= item < 2**63:
                _fail("limit", message="Integer bound exceeded")
            size += len(str(item))
        else:
            _fail("type", message="Only JSON objects, arrays, strings, integers, booleans and null are supported")
        if size > byte_limit:
            _fail("limit", message="Serialized byte bound exceeded")
    return json.loads(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def _object(value, names, loc):
    if type(value) is not dict or set(value) != set(names.split()):
        _fail("fields", loc, "Expected exactly the documented fields")


def _text(value, limit, loc, *, empty=False):
    if type(value) is not str or len(value) > limit or (not value and not empty):
        _fail("text", loc)
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        _fail("text", loc, "Control characters are not allowed")


def _identifier(value, policy, loc):
    _text(value, policy.max_id, loc)
    if not _ID.fullmatch(value):
        _fail("id", loc)


def _enum(value, choices, loc):
    if type(value) is not str or value not in choices:
        _fail("enum", loc)


def _array(value, maximum, loc):
    if type(value) is not list:
        _fail("type", loc)
    if len(value) > maximum:
        _fail("limit", loc)


def _ids(value, policy, loc, maximum):
    _array(value, maximum, loc)
    for item in value:
        _identifier(item, policy, loc)
    if len(set(value)) != len(value):
        _fail("duplicate", loc)
    value.sort()


def _path(value, policy, loc):
    _text(value, policy.max_path, loc)
    if "\\" in value or ":" in value or value.startswith("/"):
        _fail("path", loc)
    if any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in value.split("/")):
        _fail("path", loc)


def _records(bundle, name, maximum, names, policy):
    rows = bundle[name]
    _array(rows, maximum, f"$.{name}")
    result = {}
    for i, row in enumerate(rows):
        loc = f"$.{name}[{i}]"
        _object(row, names, loc)
        _identifier(row["id"], policy, loc + ".id")
        if row["id"] in result:
            _fail("duplicate", loc + ".id")
        result[row["id"]] = row
    rows.sort(key=lambda row: row["id"])
    return result


def _components(node_ids, edges):
    """Iterative Kosaraju: bounded SCCs, including self-dependencies."""
    forward = {n: [] for n in node_ids}
    reverse = {n: [] for n in node_ids}
    for source, target in edges:
        forward[source].append(target)
        reverse[target].append(source)
    visited, order = set(), []
    for start in sorted(node_ids):
        stack = [(start, False)]
        while stack:
            node, done = stack.pop()
            if done:
                order.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in forward[node] if child not in visited)
    visited, cycles = set(), []
    for start in reversed(order):
        if start in visited:
            continue
        members, stack = [], [start]
        visited.add(start)
        while stack:
            node = stack.pop()
            members.append(node)
            for child in reverse[node]:
                if child not in visited:
                    visited.add(child)
                    stack.append(child)
        if len(members) > 1 or start in forward[start]:
            cycles.append(sorted(members))
    return sorted(cycles)


def _validate(bundle, policy):
    b = _bounded_copy(bundle, policy, policy.max_input_bytes)
    _object(b, "schema_version mode project_id snapshot_id observed_at coverage selection sources nodes edges assessments constraints", "$")
    if type(b["schema_version"]) is not int or b["schema_version"] != 1:
        _fail("version", "$.schema_version")
    _enum(b["mode"], ("observation", "demo"), "$.mode")
    for name in ("project_id", "snapshot_id"):
        _identifier(b[name], policy, "$." + name)
    if type(b["observed_at"]) is not str or not _TIME.fullmatch(b["observed_at"]):
        _fail("time", "$.observed_at")
    try:
        datetime.fromisoformat(b["observed_at"])
    except ValueError:
        _fail("time", "$.observed_at")
    coverage = b["coverage"]
    _object(coverage, "state scope omissions reason", "$.coverage")
    _enum(coverage["state"], ("complete", "partial", "unknown"), "$.coverage.state")
    _ids(coverage["scope"], policy, "$.coverage.scope", policy.max_nodes)
    _array(coverage["omissions"], policy.max_nodes, "$.coverage.omissions")
    for reason in coverage["omissions"]:
        _text(reason, policy.max_text, "$.coverage.omissions")
    coverage["omissions"] = sorted(set(coverage["omissions"]))
    _text(coverage["reason"], policy.max_text, "$.coverage.reason")
    if coverage["state"] == "complete" and coverage["omissions"]:
        _fail("coverage", "$.coverage", "Complete coverage cannot have omissions")
    selection = b["selection"]
    _object(selection, "kind nodes", "$.selection")
    _enum(selection["kind"], STRUCTURE | WORK, "$.selection.kind")
    _ids(selection["nodes"], policy, "$.selection.nodes", policy.max_nodes)
    sources = _records(b, "sources", policy.max_sources,
                       "id owner locator identity adapter resolution reason synthetic", policy)
    nodes = _records(b, "nodes", policy.max_nodes,
                     "id kind title presence origin mapping sources lifecycle plan criteria reason", policy)
    edges = _records(b, "edges", policy.max_edges,
                     "id source target relation origin mapping sources", policy)
    assessments = _records(b, "assessments", policy.max_assessments,
                           "id subject sources outcome freshness applicability sufficiency coverage reason", policy)
    constraints = _records(b, "constraints", policy.max_constraints,
                           "id subject kind operation scope rule sources applicability coverage stage decision host context reason responsible condition", policy)
    diagnostics = []
    for source in sources.values():
        loc = "$.sources"
        _enum(source["owner"], "core feature evidence policy design code user".split(), loc)
        _object(source["locator"], "path fragment", loc + ".locator")
        _path(source["locator"]["path"], policy, loc + ".locator.path")
        if source["locator"]["fragment"] is not None:
            _text(source["locator"]["fragment"], policy.max_title, loc + ".locator.fragment")
        _identifier(source["adapter"], policy, loc + ".adapter")
        _enum(source["resolution"], ("resolved", "unresolved"), loc)
        if source["identity"] is not None and (type(source["identity"]) is not str or not _HASH.fullmatch(source["identity"])):
            _fail("identity", loc)
        _text(source["reason"], policy.max_text, loc + ".reason")
        if type(source["synthetic"]) is not bool:
            _fail("type", loc + ".synthetic")
        if source["synthetic"] and b["mode"] != "demo":
            _fail("synthetic", loc)
        if source["resolution"] == "unresolved" and source["identity"] is not None:
            _fail("identity", loc, "Unresolved sources cannot claim an identity")
        if source["resolution"] == "unresolved" or source["identity"] is None:
            diagnostics.append({"code": "source_unknown", "members": [source["id"]]})
    ref_count = 0
    for group in (nodes, edges, assessments, constraints):
        for row in group.values():
            _ids(row["sources"], policy, "$.sources_ref", policy.max_source_refs)
            ref_count += len(row["sources"])
            if ref_count > policy.max_source_refs:
                _fail("limit", "$.sources_ref")
            if not set(row["sources"]) <= sources.keys():
                _fail("reference", "$.sources_ref", "Declare unresolved sources explicitly")
    for node in nodes.values():
        loc = "$.nodes"
        _enum(node["kind"], STRUCTURE | WORK, loc + ".kind")
        _text(node["title"], policy.max_title, loc + ".title")
        _enum(node["presence"], "intended observed both missing unknown".split(), loc)
        _enum(node["origin"], ORIGINS, loc)
        _enum(node["mapping"], MAPPINGS, loc)
        if node["origin"] == "synthetic" and b["mode"] != "demo":
            _fail("synthetic", loc)
        _enum(node["plan"], "absent draft detailed approved stale unknown".split(), loc)
        _text(node["reason"], policy.max_text, loc + ".reason")
        _object(node["lifecycle"], "owner state", loc + ".lifecycle")
        _enum(node["lifecycle"]["owner"], ("feature", "adapter"), loc + ".lifecycle.owner")
        _enum(node["lifecycle"]["state"], FEATURE_STATES if node["lifecycle"]["owner"] == "feature" else DELIVERY, loc + ".lifecycle.state")
        if node["lifecycle"]["owner"] == "feature" and node["kind"] != "feature":
            _fail("lifecycle", loc)
        _ids(node["criteria"], policy, loc + ".criteria", policy.max_nodes)
        for criterion in node["criteria"]:
            if criterion not in nodes or nodes[criterion]["kind"] != "criterion" or node["kind"] == "criterion":
                _fail("reference", loc + ".criteria")
    if not set(coverage["scope"]) <= nodes.keys() or not set(selection["nodes"]) <= set(coverage["scope"]):
        _fail("reference", "$.coverage.scope")
    if any(nodes[n]["kind"] != selection["kind"] for n in selection["nodes"]):
        _fail("granularity", "$.selection")
    parents = {"contains_structure": {}, "contains_work": {}}
    containment = []
    dependencies = []
    for edge in edges.values():
        loc = "$.edges"
        _enum(edge["relation"], RELATIONS, loc)
        _enum(edge["origin"], ORIGINS, loc)
        _enum(edge["mapping"], MAPPINGS, loc)
        if edge["origin"] == "synthetic" and b["mode"] != "demo":
            _fail("synthetic", loc)
        for endpoint in ("source", "target"):
            _identifier(edge[endpoint], policy, loc + "." + endpoint)
            if edge[endpoint] not in nodes:
                _fail("reference", loc + "." + endpoint)
        source, target, relation = edge["source"], edge["target"], edge["relation"]
        if relation in parents:
            kinds = STRUCTURE if relation == "contains_structure" else WORK
            if nodes[source]["kind"] not in kinds or nodes[target]["kind"] not in kinds:
                _fail("containment", loc, "Structure and work trees are separate")
            if target in parents[relation]:
                _fail("parent", loc)
            parents[relation][target] = source
            containment.append((source, target))
        elif relation == "implements":
            if nodes[source]["kind"] not in STRUCTURE or nodes[target]["kind"] not in WORK:
                _fail("relation", loc)
        elif relation == "verified_by" and nodes[target]["kind"] != "criterion":
            _fail("relation", loc)
        elif relation == "depends_on":
            dependencies.append((source, target))
    if _components(nodes, containment):
        _fail("containment_cycle", "$.edges")
    for cycle in _components(nodes, dependencies):
        diagnostics.append({"code": "dependency_cycle", "members": cycle})
    for group in (assessments, constraints):
        for row in group.values():
            _identifier(row["subject"], policy, "$.subject")
            if row["subject"] not in nodes:
                _fail("reference", "$.subject")
            _enum(row["applicability"], ("applicable", "not_applicable", "unknown"), "$.applicability")
            _text(row["reason"], policy.max_text, "$.reason")
    for row in assessments.values():
        _enum(row["outcome"], "not_run partial passed failed incomplete unsupported unknown".split(), "$.assessments.outcome")
        _enum(row["freshness"], "current stale unknown".split(), "$.assessments.freshness")
        _enum(row["sufficiency"], "complete subset unknown".split(), "$.assessments.sufficiency")
        _enum(row["coverage"], "complete partial unknown".split(), "$.assessments.coverage")
    for row in constraints.values():
        _enum(row["kind"], "policy feature_perimeter approval gate".split(), "$.constraints.kind")
        _enum(row["coverage"], "supported unsupported conflicting unknown".split(), "$.constraints.coverage")
        _enum(row["stage"], "configured predicted observed".split(), "$.constraints.stage")
        _enum(row["decision"], "allow deny approval_required unknown".split(), "$.constraints.decision")
        for name in ("operation", "rule", "host", "context", "responsible", "condition"):
            _text(row[name], policy.max_text, "$.constraints." + name)
        _path(row["scope"], policy, "$.constraints.scope")
        if not row["sources"]:
            _fail("reference", "$.constraints.sources")
    diagnostics.sort(key=lambda d: (d["code"], d["members"]))
    return b, diagnostics


def validate_map_bundle(bundle, *, policy=None):
    """Return a detached normalized bundle and typed diagnostics, or raise."""
    policy = _policy(policy)
    normalized, diagnostics = _validate(bundle, policy)
    return _bounded_copy({"bundle": normalized, "diagnostics": diagnostics}, policy, policy.max_output_bytes)


def _projection(bundle, diagnostics):
    sources = {s["id"]: s for s in bundle["sources"]}
    assessments = {n["id"]: [] for n in bundle["nodes"]}
    constraints = {n["id"]: [] for n in bundle["nodes"]}
    for row in bundle["assessments"]:
        assessments[row["subject"]].append(row)
    for row in bundle["constraints"]:
        constraints[row["subject"]].append(row)

    def known(refs):
        return bool(refs) and all(sources[r]["resolution"] == "resolved" and sources[r]["identity"] is not None for r in refs)

    statuses = {}
    for node in bundle["nodes"]:
        state = node["lifecycle"]["state"]
        delivery = FEATURE_STATES[state] if node["lifecycle"]["owner"] == "feature" else state
        rows = [a for a in assessments[node["id"]] if a["applicability"] != "not_applicable"]
        evidence = bool(rows) and all(
            a["outcome"] == "passed" and a["freshness"] == "current"
            and a["applicability"] == "applicable" and a["sufficiency"] == "complete"
            and a["coverage"] == "complete" and known(a["sources"]) for a in rows)
        reasons = []
        if delivery != "accepted":
            reasons.append("delivery_not_accepted")
        if node["presence"] not in ("observed", "both"):
            reasons.append("implementation_not_observed")
        if node["mapping"] != "confirmed":
            reasons.append("mapping_not_confirmed")
        if not known(node["sources"]):
            reasons.append("source_identity_unknown")
        if not evidence:
            reasons.append("verification_insufficient")
        statuses[node["id"]] = {
            "id": node["id"], "presence": node["presence"], "mapping": node["mapping"],
            "plan": node["plan"], "delivery": delivery, "reported_lifecycle": node["lifecycle"],
            "verification": {"sufficient_current": evidence, "assessments": [a["id"] for a in assessments[node["id"]]]},
            "impediments": ["feature_reported_blocked"] if node["lifecycle"]["owner"] == "feature" and state == "blocked" else [],
            "constraints": [{"id": c["id"], "effective_observation": c["decision"] if c["stage"] == "observed" and c["applicability"] == "applicable" and c["coverage"] == "supported" and known(c["sources"]) else "unknown"} for c in constraints[node["id"]]],
            "verified_accepted": not reasons, "reasons": reasons,
        }
    # Criteria cannot themselves refer to criteria: no recursive acceptance rule.
    for node in bundle["nodes"]:
        if node["kind"] == "criterion":
            continue
        status = statuses[node["id"]]
        if not node["criteria"]:
            status["reasons"].append("criteria_unmapped")
        elif not all(statuses[c]["verified_accepted"] for c in node["criteria"]):
            status["reasons"].append("criteria_unsatisfied")
        status["verified_accepted"] = not status["reasons"]
    selected = bundle["selection"]["nodes"]
    by_id = {n["id"]: n for n in bundle["nodes"]}
    criteria = sorted({c for n in selected for c in ([n] if by_id[n]["kind"] == "criterion" else by_id[n]["criteria"])})
    count = sum(statuses[n]["verified_accepted"] for n in selected)
    summary = {
        "kind": bundle["selection"]["kind"], "units": len(selected), "verified_units": count,
        "criteria": len(criteria), "verified_criteria": sum(statuses[c]["verified_accepted"] for c in criteria),
        "coverage": bundle["coverage"]["state"],
        "declared_scope_verified": bool(selected) and bundle["coverage"]["state"] == "complete" and set(selected) == set(bundle["coverage"]["scope"]) and count == len(selected),
    }
    return {"schema_version": 1, "canonical": False, "mode": bundle["mode"],
            "bundle": bundle, "statuses": list(statuses.values()), "summary": summary,
            "diagnostics": diagnostics}


def build_map_projection(bundle, *, policy=None):
    """Build a noncanonical view; observations/assessment authority stay external."""
    policy = _policy(policy)
    normalized, diagnostics = _validate(bundle, policy)
    return _bounded_copy(_projection(normalized, diagnostics), policy, policy.max_output_bytes)


def explain_map_node(projection, node_id, *, policy=None):
    """Return finite node detail. Reject tampered or inconsistent projections."""
    policy = _policy(policy)
    supplied = _bounded_copy(projection, policy, policy.max_output_bytes)
    _object(supplied, "schema_version canonical mode bundle statuses summary diagnostics", "$")
    expected = build_map_projection(supplied["bundle"], policy=policy)
    # JSON comparison also distinguishes bool from int in derived fields.
    if json.dumps(supplied, sort_keys=True) != json.dumps(expected, sort_keys=True):
        _fail("projection", message="Projection does not match its bundle")
    _identifier(node_id, policy, "$.node_id")
    bundle = expected["bundle"]
    nodes = {n["id"]: n for n in bundle["nodes"]}
    if node_id not in nodes:
        _fail("reference", "$.node_id")
    node = nodes[node_id]
    edges = [e for e in bundle["edges"] if node_id in (e["source"], e["target"])]
    parents = {e["target"]: e["source"] for e in bundle["edges"] if e["relation"] in ("contains_structure", "contains_work")}
    ancestors, cursor = [], node_id
    while cursor in parents:
        cursor = parents[cursor]
        ancestors.append(cursor)
    assessments = [a for a in bundle["assessments"] if a["subject"] == node_id]
    constraints = [c for c in bundle["constraints"] if c["subject"] == node_id]
    refs = set(node["sources"])
    for row in edges + assessments + constraints + [nodes[c] for c in node["criteria"]]:
        refs.update(row["sources"])
    result = {
        "schema_version": 1, "canonical": False, "mode": bundle["mode"], "node": node,
        "status": next(s for s in expected["statuses"] if s["id"] == node_id),
        "ancestors": ancestors, "edges": edges,
        "criteria": [s for s in expected["statuses"] if s["id"] in node["criteria"]],
        "assessments": assessments, "constraints": constraints,
        "sources": [s for s in bundle["sources"] if s["id"] in refs],
        "diagnostics": [d for d in expected["diagnostics"]
                        if (d["code"] == "dependency_cycle" and node_id in d["members"])
                        or (d["code"] == "source_unknown" and set(d["members"]) & refs)],
        "coverage": bundle["coverage"],
    }
    return _bounded_copy(result, policy, policy.max_output_bytes)
