"""Trusted external observer boundary. Not a sandbox for arbitrary/native code.

Install only in the dedicated, one-request -I -B process. No project module is
imported and no child process or network connection is permitted after activation.
"""
import os
from pathlib import Path
import re
import stat
import sys


class ExternalError(ValueError):
    pass


def reserved(part):
    return bool(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I))


def ordinary(path, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
        raise ExternalError('unsupported_path')
    return info


def absolute(value, exists=True):
    if not isinstance(value, (str, Path)):
        raise ExternalError('invalid_path')
    value = str(value)
    if not value or len(value) > 4096 or '\x00' in value or not os.path.isabs(value):
        raise ExternalError('absolute_path_required')
    if os.name == 'nt' and (not re.match(r'^[A-Za-z]:[\\/]', value) or value.startswith(('\\', '//'))):
        raise ExternalError('unsupported_namespace')
    result = Path(value)
    if len(result.parts) > 128:
        raise ExternalError('path_limit')
    for part in result.parts[1:]:
        if part in ('.', '..') or reserved(part) or part.endswith((' ', '.')) or re.search(r'[:<>"|?*\x00-\x1f]', part):
            raise ExternalError('ambiguous_path')
    for parent in (*reversed(result.parents), result):
        try:
            ordinary(parent, True)
        except FileNotFoundError:
            if exists:
                raise ExternalError('missing_directory') from None
    # Resolve Windows short names/case aliases only after rejecting every link.
    return result.resolve(strict=exists)


def inside(child, parent):
    return child == parent or parent in child.parents


def disjoint(left, right):
    if inside(left, right) or inside(right, left):
        raise ExternalError('overlapping_roots')


def selection(value):
    if type(value) is not list or not 0 < len(value) <= 128:
        raise ExternalError('invalid_scope')
    checked = []
    for name in value:
        if type(name) is not str or not name or len(name) > 1024:
            raise ExternalError('invalid_scope')
        parts = name.split('/')
        if any(not p or p in ('.', '..') or reserved(p) or p.endswith((' ', '.')) or
               re.search(r'[\\:\x00-\x1f<>"|?*]', p) for p in parts):
            raise ExternalError('invalid_scope')
        if any(p.lower() in ('.git', '.hg', '.svn', 'node_modules', '__pycache__') for p in parts):
            raise ExternalError('unsupported_scope')
        checked.append(name)
    if len({p.casefold() for p in checked}) != len(checked):
        raise ExternalError('duplicate_scope')
    return sorted(checked)


def safe_tree(root, limit=100000):
    """Validate existing output files too, including SQLite's native sidecars."""
    pending, count = [root], 0
    while pending:
        current = pending.pop()
        ordinary(current, True)
        with os.scandir(current) as entries:
            for entry in entries:
                count += 1
                if count > limit:
                    raise ExternalError('workspace_budget')
                path = Path(entry.path)
                directory = entry.is_dir(follow_symlinks=False)
                ordinary(path, directory)
                if directory:
                    pending.append(path)


def install_guard(workspace):
    """Defense in depth for this trusted stdlib/SQLite service, not arbitrary code."""
    workspace = absolute(workspace, exists=False)

    def writable(value, dir_fd=None):
        if dir_fd not in (None, -1) or isinstance(value, int):
            raise PermissionError('external_write_denied')
        try:
            target = Path(os.fsdecode(value))
            if not target.is_absolute():
                target = Path.cwd() / target
            target = Path(os.path.abspath(target))
            if not inside(target, workspace):
                raise PermissionError('external_write_denied')
            for parent in (*reversed(target.parents), target):
                try:
                    info = parent.lstat()
                    ordinary(parent, stat.S_ISDIR(info.st_mode))
                except FileNotFoundError:
                    pass
            if not inside(target.resolve(strict=False), workspace):
                raise PermissionError('external_write_denied')
        except (ExternalError, TypeError, ValueError):
            raise PermissionError('external_write_denied') from None

    def audit(event, args):
        if (event.startswith(('subprocess.', 'os.exec', 'os.spawn', 'os.posix_spawn', 'os.fork'))
                or event in ('os.system', 'os.startfile', 'os.startfile/2')
                or event.startswith('socket.')):
            raise PermissionError('external_execution_denied')
        if event == 'open':
            mode, flags = args[1:3]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or \
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                writable(args[0])
        elif event == 'sqlite3.connect':
            writable(args[0])
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown', 'os.utime', 'os.truncate'):
            index = {'os.remove': 1, 'os.rmdir': 1, 'os.mkdir': 2, 'os.chmod': 2,
                     'os.chown': 3, 'os.utime': 3}.get(event)
            writable(args[0], args[index] if index is not None and len(args) > index else None)
        elif event in ('os.rename', 'os.link'):
            writable(args[0], args[2] if len(args) > 2 else None)
            writable(args[1], args[3] if len(args) > 3 else None)
            if event == 'os.link':
                raise PermissionError('external_link_denied')
        elif event == 'os.symlink':
            raise PermissionError('external_link_denied')
        elif event in ('os.chdir', 'os.fchdir'):
            writable(args[0])
    sys.addaudithook(audit)
    return workspace
