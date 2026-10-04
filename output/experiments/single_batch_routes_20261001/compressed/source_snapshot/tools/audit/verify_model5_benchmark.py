"""Re-audit every registered before/after result and verify frozen evidence."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.audit.benchmark_model5_alns import hashes, write
from tools.audit.summarize_model5_alns import compare


def verify(folder, before='before', after='after'):
    from configuration.loader import load_routing_case, apply_container_pool_size
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    manifest = json.loads((folder/'benchmark_manifest.json').read_text())
    assert hashes(folder/'inputs') == manifest['input_sha256'], 'Frozen inputs changed'
    summaries, problems, checks = {}, {}, []
    for phase in (before, after):
        directory = folder/phase
        saved = json.loads((directory/'manifest.json').read_text())
        assert saved['jobs'] == manifest['jobs'], 'Final phase differs from registered jobs'
        assert hashes(directory/'source') == saved['source_sha256'], 'Frozen source changed'
        rows = json.loads((directory/'summary.json').read_text())
        assert len(rows) == len(manifest['jobs']), 'Missing jobs'
        summaries[phase] = rows
        for row, job in zip(rows,manifest['jobs']):
            assert all(row[key] == value for key,value in job.items()), 'Job order/parameters changed'
            path = directory/row['result_file']
            if row['status'] in ('ERROR','OUTER_TIMEOUT'):
                assert not row['valid']
                checks.append(dict(phase=phase,file=path.name,status=row['status'],valid=False))
                continue
            record = json.loads(path.read_text())
            assert record['job'] == job
            key = job['case'], job['pool']
            if key not in problems:
                config, fleet = load_routing_case(job['case'],use_pool_config=False)
                config, fleet = apply_container_pool_size(config,fleet,job['pool'])
                problems[key] = load_instance(config,fleet)
            result = record['result']
            audit = audit_solution(problems[key],result)
            valid = audit['valid'] and audit.get('periodic_replay',{}).get('valid',False)
            assert bool(valid) == row['valid'], 'Feasibility disagrees with summary'
            assert audit == record['independent_audit'], 'Independent audit changed'
            info = result['info']
            assert not info.get('proven_optimal') and not info.get('proven_infeasible')
            if valid:
                assert math.isclose(audit['objective'],row['objective'],abs_tol=1e-6)
                assert math.isclose(audit['objective'],info['objective'],abs_tol=1e-6)
                history = info['incumbent_history']
                assert all(a['objective'] > b['objective'] for a,b in zip(history,history[1:]))
                assert math.isclose(history[-1]['objective'],info['objective'],abs_tol=1e-6)
                assert info['iterations'] <= job['iterations']
            checks.append(dict(phase=phase,file=path.name,status=row['status'],valid=bool(valid)))
    computed = compare(summaries[before],summaries[after])
    assert computed == json.loads((folder/'comparison.json').read_text()), 'Statistics changed'
    current_paths = [f'models/routing/solvers/alns/{name}.py' for name in
                     ('solver','evaluator','cycle_cache','intensification','neighborhoods','construction')]
    current_paths += [f'models/routing/common/cycle/{name}.py' for name in
                      ('instance','policy','audit','precheck','state')]
    current_paths += ['models/routing/variants/model_5/alns/solver.py']
    frozen = json.loads((folder/after/'manifest.json').read_text())['source_sha256']
    current = {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == frozen.get(path)
               for path in current_paths}
    return dict(registered_jobs=len(manifest['jobs']),checked_records=len(checks),
                valid_records=sum(c['valid'] for c in checks),
                frozen_inputs_and_sources_unchanged=True,
                independent_audits_and_statistics_match=True,
                current_m5_sources_match_frozen=current,checks=checks)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    result = verify(args.output)
    write(args.output/'verification_final.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False,indent=2))
