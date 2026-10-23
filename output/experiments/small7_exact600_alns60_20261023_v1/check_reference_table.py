"""Confirm that every published small-case reference has a direct MILP proof."""
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    out=Path(__file__).resolve().parent
    path=out/'optimal_references.json'
    table=json.loads(path.read_text(encoding='utf-8'))
    references=table['references']
    assert len(references)==36 and Counter(r['model'] for r in references)=={1:9,2:9,3:9,4:9}
    assert len({(r['case'],r['model']) for r in references})==36
    for ref in references:
        source=Path(ref['source'])
        assert hashlib.sha256(source.read_bytes()).hexdigest()==ref['source_sha256']
        result=json.loads(source.read_text(encoding='utf-8'))
        assert result['info']['status']=='OPTIMAL' and result['info']['proven_optimal']
        assert result['audit']['valid']
        assert abs(ref['objective']-result['info']['objective'])<=1e-6
    report=dict(passed=True,direct_milp_references=36,models=dict(Counter(r['model'] for r in references)),
        proof_counts=dict(Counter(r['proof'] for r in references)),
        reference_table_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (out/'reference_verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(report)


if __name__=='__main__':main()
