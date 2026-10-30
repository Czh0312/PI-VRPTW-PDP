from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.routing.test_solver_isolation import SCRIPT

if __name__ == '__main__':
    for number in range(1, 9):
        code = SCRIPT.replace("assert audit_solution(problem, result)['valid']",
            "print('DETAIL', number, result['info']['status'], result['info'].get('objective'), audit_solution(problem, result)['errors'], flush=True); assert audit_solution(problem, result)['valid']")
        run = subprocess.run([sys.executable, '-c', code, 'exact', str(number)], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
        print(number, run.returncode, run.stdout, run.stderr, flush=True)
