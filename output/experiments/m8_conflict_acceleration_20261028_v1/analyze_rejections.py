"""Explain which historical follower patterns the new valid links reject."""
from collections import Counter
from common import HERE,ROOT,load,read,save
from master_sync import violations

rows=[]
for case in ['S03','S08']:
    p,_,_,_,_=load(case)
    info=read(ROOT/'output/experiments/m8_candidate_long_budget_20261028_v1'/case/'decomposition_summary.json')
    unique={}
    for event in info['events']:
        key=(event['K'],tuple(sorted(event['delivery_batches'].items())),
             tuple(sorted(event['recovery_batches'].items())),tuple(event['masks']))
        unique[key]=event
    counts=Counter()
    rejected=0
    invalid_native=[]
    for key,event in unique.items():
        issues=violations(p,event)
        if issues:
            rejected+=1
            counts.update({issue['reason'] for issue in issues})
            if event['native_status']!=3:
                invalid_native.append(dict(K=event['K'],masks=event['masks'],native_status=event['native_status']))
    rows.append(dict(case=case,historical_unique_patterns=len(unique),rejected_by_new_links=rejected,
                     reason_counts=dict(counts),rejected_patterns_not_ultimately_native_infeasible=invalid_native))
save(HERE/'historical_rejection_analysis.json',rows)
print(rows)
