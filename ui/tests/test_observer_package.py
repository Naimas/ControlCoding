"""Portable artifact integrity and preservation tests; fixtures are external."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from itertools import product

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('observer_package', ROOT/'ui/package-observer.py')
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


@pytest.fixture
def candidate(tmp_path):
    source, build, runtime = [tmp_path/name for name in ('source','build','runtime')]
    for path in (source,build,runtime):
        path.mkdir()
    contract = json.loads((ROOT/'ui/observer-distribution.json').read_text())
    required = json.loads((ROOT/'controlcoding.release.json').read_text())['required']
    files = {}
    for name in required + contract['uiFiles']:
        dest = source/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,dest)
        files[name] = pkg.sha(dest.read_bytes())
    private = source/'docs/.env'
    private.write_text('PRIVATE_CANARY')
    files['docs/.env'] = pkg.sha(private.read_bytes())
    local = source/'_work/private.md'
    local.parent.mkdir()
    local.write_text('PRIVATE_CANARY')
    files['_work/private.md'] = pkg.sha(local.read_bytes())
    for name in contract['appFiles']:
        dest = build/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith('vendor/'):
            shutil.copyfile(source/'ui'/name, dest)
        else:
            dest.write_text('{}' if name.endswith('.json') else 'fixture')
    shutil.copyfile(ROOT/'ui/observer_paths.py', build/'observer_paths.py')
    info = {'schemaVersion':1, 'dependencies':{'react':'19.3.0','react-dom':'19.3.0','scheduler':'0.28.0','esbuild':'0.28.2','markdown-it':'12.3.2'},
            'parser':'babel-7.29.7', 'sourceHashes':{p:files[p] for p in contract['uiFiles'] if p.endswith(('.tsx','.cjs','.css','.html')) or p=='ui/observer_paths.py'},
            'assets':{p:pkg.sha((build/p).read_bytes()) for p in contract['appFiles'] if p!='BUILD-INFO.json'}}
    (build/'BUILD-INFO.json').write_text(json.dumps(info))
    for name in contract['runtimeRequired']:
        dest=runtime/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(b'fixture')
    (runtime/'version').write_text('44.0.0')
    pe=bytearray(100);pe[:2]=b'MZ';pe[60:64]=(64).to_bytes(4,'little');pe[64:70]=b'PE\0\0d\x86'
    (runtime/'electron.exe').write_bytes(pe)
    manifest=tmp_path/'source.json'
    manifest.write_text(json.dumps({'schemaVersion':1,'files':files}))
    return source,manifest,build,runtime,tmp_path/'artifact'


def test_deterministic_archive_private_exclusion_and_integrity(candidate):
    result=pkg.package(*candidate)
    second=pkg.package(*candidate[:-1],candidate[-1].with_name('second'))
    assert result['sha256']==second['sha256']
    assert pkg.verify(candidate[-1])['files']==result['files']
    assert not (candidate[-1]/'core/docs/.env').exists()
    assert not (candidate[-1]/'core/_work').exists()
    assert not (candidate[-1]/'core/ui').exists()
    assert (candidate[-1]/'source/ui/src/app.tsx').is_file()
    assert (candidate[-1]/'runtime/LICENSES.chromium.html').is_file()
    assert (candidate[-1]/'app/vendor/MARKDOWN-IT-LICENSE.txt').read_bytes() == (
        candidate[0]/'ui/vendor/MARKDOWN-IT-LICENSE.txt').read_bytes()
    assert (candidate[-1]/'app/vendor/markdown-it.cjs').read_bytes() == (
        candidate[0]/'ui/vendor/markdown-it.cjs').read_bytes()


def test_distribution_lists_current_production_ui_sources_and_vendor_notices():
    contract = json.loads((ROOT/'ui/observer-distribution.json').read_text())
    production = {p.relative_to(ROOT).as_posix() for pattern in ('*.cjs', '*.tsx', '*.css', '*.html')
                  for p in (ROOT/'ui').rglob(pattern) if 'tests' not in p.relative_to(ROOT/'ui').parts}
    assert production <= set(contract['uiFiles'])
    assert {'ui/vendor/README.md', 'ui/vendor/MARKDOWN-IT-LICENSE.txt'} <= set(contract['uiFiles'])
    assert {'vendor/markdown-it.cjs', 'vendor/MARKDOWN-IT-LICENSE.txt'} <= set(contract['appFiles'])
    assert len(contract['appFiles']) == len(set(contract['appFiles'])) == 53
    assert len(contract['uiFiles']) == len(set(contract['uiFiles']))


@pytest.mark.parametrize('name',['../outside','/absolute','a\\b','C:/outside','a/../b','a//b','a/CON.txt','a/file.','a/file '])
def test_unsafe_names_rejected(name):
    with pytest.raises(ValueError):pkg.relative(name)


@pytest.mark.parametrize('mutation',['source','build','extra_build','missing_build','version','architecture','extra_runtime','provenance',
                                    'markdown_dependency','markdown_source','markdown_build','markdown_license'])
def test_mismatch_rejected_before_output(candidate,mutation):
    source,manifest,build,runtime,output=candidate
    if mutation=='source':(source/'ui/main.cjs').write_text('changed')
    if mutation=='build':(build/'app.js').write_text('changed')
    if mutation=='extra_build':(build/'private.txt').write_text('PRIVATE_CANARY')
    if mutation=='missing_build':(build/'REACT-LICENSE.txt').unlink()
    if mutation=='version':(runtime/'version').write_text('0.0.0')
    if mutation=='architecture':(runtime/'electron.exe').write_bytes(b'wrong')
    if mutation=='extra_runtime':(runtime/'extra.exe').write_bytes(b'wrong')
    if mutation=='provenance':
        p=build/'BUILD-INFO.json';info=json.loads(p.read_text());info['sourceHashes'].pop('ui/build.cjs');p.write_text(json.dumps(info))
    if mutation=='markdown_dependency':
        p=build/'BUILD-INFO.json';info=json.loads(p.read_text());info['dependencies']['markdown-it']='12.3.1';p.write_text(json.dumps(info))
    if mutation=='markdown_source':
        p=source/'ui/vendor/markdown-it.cjs';p.write_text('changed')
    if mutation=='markdown_build':
        p=build/'vendor/markdown-it.cjs';p.write_text('changed')
    if mutation=='markdown_license':
        p=build/'vendor/MARKDOWN-IT-LICENSE.txt';p.write_text('changed')
    with pytest.raises(ValueError):pkg.package(*candidate)
    assert not output.exists()


@pytest.mark.parametrize('name', ['markdown-it.cjs', 'MARKDOWN-IT-LICENSE.txt'])
def test_reinventoried_vendor_replacement_still_requires_review(candidate, name):
    source, manifest, build, _runtime, output = candidate
    source_name = 'ui/vendor/' + name
    asset_name = 'vendor/' + name
    (source/source_name).write_bytes(b'replacement')
    (build/asset_name).write_bytes(b'replacement')
    source_info = json.loads(manifest.read_text())
    source_info['files'][source_name] = pkg.sha(b'replacement')
    manifest.write_text(json.dumps(source_info))
    build_info_path = build/'BUILD-INFO.json'
    build_info = json.loads(build_info_path.read_text())
    build_info['assets'][asset_name] = pkg.sha(b'replacement')
    if name == 'markdown-it.cjs':
        build_info['sourceHashes'][source_name] = pkg.sha(b'replacement')
    build_info_path.write_text(json.dumps(build_info))

    with pytest.raises(ValueError, match='Unapproved vendored Markdown'):
        pkg.package(*candidate)
    assert not output.exists()


def test_existing_destination_and_archive_are_preserved(candidate):
    output=candidate[-1];output.mkdir();sentinel=output/'sentinel';sentinel.write_text('keep')
    with pytest.raises(ValueError):pkg.package(*candidate)
    assert sentinel.read_text()=='keep'
    archive=output.with_name('new.zip');archive.write_bytes(b'keep')
    with pytest.raises(ValueError):pkg.package(*candidate[:-1],output.with_name('new'))
    assert archive.read_bytes()==b'keep'


@pytest.mark.parametrize('traversal',[False,True])
def test_internal_output_rejected(candidate,traversal):
    source=candidate[0]
    target=source/'output' if not traversal else candidate[2]/'..'/source.name/'output'
    with pytest.raises(ValueError):pkg.package(*candidate[:-1],target)
    assert not (source/'output').exists()


@pytest.mark.parametrize('mutation',['changed','extra','missing','manifest_escape'])
def test_extracted_tampering_fails(candidate,mutation):
    pkg.package(*candidate);output=candidate[-1]
    if mutation=='changed':(output/'app/app.js').write_text('changed')
    if mutation=='extra':(output/'surprise.txt').write_text('extra')
    if mutation=='missing':(output/'runtime/LICENSE').unlink()
    if mutation=='manifest_escape':
        p=output/'MANIFEST.json';value=json.loads(p.read_text());value['files']['../outside']='a'*64;p.write_text(json.dumps(value))
    with pytest.raises(ValueError):pkg.verify(output)


def test_hardlinked_input_rejected(candidate):
    path=candidate[2]/'app.js';original=path.with_name('original');path.rename(original);os.link(original,path)
    with pytest.raises(ValueError):pkg.read(candidate[2],'app.js')


def test_package_folder_move_preserves_integrity(candidate):
    pkg.package(*candidate);moved=candidate[-1].with_name('Moved unicode café');candidate[-1].rename(moved)
    assert pkg.verify(moved)['files']>0


@pytest.mark.skipif(os.name!='nt',reason='Windows launcher argv contract')
def test_launcher_preserves_spaces_unicode_and_trailing_separator(candidate,tmp_path):
    import ctypes
    pkg.package(*candidate)
    output=candidate[-1];captured=tmp_path/'arguments.txt';runner=tmp_path/'capture.ps1'
    runner.write_text('''param($Launcher,$Python,$Profile,$Project,$Capture)
function Start-Process {
 param($FilePath,$ArgumentList,$WorkingDirectory)
 [IO.File]::WriteAllText($Capture, ($ArgumentList -join ' '))
}
& $Launcher -Python $Python -Profile $Profile -Project $Project
''',encoding='utf-8-sig')
    profile=str(tmp_path/'Profile café')+'\\';project=str(tmp_path/'Project café')+'\\'
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(runner),
        str(output/'Launch-Observer.ps1'),sys.executable,profile,project,str(captured)],
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    parse=ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_int)]
    parse.restype=ctypes.POINTER(ctypes.c_wchar_p)
    count=ctypes.c_int();argv=parse('electron.exe '+captured.read_text(encoding='utf-8-sig'),ctypes.byref(count))
    try:
        values=[argv[i] for i in range(count.value)]
        assert values[-2:]==['--profile='+str(Path(profile)),'--project='+str(Path(project))]
        assert values[1]==str(output/'app')
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv,ctypes.c_void_p))
    assert not Path(profile).exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows namespace policy')
@pytest.mark.parametrize('operand,namespace',product(['source','build','runtime','output'],['extended','device','unc','forward']))
def test_namespace_aliases_rejected_before_any_output(tmp_path,operand,namespace):
    dirs={n:tmp_path/n for n in ['source','build','runtime']}
    for p in dirs.values():p.mkdir()
    dirs['output']=dirs['source']/'artifact'
    value=str(dirs[operand])
    variants={'extended':'\\\\?\\'+value,'device':'\\\\.\\'+value,'unc':'\\\\localhost\\'+value[0]+'$'+value[2:], 'forward':'//?/'+value.replace('\\','/')}
    dirs[operand]=Path(variants[namespace])
    with pytest.raises(ValueError,match='namespace'):
        pkg.package(dirs['source'],tmp_path/'absent-inventory',dirs['build'],dirs['runtime'],dirs['output'])
    assert not (tmp_path/'source/artifact').exists()
    assert not (tmp_path/'source/artifact.zip').exists()


def short_path(path):
    import ctypes
    function=ctypes.windll.kernel32.GetShortPathNameW
    function.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32]
    buffer=ctypes.create_unicode_buffer(32768)
    assert function(str(path),buffer,len(buffer))
    return Path(buffer.value)


@pytest.mark.skipif(os.name!='nt',reason='Windows missing drive policy')
def test_missing_drive_is_rejected_without_unbounded_ancestor_search():
    root=next((Path(c+':\\') for c in 'ZYXWVUTSRQPONMLKJIHGFEDCBA' if not Path(c+':\\').exists()),None)
    if root is None:pytest.skip('Every drive letter exists')
    with pytest.raises(ValueError,match='root does not exist'):
        pkg.physical_directory(root/'noncreated-profile')


@pytest.mark.skipif(os.name!='nt',reason='Windows 8.3 aliases')
def test_short_alias_output_cannot_enter_any_protected_input(tmp_path):
    dirs=[tmp_path/(n+' long directory') for n in ['source','build','runtime']]
    for p in dirs:p.mkdir()
    aliases=[short_path(p) for p in dirs]
    if all(str(a).lower()==str(p).lower() for a,p in zip(aliases,dirs)):
        # Read-only fallback when the workbench volume disables new 8.3 names.
        # No copy/mkdir can be reached: the inventory intentionally does not exist.
        existing=Path(os.environ['ProgramFiles']);alias=short_path(existing)
        if str(alias).lower()==str(existing).lower():pytest.skip('No existing 8.3 alias available')
        assert pkg.physical_directory(alias)==existing
        for role in range(3):
            inputs=list(dirs);inputs[role]=existing
            with pytest.raises(ValueError,match='external'):
                pkg.package(inputs[0],tmp_path/'absent-inventory',inputs[1],inputs[2],alias/'CC noncreated alias probe')
        return
    for protected,alias in zip(dirs,aliases):
        assert pkg.physical_directory(alias)==protected
        with pytest.raises(ValueError,match='external'):
            pkg.package(dirs[0],tmp_path/'absent-inventory',dirs[1],dirs[2],alias/'new artifact')
        assert not (protected/'new artifact').exists()
        assert not (protected/'new artifact.zip').exists()


@pytest.mark.parametrize('relation',['equal','profile-child','project-child','sibling'])
def test_physical_profile_separation(tmp_path,relation):
    from observer_paths import separate_profile
    root=tmp_path/'Existing project';root.mkdir()
    profile,project={'equal':(root,root),'profile-child':(root/'profile',root),
        'project-child':(root,root/'project'),'sibling':(root,root.with_name(root.name+' copy'))}[relation]
    if relation=='sibling':assert separate_profile(profile,project)==profile
    else:
        with pytest.raises(ValueError,match='separate'):separate_profile(profile,project)
    assert list(root.iterdir())==[]


@pytest.mark.parametrize('changed',['README.md','docs/contract.md','_work/handoff/START_HERE.md'])
def test_complete_source_audit_detects_every_declared_scope(tmp_path,changed):
    files={}
    for name in ['README.md','docs/contract.md','_work/handoff/START_HERE.md']:
        dest=tmp_path/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(b'original')
        files[name]=pkg.sha(dest.read_bytes())
    assert pkg.verify_source_inventory(tmp_path,files)=={'checked':3,'passed':True}
    (tmp_path/changed).write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):pkg.verify_source_inventory(tmp_path,files)


@pytest.mark.skipif(os.name!='nt',reason='Windows launcher')
def test_launcher_preflight_rejects_profile_project_overlap(candidate,tmp_path):
    pkg.package(*candidate)
    project=tmp_path/'Adopter café';project.mkdir();sentinel=project/'keep.txt';sentinel.write_bytes(b'keep')
    for profile,root in [(project,project),(project/'new profile',project),(tmp_path,project),
                         (short_path(project),project),(project,short_path(project))]:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(candidate[-1]/'Launch-Observer.ps1'),
            '-Python',sys.executable,'-Profile',str(profile),'-Project',str(root),'-Check'],capture_output=True,timeout=30)
        assert result.returncode!=0
        assert sentinel.read_bytes()==b'keep' and list(project.iterdir())==[sentinel]
