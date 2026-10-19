"""Run the registered one-box small pilots through the unchanged formal driver."""
from pathlib import Path
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
DATA = ROOT/'data/formal_benchmark27_one_box_v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def tests():
    selected = {}
    for name in ('data_tests.xml', 'historical_source_tests.xml'):
        for case in ET.parse(HERE/name).iter('testcase'):
            selected[(case.get('classname'), case.get('name'))] = not any(
                case.find(tag) is not None for tag in ('failure','error','skipped'))
    if len(selected) != 16 or not all(selected.values()):
        raise RuntimeError('Selected data tests have unresolved failures/skips')
    validation = read(HERE/'validation.json')
    validation['data_tests'] = dict(passed=16, failed=0, skipped=0,
        evidence=['data_tests.xml','historical_source_tests.xml'],
        note='15 passed in the full selected run; the remaining historical-source test passed after the archived-code fix.')
    attempt = dict(scope='16 selected data tests before archiving old generator',
        passed=15, failed=1, skipped=0, seconds=205.29,
        cause='Old provenance correctly pins the pre-change generator; original code now retained in the legacy archive.')
    if attempt not in validation['development_test_attempts']:
        validation['development_test_attempts'].append(attempt)
    write(HERE/'validation.json', validation)


def run(study, bases):
    out = HERE/study
    if not (out/'manifest.json').exists():
        command = [sys.executable,'-m','experiments.runs.run_formal_benchmark27','prepare',
            '--output',str(out),'--dataset',str(DATA),'--study-kind','smoke',
            '--bases',*bases,'--seeds','42','--workers','1','--exact-seconds','300',
            '--alns-seconds','30','--timeout-overhead','60','--overhead-basis',
            'Same-host prior native MILP small runs and strict regression; 60 seconds for interpreter, immutable artifacts and offline audit.']
        subprocess.run(command, cwd=ROOT, check=True)
    subprocess.run([sys.executable,str(out/'driver.py'),'run','--output',str(out)],cwd=ROOT,check=True)
    return read(out/'stage_gate.json')


if __name__ == '__main__':
    tests()
    phase = sys.argv[1]
    if phase == 's01':
        run('s01', ['S01'])
    elif phase == 'representatives':
        first = read(HERE/'s01/stage_gate.json')
        if not first['complete'] or first['execution_or_audit_errors'] or first['optimal_reference_contradictions']:
            raise RuntimeError('S01 execution/audit gate failed')
        run('representatives', ['S04','S07'])
    else:
        raise ValueError(phase)
