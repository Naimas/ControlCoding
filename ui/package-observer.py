"""Build an optional observer candidate from an explicit source hash inventory.

No install, Git operation, network request or adopter-project action. See DISTRIBUTION.md.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from cc_docs import load_release_manifest_contract, release_manifest_path_matches
sys.path.insert(0, str(ROOT / 'ui'))
from observer_paths import physical_directory, overlap


def require(value, message):
    if not value:
        raise ValueError(message)


def relative(value):
    require(type(value) is str and 0 < len(value) <= 1024, 'Invalid package path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value and all(
        p not in ('.', '..') and not p.endswith((' ', '.')) and
        not any(ord(c) < 32 or c in '\\:<>"|?*' for c in p) and
        p.split('.')[0].upper() not in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(n) for n in range(1,10)), *('LPT'+str(n) for n in range(1,10))}
        for p in path.parts), 'Unsafe package path')
    return value


def ordinary(path, directory=False):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024,
            'Linked package input or destination')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            'Nonordinary package input or destination')
    return info


def confined(root, name):
    relative(name)
    for parent in [root, *reversed(root.parents)]:
        ordinary(parent, True)
    path = root
    for part in PurePosixPath(name).parts[:-1]:
        path /= part
        ordinary(path, True)
    return root / name


def read(root, name, limit=536870912):
    path = confined(root, name)
    before = ordinary(path)
    require(before.st_size <= limit, 'Package input too large')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
    require(len(data) <= limit and signature(before) == signature(after) == signature(ordinary(path)),
            'Package input changed')
    return data


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    result, pending, count = [], [root], 0
    while pending:
        folder = pending.pop()
        ordinary(folder, True)
        for entry in sorted(folder.iterdir()):
            count += 1
            require(count <= 5000, 'Package inventory limit')
            info = entry.lstat()
            if stat.S_ISDIR(info.st_mode):
                ordinary(entry, True)
                pending.append(entry)
            else:
                ordinary(entry)
                result.append(relative(entry.relative_to(root).as_posix()))
    require(len({p.casefold() for p in result}) == len(result), 'Case-colliding package paths')
    return sorted(result)


def core_paths(root, files):
    contract = load_release_manifest_contract(root)
    require(contract['present'] and not contract['issues'], 'Invalid Core release contract')
    def public(p):
        return all(not part.startswith('.') or i == 0 and part in ('.github', '.gitignore') for i,part in enumerate(PurePosixPath(p).parts)) and Path(p).suffix.lower() not in ('.pyc','.pem','.key','.pfx','.db','.sqlite','.log')
    selected = [p for p in files if public(p) and any(release_manifest_path_matches(p, e['pattern']) for e in contract['allow'])
                and not any(release_manifest_path_matches(p, e['pattern']) for e in contract['deny'])]
    require(set(contract['required']) <= set(selected), 'Required Core source missing from inventory')
    return sorted(selected)


def verify(folder):
    manifest = json.loads(read(folder, 'MANIFEST.json', 2097152))
    require(manifest.get('schemaVersion') == 1 and type(manifest.get('files')) is dict, 'Invalid artifact manifest')
    require(set(inventory(folder)) == set(manifest['files']) | {'MANIFEST.json'}, 'Artifact file inventory mismatch')
    for name, identity in manifest['files'].items():
        require(sha(read(folder, name)) == identity, 'Artifact hash mismatch')
    return {'files': len(manifest['files']), 'status': manifest['status']}


def verify_source_inventory(root, files):
    """Audit every declared entry, including root files and intentional local handoffs.

    Callers must provide only explicitly inventoried files; there is no prefix filter.
    Package input inventories and final workspace inventories are separate snapshots.
    """
    require(type(files) is dict and 0 < len(files) <= 5000, 'Invalid source inventory')
    for name, identity in files.items():
        require(type(identity) is str and len(identity)==64, 'Invalid source hash')
        require(sha(read(root,name))==identity, 'Source inventory hash mismatch: '+name)
    return {'checked':len(files), 'passed':True}


def package(source, source_manifest, build, runtime, output):
    source, build, runtime = [physical_directory(p, must_exist=True) for p in (source, build, runtime)]
    output = physical_directory(output)
    for parent in [output.parent, *output.parent.parents]:
        ordinary(parent, True)
    for protected in (source, build, runtime):
        require(not overlap(output, protected), 'Output must be external')
    archive = output.with_suffix('.zip')
    require(archive != output, 'Choose a directory name without .zip suffix')
    require(not output.exists() and not archive.exists(), 'Output already exists; nothing replaced')
    source_manifest = Path(source_manifest).absolute()
    source_info = json.loads(read(source_manifest.parent, source_manifest.name, 2097152).decode('utf-8-sig'))
    require(source_info.get('schemaVersion') == 1 and type(source_info.get('files')) is dict and len(source_info['files']) <= 5000, 'Invalid source inventory')
    files = source_info['files']
    for name, identity in files.items():
        relative(name)
        require(type(identity) is str and len(identity) == 64 and all(c in '0123456789abcdef' for c in identity), 'Invalid source hash')
    contract = json.loads(read(source, 'ui/observer-distribution.json', 65536))
    require(contract['schemaVersion'] == 1 and contract['platform'] == 'win32-x64', 'Unsupported distribution')
    core = core_paths(source, files)
    ui = contract['uiFiles']
    require(set(ui) <= set(files), 'UI source inventory incomplete')
    inputs = {}
    for name in core + ui:
        data = read(source, name)
        require(sha(data) == files[name], 'Source differs from reviewed inventory')
        inputs[('core/' + name) if name in core else ('source/' + name)] = (source, name, sha(data))
    require(set(inventory(build)) == set(contract['appFiles']), 'Unexpected or missing build file')
    info = json.loads(read(build, 'BUILD-INFO.json', 2097152))
    require(info['schemaVersion'] == 1 and info['dependencies'] == {
        'react':'19.3.0', 'react-dom':'19.3.0', 'scheduler':'0.28.0',
        'esbuild':'0.28.2', 'markdown-it':'12.3.2',
    } and info['parser'] == 'babel-7.29.7', 'Unapproved build dependencies')
    require(set(info['assets']) == set(contract['appFiles']) - {'BUILD-INFO.json'}, 'Build asset inventory mismatch')
    required_sources = {p for p in ui if p.endswith(('.tsx','.cjs','.css','.html')) or p == 'ui/observer_paths.py'}
    require(set(info['sourceHashes']) == required_sources, 'Build source provenance incomplete')
    for name, identity in info['sourceHashes'].items():
        require(files.get(name) == identity, 'Build does not match source inventory')
    require(files['ui/vendor/markdown-it.cjs'] ==
            '2e77c809205e08971b002366974580295051cad390a607cc25c12731b066790c',
            'Unapproved vendored Markdown parser')
    require(files['ui/vendor/MARKDOWN-IT-LICENSE.txt'] ==
            '792c48c5a849a15fdf9e37e8bcf9e6d1dd13b32b46c642a748a0a46a9919d473',
            'Unapproved vendored Markdown license')
    require(info['assets']['vendor/markdown-it.cjs'] == files['ui/vendor/markdown-it.cjs'] and
            info['assets']['vendor/MARKDOWN-IT-LICENSE.txt'] == files['ui/vendor/MARKDOWN-IT-LICENSE.txt'],
            'Vendored Markdown parser or license differs from reviewed source')
    for name in contract['appFiles']:
        identity = sha(read(build, name))
        require(name == 'BUILD-INFO.json' or info['assets'][name] == identity, 'Changed build asset')
        inputs['app/' + name] = (build, name, identity)
    runtime_files = inventory(runtime)
    require(set(contract['runtimeRequired']) <= set(runtime_files), 'Runtime incomplete')
    require(read(runtime, 'version', 100).decode().strip() == contract['electron'], 'Unsupported Electron version')
    executable = read(runtime, 'electron.exe')
    offset = int.from_bytes(executable[60:64], 'little')
    require(executable[:2] == b'MZ' and executable[offset:offset+4] == b'PE\0\0' and
            executable[offset+4:offset+6] == b'd\x86', 'Runtime is not Windows x64')
    del executable
    for name in runtime_files:
        require(name in contract['runtimeRequired'] or name == 'resources/default_app.asar' or
                name.startswith('locales/') and name.count('/') == 1 and name.endswith('.pak') or
                name in ('chrome_100_percent.pak','chrome_200_percent.pak','d3dcompiler_47.dll','dxcompiler.dll','dxil.dll',
                         'ffmpeg.dll','snapshot_blob.bin','vk_swiftshader.dll','vk_swiftshader_icd.json','vulkan-1.dll'), 'Unexpected runtime file')
        inputs['runtime/' + name] = (runtime, name, sha(read(runtime, name)))
    inputs['Launch-Observer.ps1'] = (source, 'ui/Launch-Observer.ps1', files['ui/Launch-Observer.ps1'])
    inputs['README.md'] = (source, 'ui/DISTRIBUTION.md', files['ui/DISTRIBUTION.md'])
    require(len(inputs) <= contract['limits']['files'], 'Artifact file limit')
    total, hashes = 0, {}
    output.mkdir()  # Exclusive creation. Failure leaves an inspectable partial directory.
    for name, (base, origin, identity) in sorted(inputs.items()):
        data = read(base, origin, contract['limits']['fileBytes'])
        require(sha(data) == identity, 'Input changed before copy')
        total += len(data)
        require(total <= contract['limits']['totalBytes'], 'Artifact byte limit')
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(data)
        hashes[name] = identity
    manifest = {'schemaVersion':1, 'package':contract['package'], 'status':contract['status'],
                'platform':contract['platform'], 'electron':contract['electron'], 'pythonMinimum':contract['pythonMinimum'],
                'coreContractSha256':files['controlcoding.release.json'], 'unavailable':contract['unavailable'],
                'files':hashes}
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2)+'\n').encode('utf-8')
    (output/'MANIFEST.json').write_bytes(manifest_bytes)
    verify(output)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipout:
        for name in inventory(output):
            entry = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            data = read(output, name)
            require(sha(data) == (sha(manifest_bytes) if name == 'MANIFEST.json' else hashes[name]),
                    'Artifact changed before archive copy')
            zipout.writestr(entry, data)
    return {'archive':str(archive), 'sha256':sha(archive.read_bytes()), 'files':len(hashes), 'bytes':total}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', type=Path)
    for name in ('source','source-manifest','build','runtime','output'):
        parser.add_argument('--'+name, type=Path)
    args = parser.parse_args()
    try:
        if args.verify:
            result = verify(args.verify)
        else:
            require(all(getattr(args,n) is not None for n in ('source','source_manifest','build','runtime','output')), 'Missing package arguments')
            result = package(args.source,args.source_manifest,args.build,args.runtime,args.output)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print('Observer package failed: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
