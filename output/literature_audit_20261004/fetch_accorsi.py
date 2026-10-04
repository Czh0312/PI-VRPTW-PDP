"""Download a public university-hosted paper discovered in supplied references."""
from pathlib import Path
from urllib.request import Request, urlopen
from hashlib import sha256
import json
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/literature_audit_20261004'
URLS = [
    'https://cris.unibo.it/retrieve/0d3dcbe4-3f52-4a4b-99f1-763637be5bbe/PP%20Managing%20uncertain.pdf',
    'https://www.sustainablebusiness.pitt.edu/sites/default/files/reusable-containers-sustprodcons-2022.pdf',
]
errors = []
for url in URLS:
    try:
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=45) as response:
            body = response.read()
        if not body.startswith(b'%PDF'):
            raise ValueError('Not a PDF')
        path = OUT / 'sources/accorsi_2022_washing.pdf'
        if path.exists() and sha256(path.read_bytes()).digest() != sha256(body).digest():
            raise ValueError('Existing file differs; refusing overwrite')
        path.write_bytes(body)
        reader = PdfReader(path)
        pages = [page.extract_text() or '' for page in reader.pages]
        txt = OUT / 'fulltexts/accorsi_2022_washing.txt'
        txt.write_text('\n\n'.join(f'===== PDF PAGE {i+1} =====\n{p}' for i, p in enumerate(pages)), encoding='utf-8')
        entry = dict(url=url, path=str(path), sha256=sha256(body).hexdigest(), pages=len(pages), extracted=str(txt), errors=errors)
        (OUT / 'fulltexts/accorsi_source.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(entry, ensure_ascii=False))
        break
    except Exception as exc:
        errors.append({'url': url, 'error': str(exc)})
else:
    print(json.dumps({'errors': errors}, ensure_ascii=False))
    raise SystemExit(1)
