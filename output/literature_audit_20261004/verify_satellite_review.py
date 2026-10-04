"""Verify the third literature audit without changing existing solver evidence."""
from pathlib import Path
import csv
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "output/literature_audit_20261004"
OUT = AUDIT / "satellite_sync_verification.json"
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

base = json.loads((AUDIT / "audit_verification.json").read_text(encoding="utf-8"))
old = json.loads((AUDIT / "fulltext_verification.json").read_text(encoding="utf-8"))
source = json.loads((AUDIT / "fulltexts/zhou2024_source.json").read_text(encoding="utf-8"))
original_matches = digest(Path(source["original"])) == source["sha256"]
copy_matches = digest(Path(source["copy"])) == source["sha256"]
core = {k:dict(prior_sha256=v,current_sha256=digest(ROOT/k),unchanged=v==digest(ROOT/k))
        for k,v in base["hashes"].items() if k.startswith(("models/", "config/"))}
prior_sources=[]
for s in json.loads((AUDIT / "fulltexts/supplied_sources.json").read_text(encoding="utf-8")):
    prior_sources.append(dict(file=s["file"],original_unchanged=digest(Path(s["original"]))==s["sha256"],copy_matches=digest(Path(s["copy"]))==s["sha256"]))

rendered=[]
for n in range(3,20):
    p = AUDIT / f"fulltexts/rendered/zhou_2024_satellite_sync-{n:02d}.png"
    with Image.open(p) as im:
        rendered.append(dict(page=n,size=list(im.size),file=str(p.relative_to(ROOT))))
        im.verify()

m5=ROOT/"output/experiments/m5_comparison_20261004/full"
manifest=json.loads((m5/"manifest.json").read_text(encoding="utf-8"))
q={x["case_id"]:x["total_container_uses"] for x in manifest["selected_dataset_cases"]}
with (m5/"runs.csv").open(encoding="utf-8-sig",newline="") as f:
    rows=list(csv.DictReader(f))
pool=dict(runs=len(rows),configurations=len(q),input_pool_equals_total_demand=sum(int(r["pool"])==q[r["case_id"]] for r in rows),valid=sum(r["valid"]=="True" for r in rows))
reports=[ROOT/"docs/novelty_satellite_sync_20261004.md",ROOT/"docs/novelty_fulltext_comparison_20261004.md",ROOT/"docs/novelty_overlap_audit_20261004.md",ROOT/"reference/novelty_fulltexts_20261004/reading_list.md"]
record=dict(verified_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),purpose="Masters thesis satellite synchronization full-text comparison; not worldwide originality clearance",source=dict(**source,original_matches=original_matches,copy_matches=copy_matches),visually_reviewed_pdf_pages=list(range(3,20)),rendered_pages=rendered,core_files=core,prior_user_sources=prior_sources,m5_pool_check=pool,remaining_priority_fulltext=["10.1080/00207543.2023.2275639"],additional_publisher_abstract_only=["10.1287/trsc.2022.1136"],prior_snapshot=dict(path=str((AUDIT/"fulltext_verification.json").relative_to(ROOT)),sha256=digest(AUDIT/"fulltext_verification.json")),notes=["No active model, solver, dataset or pool configuration change.","No new optimization experiment or repeated unchanged mechanism tests.","Zhou2024 intended g eligibility compared using Table4, equations32-34 and figures3-4; no claim that printed equation34 alone proves logical equivalence.","Appendices A-E and source algorithm not reviewed."])
OUT.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
broken=[]
checked=[]
matrix_checks=[]
for p in reports:
    t=p.read_text(encoding="utf-8")
    assert "\ufffd" not in t
    assert t.count("$$")%2==0
    expected=None
    for line in t.splitlines():
        if line.startswith("|"):
            count=line.count("|")
            if expected is None:
                expected=count
            assert expected==count,(p,line)
        else:
            expected=None
    matrix_checks.append(dict(report=str(p.relative_to(ROOT)),table_columns_consistent=True))
    for target in re.findall(r"\]\(([^)]+)\)",t):
        target=target.strip("<>")
        if target.startswith(("http://","https://","#")):
            continue
        local=re.sub(r":\d+$","",target.split("#",1)[0])
        candidate=Path(local) if re.match(r"^[A-Za-z]:[/\\]",local) else p.parent/local
        checked.append(dict(report=str(p.relative_to(ROOT)),target=target))
        if not candidate.exists():
            broken.append(checked[-1])
record.update(local_links_checked=len(checked),broken_local_links=broken,matrix_checks=matrix_checks,report_files={str(p.relative_to(ROOT)):dict(sha256=digest(p),characters=len(p.read_text(encoding="utf-8")),lines=len(p.read_text(encoding="utf-8").splitlines())) for p in reports})
assert original_matches and copy_matches
assert all(v["unchanged"] for v in core.values())
assert all(v["original_unchanged"] and v["copy_matches"] for v in prior_sources)
assert not broken,broken
assert pool["runs"]==pool["input_pool_equals_total_demand"]==pool["valid"]==560
assert old["remaining_fulltext_coverage"]==["10.1016/j.cor.2024.106600","10.1080/00207543.2023.2275639"]
OUT.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(dict(reports=record["report_files"],links_checked=len(checked),broken_links=len(broken),core_unchanged=True,rendered_pages=len(rendered),new_original_and_copy_verified=True,prior_sources_verified=len(prior_sources),m5=pool),ensure_ascii=False,indent=2))
