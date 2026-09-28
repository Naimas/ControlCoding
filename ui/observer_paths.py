"""Ordinary physical directory policy for the local observer and its packager."""
import json
import base64
import os
from pathlib import Path
import re
import stat
import sys


def physical_directory(value, *, must_exist=False):
    raw = os.fspath(value)
    if not raw or len(raw) > 4096 or '\0' in raw:
        raise ValueError('Invalid directory path')
    if os.name == 'nt' and raw.replace('/', '\\').startswith('\\'):
        raise ValueError('Device and UNC namespace paths are unsupported')
    path = Path(os.path.abspath(raw))
    if os.name == 'nt':
        if not re.match(r'^[A-Za-z]:\\', str(path)):
            raise ValueError('An ordinary local drive path is required')
        for part in path.parts[1:]:
            if part.endswith((' ', '.')) or any(c in part for c in ':<>"|?*'):
                raise ValueError('Ambiguous Windows path component')
    if len(path.parts) > 128:
        raise ValueError('Directory depth limit')
    # Inspect lexical ancestors before resolving: a junction must not disappear
    # from the safety check simply because realpath resolves its target.
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            raise ValueError('Only ordinary unlinked directories are supported')
    ancestor, suffix = path, []
    while not ancestor.exists():
        if ancestor.parent == ancestor:
            raise ValueError('Directory root does not exist')
        suffix.append(ancestor.name)
        ancestor = ancestor.parent
    if must_exist and suffix:
        raise ValueError('Directory does not exist')
    # Windows realpath uses the final handle path and expands existing 8.3 names.
    resolved = Path(os.path.realpath(ancestor, strict=True))
    return resolved.joinpath(*reversed(suffix))


def overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def separate_profile(profile, project=None, package=None):
    result = physical_directory(profile)
    for target in (project, package):
        if target and overlap(result, physical_directory(target)):
            raise ValueError('The profile must be separate from the project and package in both directions')
    return result


if __name__ == '__main__':
    try:
        if len(sys.argv)!=2 or len(sys.argv[1])>65536:
            raise ValueError('Invalid path request')
        request = json.loads(base64.b64decode(sys.argv[1],validate=True))
        profile, project, package = (request[k] for k in ('profile','project','package'))
        result = separate_profile(profile, project, package)
        response = {'profile': str(result), 'project': str(physical_directory(project)) if project else ''}
        if request.get('external') is True:
            source, workspace = request.get('source'), request.get('workspace')
            source = physical_directory(source, must_exist=True) if source else None
            workspace = physical_directory(workspace) if workspace else None
            if workspace and (workspace / 'external.json').exists():
                descriptor = workspace / 'external.json'
                info = descriptor.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384
                        or stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024):
                    raise ValueError('Unsafe external descriptor')
                binding = physical_directory(json.loads(descriptor.read_text(encoding='utf-8'))['source'], must_exist=True)
                if source and source != binding:
                    raise ValueError('External source mismatch')
                source = binding
            package_path = physical_directory(package, must_exist=True)
            for left, right in ((source, result), (source, package_path), (workspace, result),
                                (workspace, package_path), (source, workspace)):
                if left and right and overlap(left, right):
                    raise ValueError('External paths must be physically disjoint')
            response.update(source=str(source) if source else '', workspace=str(workspace) if workspace else '')
        print(json.dumps(response))
    except (OSError, ValueError, TypeError, KeyError):
        print('Choose ordinary local, non-overlapping profile, project and package directories.', file=sys.stderr)
        sys.exit(1)
