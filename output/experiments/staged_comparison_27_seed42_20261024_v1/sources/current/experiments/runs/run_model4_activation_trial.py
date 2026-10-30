"""Ablate activation capacities without changing the running M4 baseline."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import zipfile

from experiments.runs.run_model4_strengthening_trial import ROOT, run, write


def install_builder():
    import models.routing.model_4.exact.formulation as formulation
    from models.routing.model_4.exact.activation_capacity import add_activation_capacity
    original = formulation.build_model
    def build(*args, strengthening='none', **kwargs):
        base = 'capacity' if strengthening == 'capacity_activation' else 'none' if strengthening == 'activation' else strengthening
        model, context = original(*args, strengthening=base, **kwargs)
        if strengthening in ('activation', 'capacity_activation'):
            context['strengthening']['base_profile'] = base
            context['strengthening']['profile'] = strengthening
            context['strengthening']['activation_capacity'] = add_activation_capacity(args[0], context)
            model.update()
        return model, context
    formulation.build_model = build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--cases', nargs='+', default=['S02', 'S04', 'S06'])
    parser.add_argument('--profiles', nargs='+', choices=['none','capacity','activation','capacity_activation'], default=['activation','capacity_activation'])
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    out = (ROOT/args.output).resolve()
    out.relative_to(ROOT)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    files = [p for base in (ROOT/'models', ROOT/'tools/configuration') for p in base.rglob('*.py')]
    files += [Path(__file__), ROOT/'experiments/runs/run_model4_strengthening_trial.py']
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with zipfile.ZipFile(out/'source.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in hashes:
            archive.write(ROOT/name, name)
    write(out/'protocol.json', dict(cases=args.cases, profiles=args.profiles, seconds=args.seconds,
        seed=args.seed, source_sha256=hashes, threads=1, formulation='compact', external_start=False))
    install_builder()
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
                    writer.writeheader();writer.writerows(rows)
                print(json.dumps(row), flush=True)
    finally:
        write(out/'integrity.json', dict(completed=len(rows), source_unchanged=all(
            hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in hashes.items())))


if __name__ == '__main__':
    main()
