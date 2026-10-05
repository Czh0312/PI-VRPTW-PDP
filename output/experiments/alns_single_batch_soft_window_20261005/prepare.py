"""Seal candidate and adapt the existing serial paired runner before quality."""
from pathlib import Path
import shutil
from freeze import OUT,OLD,MODULE,manifest,sha,write

source=OUT/'after_source'
disabled=OUT/'disabled_source'
shutil.copytree(source,disabled,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
p=disabled/MODULE
text=p.read_text();assert text.count('_soft_windows=True')==1
p.write_text(text.replace('_soft_windows=True','_soft_windows=False'))
for name in ('after_source','disabled_source'):
    write(OUT/(name+'_manifest.json'),manifest(OUT/name))

text=(OLD/'experiment.py').read_text()
text=text.replace('diversity_enumerations','soft_window_accepted_infeasible')
text=text.replace("info.get('descent_soft_window_accepted_infeasible', 0)","info.get('soft_window_accepted_infeasible', 0)")
text=text.replace("timeout=job['budget'] + 30", "timeout=(120 if phase in ('verification', 'unaffected') else job['budget'] + 30)")
needle="                            soft_window_accepted_infeasible=info.get('soft_window_accepted_infeasible', 0),"
assert text.count(needle)==1
text=text.replace(needle,needle+"\n                            soft_window_returns_to_feasible=info.get('soft_window_returns_to_feasible', 0),\n                            soft_window_entered_from_feasible=info.get('soft_window_entered_from_feasible', 0),")
needle="                          soft_window_accepted_infeasible=b.get('soft_window_accepted_infeasible', 0), descent_improvements=b.get('descent_improvements', 0)))"
assert text.count(needle)==1
text=text.replace(needle,"                          soft_window_accepted_infeasible=b.get('soft_window_accepted_infeasible', 0),\n                          soft_window_returns_to_feasible=b.get('soft_window_returns_to_feasible', 0),\n                          soft_window_entered_from_feasible=b.get('soft_window_entered_from_feasible', 0)))")
text=text.replace("            triggered = any(p['soft_window_accepted_infeasible'] for p in subset)",
                  "            triggered = any(p['soft_window_accepted_infeasible'] > 0 and p['soft_window_returns_to_feasible'] > 0 for p in subset)")
insertion='''
def compare_unaffected():
    checks = []
    keys = ('objective', 'iterations', 'operator_attempts', 'operator_improvements', 'operator_noops',
            'late_pickup_checks', 'late_pickup_vetoes')
    jobs = json.loads((OUT / 'unaffected_manifest.json').read_text())['jobs']
    for index, job in enumerate(jobs):
        a, b = [json.loads((OUT / 'unaffected' / ('%03d_%s.json' % (index,source))).read_text())
                for source in ('before_source','after_source')]
        equal = all(a['result'].get(k) == b['result'].get(k) for k in ('e1_trips','e2_trips','assignment'))
        equal &= all(a['result']['info'].get(k) == b['result']['info'].get(k) for k in keys)
        valid = all(r['independent_audit']['valid'] and r['independent_audit']['periodic_replay']['valid']
                    and r['result']['info']['iterations'] == 500 for r in (a,b))
        disabled = not b['result']['info']['soft_window_enabled']
        checks.append(dict(job=job,equal=equal,valid=valid,disabled=disabled))
    write(OUT / 'unaffected_equivalence.json',checks)
    assert len(checks) == 6 and all(r['equal'] and r['valid'] and r['disabled'] for r in checks)
    print('Unaffected exact equivalence:6 passed')

'''
text=text.replace("if __name__ == '__main__':",insertion+"if __name__ == '__main__':")
text=text.replace("    elif command == 'compare-fixed':", "    elif command == 'compare-unaffected':\n        compare_unaffected()\n    elif command == 'compare-fixed':")
(OUT/'experiment.py').write_text(text)
write(OUT/'driver_origin.json',dict(path=str(OLD/'experiment.py'),sha256=sha(OLD/'experiment.py'),
    modifications='Only candidate metrics/recovery gate, fixed-work timeout, and six unaffected comparisons. Original serial alternation, frozen-source/input checks, failures, fresh audits and mean/worst/win quality gates preserved.'))
write(OUT/'execution_hashes.json',{p.name:sha(p) for p in [OUT/'freeze.py',OUT/'prepare.py',OUT/'experiment.py',
    OUT/'after_source_manifest.json',OUT/'disabled_source_manifest.json',OUT/'driver_origin.json']})
print('Sealed candidate, disabled source, and serial runner')
