"""Render the complete relevant model and method pages for visual reading."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import subprocess
import json

ROOT = Path(__file__).resolve().parents[2]
POPPLER = Path("C:/Users/猫的秋刀鱼/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe")
DEST = ROOT / "output/literature_audit_20261004/fulltexts/rendered"
SECTIONS = [
    ("he_2026_multiperiod_crossdock", 3, 8),
    ("guo_2023_rti_sharing", 6, 12),
    ("yavrucu_2025_closed_loop", 6, 11),
    ("soysal_2023_vmi", 3, 4),
    ("soysal_2023_vmi", 9, 9),
]

def render(section):
    name, first, last = section
    src = ROOT / "reference/novelty_fulltexts_20261004" / (name + ".pdf")
    prefix = DEST / name
    res = subprocess.run([str(POPPLER), "-f", str(first), "-l", str(last),
                          "-scale-to", "1800", "-png", str(src), str(prefix)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode:
        raise RuntimeError(res.stderr)
    return dict(name=name, first=first, last=last, files=[p.name for p in DEST.glob(name+"-*.png")])

if __name__ == "__main__":
    DEST.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(render, SECTIONS))
    print(json.dumps(results, ensure_ascii=False, indent=2))
