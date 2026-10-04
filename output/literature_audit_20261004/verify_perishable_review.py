"""Verify the final priority full-text review; preserve earlier audit snapshots."""
from pathlib import Path
import csv
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "output/literature_audit_20261004"
OUT = AUDIT / "perishable_rti_verification.json"

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

base = json.loads((AUDIT / "audit_verification.json").read_text(encoding="utf-8"))
source = json.loads((AUDIT / "fulltexts/zhang2024_source.json").read_text(encoding="utf-8"))
original_matches = digest(Path(source["original"])) == source["sha256"]
copy_matches = digest(Path(source["copy"])) == source["sha256"]
core = {
    k: dict(prior_sha256=v, current_sha256=digest(ROOT/k), unchanged=v==digest(ROOT/k))
    for k, v in base["hashes"].items() if k.startswith(("models/", "config/"))
}
prior_sources = []
for s in json.loads((AUDIT / "fulltexts/supplied_sources.json").read_text(encoding="utf-8")):
    prior_sources.append(dict(
        file=s["file"], original_unchanged=digest(Path(s["original"]))==s["sha256"],
        copy_matches=digest(Path(s["copy"]))==s["sha256"]
    ))
zhou = json.loads((AUDIT / "fulltexts/zhou2024_source.json").read_text(encoding="utf-8"))
prior_sources.append(dict(
    file="zhou2024", original_unchanged=digest(Path(zhou["original"]))==zhou["sha256"],
    copy_matches=digest(Path(zhou["copy"]))==zhou["sha256"]
))
accorsi = json.loads((AUDIT / "fulltexts/accorsi_source.json").read_text(encoding="utf-8"))
accorsi_matches = digest(Path(accorsi["path"])) == accorsi["sha256"]

rendered = []
for n in range(5, 20):
    p = AUDIT / f"fulltexts/rendered/zhang_2024_perishable_rti_selection-{n:02d}.png"
    with Image.open(p) as im:
        rendered.append(dict(pdf_page=n, printed_page=n+499, size=list(im.size), file=str(p.relative_to(ROOT))))
        im.verify()

m5 = ROOT / "output/experiments/m5_comparison_20261004/full"
manifest = json.loads((m5 / "manifest.json").read_text(encoding="utf-8"))
q = {x["case_id"]: x["total_container_uses"] for x in manifest["selected_dataset_cases"]}
with (m5 / "runs.csv").open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
pool = dict(
    runs=len(rows), configurations=len(q),
    input_pool_equals_total_demand=sum(int(r["pool"])==q[r["case_id"]] for r in rows),
    valid=sum(r["valid"]=="True" for r in rows)
)

reports = [
    ROOT / "docs/novelty_perishable_rti_20261004.md",
    ROOT / "docs/novelty_satellite_sync_20261004.md",
    ROOT / "docs/novelty_fulltext_comparison_20261004.md",
    ROOT / "docs/novelty_overlap_audit_20261004.md",
    ROOT / "reference/novelty_fulltexts_20261004/reading_list.md",
]
record = dict(
    verified_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
    purpose="Masters thesis perishable RTI full-text comparison; not worldwide originality clearance",
    literature_entries=21,
    highlighted_core_fulltexts_reviewed=7,
    source=dict(**source, original_matches=original_matches, copy_matches=copy_matches),
    visually_reviewed_pdf_pages=list(range(5, 20)), rendered_pages=rendered,
    core_files=core, prior_user_sources=prior_sources, accorsi_unchanged=accorsi_matches,
    m5_pool_check=pool, remaining_priority_user_requested_fulltexts=[],
    additional_publisher_abstract_only=["10.1287/trsc.2022.1136", "10.3390/su14105805"],
    achamrah_discovery=dict(
        url="https://www.mdpi.com/2071-1050/14/10/5805",
        title="An Artificial-Immune-System-Based Algorithm Enhanced with Deep Reinforcement Learning for Solving Returnable Transport Item Problems",
        method="Publisher-indexed abstract returned by web search; direct page request previously rate-limited; main model not reviewed",
        claim_scope="Multi-supplier, multi-type RTI inventory routing and virtual pooling; not proof of two physical echelons or of the M5 event chain",
    ),
    prior_snapshots={
        str(p.relative_to(ROOT)): digest(p)
        for p in [AUDIT/"audit_verification.json", AUDIT/"fulltext_verification.json", AUDIT/"satellite_sync_verification.json"]
    },
    notes=[
        "No active model, solver, dataset or pool configuration change.",
        "No new optimization experiment or repeated unchanged mechanism tests.",
        "Zhang2024 directly reusable empty RTIs do not imply same-period reuse; constraints13-14 checked visually.",
        "Per-period joint routing and reusable inventory already exist; candidate contribution is specific event eligibility and release coupling, not generic route-inventory feedback.",
        "Kernel search code and original random instances not reproduced; restricted-model and global-bound distinctions preserved.",
        "Completed priority full-text gaps do not imply exhaustive database coverage or worldwide novelty clearance.",
    ],
)

# Create the self-linked record before checking document links.
OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
broken = []
checked = []
matrix_checks = []
for p in reports:
    t = p.read_text(encoding="utf-8")
    assert "\ufffd" not in t
    assert t.count("$$") % 2 == 0
    if p.name == "novelty_fulltext_comparison_20261004.md":
        header = next(line for line in t.splitlines() if line.startswith("| 维度 | 当前 M5 |"))
        assert header.count("|") == 9, header
        assert "| Zhou2024 | Zhang2024 |" in header, header
    expected = None
    for line in t.splitlines():
        if line.startswith("|"):
            count = line.count("|")
            if expected is None:
                expected = count
            assert expected == count, (p, line)
        else:
            expected = None
    matrix_checks.append(dict(report=str(p.relative_to(ROOT)), table_columns_consistent=True))
    for target in re.findall(r"\]\(([^)]+)\)", t):
        target = target.strip("<>")
        if target.startswith(("http://", "https://", "#")):
            continue
        local = re.sub(r":\d+$", "", target.split("#", 1)[0])
        candidate = Path(local) if re.match(r"^[A-Za-z]:[/\\]", local) else p.parent/local
        checked.append(dict(report=str(p.relative_to(ROOT)), target=target))
        if not candidate.exists():
            broken.append(checked[-1])
record.update(
    local_links_checked=len(checked), broken_local_links=broken, matrix_checks=matrix_checks,
    report_files={
        str(p.relative_to(ROOT)): dict(sha256=digest(p), characters=len(p.read_text(encoding="utf-8")), lines=len(p.read_text(encoding="utf-8").splitlines()))
        for p in reports
    },
)
assert original_matches and copy_matches
assert all(v["unchanged"] for v in core.values())
assert all(v["original_unchanged"] and v["copy_matches"] for v in prior_sources)
assert accorsi_matches
assert not broken, broken
assert pool["runs"] == pool["input_pool_equals_total_demand"] == pool["valid"] == 560
for key in ("main", "full", "reading"):
    p = {"main": reports[3], "full": reports[2], "reading": reports[4]}[key]
    t = p.read_text(encoding="utf-8")
    assert not any(s in t for s in ["L12食品RTI时序仍待全文", "L12仍需补全文", "仍建议补 *Closed-loop", "仍值得补充的是 **Zhang"])
OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(dict(
    reports=record["report_files"], links_checked=len(checked), broken_links=len(broken),
    core_unchanged=True, visually_reviewed_pages=len(rendered),
    new_original_and_copy_verified=True, prior_user_sources_verified=len(prior_sources),
    accorsi_verified=accorsi_matches, m5=pool, remaining_priority_fulltexts=[]
), ensure_ascii=False, indent=2))
