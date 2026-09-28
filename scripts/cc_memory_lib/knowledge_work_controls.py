"""Reuse the Project Map feature owner adapter for knowledge projections."""
import json
from cc_setup_service import _Snapshots, _root
from cc_project_map_controls import Inputs, Projection, REGISTRY
from .knowledge_sources import KnowledgeReadPolicy, digest
from .knowledge_store import KnowledgeError


def capture(root):
    root = _root(str(root))
    with _Snapshots(root, {}, KnowledgeReadPolicy(file_bytes=1024*1024)) as reader:
        inputs = Inputs(root, reader)
        bundle = {'nodes': [{'id': 'knowledge-root', 'kind': 'system'}], 'edges': [], 'sources': []}
        projection = Projection({'projection': {'bundle': bundle}}, inputs)
        projection.features()
        inputs.recheck()
        if projection.summary['features']['state'] == 'invalid':
            raise KnowledgeError('invalid_feature_registry')
        bundle = projection.bundle
        registry = inputs.data(REGISTRY) or {'features': []}
        declarations = {f['id']: f for f in registry['features']}
        nodes = [n for n in bundle['nodes'] if n['kind'] != 'system']
        if len(nodes) > 128:
            raise KnowledgeError('work_control_budget')
        sources = []
        for node in nodes:
            locators = [s for s in bundle['sources'] if s['id'] in node['sources']]
            declared = declarations[locators[0]['locator']['fragment']]
            criteria = declared.get('acceptanceCriteria', [])
            if node['kind'] == 'criterion':
                parent = next(n for n in nodes if node['id'] in n.get('criteria', []))
                criteria = [criteria[parent['criteria'].index(node['id'])]]
            payload = {'adapter': 'cc-project-map-controls/v1', 'node': node, 'sources': locators,
                       'registry_issues': projection.summary['features']['issues'],
                       'declared_acceptance_criteria': criteria,
                       'notice': 'Observed canonical feature state; no new acceptance or receipt certification.'}
            title = (node['title'] if node['kind'] == 'feature' else declared['title']+' / '+node['title'])[:180]
            body = '# '+title+'\n\n```json\n'+json.dumps(payload, ensure_ascii=False, indent=2)+'\n```'
            sources.append({'id': node['id'], 'path': REGISTRY+'#'+node['id'], 'title': title,
                            'revision': digest(body), 'body': body, 'kind': 'dev-'+node['kind']})
        ids = {s['id'] for s in sources}
        edges = [{'source': e['source'], 'target': e['target'], 'kind': 'canonical_work:'+e['relation']}
                 for e in bundle['edges'] if e['source'] in ids and e['target'] in ids]
        return sources, edges
