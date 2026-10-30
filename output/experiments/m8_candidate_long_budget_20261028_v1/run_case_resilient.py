"""Keep the frozen solver unchanged; retry Windows progress-file writes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('original_long_runner',HERE/'run_case.py')
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def save_with_retry(path,value):
    if path.name=='manifest.json' and 'driver_hashes' in value:
        value['driver_hashes'][str(Path(__file__).resolve())]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        value['progress_io']='Direct UTF-8 writes with bounded PermissionError retry; no solver or cut-generation change.'
    payload=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    for attempt in range(10):
        try:
            path.write_text(payload,encoding='utf-8')
            return
        except PermissionError:
            if attempt==9:
                raise
            time.sleep(min(.02*2**attempt,.5))


runner.save=save_with_retry

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['S03','S08'],required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--seconds',type=float,default=3600)
    args=parser.parse_args()
    runner.run(args.case,args.seed,args.seconds)
