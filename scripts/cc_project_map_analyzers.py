"""Pure, versioned static measurements; no project imports or execution."""
import ast
import hashlib
import json
import posixpath
import re

PYTHON = 'python-ast/v1'
JAVASCRIPT = 'babel-static/v1'
NAME = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\Z')
SPECIFIER = re.compile(r'[A-Za-z0-9_@./-]{1,160}\Z')
CODE = frozenset('.py .pyi .js .jsx .ts .tsx .mjs .cjs'.split())


def digest(value):
    return hashlib.sha256(value).hexdigest()


def python_ast(data, tick):
    try:
        tree = ast.parse(data, filename='<map-analysis>')
    except (ValueError, SyntaxError, UnicodeError, RecursionError):
        return {'state': 'parse_unavailable', 'symbols': [], 'imports': []}
    symbols, imports, counts = [], [], {}
    stack, count = [(tree, None, None)], 0
    while stack:
        tick()
        node, parent, function = stack.pop()
        count += 1
        if count > 20000:
            raise ValueError('parse_limit')
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            title = (parent['title'] + '.' if parent else '') + ('lambda' if isinstance(node, ast.Lambda) else node.name)
            if len(title) > 160 or not NAME.fullmatch(title):
                return {'state': 'unsupported_identifier', 'symbols': [], 'imports': []}
            counts[title] = counts.get(title, 0) + 1
            row = {'key': title + ':' + str(counts[title]), 'title': title,
                   'kind': 'class' if isinstance(node, ast.ClassDef) else 'function',
                   'line': node.lineno, 'end_line': node.end_lineno, 'parent': parent['key'] if parent else None,
                   'branches': 0 if isinstance(node, ast.ClassDef) else 1, 'duplicate': None}
            symbols.append(row)
            if row['kind'] == 'function' and not isinstance(node, ast.Lambda):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                    body = body[1:]
                if row['end_line'] - row['line'] + 1 >= 8 and len(body) >= 5:
                    row['duplicate'] = digest((ast.dump(node.args) + ''.join(ast.dump(n) for n in body)).encode())
            parent, function = row, row if row['kind'] == 'function' else None
        elif function:
            if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)):
                function['branches'] += 1
            elif isinstance(node, ast.BoolOp):
                function['branches'] += len(node.values) - 1
            elif isinstance(node, ast.comprehension):
                function['branches'] += 1 + len(node.ifs)
            elif isinstance(node, ast.match_case):
                function['branches'] += 1
        if isinstance(node, ast.Import):
            imports.extend({'module': a.name, 'level': 0, 'names': [], 'line': node.lineno, 'dynamic': False} for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            groups = [[a.name] for a in node.names] if not node.module else [[a.name for a in node.names]]
            imports.extend({'module': node.module or '', 'level': node.level, 'names': names, 'line': node.lineno, 'dynamic': False} for names in groups)
        elif isinstance(node, ast.Call) and (isinstance(node.func, ast.Name) and node.func.id == '__import__' or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'):
            imports.append({'module': '', 'level': 0, 'names': [], 'line': node.lineno, 'dynamic': True})
        stack.extend((child, parent, function) for child in reversed(list(ast.iter_child_nodes(node))))
    if len(symbols) > 512 or len(imports) > 512:
        raise ValueError('parse_limit')
    return {'state': 'parsed', 'symbols': symbols, 'imports': imports, 'ast_nodes': count}


def resolve_import(path, item, paths, python_roots):
    """Conservative candidate lookup in the retained inventory, never the runtime."""
    specifier = item['module']
    if item['dynamic'] or not specifier and not item['level']:
        return [], 'unresolved', 'dynamic_reference'
    if specifier and not SPECIFIER.fullmatch(specifier):
        return [], 'unresolved', 'unsupported_specifier'
    candidates = set()
    if path.lower().endswith(('.py', '.pyi')):
        roots = [r for r in python_roots if not r or path.startswith(r + '/')]
        if item['level']:
            for root in roots:
                package = posixpath.dirname(path[len(root) + 1:] if root else path).split('/')
                package = [part for part in package if part]
                if item['level'] > len(package):
                    continue
                base = package[:len(package) - item['level'] + 1]
                names = ['.'.join(base + ([specifier] if specifier else []) + [name]) for name in item['names'] if name != '*'] if not specifier else ['.'.join(base + [specifier])]
                for name in names:
                    prefix = (root + '/' if root else '') + name.replace('.', '/')
                    candidates.update(p for p in (prefix + '.py', prefix + '/__init__.py') if p in paths)
        else:
            for root in python_roots:
                prefix = (root + '/' if root else '') + specifier.replace('.', '/')
                candidates.update(p for p in (prefix + '.py', prefix + '/__init__.py') if p in paths)
    elif specifier.startswith('./') or specifier.startswith('../'):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
        if base == '..' or base.startswith('../'):
            return [], 'unresolved', 'outside_selected_root'
        if posixpath.splitext(base)[1]:
            # No tsconfig-dependent .js-to-.ts substitutions are guessed.
            candidates = {base} & paths
        else:
            candidates = {base + suffix for suffix in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '/index.js', '/index.ts', '/index.tsx', '/index.jsx')} & paths
    else:
        return [], 'unresolved', 'package_or_alias_context_unassessed'
    ordered = sorted(candidates)
    return ordered, 'local_candidate' if len(ordered) == 1 else 'ambiguous' if ordered else 'unresolved', 'static_inventory_match' if ordered else 'no_local_candidate_not_proof_of_breakage'


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
