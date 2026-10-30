"""One-case, one-seed comparison of M4's original and network formulations."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('baseline', 'network'), required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--case', default='S02')
    parser.add_argument('--scale', choices=('small', 'medium', 'large'), default='small')
    parser.add_argument('--seconds', type=float, default=300.)
    args = parser.parse_args()
    folder = (ROOT / args.output / args.mode).resolve()
    folder.relative_to(ROOT)
    folder.mkdir(parents=True, exist_ok=False)
    case = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_4/{args.scale}/{args.case}/case.json'
    config = json.loads(case.read_text(encoding='utf-8'))
    inputs = {'case.json': case}
    for key in ('data', 'routing_config', 'economic_config', 'dataset_metadata', 'orders',
                'operating_policy', 'parameter_contract', 'algorithm_config', 'distance_matrix', 'duration_matrix'):
        if isinstance(config.get(key), str):
            inputs[f'{key}{Path(config[key]).suffix}'] = ROOT / config[key]
    (folder / 'inputs').mkdir()
    for name, path in inputs.items():
        shutil.copyfile(path, folder / 'inputs' / name)
    source_paths = [p for base in ('models', 'tools/configuration') for p in (ROOT / base).rglob('*.py')]
    source_paths.append(ROOT / 'tools/__init__.py')
    source_paths.append(Path(__file__))
    source_bytes = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in sorted(source_paths)}
    sources = {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()}
    frozen = folder / 'snapshot'
    snapshot_hashes = {}
    for name, data in source_bytes.items():
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        snapshot_hashes[name] = sources[name]
    for path in inputs.values():
        name = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        snapshot_hashes[name] = hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(folder / 'source.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in source_bytes.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(folder / 'source_inputs.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in snapshot_hashes:
            archive.write(frozen / name, name)
    write(folder / 'protocol.json', dict(case=args.case, scale=args.scale, model=4, mode=args.mode,
        dataset=config['dataset_version'], budget=args.seconds, seed=42, threads=1,
        input_sha256={name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in inputs.items()},
        source_sha256=sources, snapshot_sha256=snapshot_hashes,
        source_inputs_zip_sha256=hashlib.sha256((folder / 'source_inputs.zip').read_bytes()).hexdigest(),
        external_start=False, external_bound=False, callback=False,
        timing='load, complete solve, extraction, and independent replay; runtime preflight excluded'))

    sys.path.insert(0, str(frozen))
    from tools.configuration.loader import load_routing_case, PROJECT_ROOT
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_4.exact import solve_exact
    assert PROJECT_ROOT == frozen

    options = dict(threads=1)
    kwargs = {}
    if 'network_strengthening' in inspect.signature(solve_exact).parameters:
        kwargs['network_strengthening'] = args.mode == 'network'
    elif args.mode == 'network':
        raise RuntimeError('The network formulation has not been implemented')
    begun = time.perf_counter()
    config, fleet = load_routing_case(str(frozen / case.relative_to(ROOT)), use_pool_config=False)
    problem = load_instance(config, fleet)
    loaded = time.perf_counter() - begun
    result = solve_exact(problem, time_limit=max(.001, args.seconds-loaded), seed=42,
                         verbose=True, milp_options=options, **kwargs)
    audit = audit_solution(problem, result) if result.get('audit', {}).get('valid') else result.get('audit', {})
    total = time.perf_counter() - begun
    info = result['info']
    write(folder / 'result.json', result)
    write(folder / 'audit.json', audit)
    row = dict(case=args.case, scale=args.scale, mode=args.mode, seed=42, budget=args.seconds,
        status=info['status'], proven_optimal=info.get('proven_optimal', False),
        valid=audit.get('valid', False), periodic_valid=audit.get('periodic_replay', {}).get('valid', False),
        objective=info.get('objective'), bound=info.get('best_bound'), gap=info.get('mip_gap'),
        total_seconds=total, load_seconds=loaded, solver_info=info,
        customer_count=len({order.customer for order in problem.orders}), order_count=len(problem.orders),
        satellite_count=len(problem.satellites))
    loaded_sources = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if path and (name == 'models' or name.startswith('models.')
                     or name == 'tools.configuration' or name.startswith('tools.configuration.')):
            loaded_sources[name] = Path(path).resolve().relative_to(frozen).as_posix()
    write(folder / 'loaded_sources.json', loaded_sources)
    for name, digest in snapshot_hashes.items():
        if hashlib.sha256((frozen / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Frozen source/input changed during solving: {name}')
    write(folder / 'row.json', row)
    print(json.dumps({key: value for key, value in row.items() if key != 'solver_info'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
