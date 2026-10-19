"""Run the isolated linear-indicator prototype after the native comparisons."""
import json
from pathlib import Path
import subprocess
import sys
import diagnose

OUT = Path(__file__).resolve().parent
PLAN = [dict(id='perspective_m6_compact_consistency', kind='consistency', model=6,
             scope='unified', count=1, formulation='compact', encoding='perspective_linear'),
        dict(id='perspective_m6_compact_free', kind='performance', model=6,
             scope='unified', count=None, formulation='compact', encoding='perspective_linear'),
        dict(id='perspective_m8_compact_consistency', kind='consistency', model=8,
             scope='unified', count=1, formulation='compact', encoding='perspective_linear'),
        dict(id='perspective_m8_compact_free', kind='performance', model=8,
             scope='unified', count=None, formulation='compact', encoding='perspective_linear')]


def main():
    baseline = diagnose.read(OUT/'manifest.json')
    assert baseline['completed'] == 20, 'Wait for the native comparison to finish first'
    rows = []
    for job in PLAN:
        path = OUT/'runs'/job['id']/'row.json'
        if path.exists():
            rows.append(diagnose.read(path))
            continue
        print(json.dumps(dict(event='start_prototype', **job)), flush=True)
        log = OUT/'console'/f"{job['id']}.log"
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, '-X', 'utf8', str(__file__), 'worker', job['id']],
                                    cwd=diagnose.ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=480)
        if result.returncode:
            raise RuntimeError(f'Prototype needs inspection; see {log}')
        row = diagnose.read(path)
        if job['kind'] == 'consistency' and not row['valid']:
            raise RuntimeError('Stop: prototype did not retain the known witness')
        rows.append(row)
        diagnose.write(OUT/'prototype_summary.json', rows)
        print(json.dumps(dict(event='complete_prototype', **row)), flush=True)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        diagnose.worker(next(job for job in PLAN if job['id'] == sys.argv[2]))
    else:
        main()
