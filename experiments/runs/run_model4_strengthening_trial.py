"""Same-budget cold M4 comparison of valid formulation strengthenings."""
import argparse
import csv
import hashlib
from importlib import import_module
import json
from pathlib import Path
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run(case, profile, budget, seed, folder):
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.state import empty_result
    from models.common.cycle.audit import audit_solution
    from models.common.solver_config import read_milp_info, accept_milp_audit
    from models.routing.model_4.exact.formulation import build_model
    from models.routing.model_4.exact.solution import extract_solution
    folder.mkdir(parents=True, exist_ok=False)
    config, fleet = load_routing_case(f'data/formal_benchmark27_spacetime_v3/model_4/small/{case}/case.json', use_pool_config=False)
    problem = load_instance(config, fleet)
    begun = time.perf_counter()
    model, context = build_model(problem, compress_routes=True, strengthening=profile, deadline=begun+budget)
    try:
        model.Params.LogToConsole = 0
        model.Params.LogFile = (folder/'gurobi.log').relative_to(ROOT).as_posix()
        model.Params.OutputFlag = 1
        model.Params.Threads = 1
        model.Params.Seed = seed
        built = time.perf_counter()-begun
        model.Params.TimeLimit = max(.001, budget-built)
        model.optimize()
        result = empty_result(problem, 'exact')
        result['info'] = read_milp_info(model, proof_scope='full_model_4_integer_time_MILP', build_time=built)
        result['info'].update(strengthening=context['strengthening'], runtime=time.perf_counter()-begun,
                              mip_start_supplied=False, threads=1, seed=seed)
        if model.SolCount:
            extract_solution(problem, model, context, result)
            result['audit'] = audit_solution(problem, result)
            accept_milp_audit(result, float(model.ObjVal))
        write(folder/'result.json', result)
        row = dict(case=case, profile=profile, budget=budget, seed=seed,
                   status=result['info']['status'], valid=result.get('audit',{}).get('valid', False),
                   objective=result['info']['objective'], bound=result['info']['best_bound'],
                   gap=result['info']['mip_gap'], optimal=result['info']['proven_optimal'],
                   seconds=time.perf_counter()-begun, build_seconds=built,
                   variables=model.NumVars, constraints=model.NumConstrs+model.NumGenConstrs,
                   nodes=model.NodeCount, work=model.Work)
        write(folder/'row.json', row)
        return row
    finally:
        model.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--cases', nargs='+', default=['S02', 'S04', 'S06'])
    parser.add_argument('--profiles', nargs='+', choices=['none','bounds','capacity','combined','activation','capacity_activation'], default=['none','capacity_activation'])
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    out = (ROOT/args.output).resolve()
    out.relative_to(ROOT)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    paths = [p for base in (ROOT/'models', ROOT/'tools/configuration') for p in base.rglob('*.py')]
    paths.append(Path(__file__))
    hashes = {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    with zipfile.ZipFile(out/'source.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in hashes:
            archive.write(ROOT/name, name)
    write(out/'protocol.json', dict(cases=args.cases, profiles=args.profiles, budget=args.seconds, seed=args.seed,
        source_sha256=hashes, threads=1, formulation='compact', external_start=False,
        observation_callback=False, dataset='formal_benchmark27_spacetime_v3'))
    rows = []
    try:
        for case in args.cases:
            for profile in args.profiles:
                print(f'START {case} {profile}', flush=True)
                row = run(case, profile, args.seconds, args.seed, out/f'{case}_{profile}')
                rows.append(row)
                write(out/'rows.json', rows)
                with (out/'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader(); writer.writerows(rows)
                print(json.dumps(row), flush=True)
    finally:
        write(out/'integrity.json', dict(completed=len(rows), source_unchanged=all(
            hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())))


if __name__ == '__main__':
    main()
