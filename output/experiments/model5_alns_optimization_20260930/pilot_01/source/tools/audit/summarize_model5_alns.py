"""Paired distance and seed-dispersion summaries, retaining feasibility failures."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import statistics


def compare(before, after):
    def key(row):
        return row['case_id'], row['pool'], row['budget'], row['seed']
    left, right = {key(r):r for r in before}, {key(r):r for r in after}
    if len(left)!=len(before) or len(right)!=len(after) or left.keys()!=right.keys():
        raise ValueError('Comparison requires identical unique registered jobs')
    paired, groups = [], defaultdict(list)
    for k,a in left.items():
        b = right[k]
        common = a['valid'] and b['valid']
        delta = (b['objective']/a['objective']-1)*100 if common and a['objective'] else None
        row = {field:a[field] for field in ('case_id','family','split','customers','batches','pool','pool_label','budget','seed')}
        row.update(before_valid=a['valid'],after_valid=b['valid'],before=a.get('objective'),
                   after=b.get('objective'),change_percent=delta)
        paired.append(row)
        groups[k[:3]].append(row)
    summaries = []
    for rows in groups.values():
        row = {k:v for k,v in rows[0].items() if k in ('case_id','split','customers','batches','pool','pool_label','budget')}
        row['runs'] = len(rows)
        for version in ('before','after'):
            values = [r[version] for r in rows if r[version+'_valid']]
            mean = statistics.mean(values) if values else None
            sd = statistics.stdev(values) if len(values)>1 else 0.0 if values else None
            row.update({version+'_valid':len(values),version+'_mean':mean,
                        version+'_best':min(values) if values else None,
                        version+'_worst':max(values) if values else None,
                        version+'_sd':sd,version+'_cv_percent':100*sd/mean if mean else None})
        common = [r['change_percent'] for r in rows if r['change_percent'] is not None]
        row['paired_change_percent'] = statistics.mean(common) if common else None
        summaries.append(row)
    changes = [r['change_percent'] for r in paired if r['change_percent'] is not None]
    complete = [r for r in summaries if r['before_valid']==r['after_valid']==r['runs']]
    overview = dict(jobs=len(paired),before_valid=sum(r['before_valid'] for r in paired),
        after_valid=sum(r['after_valid'] for r in paired),common_valid=len(changes),
        improved=sum(v < -1e-6 for v in changes),tied=sum(abs(v)<=1e-6 for v in changes),
        worse=sum(v > 1e-6 for v in changes),paired_mean_change_percent=statistics.mean(changes) if changes else None,
        groups=len(summaries),dispersion_groups=len(complete),
        before_mean_cv_percent=statistics.mean(r['before_cv_percent'] for r in complete) if complete else None,
        after_mean_cv_percent=statistics.mean(r['after_cv_percent'] for r in complete) if complete else None,
        cv_improved_groups=sum(r['after_cv_percent']<r['before_cv_percent']-1e-6 for r in complete),
        worst_improved_groups=sum(r['after_worst']<r['before_worst']-1e-8 for r in complete))
    for split in ('development','held_out'):
        values = [r['change_percent'] for r in paired if r['split']==split and r['change_percent'] is not None]
        overview[split+'_paired_change_percent'] = statistics.mean(values) if values else None
    return dict(overview=overview,groups=summaries,paired=paired)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--before',default='before')
    parser.add_argument('--after',default='after')
    args=parser.parse_args()
    load=lambda phase:json.loads((args.output/phase/'summary.json').read_text())
    data=compare(load(args.before),load(args.after))
    (args.output/'comparison.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    for name in ('groups','paired'):
        with (args.output/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(data[name][0]))
            writer.writeheader();writer.writerows(data[name])
    print(json.dumps(data['overview'],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
