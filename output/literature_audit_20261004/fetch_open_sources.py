"""Fetch public author manuscripts for the read-only literature audit."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen
import hashlib
import json

ROOT = Path(__file__).resolve().parent
SOURCES = {
    "zhou_2022_author_preprint.pdf": "https://assets-eu.researchsquare.com/files/rs-604710/v1_covered.pdf?c=1641840236",
    "iassinovskaia_2017_author_preprint.pdf": "https://orbi.uliege.be/bitstream/2268/199421/1/PDIRPTWManuscript.pdf",
    "grangier_2016_working_paper_2014.pdf": "https://www.cirrelt.ca/documentstravail/cirrelt-2014-33.pdf",
    "hemmelmayr_2012_working_paper_2011.pdf": "https://www.cirrelt.ca/documentstravail/CIRRELT-2011-42.pdf",
    "charaf_2024_published.pdf": "https://pure.tue.nl/ws/portalfiles/portal/341282855/1-s2.0-S0360835224005849-main.pdf",
    "hemmelmayr_2012_published.pdf": "https://research.wu.ac.at/files/19820077/Hemmelmayr_etal_2012_COR_An-adaptive-large-neighborhood.pdf",
}

def fetch(item):
    name, url = item
    try:
        path = ROOT / "sources" / name
        if path.exists():
            data = path.read_bytes()
        else:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=25) as response:
                data = response.read()
        if not data.startswith(b"%PDF"):
            raise ValueError("Response is not a PDF")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        from pypdf import PdfReader
        reader = PdfReader(path)
        text = "\n\n".join(f"[PDF page {i + 1}]\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages))
        (ROOT / "extracted").mkdir(exist_ok=True)
        (ROOT / "extracted" / (path.stem + ".txt")).write_text(text, encoding="utf-8")
        return {"file": name, "url": url, "status": "downloaded", "pages": len(reader.pages), "sha256": hashlib.sha256(data).hexdigest()}
    except Exception as exc:
        return {"file": name, "url": url, "status": "failed", "error": str(exc)}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, SOURCES.items()))
    (ROOT / "source_manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
