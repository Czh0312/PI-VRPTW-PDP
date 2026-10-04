"""Compare every registered development job; never expose held-out quality."""
import argparse
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from tools.audit.summarize_model5_alns import compare


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase')
    args = parser.parse_args()
    before = [r for r in json.loads((OUT/'before/summary.json').read_text())
              if r['split'] == 'development']
    after = json.loads((OUT/args.phase/'summary.json').read_text())
    assert len(before) == len(after) == 30
    assert all(r['split'] == 'development' for r in after)
    data = compare(before, after)
    (OUT/(args.phase+'_comparison.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(data['overview'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
