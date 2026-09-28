"""One-request private stdio transport for the optional desktop observer.

Run using an absolute Python executable with -I -B. No generic CLI/apply route.
"""
import datetime
import json
from pathlib import Path
import re
import sys

MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 1024 * 1024
WORK_OPERATIONS = ('work_preview_v1', 'work_read_v1')
CONFIG_FIELDS = {
    'work_manage_preview_v1': {'value', 'source_paths', 'request_id', 'timestamp'},
    'work_manage_commit_v1': {'value', 'source_paths', 'request_id', 'timestamp', 'approval_id'},
    'config_read_v1': set(),
    'config_preview_v1': {'draft', 'revision', 'intent'},
    'config_commit_v1': {'draft', 'revision', 'intent', 'approval_id'},
    'config_analysis_v1': {'draft'},
    'config_import_v1': {'draft', 'proposal'},
}
MAP_OPERATIONS = ("map_preview_v1", "map_read_v1", "map_review_preview_v1", "map_review_apply_v1", "map_controls_preview_v1", "map_controls_read_v1", "map_analysis_preview_v1", "map_analysis_read_v1")


def dispatch(request, analysis_runtime=None):
    if type(request) is not dict:
        return {"version": 1, "status": "error", "error": {"code": "invalid_message", "source": "request"}}
    operation = request.get("operation")
    expected = {"version", "id", "operation", "project_root"}
    if operation == 'knowledge_v1':
        expected |= {'action', 'value'}
    if type(operation) is str and operation in CONFIG_FIELDS:
        expected |= CONFIG_FIELDS[operation]
    if operation == 'work_read_v1':
        expected |= {'scope_id', 'query'}
    if operation == 'documentation_read_v1':
        expected.add('scope')
    if operation == 'document_read_v1':
        expected |= {'path', 'sha256', 'images'}
    if operation in MAP_OPERATIONS:
        expected |= {"design_paths", "observed_at"}
        if operation != "map_preview_v1":
            expected.add("preview_id")
        if operation.startswith("map_review_"):
            expected |= {"snapshot", "revision", "change"}
        if operation == "map_review_apply_v1":
            expected.add("approval_id")
        if operation.startswith("map_controls_"):
            expected |= {"snapshot", "revision"}
        if operation == "map_controls_read_v1":
            expected.add("scope_id")
        if operation.startswith('map_analysis_'):
            expected |= {'snapshot', 'revision', 'controls_scope_id'}
        if operation == 'map_analysis_read_v1':
            expected.add('scope_id')
    if (set(request) != expected
            or type(request.get("version")) is not int or request["version"] != 1
            or type(request.get("id")) is not str
            or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", request["id"])
            or operation not in ("read", "preview", 'knowledge_v1', 'documentation_read_v1', 'document_read_v1', *MAP_OPERATIONS, *WORK_OPERATIONS, *CONFIG_FIELDS)):
        return {"version": 1, "status": "error", "error": {"code": "invalid_message", "source": "request"}}
    from cc_setup_service import SetupServiceError, read_setup_state, preview_minimal_init
    response = {"version": 1, "id": request["id"], "operation": request["operation"],
                "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if operation == 'knowledge_v1':
        from cc_memory_lib.knowledge_service import dispatch as knowledge_dispatch
        from cc_memory_lib.knowledge_store import KnowledgeError
        try:
            result = knowledge_dispatch(request['project_root'], request['action'], request['value'])
            response.update(status='ok', result={'project_root': request['project_root'], 'knowledge': result})
        except (KnowledgeError, SetupServiceError) as error:
            response.update(status='error', error={'code': getattr(error, 'code', str(error)), 'source': 'knowledge'})
        except Exception:
            response.update(status='error', error={'code': 'knowledge_operation_failed', 'source': 'knowledge'})
        return response
    if operation == 'document_read_v1':
        from cc_document_reader import read_document, DocumentError
        try:
            response.update(status='ok', result=read_document(request['project_root'], request['path'], request['sha256'], request['images']))
        except DocumentError as error:
            response.update(status='error', error={'code': error.code, 'source': 'document'})
        return response
    if operation == 'documentation_read_v1':
        from cc_documentation_observer import observe, DocumentationError
        try:
            response.update(status='ok', result=observe(request['project_root'], request['scope']))
        except DocumentationError as error:
            response.update(status='error', error={'code': error.code, 'source': 'documentation'})
        return response
    if operation in CONFIG_FIELDS:
        from cc_panel_configuration import read_draft, prepare, save_or_apply, analysis_packet, import_proposals, ConfigurationError
        from cc_panel_transaction import PanelWriteError
        from cc_project_map_sources import MapSourceError
        try:
            root = request['project_root']
            result = {'project_root': root}
            if operation.startswith('work_manage_'):
                from cc_controlwork_manage import prepare as prepare_work, save as save_work
                args = (root, request['value'], request['source_paths'], request['request_id'], request['timestamp'])
                if operation == 'work_manage_preview_v1':
                    result['work_manage_preview'] = prepare_work(*args)[0]
                else:
                    result['work_manage_saved'] = save_work(*args, request['approval_id'])
            elif operation == 'config_read_v1':
                result['configuration'] = read_draft(root)
            elif operation == 'config_preview_v1':
                result['config_preview'] = prepare(root, request['draft'], request['revision'], request['intent'])[0]
            elif operation == 'config_commit_v1':
                result['config_saved'] = save_or_apply(root, request['draft'], request['revision'], request['intent'], request['approval_id'])
            elif operation == 'config_analysis_v1':
                result['config_analysis'] = analysis_packet(root, request['draft'])
            else:
                result['config_draft'] = import_proposals(root, request['draft'], request['proposal'])
            response.update(status='ok', result=result)
        except (ConfigurationError, PanelWriteError, SetupServiceError, MapSourceError) as error:
            response.update(status='error', error={'code': error.code, 'source': 'configuration'})
        except (ValueError, TypeError, KeyError, RecursionError):
            response.update(status='error', error={'code': 'invalid_draft', 'source': 'configuration'})
        return response
    if operation in WORK_OPERATIONS:
        from cc_controlwork_observer import observe, WorkObserverError
        try:
            result = observe(request['project_root'], preview=operation == 'work_preview_v1',
                             scope_id=request.get('scope_id'), query=request.get('query', ''))
            response.update(status='ok', result=result)
        except WorkObserverError as error:
            response.update(status='error', error={'code': error.code, 'source': 'controlwork'})
        return response
    if operation in MAP_OPERATIONS:
        from cc_project_map_sources import MapSourceError, preview_project_map_scope
        from cc_project_map_definition import DefinitionError, observe_reviewed, preview_change, commit_change
        from cc_project_map_controls import ControlsError, preview_controls, observe_controls
        from cc_project_map_analysis import AnalysisError, preview_analysis, observe_analysis
        source_request = {"project_root": request["project_root"], "project_id": "selected-project",
                          "observed_at": request["observed_at"], "design_paths": request["design_paths"]}
        try:
            if operation == "map_preview_v1":
                result = {"project_root": request["project_root"], "scope": preview_project_map_scope(source_request)}
            else:
                if type(request["preview_id"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", request["preview_id"]):
                    raise MapSourceError("preview_mismatch")
                if operation == "map_read_v1":
                    result = {"project_root": request["project_root"], "map": observe_reviewed(source_request, request["preview_id"])}
                elif operation.startswith('map_analysis_'):
                    args = (source_request, request['preview_id'], request['snapshot'], request['revision'], request['controls_scope_id'])
                    result = {'project_root': request['project_root']}
                    if operation == 'map_analysis_preview_v1':
                        result['analysis_scope'] = preview_analysis(*args, runtime=analysis_runtime)
                    else:
                        result['map'] = observe_analysis(*args, request['scope_id'], runtime=analysis_runtime)
                elif operation.startswith("map_controls_"):
                    args = (source_request, request["preview_id"], request["snapshot"], request["revision"])
                    result = {"project_root": request["project_root"]}
                    if operation == "map_controls_preview_v1":
                        result['controls_scope'] = preview_controls(*args)
                    else:
                        result['map'] = observe_controls(*args, request['scope_id'])
                else:
                    args = (source_request, request["preview_id"], request["snapshot"], request["revision"], request["change"])
                    result = {"project_root": request["project_root"]}
                    if operation == "map_review_preview_v1":
                        result["review_preview"] = preview_change(*args)[0]
                    else:
                        result["review_saved"] = commit_change(*args, request["approval_id"])
            response.update(status="ok", result=result)
        except (MapSourceError, DefinitionError, ControlsError, AnalysisError) as error:
            response.update(status="error", error={"code": error.code, "source": "project_map"})
        return response
    try:
        if request["operation"] == "read":
            result = read_setup_state(request["project_root"])
        else:
            result = preview_minimal_init({"schema_version": 1, "operation": "minimal_init",
                                          "project_root": request["project_root"], "central_hooks": False})
        response.update(status="ok", result=result)
    except SetupServiceError as error:
        response.update(status="error", error=error.as_dict()["error"])
    return response


def serve(source, destination, analysis_runtime=None):
    try:
        data = source.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            response = {"version": 1, "status": "error", "error": {"code": "message_limit", "source": "request"}}
        else:
            response = dispatch(json.loads(data.decode("utf-8")), analysis_runtime)
    except (ValueError, UnicodeError, RecursionError):
        response = {"version": 1, "status": "error", "error": {"code": "invalid_message", "source": "request"}}
    except Exception:
        # No exception text, source fragments, environment or traceback on the wire.
        response = {"version": 1, "status": "error", "error": {"code": "helper_failure", "source": "service"}}
    encoded = json.dumps(response, ensure_ascii=response.get("operation") not in ('document_read_v1', 'documentation_read_v1', *MAP_OPERATIONS, *WORK_OPERATIONS, *CONFIG_FIELDS), separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESPONSE:
        encoded = b'{"version":1,"status":"error","error":{"code":"response_limit","source":"service"}}'
    destination.write(encoded + b"\n")
    destination.flush()


if __name__ == "__main__":
    # Isolated Python excludes even the script directory; restore only our own
    # installed trusted modules, never the selected project's import path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # These optional runtime paths are trusted launcher configuration, never JSON fields.
    runtime = None
    if len(sys.argv) == 5 and sys.argv[1] == '--analysis-node' and sys.argv[3] == '--analysis-worker':
        runtime = (sys.argv[2], sys.argv[4])
    elif len(sys.argv) != 1:
        raise SystemExit(2)
    serve(sys.stdin.buffer, sys.stdout.buffer, runtime)
