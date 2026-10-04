"""Preserve and extract user-supplied papers for the second literature audit."""
from pathlib import Path
import hashlib
import json
import shutil
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "reference" / "novelty_fulltexts_20261004"
OUT = ROOT / "output" / "literature_audit_20261004" / "fulltexts"
FILES = [
    ("he_2026_multiperiod_crossdock.pdf", Path("C:/Users/猫的秋刀鱼/Downloads/多周期汽车循环包装器具调度及越库配送协同优化研究_和剑波.pdf")),
    ("guo_2023_rti_sharing.pdf", Path("C:/Users/猫的秋刀鱼/Downloads/Integrated inventory control and scheduling decision framework for packaging and products on a reusable transport item sharing platform.pdf")),
    ("yavrucu_2025_closed_loop.pdf", Path("C:/Users/猫的秋刀鱼/Downloads/Modeling a closed-loop inventory routing problem for returnable transport items under horizontal logistics collaborations and dynamic demand structure.pdf")),
    ("soysal_2023_vmi.pdf", Path("C:/Users/猫的秋刀鱼/Desktop/1-s2.0-S0038012122003111-main.pdf")),
]

def main():
    DEST.mkdir(exist_ok=True, parents=True)
    OUT.mkdir(exist_ok=True, parents=True)
    manifest = []
    for name, original in FILES:
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        copied = DEST / name
        if copied.exists():
            if hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Different source already exists: {copied}")
        else:
            shutil.copy2(original, copied)
        reader = PdfReader(copied)
        texts = [page.extract_text() or "" for page in reader.pages]
        target = OUT / (Path(name).stem + ".txt")
        target.write_text("\n\n".join(f"[PDF page {i+1}]\n{t}" for i, t in enumerate(texts)), encoding="utf-8")
        record = dict(file=name, original=str(original), copy=str(copied), sha256=digest,
                      pages=len(texts), characters_per_page=[len(t) for t in texts],
                      extracted=str(target), metadata={str(k): str(v) for k, v in (reader.metadata or {}).items()})
        manifest.append(record)
        print(json.dumps(dict(file=name, pages=len(texts), first_page=texts[0][:1900]), ensure_ascii=False))
    (OUT / "supplied_sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
