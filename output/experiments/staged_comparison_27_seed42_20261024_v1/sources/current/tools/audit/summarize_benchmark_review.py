"""Summarize retained attempts, recomputing distance from physical audit fields."""
from collections import Counter
import csv
import json
from pathlib import Path
import sys
import zipfile


def summarize(study):
    root=Path(study)
    summary=json.loads((root/'summary.json').read_text())
    manifest=json.loads((root/'manifest.json').read_text())
    jobs={j['id']:j for j in manifest['jobs']}
    rows=[]
    z=zipfile.ZipFile(root/'artifacts.zip') if (root/'artifacts.zip').exists() else None
    try:
        for original in summary['rows']:
            row=dict(original)
            name=f'runs/{row["id"]}/result.json'
            if row['valid']:
                result=json.loads(z.read(name) if z else (root/name).read_bytes())
                audit=result['independent_review_audit']
                row['total_distance']=sum(audit['distances'].values())
                row['selected_count_iterations']=result['info'].get('iterations')
                row['all_count_iterations']=sum(v.get('iterations',0) for r in result.get('count_search',[]) for v in r.get('visits',[]))
                row['pool_saving_fraction']=(row['Q']-row['N'])/row['Q'] if row.get('N') is not None else None
            row['fixed_k_diagnostic']=jobs[row['id']].get('fixed_k')
            row['pool_rate_override']=jobs[row['id']].get('pool_rate')
            rows.append(row)
    finally:
        if z:z.close()
    report=dict(planned=summary['planned'],completed=summary['completed'],valid=sum(r['valid'] for r in rows),
        descriptive_only=True, budget=manifest['budget'], method=manifest['method'],
        distance_definition='sum(independent_review_audit.distances), not native cost objective',
        baseline_summary_correction='Original baseline_all summary used native objective in total_distance; derived table corrects this without altering raw results.',
        by_model={str(m):dict(runs=sum(r['model']==m for r in rows),
            valid=sum(r['model']==m and r['valid'] for r in rows),
            K_counts=dict(Counter(str(r.get('K')) for r in rows if r['model']==m and r['valid'])),
            reduced_pool_runs=sum(r['model']==m and r.get('N') is not None and r['N']<r['Q'] for r in rows))
            for m in sorted({r['model'] for r in rows})},rows=rows)
    (root/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    fields=sorted({k for r in rows for k in r})
    with (root/'analysis.csv').open('w',newline='',encoding='utf-8-sig') as handle:
        w=csv.DictWriter(handle,fieldnames=fields);w.writeheader();w.writerows(rows)
    return {k:v for k,v in report.items() if k!='rows'}


if __name__=='__main__':
    print(json.dumps(summarize(sys.argv[1]),ensure_ascii=False))
