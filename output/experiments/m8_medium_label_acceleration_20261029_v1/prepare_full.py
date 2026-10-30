"""Freeze an optimized run without overwriting the original M01 experiment."""
from pathlib import Path
import hashlib
import json
import os
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'output/experiments/m8_medium_optimized_20261029_v1'
BASE = ROOT / 'output/experiments/m8_medium_m01_20261029_v1'
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise FileExistsError(OUT)
    OUT.mkdir()
    code = (BASE / 'probe.py').read_text(encoding='utf-8')
    start = code.index('def prepare():')
    end = code.index('def finite(value):')
    code = code[:start]+code[end:]
    code = code.replace('import argparse\n', 'import argparse\nimport gzip\nimport pickle\n')
    code = code.replace("    target = Path(path)\n    target.parent.mkdir(parents=True, exist_ok=True)",
        "    target = Path(path)\n    target.parent.mkdir(parents=True, exist_ok=True)\n"
        "    if target.name == 'trip_library.json':\n"
        "        with gzip.open(target.with_suffix('.pkl.gz'), 'wb', compresslevel=1) as stream:\n"
        "            pickle.dump(value, stream, protocol=5)\n"
        "        return")
    code = code.replace("    save(HERE / 'started.json', dict(at=datetime.now(timezone.utc).isoformat()))\n    p = load()",
        "    save(HERE / 'started.json', dict(at=datetime.now(timezone.utc).isoformat()))\n"
        "    from experiments.runs.model8_trip_library_native import build_native\n"
        "    compile_started = time.perf_counter()\n"
        "    build_native(manifest['compiler'], HERE / 'TripOracle.exe')\n"
        "    compile_seconds = time.perf_counter()-compile_started\n"
        "    p = load()")
    code = code.replace("seed=42, threads=1, budget_seconds=manifest['budget_seconds'], complete_route_library=False,",
        "seed=42, threads=1, budget_seconds=manifest['budget_seconds'], complete_route_library=False, compile_seconds=compile_seconds,")
    code = code.replace("        save(HERE / 'trip_library.json', library)\n", "        export_started = time.perf_counter()\n        save(HERE / 'trip_library.json', library)\n        row['library_export_seconds'] = time.perf_counter()-export_started\n")
    code = code.replace("            if domain['N_min'] == domain['N_max'] and unchanged_clocks:", "            if unchanged_clocks:")
    code = code.replace("best, domain['retained_K'], domain['N_min'], lower, library,", "best, domain['retained_K'], [domain['N_min'], domain['N_max']], lower, library,")
    code = code.replace("    parser.add_argument('action', choices=['prepare', 'run'])", "    parser.add_argument('action', choices=['run'])")
    code = code.replace("    prepare() if args.action == 'prepare' else run()", "    run()")
    (OUT / 'study.py').write_text(code, encoding='utf-8')
    shared = '''from pathlib import Path
from experiments.runs.research_model8_shared_relaxation import build_relaxation as _build
from experiments.runs.model8_trip_library_native import enumerate_trip_library as _enumerate
HERE = Path(__file__).resolve().parent
def build_relaxation(problem, library):
    return _build(problem, library, max_orders=20, aggregate_route_links=True)
def enumerate_trip_library(problem, **kwargs):
    return _enumerate(problem, native_command=HERE/'TripOracle.exe', include_paths=False,
                      archive_directory=HERE/'native_routes', **kwargs)
'''
    (OUT / 'shared_medium.py').write_text(shared, encoding='utf-8')
    (OUT / 'master_sync.py').write_text('from experiments.runs.model8_trip_sync import augment\n', encoding='utf-8')
    for name in ('phase_bound.py', 'route_bounds_medium.py'):
        (OUT / name).write_bytes((BASE / name).read_bytes())
    decomposition = (BASE / 'decomposition_medium.py').read_text(encoding='utf-8')
    decomposition = decomposition.replace("        ctx['pool'].LB = ctx['pool'].UB = pool", """        pool_min, pool_max = (pool, pool) if isinstance(pool, int) else tuple(pool)
        ctx['pool'].LB, ctx['pool'].UB = pool_min, pool_max
        pool_selectors = master.addVars(range(pool_min, pool_max+1), vtype=gp.GRB.BINARY, name='exact_pool_choice')
        master.addConstr(pool_selectors.sum() == 1)
        master.addConstr(ctx['pool'] == gp.quicksum(value*variable for value, variable in pool_selectors.items()))""")
    decomposition = decomposition.replace("        cache = {}", "        cache = {}")
    decomposition = decomposition.replace("    cache = {}", "    cache = {}\n    patterns_seen = set()")
    decomposition = decomposition.replace("            k = next(k for k,v in ctx['selectors'].items() if v.X>.5)",
        "            k = next(k for k,v in ctx['selectors'].items() if v.X>.5)\n            selected_pool = next(value for value, variable in pool_selectors.items() if variable.X>.5)")
    decomposition = decomposition.replace("signature = (k,tuple(assignments", "signature = (k,selected_pool,tuple(assignments")
    decomposition = decomposition.replace("            if signature not in cache:\n", """            if signature not in cache:
                patterns_seen.add(signature)
                if len(cache) >= 4:
                    oldest = next(iter(cache))
                    cache.pop(oldest)['model'].dispose()
""")
    decomposition = decomposition.replace("sub['pool'].LB = sub['pool'].UB = pool", "sub['pool'].LB = sub['pool'].UB = selected_pool")
    decomposition = decomposition.replace("                add_shared_route_bounds(child,model,sub,library)\n                for c,row in enumerate(library):", "                pattern_library = [row for row in library if row['mask'] in masks]\n                add_shared_route_bounds(child,model,sub,pattern_library)\n                for c,row in enumerate(pattern_library):")
    decomposition = decomposition.replace("chosen_variables = [ctx['selectors'][k]]", "chosen_variables = [ctx['selectors'][k], pool_selectors[selected_pool]]")
    decomposition = decomposition.replace("event = dict(iteration=len(events)+1,K=k,", "event = dict(iteration=len(events)+1,K=k,N=selected_pool,")
    decomposition = decomposition.replace("patterns=len(cache)", "patterns=len(patterns_seen)")
    decomposition = decomposition.replace("retained_N=[pool,pool]", "retained_N=[pool_min,pool_max]")
    (OUT / 'decomposition_medium.py').write_text(decomposition, encoding='utf-8')
    for name in ('initial_audited_result.json', 'initial_audit.json'):
        (OUT / name).write_bytes((BASE / name).read_bytes())
    manifest = json.loads((BASE / 'manifest.json').read_text(encoding='utf-8'))
    files = [Path(name) for name in manifest['hashes'] if '/model_3/' not in name.replace('\\', '/')]
    files = [p for p in files if BASE not in p.parents]
    files += list(OUT.glob('*.py'))+[OUT / 'initial_audited_result.json']
    files += [ROOT / 'experiments/runs'/name for name in (
        'model8_trip_library.py', 'model8_trip_library_native.py', 'model8_trip_library_native.cs',
        'model8_trip_sync.py', 'research_model8_shared_relaxation.py')]
    manifest.update(registered_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        compiler=str(Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'),
        optimization='Compiled complete reference enumeration; grouped exact route contacts; binary path archive; '
                     'zero-column elimination and exact pool-value indicators in pattern followers',
        local_extension='Same original K/time/physical inputs; same audited upper bound; production MILP entry unchanged',
        timing_scope='Includes compiler call, reload/audit, fresh complete enumeration, compact export, master build/solve, endpoints, refinement and final audit',
        hashes={str(p.resolve()): sha(p) for p in set(files)})
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with zipfile.ZipFile(OUT / 'frozen_sources_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(manifest['hashes']):
            archive.write(path, Path(path).relative_to(ROOT).as_posix())
    print(json.dumps(dict(output=str(OUT), customers=20, K=manifest['original_K'], U=manifest['initial_audited_upper_bound'])))


if __name__ == '__main__':
    main()
