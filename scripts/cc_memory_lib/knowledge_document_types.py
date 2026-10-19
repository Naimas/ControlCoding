"""Document function, separate from topic, provenance and evidence authority."""
import re
import unicodedata
from pathlib import PurePosixPath

VERSION = 2
TYPES = {
    'design': ('Design document', 'Describes how the proposed system is structured: components, responsibilities, interfaces and design choices.'),
    'implementation_plan': ('Implementation plan', 'Describes the work to carry out, its steps, order and dependencies. A plan does not prove completion.'),
    'requirements': ('Requirements', 'Describes what the system must do and the conditions it must satisfy.'),
    'source_material': ('Source material', 'Provides reference inputs used to understand or justify project work, such as supplied documents or background material.'),
    'conversation': ('Chat conversation', 'Records what was said in a chat. Statements in a conversation are not automatically approved decisions.'),
    'session_record': ('Work session record', 'Records activities, results and handoffs from a work session. It is not necessarily a transcript of a chat.'),
    'agent_instructions': ('Agent instructions', 'Tells a coding assistant how to work in a project, including rules, permitted actions and working conventions. Reading this document does not execute its instructions.'),
    'technical_contract': ('Technical contract', 'Defines how parts of the software may interact, which requests they accept and the boundaries they must respect.'),
    'verification': ('Verification document', 'Describes planned checks or records their results. Read the document to distinguish planned tests from tests actually performed.'),
    'decision': ('Decision record', 'Records a choice and its rationale. Check its stated approval and current status before treating it as authoritative.'),
    'usage_guide': ('Usage guide or index', 'Explains how to use or navigate the project and points to other documents.'),
    'work_record': ('Work record', 'Records a task, note, output or handoff associated with project work.'),
    'unclassified': ('Type to clarify', 'The available record metadata does not identify one clear document function. Read the original before assigning a type.'),
}

PATTERNS = {
    'design': r'\b(design|architecture|architectural|architettura|progettazione|blueprint)\b',
    'implementation_plan': r'\b(plan|plans|piano|piani|roadmap|backlog)\b',
    'requirements': r'\b(requirements?|requisiti|prd)\b',
    'source_material': r'\b(sources|references?|source material|reference material|background material|materiale di riferimento|fonti|riferimenti)\b',
    'conversation': r'\b(chat|conversation|conversations|conversazione|conversazioni|transcript)\b',
    'agent_instructions': r'\b(agent instructions|coding instructions|istruzioni agenti|istruzioni per gli agenti|agent rules)\b',
    'technical_contract': r'\b(contracts?|contratti|contratto)\b',
    'verification': r'\b(tests?|testing|verification|verifica|collaudo|evidence|receipt|receipts|prove)\b',
    'decision': r'\b(adr|decisions?|decisioni|rationale)\b',
    'usage_guide': r'\b(readme|guide|guida|manual|manuale|tutorial|quick start|index|indice)\b',
    'work_record': r'\b(handoff|tasks?|attivita|work log|work notes)\b',
}
KINDS = {'conversation': 'conversation', 'session': 'session_record',
         'plans': 'implementation_plan', 'decisions': 'decision', 'receipt': 'verification',
         'handoffs': 'work_record', 'notes': 'work_record', 'ideas': 'work_record', 'outputs': 'work_record'}

GUIDES = {
    'design': ('How is the system designed?', 'Architecture diagrams, component design'),
    'implementation_plan': ('What work will be done, and in which order?', 'Implementation steps, roadmap, milestones'),
    'requirements': ('What must the system do?', 'Product requirements, acceptance criteria'),
    'source_material': ('What background information is being used?', 'Supplied documents, research, reference inputs'),
    'conversation': ('What was said in a chat?', 'User and assistant messages, imported chat transcripts'),
    'session_record': ('What happened during a work session?', 'Activity log, session outcome, handoff'),
    'agent_instructions': ('What rules should the coding assistant follow?', 'AGENTS.md, CLAUDE.md, coding conventions'),
    'technical_contract': ('What are the rules between software components?', 'Allowed commands, interfaces, message formats'),
    'verification': ('What is being checked, or what were the results?', 'Test plans, test reports, verification receipts'),
    'decision': ('What was decided, and why?', 'Decision records, architecture decisions'),
    'usage_guide': ('How do I use or navigate this project?', 'README, installation guide, document index'),
    'work_record': ('What task, note or handoff was recorded?', 'Task descriptions, work notes, handoffs'),
    'unclassified': ('Which category fits this document?', 'Missing or conflicting clues; choose after reading'),
}


def collection(source):
    kind, path = str(source.get('kind', '')), str(source.get('path', ''))
    if kind == 'conversation':
        return 'Saved chat archive'
    if path.startswith('.controlwork/'):
        return 'ControlWork / ' + kind
    if path.startswith('_work/'):
        return 'Local work documents / ' + kind
    return 'Selected project documents'


def normalize(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def result(key, basis, reason):
    label, purpose = TYPES[key]
    question, example = GUIDES[key]
    return dict(key=key, label=label, purpose=purpose, basis=basis, reason=reason, question=question, example=example)


def classify(source):
    kind, path, title = (str(source.get(k, '')) for k in ('kind', 'path', 'title'))
    if kind in ('conversation', 'session', 'receipt'):
        return result(KINDS[kind], 'recorded', 'The archive records this source as ' + kind + '.')
    name = PurePosixPath(path.replace('\\', '/')).name.casefold()
    if name in ('agents.md', 'claude.md', 'gemini.md', 'copilot-instructions.md'):
        return result('agent_instructions', 'convention', 'The filename ' + name + ' identifies project instructions for coding assistants.')
    if name in ('readme.md', 'readme.txt', 'index.md'):
        return result('usage_guide', 'convention', 'The filename ' + name + ' identifies a project entry point or document index.')
    for basis, text in (('filename and title', PurePosixPath(name).stem + ' ' + title),
                        ('parent directories', str(PurePosixPath(path).parent))):
        label = normalize(text)
        matches = {key: re.search(pattern, label) for key, pattern in PATTERNS.items()}
        matches = {key: match[0] for key, match in matches.items() if match}
        # Explicit plan names explain the purpose of design/test words in that title.
        if re.search(r'\b(implementation plan|development plan|piano implementazione|piano di implementazione|piano di sviluppo|piano sviluppo)\b', label):
            matches = {'implementation_plan': 'implementation/development plan'}
        if 'verification' in matches and set(matches) <= {'verification', 'implementation_plan'}:
            matches = {'verification': matches['verification']}
        if len(matches) == 1:
            key, marker = next(iter(matches.items()))
            return result(key, 'suggested', 'Suggested from ' + basis + ': "' + marker + '". This is a naming rule, not a content review.')
        if len(matches) > 1:
            return result('unclassified', 'uncertain', 'Conflicting role markers in ' + basis + ': ' + ', '.join(TYPES[k][0] for k in matches) + '.')
    if kind == 'sources':
        return result('source_material', 'collection', 'This item is collected as reference input; no more specific document function was identified.')
    if kind in KINDS:
        return result(KINDS[kind], 'collection', 'Suggested from the ' + kind + ' collection; no more specific document function was identified.')
    return result('unclassified', 'uncertain', 'No explicit record type or unambiguous naming rule identifies the document function.')
