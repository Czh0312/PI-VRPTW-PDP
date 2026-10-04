"""Re-audit all pilot runs; do not expose held-out objective quality."""
import hashlib
import json
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
sys.path.insert(0,str(ROOT))
from configuration.loader import load_routing_case,apply_container_pool_size
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from tools.audit.benchmark_model5_alns import hashes
from tools.audit.summarize_model5_alns import compare

manifest=json.loads((OUT/'benchmark_manifest.json').read_text())
assert hashes(OUT/'inputs')==manifest['input_sha256']
jobs=[j for j in manifest['jobs'] if j['split']=='development']
before=[r for r in json.loads((OUT/'before/summary.json').read_text()) if r['split']=='development']
problems,checks={},[]
for phase in ('pilot_01','pilot_02','pilot_03'):
    directory=OUT/phase
    saved=json.loads((directory/'manifest.json').read_text())
    assert saved['jobs']==jobs
    assert hashes(directory/'source')==saved['source_sha256']
    rows=json.loads((directory/'summary.json').read_text())
    assert len(rows)==len(jobs)
    assert compare(before,rows)==json.loads((OUT/(phase+'_comparison.json')).read_text())
    for row,job in zip(rows,jobs):
        assert all(row[k]==v for k,v in job.items())
        record=json.loads((directory/row['result_file']).read_text())
        assert record['job']==job
        key=job['case'],job['pool']
        if key not in problems:
            config,fleet=load_routing_case(job['case'],use_pool_config=False)
            config,fleet=apply_container_pool_size(config,fleet,job['pool'])
            problems[key]=load_instance(config,fleet)
        result=record['result']
        audit=audit_solution(problems[key],result)
        assert audit==record['independent_audit']
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert abs(audit['objective']-row['objective'])<1e-6
        history=result['info']['incumbent_history']
        assert all(a['objective']>b['objective'] for a,b in zip(history,history[1:]))
        assert abs(history[-1]['objective']-row['objective'])<1e-6
        checks.append(dict(phase=phase,file=row['result_file'],valid=True,
            solver_overrun_seconds=max(0,result['info']['runtime']-job['budget'])))
record=dict(records=len(checks),valid=sum(c['valid'] for c in checks),
    frozen_sources_and_inputs_unchanged=True,independent_audits_and_statistics_match=True,
    max_solver_overrun_seconds=max(c['solver_overrun_seconds'] for c in checks),
    checks=checks)
(OUT/'development_verification.json').write_text(json.dumps(record,indent=2)+'\n')
print({k:v for k,v in record.items() if k!='checks'})
