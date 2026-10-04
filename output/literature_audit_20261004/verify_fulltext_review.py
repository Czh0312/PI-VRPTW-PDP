"""Check source integrity, current experimental evidence, and report links."""
from pathlib import Path
import csv
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / 'output/literature_audit_20261004'
OUT = AUDIT / 'fulltext_verification.json'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

previous = json.loads((AUDIT / 'audit_verification.json').read_text(encoding='utf-8'))
core = {k: dict(prior_sha256=v, current_sha256=digest(ROOT/k), unchanged=digest(ROOT/k)==v)
        for k,v in previous['hashes'].items() if k.startswith(('models/', 'config/'))}
supplied = json.loads((AUDIT / 'fulltexts/supplied_sources.json').read_text(encoding='utf-8'))
sources = []
for entry in supplied:
    original = Path(entry['original'])
    copied = Path(entry['copy'])
    sources.append(dict(name=entry['file'], pages=entry['pages'], sha256=entry['sha256'],
                        original_unchanged=digest(original)==entry['sha256'],
                        copy_matches=digest(copied)==entry['sha256']))
extra = json.loads((AUDIT / 'fulltexts/accorsi_source.json').read_text(encoding='utf-8'))
assert digest(Path(extra['path'])) == extra['sha256']

m5 = ROOT / 'output/experiments/m5_comparison_20261004/full'
manifest = json.loads((m5/'manifest.json').read_text(encoding='utf-8'))
q = {x['case_id']:x['total_container_uses'] for x in manifest['selected_dataset_cases']}
with (m5/'runs.csv').open(encoding='utf-8-sig', newline='') as f:
    runs = list(csv.DictReader(f))
experiments = {}
for key, folder in [('m1_m4', ROOT/'output/experiments/m1_m4_comparison_20261004/full'), ('m5', m5)]:
    experiments[key] = json.loads((folder/'progress.json').read_text(encoding='utf-8'))
experiments['m5_pool_check'] = dict(runs=len(runs), configurations=len(q),
    input_pool_equals_total_demand=sum(int(r['pool'])==q[r['case_id']] for r in runs),
    valid=sum(r['valid']=='True' for r in runs),
    schedule_minimum_less_than_input_pool=sum(float(r['minimum_pool_for_schedule'])<int(r['pool']) for r in runs),
    note='A postprocessed minimum for one schedule is not a globally optimized minimum pool.')

reports = [ROOT/'docs/novelty_fulltext_comparison_20261004.md',
           ROOT/'docs/novelty_overlap_audit_20261004.md',
           ROOT/'reference/novelty_fulltexts_20261004/reading_list.md']
review = dict(date=datetime.now(timezone(timedelta(hours=8))).isoformat(),
    purpose='Masters thesis overlap review; not a full-database originality clearance',
    sources=sources, extra_source=dict(path=extra['path'], sha256=extra['sha256'], pages=extra['pages'], url=extra['url']),
    core_files=core, experiments=experiments,
    visually_reviewed_pdf_pages={
      'he_2026_multiperiod_crossdock':list(range(2,12)),
      'guo_2023_rti_sharing':list(range(6,13))+[15,16],
      'yavrucu_2025_closed_loop':list(range(6,15))+[19,20],
      'soysal_2023_vmi':[3,4,5,9],
      'accorsi_2022_washing':list(range(1,11))},
    notes=['Yavrucu equations on PDF8-9 visually read; text extraction omits most equations.',
           'Accorsi scanned pages visually read; no usable text layer.',
           'No active solver, policy, dataset or pool config changes; no new optimization experiments.',
           'Previous 12 mechanism tests passed in first round; unchanged tests not repeated.'],
    remaining_fulltext_coverage=['10.1016/j.cor.2024.106600', '10.1080/00207543.2023.2275639'])
# The reports link to this evidence file; create it before checking targets.
OUT.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
broken = []
checked = []
for report in reports:
    text = report.read_text(encoding='utf-8')
    for target in re.findall(r'\]\(([^)]+)\)', text):
        target = target.strip('<>')
        if target.startswith(('http://', 'https://', '#')):
            continue
        local = target.split('#',1)[0]
        local = re.sub(r':\d+$', '', local)
        candidate = Path(local) if re.match(r'^[A-Za-z]:[/\\]', local) else report.parent/local
        entry = dict(report=str(report.relative_to(ROOT)), target=target)
        checked.append(entry)
        if not candidate.exists():
            broken.append(entry)
    assert text.count('$$') % 2 == 0, report
    assert '\ufffd' not in text, report
review['local_links_checked'] = len(checked)
review['broken_local_links'] = broken
review['report_files'] = {str(p.relative_to(ROOT)):dict(sha256=digest(p), characters=len(p.read_text(encoding='utf-8')), lines=len(p.read_text(encoding='utf-8').splitlines())) for p in reports}
assert all(x['original_unchanged'] and x['copy_matches'] for x in sources)
assert all(v['unchanged'] for v in core.values())
assert not broken, broken
assert experiments['m5_pool_check']['input_pool_equals_total_demand'] == len(runs) == 560
OUT.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(reports=review['report_files'], links_checked=len(checked), broken_links=len(broken), core_unchanged=True, source_copies_verified=len(sources), m5=experiments['m5_pool_check']), ensure_ascii=False, indent=2))
