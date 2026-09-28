"""Explicitly reviewed OCR text, stored privately and bound to the PDF bytes."""
import json
from pathlib import Path
from cc_setup_service import _Snapshots, _root
from .knowledge_read_batches import SnapshotBatches
from .knowledge_sources import digest
from .knowledge_store import database, get, put, KnowledgeError

KEY = 'reviewed_ocr_v1'


def dispatch(root, action, value):
    from .knowledge_service import policy, require
    require(type(value) is dict and set(value) == ({'path', 'text'} if action == 'ocr-preview' else {'path', 'text', 'approval'}))
    require(type(value['path']) is str and value['path'].lower().endswith('.pdf'))
    require(type(value['text']) is str and bool(value['text'].strip()) and len(value['text'].encode('utf-8')) <= 24000)
    with database(root) as db:
        require(db is not None and get(db, 'policy') is not None, 'knowledge_not_enabled')
        config = get(db, 'policy')
        policy({**config, 'rich_paths': [value['path']]})  # same confined path contract
        require('rich-documents' in config['scopes'] and
                (config.get('rich_paths') is None or value['path'] in config['rich_paths']), 'source_not_followed')
        root = _root(str(root))
        with SnapshotBatches(root, _Snapshots) as readers:
            raw, _ = readers.read(value['path'], 1024*1024)
        require(raw.startswith(b'%PDF'), 'unsupported_document')
        records = get(db, KEY, {})
        payload = {'schemaVersion': 'controlwork-ocr-sidecar/v1', 'sourcePath': value['path'],
                   'sourceHash': digest(raw), 'extractor': 'human-reviewed-transcription',
                   'pages': [{'text': value['text']}],
                   'notice': 'User-reviewed transcription; original page boundaries and OCR accuracy are not certified.'}
        approval = digest(json.dumps([str(Path(root).resolve()), payload, records.get(value['path'])], sort_keys=True))
        if action == 'ocr-preview':
            return {'approval': approval, 'path': value['path'], 'source_hash': digest(raw), 'text': value['text'], 'sidecar': payload}
        require(value['approval'] == approval, 'ocr_preview_changed')
        require(value['path'] in records or len(records) < 128, 'ocr_record_budget')
        records[value['path']] = payload
        with db:
            put(db, KEY, records)
            put(db, 'needs_reconcile', True)
        return {'saved': True, 'path': value['path'], 'source_hash': digest(raw)}
