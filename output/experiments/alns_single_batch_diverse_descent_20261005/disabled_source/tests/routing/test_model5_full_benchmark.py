"""M5 reporting must keep batch configurations and failed runs distinct."""
import csv
import hashlib
import json

import pytest

from experiments.runs.run_model5_benchmark import save_progress, verify_frozen


def job(batch, solver, seed):
    return dict(job_index=batch*10+seed, run=f'b{batch}_{solver}_{seed}',
                case_id=f'example_b{batch}', family_id='example', size='small',
                customer_count=7, batch_count=batch, pool=10, model=5,
                solver=solver, seed=seed, budget=1)


def result(j, objective, *, bound=None, optimal=False):
    return {**j, 'status':'OPTIMAL' if optimal else ('FEASIBLE' if objective is not None else 'TIME_LIMIT'),
            'objective':objective, 'valid':objective is not None, 'periodic_replay':objective is not None,
            'proven_optimal':optimal, 'best_bound':bound, 'mip_gap':None, 'solver_runtime':1.0,
            'wall_time':1.1}


def manifest(jobs):
    return dict(started_at='test',jobs=jobs,selected_dataset_cases=['b2','b3'],
                arguments=dict(exact_time=300,time=30,seeds=[42,43,44],workers=4,timeout_overhead=60))


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as handle:
        return list(csv.DictReader(handle))


def test_batch_summaries_preserve_failure_counts_and_unknown_optimum(tmp_path):
    jobs = [job(b,solver,seed) for b in (2,3) for solver,seed in
            [('exact',42),('alns',42),('alns',43),('alns',44)]]
    rows = [result(j,100 if j['solver']=='exact' else (80 if j['seed']!=44 else None),
                   bound=60 if j['solver']=='exact' else None) for j in jobs]
    save_progress(tmp_path,manifest(jobs),rows,status='COMPLETED')
    summaries = read_csv(tmp_path/'summary.csv')
    assert [r['batch_count'] for r in summaries]==['2','3']
    assert all(r['alns_completed']=='3' and r['alns_valid']=='2' for r in summaries)
    pairs = read_csv(tmp_path/'comparison.csv')
    assert all(float(r['alns_best_vs_exact_percent'])==-20 for r in pairs)
    assert all(r['alns_best_optimality_gap_percent']=='' for r in pairs)
    assert all(float(r['alns_best_bound_gap_percent'])==25 for r in pairs)
    assert json.loads((tmp_path/'verification.json').read_text())['all_jobs_recorded']


def test_bound_contradiction_excluded_from_scale_means(tmp_path):
    jobs = [job(2,'exact',42),job(2,'alns',42)]
    registered = manifest(jobs)
    registered['arguments']['seeds']=[42]
    rows = [result(jobs[0],100,bound=90,optimal=True),result(jobs[1],80)]
    save_progress(tmp_path,registered,rows)
    state = json.loads((tmp_path/'progress.json').read_text())
    assert len(state['consistency_errors'])==1
    assert read_csv(tmp_path/'summary.csv')[0]['paired_cases']=='0'


def test_resume_rejects_modified_generated_case(tmp_path):
    case = tmp_path/'case.json'
    case.write_text('{}')
    frozen = dict(source_sha256={},input_snapshots={},
                  generated_case_sha256={'case.json':hashlib.sha256(case.read_bytes()).hexdigest()})
    verify_frozen(tmp_path,frozen)
    case.write_text('{"container_pool_size":999}')
    with pytest.raises(ValueError,match='generated case changed'):
        verify_frozen(tmp_path,frozen)
