"""Explicit external combined-capacity experiment, optionally using local embeddings."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service, knowledge_store as storage
from cc_memory_lib.knowledge_sources import LIMITS
from cc_memory_lib.knowledge_semantic import OllamaEmbedding


def memory():
    if os.name != 'nt':
        import resource
        return {'peak_rss_platform_units': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
             'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
             'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'rss_bytes': counters.WorkingSetSize, 'peak_rss_bytes': counters.PeakWorkingSetSize,
            'private_bytes': counters.PrivateUsage, 'scope': 'Python benchmark process only; excludes Ollama and GPU'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--embed', action='store_true')
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--recovery', action='store_true')
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    root = args.destination.resolve()
    checkout = Path(__file__).resolve().parents[1]
    if root == checkout or checkout in root.parents or root in checkout.parents:
        raise ValueError('External workbench required')
    marker = root / 'combined-scale.json'
    if args.resume:
        report = json.loads(marker.read_text(encoding='utf-8'))
        assert report['fixture'] == 'cc-combined-scale-v1'
    else:
        if root.exists():
            raise ValueError('New fixture directory required')
        root.mkdir(parents=True)
        report = {'fixture': 'cc-combined-scale-v1', 'timings': {}, 'production_limits_unchanged': True}
    if not args.production:
        LIMITS.update(files=5000, sources=6000, conversations=1000, chunks=50000,
                      total_bytes=128*1024*1024, entries=60000, seconds=180)
        storage.MAX_DATABASE_BYTES = 2*1024*1024*1024
    report['production_limits_unchanged'] = not args.production
    report['uses_production_limits'] = args.production
    report['completed'] = False
    report['experimental_limits'] = dict(LIMITS)
    project = root / 'project'
    def save():
        report['memory'] = memory()
        marker.write_text(json.dumps(report, indent=2), encoding='utf-8')
    def phase(name, call):
        start = time.perf_counter()
        result = call()
        report['timings'][name] = time.perf_counter()-start
        save()
        print(json.dumps({'phase': name, 'seconds': report['timings'][name]}), flush=True)
        return result
    def text(number, sections):
        return '\n'.join((f'Section {section} document {number}: ' + ('documented requirement evidence ' * 60))[:1500] for section in range(sections))
    if not args.resume:
        docs = project / 'docs'
        docs.mkdir(parents=True)
        for number in range(5000):
            folder = docs / f'group-{number//100:02}'
            folder.mkdir(exist_ok=True)
            (folder / f'item-{number:04}.md').write_text(f'# Source {number}\n\n'+text(number, 9), encoding='utf-8')
        service.configure(project, {**service.DEFAULT, 'embedding': 'bge-m3:latest', 'automatic': False})
        for number in range(1000):
            service.conversation(project, {'id': f'scale-{number}', 'title': f'Session {number}',
                'retention': 'summary', 'summary': text(number, 5), 'status': 'closed', 'turns': []})
        state = phase('cold_reconcile', lambda: service.reconcile(project))
        assert state['counts']['sources'] == 6000 and state['counts']['chunks'] == 50000 and state['counts']['conversations'] == 1000, state['counts']
        report['counts'] = state['counts']
        save()
    if args.embed:
        adapter = OllamaEmbedding('bge-m3:latest')
        report['embedding_identity'] = adapter.pin()
        start = time.perf_counter()
        state = service.status(project)
        while state['counts']['embedded'] < state['counts']['chunks']:
            state = service.index(project, adapter)
            if state['counts']['embedded'] % 256 == 0 or state['counts']['embedded'] == state['counts']['chunks']:
                report['counts'] = state['counts']
                report['embedding_elapsed_this_run'] = time.perf_counter()-start
                save()
                print(json.dumps({'embedded': state['counts']['embedded'], 'total': state['counts']['chunks']}), flush=True)
    samples = []
    for index in range(args.samples):
        phase(f'warm_reconcile_{index}', lambda: service.reconcile(project))
        start = time.perf_counter()
        result = service.query(project, 'documented requirement evidence', args.embed)
        samples.append(time.perf_counter()-start)
        assert result['citations']
    report['query_seconds'] = samples
    report['query_p95_small_sample'] = max(samples)
    report['refresh_p95_seconds'] = sorted(report['timings'][f'warm_reconcile_{i}'] for i in range(args.samples))[max(0, int(.95*args.samples+.999)-1)]
    if args.recovery:
        edited = project/'docs/group-00/item-0000.md'
        def change(label):
            lines = edited.read_text(encoding='utf-8').splitlines()
            lines[0] = '# Source 0 '+label+' '+str(time.time_ns())
            edited.write_text('\n'.join(lines), encoding='utf-8')
        change('changed')
        phase('changed_reconcile', lambda: service.reconcile(project))
        before = service.status(project)['generation']
        change('restart')
        worker = root/'interrupt-capture.py'
        worker.write_text('import sys,os\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(checkout/'scripts'))+')\n'
            'from cc_memory_lib import knowledge_service as s, knowledge_store as d, knowledge_checkpoints as c\n'
            'from cc_memory_lib.knowledge_sources import LIMITS\nLIMITS.update('+repr(dict(LIMITS))+')\n'
            'd.MAX_DATABASE_BYTES=2147483648\noriginal=c.Writer.flush\n'
            'def stop(self):\n original(self)\n os._exit(17)\n'
            'c.Writer.flush=stop\ns.reconcile(Path('+repr(str(project))+'))\n', encoding='utf-8')
        process = subprocess.run([sys.executable, '-I', '-B', str(worker)], timeout=60,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert process.returncode == 17
        interrupted = service.status(project)
        assert interrupted['generation'] == before and interrupted['needs_reconcile']
        phase('recovery_reconcile', lambda: service.reconcile(project))
        report['interruption_recovered'] = True
        if args.embed:
            while service.status(project)['counts']['embedded'] < service.status(project)['counts']['chunks']:
                service.index(project, adapter)
        report['counts'] = service.status(project)['counts']
    report['database_bytes'] = (project / '.controlcoding/knowledge/knowledge.db').stat().st_size
    report['completed'] = True
    save()


if __name__ == '__main__':
    main()
