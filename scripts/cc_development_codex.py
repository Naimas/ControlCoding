"""Standalone, explicitly configured Codex CLI proposal worker.

The read-only Codex invocation proposes complete UTF-8 files. The controller
owns promotion; evaluation mode materializes only the disposable case directory.
Model identity is declared configuration, not provider attestation. Monetary
usage is an explicit configured estimate, never a provider invoice. This module
installs nothing and does not isolate the inherited Codex user configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
import subprocess
import sys
import threading


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["files"],
          "properties": {"files": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["path", "content"], "properties": {
                  "path": {"type": "string"}, "content": {"type": "string"}}}}}}
RESERVED = {".git", ".controlcoding", ".agents", ".codex", "request.json"}


class WorkerError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise WorkerError(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "codex_json_duplicate_key")
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, "codex_json_invalid"))


def safe_path(root, relative):
    require(type(relative) is str and relative and "\\" not in relative
            and ":" not in relative and "\x00" not in relative, "codex_path_invalid")
    parts = relative.split("/")
    require(not PurePosixPath(relative).is_absolute()
            and all(p and p not in {".", ".."} and not p.endswith((".", " "))
                    and p.casefold() not in RESERVED and not p.casefold().startswith(".cc-codex-")
                    and p.split(".")[0].casefold() not in {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)], *[f"lpt{i}" for i in range(1, 10)]}
                    for p in parts), "codex_path_reserved")
    target = root
    for part in parts:
        target = target / part
        require(not target.is_symlink() and not (hasattr(target, "is_junction") and target.is_junction()), "codex_path_link")
    require(target.resolve().is_relative_to(root.resolve()), "codex_path_escape")
    if target.exists():
        metadata = target.stat()
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1, "codex_path_not_regular")
    return target


def proposal(value, root):
    require(type(value) is dict and set(value) == {"files"}
            and type(value["files"]) is list and len(value["files"]) <= 64, "codex_proposal_invalid")
    seen, total = set(), 0
    for item in value["files"]:
        require(type(item) is dict and set(item) == {"path", "content"}
                and type(item["content"]) is str, "codex_file_invalid")
        safe_path(root, item["path"])
        require(item["path"].casefold() not in seen, "codex_file_duplicate")
        seen.add(item["path"].casefold())
        size = len(item["content"].encode("utf-8"))
        total += size
        require(size <= 65536 and total <= 2 * 1024 * 1024, "codex_proposal_limit")
    require(not any(a != b and b.startswith(a + "/") for a in seen for b in seen), "codex_file_hierarchy_conflict")
    return value["files"]


def command_preview(exe, model, workspace, schema, prompt):
    require(type(model) is dict and set(model) == {"provider", "id", "revision"}
            and model["provider"].casefold() == "openai"
            and all(type(v) is str and v.strip() and len(v) <= 200
                    and not v.startswith("-") and not any(ord(c) < 32 for c in v)
                    for v in model.values()), "codex_model_invalid")
    binary = Path(exe)
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink()
            and binary.suffix.casefold() not in {".ps1", ".cmd", ".bat", ".py", ".sh"}, "codex_native_executable_required")
    return [str(binary), "-a", "never", "exec", "--sandbox", "read-only", "--ephemeral",
            "--json", "--output-schema", str(schema), "--skip-git-repo-check",
            "-C", str(workspace), "-m", model["id"], prompt]


def parse_events(captured, root):
    completed, messages = [], []
    for line in captured.splitlines():
        if not line.strip():
            continue
        event = decode(line)
        require(type(event) is dict and type(event.get("type")) is str, "codex_event_invalid")
        require(event["type"] not in {"error", "turn.failed"}, "codex_turn_failed")
        if event["type"] == "turn.completed":
            completed.append(event)
        if event["type"] == "item.completed":
            require(type(event.get("item")) is dict, "codex_item_invalid")
            if event["item"].get("type") == "agent_message":
                messages.append(event["item"].get("text"))
    require(len(completed) == 1 and len(messages) >= 1 and type(messages[-1]) is str, "codex_completion_missing")
    usage = completed[0].get("usage")
    require(type(usage) is dict and all(type(usage.get(k)) is int and 0 <= usage[k] <= 10**12
            for k in ("input_tokens", "output_tokens")), "codex_usage_missing")
    files = proposal(decode(messages[-1]), root)
    return files, {"inputTokens": usage["input_tokens"], "outputTokens": usage["output_tokens"], "costMicros": None}


def run_bounded(argv, cwd, timeout, limit):
    """Bound both captured pipes and duration; no shell or automatic retries."""
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "OPENAI_API_KEY", "CODEX_HOME"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    buffers, sizes, exceeded = [bytearray(), bytearray()], [0, 0], threading.Event()
    def collect(index, stream):
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            sizes[index] += len(chunk)
            buffers[index].extend(chunk[:max(0, limit - len(buffers[index]))])
            if sizes[index] > limit:
                exceeded.set()
                process.kill()
                break
        stream.close()
    threads = [threading.Thread(target=collect, args=(i, stream), daemon=True)
               for i, stream in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    failure = None
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        failure = "codex_timeout"
    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        failure = "codex_output_incomplete"
    if exceeded.is_set():
        failure = "codex_output_limit"
    if process.returncode and failure is None:
        failure = "codex_process_failed"
    return bytes(buffers[0]), bytes(buffers[1]), failure


def binary_digest(exe):
    path = Path(exe)
    require(path.is_absolute() and path.is_file() and not path.is_symlink()
            and not (hasattr(path, "is_junction") and path.is_junction()), "codex_binary_invalid")
    for parent in path.parents:
        require(not parent.is_symlink() and not (hasattr(parent, "is_junction") and parent.is_junction()), "codex_binary_link")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def worker(request_path, exe, executable_sha256, input_rate, output_rate, timeout=120, output_limit=1024 * 1024):
    require(type(executable_sha256) is str and len(executable_sha256) == 64
            and all(c in "0123456789abcdef" for c in executable_sha256), "codex_binary_pin_invalid")
    require(binary_digest(exe) == executable_sha256, "codex_binary_changed")
    require(type(input_rate) is int and type(output_rate) is int
            and 0 <= input_rate <= 10**12 and 0 <= output_rate <= 10**12, "codex_cost_estimate_unconfigured")
    path = Path(request_path).absolute()
    require(path.name == "request.json" and not path.is_symlink(), "codex_request_invalid")
    root = path.parent
    for directory in (root, *root.parents):
        require(not directory.is_symlink() and not (hasattr(directory, "is_junction") and directory.is_junction()), "codex_workspace_link")
    require(path.is_file() and path.stat().st_nlink == 1, "codex_request_not_regular")
    require(path.stat().st_size <= 1024 * 1024, "codex_request_limit")
    request = decode(path.read_text(encoding="utf-8"))
    require(type(request) is dict and type(request.get('schemaVersion')) is int and request['schemaVersion'] in (1, 2)
            and type(request.get("prompt")) is str and len(request["prompt"]) <= 262144
            and type(request.get("files")) is list, "codex_request_invalid")
    if request['schemaVersion'] == 2:
        expected = {'schemaVersion', 'model', 'prompt', 'files', 'planningDetail', 'planningBinding'}
        expected.add('caseId' if 'caseId' in request else 'plan')
        require(set(request) == expected, 'codex_planning_request_invalid')
        require(type(request.get('planningDetail')) is dict and type(request.get('planningBinding')) is dict
                and request['planningDetail'].get('contract') == 'detailed_contract_v1'
                and request['planningBinding'].get('contract') == 'detailed_contract_v1', 'codex_planning_request_invalid')
    context = []
    require(len(request["files"]) <= 64, "codex_context_limit")
    for item in request["files"]:
        relative = item if type(item) is str else item.get("path") if type(item) is dict else None
        target = safe_path(root, relative)
        if type(item) is dict and item.get("state") == "absent":
            require(not target.exists(), "codex_context_changed")
            context.append({"path": relative, "state": "absent"})
            continue
        require(target.is_file() and target.stat().st_size <= 65536, "codex_context_invalid")
        context.append({"path": relative, "content": target.read_text(encoding="utf-8")})
    prompt_content = {"intent": request["prompt"], "context": context}
    if request['schemaVersion'] == 2:
        prompt_content.update(planningDetail=request['planningDetail'], planningBinding=request['planningBinding'])
    prompt = "Return only the JSON proposal matching the supplied schema. Do not execute commands or write files. Propose complete replacement files.\n" + json.dumps(prompt_content, ensure_ascii=True)
    schema = root / ".cc-codex-schema.json"
    with schema.open("x", encoding="utf-8") as stream:
        json.dump(SCHEMA, stream)
    argv = command_preview(exe, request["model"], root, schema, prompt)
    stdout, stderr, failure = run_bounded(argv, root, timeout, output_limit)
    # Trace contains model output and therefore stays in the controller-owned
    # disposable workspace. No credentials or inherited environment are logged.
    with (root / ".cc-codex-trace.json").open("x", encoding="utf-8") as stream:
        json.dump({"modelSource": "declared", "costSource": "configured-estimate",
                   "inputCostMicrosPerMillion": input_rate, "outputCostMicrosPerMillion": output_rate,
                   "executableSha256": executable_sha256,
                   "status": failure or "completed", "stdout": stdout.decode("utf-8", "replace"),
                   "stderr": stderr.decode("utf-8", "replace")}, stream)
    require(failure is None, failure)
    require(binary_digest(exe) == executable_sha256, "codex_binary_changed")
    files, usage = parse_events(stdout.decode("utf-8"), root)
    usage["costMicros"] = (usage["inputTokens"] * input_rate + usage["outputTokens"] * output_rate + 999999) // 1000000
    if "caseId" in request:
        for item in files:
            target = safe_path(root, item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target = safe_path(root, item["path"])
            with target.open("wb") as stream:
                stream.write(item["content"].encode("utf-8"))
        return {"model": request["model"], "usage": usage}
    return {"model": request["model"], "usage": usage, "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--codex", required=True)
    parser.add_argument("--codex-sha256", required=True)
    parser.add_argument("--input-cost-micros-per-million", type=int, required=True)
    parser.add_argument("--output-cost-micros-per-million", type=int, required=True)
    parser.add_argument("--timeoutSeconds", type=int, default=120)
    parser.add_argument("--outputLimitBytes", type=int, default=1024 * 1024)
    args = parser.parse_args()
    try:
        require(1 <= args.timeoutSeconds <= 300 and 4096 <= args.outputLimitBytes <= 2 * 1024 * 1024, "codex_limits_invalid")
        result = worker(args.request, args.codex, args.codex_sha256, args.input_cost_micros_per_million,
                        args.output_cost_micros_per_million, args.timeoutSeconds, args.outputLimitBytes)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        print(json.dumps({"error": str(error) if isinstance(error, WorkerError) else "codex_worker_invalid"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
