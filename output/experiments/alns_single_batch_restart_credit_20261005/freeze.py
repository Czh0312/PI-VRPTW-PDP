"""Freeze this experiment once, before editing any solver code."""
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = OUT.parent / 'alns_single_batch_general_score_branch_20261002'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')

def hashes(directory):
    return {str(p.relative_to(directory)): sha(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}

assert not (OUT / 'protocol.json').exists(), 'Never overwrite a registered experiment'
owned = ['models/routing/solvers/alns/single_batch.py',
         'models/routing/solvers/alns/single_batch_descent.py',
         'models/routing/solvers/alns/single_batch_scoring.py',
         'tests/routing/test_single_batch_late_pickup.py']
write(OUT / 'baseline_owned_hashes.json', {p: sha(ROOT / p) for p in owned})
for directory in ('models', 'configuration', 'config', 'tests'):
    for src in sorted((ROOT / directory).rglob('*')):
        if src.is_file() and '__pycache__' not in src.parts and src.suffix in ('.py', '.json', '.md'):
            target = OUT / 'before_source' / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
src = ROOT / 'tools/audit/benchmark_single_batch_solvers.py'
target = OUT / 'before_source' / src.relative_to(ROOT)
target.parent.mkdir(parents=True)
shutil.copy2(src, target)
write(OUT / 'before_source_manifest.json', hashes(OUT / 'before_source'))
families = ['zhou_Set2_100-5-3_subset50_scenario02',
            'zhou_Set2_100-5-3_scenario02', 'zhou_Set2_100-10-2_scenario02']
origins = {}
for family in families:
    src = PRIOR / 'benchmark_inputs' / family
    target = OUT / 'benchmark_inputs' / family
    shutil.copytree(src, target)
    for path in target.iterdir():
        origins[str(path.relative_to(OUT))] = {'source': str(src / path.name), 'source_sha256': sha(src / path.name)}
    case = json.loads((target / 'case.json').read_text())
    for key, value in case.items():
        if isinstance(value, str) and (src / Path(value).name).is_file():
            case[key] = str(target / Path(value).name)
    write(target / 'case.json', case)
write(OUT / 'input_origins.json', origins)
write(OUT / 'input_hashes.json', hashes(OUT / 'benchmark_inputs'))

for phase, short, long, seeds in [('pilot', 5, 15, (501, 502, 503)),
                                  ('confirmation', 30, 60, (511, 512, 513))]:
    jobs = []
    for model in (1, 2, 3, 4):
        for family in families[:2]:
            jobs.extend(dict(family=family, case=str(OUT / 'benchmark_inputs' / family / 'case.json'),
                             model=model, solver='alns', seed=seed, budget=short,
                             iterations=1000000, size=50 if 'subset50' in family else 100)
                        for seed in seeds)
        jobs.extend(dict(family=families[2], case=str(OUT / 'benchmark_inputs' / families[2] / 'case.json'),
                         model=model, solver='alns', seed=seed, budget=long,
                         iterations=1000000, size=100) for seed in seeds[:2])
    write(OUT / (phase + '_manifest.json'), {'short_budget': short, 'long_budget': long, 'jobs': jobs})
write(OUT / 'verification_manifest.json', {'jobs': [
    dict(family=family, case=str(OUT / 'benchmark_inputs' / family / 'case.json'), model=model,
         solver='alns', budget=60, iterations=500, seed=500, size=100)
    for family in families[1:] for model in (1, 2, 3, 4)]})

write(OUT / 'protocol.json', {
    'registered': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
    'mode': 'Continuous run and descriptive validation; user explicitly authorizes implementation and experiments.',
    'hypothesis': 'Classify scheduled randomized reconstruction as restart, never credit or penalize an unrelated sampled operator, and never apply pickup-only late acceptance to a restart. Keep operator RNG selection draw, reconstruction schedule, actual pickup policy, decoding and audits unchanged.',
    'scope': 'M1-M4 as one candidate, no subset adoption. Only single_batch.py and new targeted tests. Freeze all imported source and inputs. M5/M6/exact and existing archives untouched.',
    'correctness_gate': 'Targeted forced-restart tests: accepted/rejected/noop accounting, sampled weights unchanged across restart, actual pickup policy intact, preserved RNG draw and descent/assignment precedence. Disabled source vs baseline on two100 inputs x M1-M4 fixed500 exact routes, assignment, objective, iterations and full audit/replay. Fresh decode every quality result.',
    'pilot': '32 pairs64 serialruns5/15 seconds. This is a rejection screen, never enough to adopt. Run all registered jobs once, retain failures.',
    'confirmation': 'Only if all pilot gates pass, 32pairs64 serialruns30/60 seconds using fixed new seeds. Same per-model rules. Even a pass is limited evidence, not stable/general/near-optimal proof; longer60/120 and unseen geometries remain future work.',
    'quality_gate': 'EACH model: short/long/short50/short100 means <=0%; every pair <=5% worsening; short>=3/6 wins, long>=1/2; all results feasible by fresh decoder and fixed/free independent audit+periodic replay; actual corrected restart attempts required in both budget strata for each model. Any failure rejects entire candidate.',
    'withdrawal': 'Keep production baseline unless all gates pass; restore only our owned module if needed and archive new candidate tests. Never change thresholds, reseed, discard bad runs, or adopt a successful model subset.',
    'timing': 'Alternating source order; strictly serial solver runs, no own tests/profiles/other heavy work during quality. Load averages and process wall recorded, external load not controlled. Worker hard timeout budget+30 seconds.',
    'interpretation': 'Paired percent changes of audited original distance, not penalized score or true GAP. These previously used geometries and nested sizes do not constitute independent held-out instances; order-window differences preclude pure-size conclusions.',
    'cpu_gate': 'Not applicable: intentional algorithmic change, no equivalence or speedup claim.',
    'production_edits_started': False,
})
write(OUT / 'context.json', {'load': os.getloadavg(), 'active_owned_process': None,
                            'external_process_inventory': 'Unavailable in previous sandbox; no claim of exclusivity',
                            'git_status': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True)})
write(OUT / 'registration_hashes.json', {p.name: sha(p) for p in OUT.glob('*.json')})
print(json.dumps({'frozen_source_files': len(hashes(OUT / 'before_source')), 'pairs_per_phase': 32,
                  'production_edits_started': False}))
