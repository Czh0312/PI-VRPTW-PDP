"""Preserve and extract the supplied perishable-food RTI-selection paper."""
from pathlib import Path
import hashlib
import json
import shutil
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
original = Path("C:/Users/猫的秋刀鱼/Downloads/Closed-loop inventory routing problem for perishable food with returnable transport items selection.pdf")
copied = ROOT / "reference/novelty_fulltexts_20261004/zhang_2024_perishable_rti_selection.pdf"
out = ROOT / "output/literature_audit_20261004/fulltexts"
digest = hashlib.sha256(original.read_bytes()).hexdigest()
if copied.exists():
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == digest
else:
    shutil.copy2(original, copied)
reader = PdfReader(copied)
texts = [p.extract_text() or "" for p in reader.pages]
target = out / "zhang_2024_perishable_rti_selection.txt"
target.write_text("\n\n".join(f"[PDF page {i+1}]\n{t}" for i,t in enumerate(texts)), encoding="utf-8")
record = dict(original=str(original), copy=str(copied), sha256=digest,
              pages=len(texts), characters_per_page=[len(t) for t in texts],
              extracted=str(target), metadata={str(k):str(v) for k,v in (reader.metadata or {}).items()})
(out / "zhang2024_source.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(dict(pages=len(texts), metadata=record['metadata'], first_pages='\n'.join(texts[:2])[:6000]), ensure_ascii=False))
