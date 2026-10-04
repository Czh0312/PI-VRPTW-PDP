"""Audit the OSF Study 7 SAV; export aggregates, never participant records."""
from pathlib import Path
import hashlib
import json
import sys
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / 'output/tmp/topanga_python_deps'))
import pyreadstat

RAW = ROOT / 'data/real_case/raw/topanga_study7'
OUT = ROOT / 'data/real_case/reports/topanga_study7'
OUT.mkdir(parents=True, exist_ok=True)
df, meta = pyreadstat.read_sav(str(RAW / 'Study 7 - Loops data.sav'))
seconds = (df.CLOSED_AT - df.OPENED_AT).dt.total_seconds()
valid = seconds.ge(300)
paper = valid & df.Both_pre_post.eq(3)

def summary(mask):
    sample = df.loc[mask]
    days = seconds.loc[mask] / 86400
    locations = sample.LOOP_OPENED_SHORT_LOCATION.ne(sample.LOOP_CLOSED_SHORT_LOCATION)
    return dict(n=len(sample), users=int(sample.CONSUMER_ID.nunique()),
                mean_days=float(days.mean()),
                quantile_days={str(q): float(days.quantile(q)) for q in [.5,.75,.9,.95,.99]},
                cumulative_elapsed_days={str(d): float(days.le(d).mean()) for d in [1,2,3,4,7,14,30]},
                max_days=float(days.max()), different_return_location_fraction=float(locations.mean()))

manifest = json.loads((RAW / 'osf_file_manifest.json').read_text(encoding='utf-8-sig'))
checksums = []
for entry in manifest['data']:
    path = RAW / entry['attributes']['name']
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = entry['attributes']['extra']['hashes']['sha256']
    assert actual == expected, path.name
    checksums.append(dict(name=path.name, sha256=actual, matches_osf=True))
assert np.allclose(seconds.dropna(), df.loc[seconds.notna(), 'RETURN_TIME'])

result = dict(source_doi='10.1093/jcr/ucag007', audit_date='2026-09-08',
              license='OSF overview: No License; view-only access. No redistribution assumed.',
              rows=len(df), columns=len(df.columns), users=int(df.CONSUMER_ID.nunique()),
              fields=list(df.columns), checksums=checksums,
              duplicate_loop_ids=int(df.LOOP_ID.duplicated().sum()),
              missing_return=int(seconds.isna().sum()), negative_lags=int(seconds.lt(0).sum()),
              under_five_minutes=int(seconds.lt(300).sum()),
              opened_range=[str(df.OPENED_AT.min()),str(df.OPENED_AT.max())],
              closed_range=[str(df.CLOSED_AT.min()),str(df.CLOSED_AT.max())],
              all_completed_at_least_5min=summary(valid), paper_selection=summary(paper),
              paper_count_matches=bool(paper.sum()==22904 and df.loc[paper,'CONSUMER_ID'].nunique()==710),
              caveats=['Closed time is batched return registration, not pickup-ready time.',
                       'Missing returns are not proven loss; extraction cutoff and cohort completeness unverified.',
                       'US campus return behavior is external calibration for Hangzhou, not Hangzhou observation.',
                       'No container asset ID, washing completion, scrap status, road distances or vehicle routes.'])
for name, mask in [('all_valid',valid),('paper',paper)]:
    days=seconds.loc[mask]/86400
    lag=np.ceil(days).astype(int)
    counts=lag.value_counts().sort_index()
    pd.DataFrame({'lag_day':counts.index,'count':counts.values,'probability':counts.values/counts.sum()}).to_csv(OUT/f'{name}_elapsed_day_pmf.csv',index=False)
    pd.crosstab(df.loc[mask,'LOOP_OPENED_SHORT_LOCATION'],df.loc[mask,'LOOP_CLOSED_SHORT_LOCATION']).to_csv(OUT/f'{name}_location_flows.csv')
    rows=[]
    for phase, phase_mask in [('pre',df.OPENED_AT.lt('2025-01-27')),('during',df.OPENED_AT.ge('2025-01-27') & df.OPENED_AT.lt('2025-02-24')),('post',df.OPENED_AT.ge('2025-02-24'))]:
        rows.append({'phase':phase,**summary(mask & phase_mask)})
    result[name+'_by_phase']=rows
(OUT/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ['fields','checksums','all_valid_by_phase','paper_by_phase']},ensure_ascii=False,indent=2))
