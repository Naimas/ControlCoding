"""Read-only, fixed-scope Markdown inventory for the desktop knowledge map."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import time
from urllib.parse import unquote, urlsplit

from cc_setup_service import ReadPolicy, SetupServiceError, _Snapshots, _root
from cc_layout import is_contained, managed_path

SCOPES = {'project': ['/*.md (except AGENTS.md)', '/docs/**/*.md'],
          'plans': ['/_work/plans/*.md'], 'handoffs': ['/_work/handoff/*.md']}
POLICY = ReadPolicy(file_bytes=256 * 1024, target_bytes=8 * 1024 * 1024)
MAX_DOCUMENTS = 200
MAX_LINKS = 2000


class DocumentationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def references(text, source):
    # Deliberately bounded Markdown subset, not a renderer or link follower.
    # Remove fenced blocks and inline code to avoid treating examples as links.
    text = re.sub(r'^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$', '', text, flags=re.M | re.S)
    text = re.sub(r'`[^`\n]*`', '', text)
    for match in re.finditer(r'(?<!!)\[[^\]\n]{0,200}\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+[^)\n]*)?\)', text):
        raw = match[1].strip('<>')
        if len(raw) > 512:
            continue
        try:
            parts = urlsplit(raw)
        except ValueError:
            continue
        if parts.scheme or parts.netloc or not parts.path:
            continue
        relative = unquote(parts.path).replace('\\_', '_')
        if any(ord(c) < 32 for c in relative) or '\\' in relative or ':' in relative:
            continue
        target = posixpath.normpath(relative.lstrip('/') if relative.startswith('/') else posixpath.join(posixpath.dirname(source), relative))
        if target == '..' or target.startswith('../'):
            continue
        yield target


def observe(root_value, scope):
    if type(scope) is not str or scope not in SCOPES:
        raise DocumentationError('invalid_document_scope')
    started = time.monotonic()

    def tick():
        if time.monotonic() - started > 8:
            raise DocumentationError('documentation_limit')

    try:
        root = _root(root_value)
        with _Snapshots(root, {}, POLICY) as reader:
            if reader.observe(root, {}, directory=True) is None:
                raise DocumentationError('root_unavailable')
            directories, captured, physical_paths = {}, {}, {}
            entries_seen = 0

            def listing(directory):
                tick()
                nonlocal entries_seen
                fd = reader.entries[directory][2]
                with os.scandir(directory if os.name == 'nt' else fd) as entries:
                    names = []
                    for entry in entries:
                        entries_seen += 1
                        if entries_seen > 4096:
                            raise DocumentationError('documentation_limit')
                        names.append(entry.name)
                return sorted(names)

            def walk(relative, recursive=False, depth=0):
                directory = root / relative
                if reader.observe(directory, {}, directory=True) is None:
                    return
                names = listing(directory)
                directories[directory] = names
                for name in names:
                    tick()
                    if name.startswith('.') or name in ('node_modules', '__pycache__'):
                        continue
                    if any(c in name for c in ':\\/') or name.endswith(('.', ' ')):
                        raise DocumentationError('unsupported_path')
                    path = directory / name
                    # Metadata only here. _Snapshots rejects links before opening content.
                    stat = path.lstat()
                    linked = bool(getattr(stat, 'st_file_attributes', 0) & 0x400) or path.is_symlink()
                    if linked:
                        if recursive or name.lower().endswith('.md'):
                            raise DocumentationError('unsupported_path')
                        continue
                    if path.is_dir():
                        if recursive:
                            if depth >= 5:
                                raise DocumentationError('documentation_limit')
                            walk(path.relative_to(root), True, depth + 1)
                        continue
                    if not name.lower().endswith('.md') or name.upper() == 'AGENTS.MD':
                        continue
                    if len(captured) >= MAX_DOCUMENTS:
                        raise DocumentationError('documentation_limit')
                    data = reader.observe(path, {})
                    if data is None:
                        raise DocumentationError('changed_input')
                    relative = path.relative_to(root).as_posix()
                    captured[relative] = data[-1]
                    physical_paths[relative] = relative

            if scope == 'project':
                walk(Path('.'))
                walk(Path('docs'), True)
                if is_contained(root):
                    # Keep application documents at the project root distinct from
                    # CC-owned canonical documents stored in the managed namespace.
                    for relative in ('CONTROLCODING.md', 'CONTROLWORK.md', 'PROJECT.md', 'STATUS.md', 'ROADMAP.md', 'BUGS.md'):
                        physical = managed_path(root, relative)
                        data = reader.observe(physical, {})
                        if data is not None:
                            managed_relative = 'cc/' + relative
                            captured[managed_relative] = data[-1]
                            physical_paths[managed_relative] = physical.relative_to(root).as_posix()
            else:
                walk(Path('_work') / ('plans' if scope == 'plans' else 'handoff'))
            reader.recheck()
            # Reset the enumeration budget for membership revalidation.
            entries_seen = 0
            if any(listing(p) != names for p, names in directories.items()):
                raise DocumentationError('changed_input')
            documents, links = [], set()
            for relative, data in sorted(captured.items()):
                tick()
                text = data.decode('utf-8-sig')
                heading = re.search(r'^#\s+(.+)', text, re.M)
                title = (heading[1].strip() if heading else PurePosixPath(relative).name)[:180]
                name = PurePosixPath(relative).name.lower()
                area = 'plans' if scope == 'plans' or 'roadmap' in name or name.endswith('-plan.md') else 'archive' if scope == 'handoffs' else 'evidence' if 'evidence' in name else 'documentation'
                documents.append({'id': 'source:' + digest(relative.encode()), 'path': relative,
                                  'physicalPath': physical_paths.get(relative, relative), 'title': title,
                                  'area': area, 'sha256': digest(data), 'bytes': len(data),
                                  'excerpt': text[:1200], 'excerpt_truncated': len(text) > 1200})
                for target in references(text, relative):
                    links.add((relative, target))
                    if len(links) > MAX_LINKS:
                        raise DocumentationError('documentation_limit')
            by_path = {d['path']: d['id'] for d in documents}
            edges = [{'source': by_path[source], 'target': by_path[target], 'kind': 'markdown_reference'}
                     for source, target in sorted(links) if target in by_path]
            unresolved = sum(target not in by_path for _, target in links)
            paths = list(SCOPES[scope])
            if scope == 'project' and is_contained(root):
                paths.extend('/cc/' + name for name in ('CONTROLCODING.md', 'CONTROLWORK.md', 'PROJECT.md', 'STATUS.md', 'ROADMAP.md', 'BUGS.md'))
            result = {'schema_version': 1, 'scope': scope, 'paths': paths, 'documents': documents,
                      'edges': edges, 'unresolved_references': unresolved,
                      'snapshot_id': digest(json.dumps({p: digest(v) for p, v in sorted(captured.items())}).encode()),
                      'notice': 'Observed source files, not imported memory or verified work. Markdown inline links only; external links and out-of-scope targets are never read. Unresolved references may be outside scope, not broken.',
                      'bytes': reader.used['target']}
            if len(json.dumps(result).encode()) > 768 * 1024:
                raise DocumentationError('documentation_limit')
            return {'project_root': root_value, 'documentation': result}
    except DocumentationError:
        raise
    except SetupServiceError as error:
        raise DocumentationError({'too_large': 'documentation_limit', 'input_limit': 'documentation_limit'}.get(error.code, error.code)) from None
    except (OSError, ValueError, TypeError, KeyError):
        raise DocumentationError('invalid_documentation') from None
