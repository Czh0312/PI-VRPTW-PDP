"""Check the existing M4 strengthening/ALNS combination on frozen small7 cases."""
from __future__ import annotations

import argparse
import gc
import hashlib
from pathlib import Path
import zipfile

from experiments.runs import run_single_batch_warm_start_trial as trial

ROOT=Path(__file__).resolve().parents[2]
DATASET='data/formal_benchmark27_spacetime_v4_1'
PRIOR=Path('output/experiments/small7_m1_m4_exact300_20261023_v2')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    result=trial.source_hashes()
    result[Path(__file__).relative_to(ROOT).as_posix()]=digest(Path(__file__))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    args.dataset=DATASET
    args.prior=PRIOR
    args.total_seconds=300
    args.alns_seconds=30
    args.seed=42
    args.m4_strengthening='capacity_activation'
    cases=[f'S{i:02}' for i in range(1,10)]
    out=(ROOT/args.output).resolve()
    out.relative_to(ROOT)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Use a new output directory; historical results are immutable')
    frozen=trial.read(ROOT/'config/experiments/benchmark27_spacetime_v4_1/frozen.json')
    dataset=ROOT/DATASET
    dataset_hash=digest(dataset/'manifest.json')
    assert dataset_hash==frozen['manifest_sha256']
    suite=trial.read(dataset/'manifest.json')
    assert all(digest(dataset/name)==expected for name,expected in suite['artifact_sha256'].items())
    records=[r for r in suite['cases'] if r['size']=='small' and r['model']==4]
    assert len(records)==9 and all(r['customer_count']==7 for r in records)
    prior=trial.read(ROOT/PRIOR/'rows.json')
    assert len(prior)==36 and all(r['status'] not in ('NOT_RUN','RUNNING') for r in prior)
    prior_manifest=trial.read(ROOT/PRIOR/'manifest.json')
    assert prior_manifest['dataset_sha256']==dataset_hash
    out.mkdir(parents=True,exist_ok=True)
    before=sources()
    for name,expected in before.items():
        if name.startswith(('models/','tools/configuration/')):
            assert prior_manifest['source_hashes']['source/'+name]==expected,name
    with zipfile.ZipFile(out/'source.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(before):
            archive.write(ROOT/name,name)
    archive_hash=digest(out/'source.zip')
    trial.write(out/'frozen_dataset.json',frozen)
    trial.write(out/'dataset_manifest.json',suite)
    trial.write(out/'protocol.json',dict(dataset=DATASET,dataset_manifest_sha256=dataset_hash,
        cases=cases,models=[4],modes=['alns'],total_seconds=300,alns_seconds=30,
        seed=42,threads=1,formulation='compact',objective='distance',m4_strengthening='capacity_activation',
        prior_cold_study=PRIOR.as_posix(),solver_sources_match_cold_snapshot=True,
        source_sha256=before,source_archive_sha256=archive_hash,
        budget_scope='ALNS, initial audit, formulation, MIP-start mapping and MILP share each 300-second budget; final extraction/audit separately visible in total_seconds.',
        source_comparison='Existing optional strengthening and ALNS start combined; no tuning by case or external lower bound. Historical cold rows used only for reporting.'))
    rows=[]
    try:
        for case in cases:
            print(f'START {case} M4 ALNS30 + capacity_activation / total300',flush=True)
            row=trial.trial(case,4,'alns',args,out/f'{case}_m4_alns')
            rows.append(row)
            trial.report(out,rows)
            print(row,flush=True)
            gc.collect()
    finally:
        intact=digest(dataset/'manifest.json')==dataset_hash and all(
            digest(dataset/name)==expected for name,expected in suite['artifact_sha256'].items())
        trial.write(out/'integrity.json',dict(source_unchanged=before==sources(),dataset_unchanged=intact,
            source_archive_unchanged=digest(out/'source.zip')==archive_hash,completed=len(rows),planned=9,
            artifact_sha256={p.relative_to(out).as_posix():digest(p) for p in out.rglob('*') if p.is_file() and p.name!='integrity.json'}))


if __name__=='__main__':
    main()
