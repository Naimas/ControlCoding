"""Internal Windows writer for reviewed panel plans; never accepts renderer paths."""
from contextlib import ExitStack
import hashlib
import json
import os

from cc_setup_service import _Snapshots, _ordinary, ReadPolicy, SetupServiceError, _signature
from cc_project_map_definition import _exclusive, _write, _delete_owned

JOURNAL = '.controlcoding/panel-setup-transaction.json'


class PanelWriteError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def commit(root, inputs, trusted, changes, approval_id, planned_directories=()):
    """Revalidate all captured inputs, exclusively lock outputs, journal and write.

    A crash leaves a journal for diagnosis. No automatic recovery or lock stealing.
    Existing file changes use retained exclusive handles, never path replacement.
    """
    if os.name != 'nt':
        raise PanelWriteError('write_unsupported')
    journal = None
    modified, created = [], []
    with ExitStack() as stack:
        reader = stack.enter_context(_Snapshots(root, trusted, ReadPolicy()))
        handles = {}
        try:
            if reader.observe(root, {}, directory=True) is None:
                raise PanelWriteError('missing_root')
            for path, (directory, expected) in inputs.items():
                if path in changes and expected is not None:
                    reader.observe(path.parent, {}, directory=True)
                    fd = _exclusive(path)
                    stack.callback(os.close, fd)
                    info = os.fstat(fd)
                    _ordinary(info, False, 'output')
                    if _signature(info) != expected[:6] or info.st_size > 1024 * 1024:
                        raise PanelWriteError('changed_input')
                    before = os.read(fd, info.st_size + 1)
                    if before != expected[-1]:
                        raise PanelWriteError('changed_input')
                    handles[path] = (fd, before)
                else:
                    actual = reader.observe(path, {}, directory=directory)
                    if actual != expected:
                        raise PanelWriteError('changed_input')
            reader.recheck()
            # Missing inputs have been checked. CREATE_NEW prevents a late file
            # from being overwritten; retained existing ancestors prevent relinking.
            for path in [p for p, (_, snap, _) in reader.entries.items() if snap is None]:
                del reader.entries[path]
            directories = {root / '.controlcoding', *planned_directories}
            if any(not p.is_relative_to(root) or p == root for p in directories):
                raise PanelWriteError('invalid_plan')
            for path in changes:
                if not path.is_relative_to(root):
                    raise PanelWriteError('invalid_plan')
                directories.update(p for p in path.parents if p.is_relative_to(root) and p != root)
            for path in sorted(directories, key=lambda p: len(p.parts)):
                try:
                    path.mkdir()
                    created.append(path)
                except FileExistsError:
                    pass
                reader.observe(path, {}, directory=True)
            journal = _exclusive(root / JOURNAL, create=True)
            stack.callback(os.close, journal)
            backup = {'schema_version': 1, 'approval_id': approval_id, 'state': 'prepared', 'files': [
                {'path': p.relative_to(root).as_posix(), 'before': handles[p][1].decode('utf-8') if p in handles else None,
                 'after_sha256': hashlib.sha256(data).hexdigest()} for p, data in changes.items()]}
            _write(journal, json.dumps(backup, ensure_ascii=False).encode('utf-8'))
            for path in changes:
                if path not in handles:
                    fd = _exclusive(path, create=True)
                    stack.callback(os.close, fd)
                    handles[path] = (fd, None)
            reader.recheck()
            for path, data in changes.items():
                fd, before = handles[path]
                modified.append(path)
                _write(fd, data)
            _delete_owned(journal)
            return {'saved': True, 'files': [p.relative_to(root).as_posix() for p in changes],
                    'directories_created': [p.relative_to(root).as_posix() for p in created]}
        except (OSError, SetupServiceError, ValueError) as error:
            rollback_ok = True
            for path, (fd, before) in reversed(list(handles.items())):
                try:
                    if before is None:
                        _delete_owned(fd)
                    elif path in modified:
                        _write(fd, before)
                except OSError:
                    rollback_ok = False
            if journal is not None and rollback_ok:
                try:
                    _delete_owned(journal)
                except OSError:
                    rollback_ok = False
            if not rollback_ok:
                raise PanelWriteError('recovery_required') from None
            if isinstance(error, PanelWriteError):
                raise
            raise PanelWriteError('write_conflict') from None
