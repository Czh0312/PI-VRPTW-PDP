"""Preserve the stage-limit diagnostic and allocate the full remaining budget."""
from pathlib import Path
import hashlib
import json
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / 'output/experiments/m8_medium_optimized_20261029_v1'
OUT = ROOT / 'output/experiments/m8_medium_optimized_20261029_v2'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise FileExistsError(OUT)
    OUT.mkdir()
    for name in ('study.py', 'shared_medium.py', 'master_sync.py', 'phase_bound.py',
                 'route_bounds_medium.py', 'decomposition_medium.py', 'initial_audited_result.json',
                 'initial_audit.json', 'audit_native.py', 'test_setup.py'):
        (OUT / name).write_bytes((OLD / name).read_bytes())
    source = (OUT / 'study.py').read_text(encoding='utf-8')
    helper = '''def master_lower_bound(model, fallback):
    import gurobipy as gp
    if model.Status in (gp.GRB.INFEASIBLE, gp.GRB.INF_OR_UNBD):
        raise RuntimeError('Master contradicts the original audited feasible witness')
    bound = finite(model.ObjBound)
    return max(fallback, bound) if bound is not None else fallback


'''
    source = source.replace('def master(p, library):', helper+'def master(p, library):')
    source = source.replace('max(.01, min(60., deadline-time.perf_counter()))', 'max(.01, deadline-time.perf_counter())')
    source = source.replace("            if bound is None or m.Status in (gp.GRB.INFEASIBLE, gp.GRB.INF_OR_UNBD):\n                raise RuntimeError(f'Invalid master bound/status: {bound}, {m.Status}')\n            lower = max(lower, bound)",
        '            lower = master_lower_bound(m, lower)')
    source = source.replace("raw = dict(native_status=m.Status, lower_bound=bound,", "raw = dict(native_status=m.Status, lower_bound=bound, effective_global_lower_bound=lower, finite_native_bound_available=bound is not None,")
    (OUT / 'study.py').write_text(source, encoding='utf-8')
    manifest = json.loads((OLD / 'manifest.json').read_text(encoding='utf-8'))
    files = [Path(name) for name in manifest['hashes'] if OLD not in Path(name).parents]
    files += list(OUT.glob('*.py'))+[OUT / 'initial_audited_result.json']
    manifest.update(first_master_optimization_budget='All remaining wall-clock budget, rather than a sixty-second phase cap',
        timeout_without_native_bound='Retain the previously valid physical lower bound; do not claim an error or infeasibility',
        previous_attempt=str(OLD), hashes={str(p.resolve()):sha(p) for p in set(files)})
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with zipfile.ZipFile(OUT / 'frozen_sources_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(manifest['hashes']):
            archive.write(path, Path(path).relative_to(ROOT).as_posix())
    print(str(OUT))


if __name__ == '__main__':
    main()
