"""Check the discrete assignment guarantees used by the physical redesign."""
import itertools
import random

from tools.data.build_benchmark27_spacetime import (
    hungarian,balanced_facilities,period_cells,PROFILES,make_reference,SOURCE,read)
from tools.configuration.loader import PROJECT_ROOT


def test_hungarian_matches_exhaustive_assignments():
    rng=random.Random(842)
    for n in range(1,7):
        matrix=[[rng.randrange(25) for _ in range(n)] for _ in range(n)]
        assignment=hungarian(matrix)
        assert len(set(assignment))==n
        actual=sum(matrix[i][j] for i,j in enumerate(assignment))
        assert actual==min(sum(matrix[i][j] for i,j in enumerate(p)) for p in itertools.permutations(range(n)))


def test_balanced_facility_assignment_matches_brute_force():
    customers=[dict(node_id=f'C{i}',x=x,y=0) for i,x in enumerate((0,1,2,3,20,21,22))]
    candidates=[dict(node_id=f'S{i}',x=x,y=0) for i,x in enumerate((0,10,22))]
    selected,mapping,stats=balanced_facilities(customers,candidates,2)
    best=float('inf')
    for a,b in itertools.combinations(candidates,2):
        for first in itertools.combinations(range(7),3):
            first=set(first)
            for x,y in ((a,b),(b,a)):
                best=min(best,sum(abs(c['x']-(x if i in first else y)['x']) for i,c in enumerate(customers)))
    assert abs(stats['distance_sum']-best)<1e-9
    assert sorted(list(mapping.values()).count(s['node_id']) for s in selected)==[3,4]


def test_site_period_cells_preserve_margins_and_balance():
    for n in (7,10,12,20,30,50,100):
        for s in range(1,6):
            for periods,profiles in PROFILES.items():
                if n<s*periods:continue
                counts={f'S{i}':n//s+int(i<n%s) for i in range(s)}
                for weights in profiles:
                    cells,totals=period_cells(counts,weights)
                    assert sum(totals)==n
                    assert all(sum(cells[key])==value for key,value in counts.items())
                    assert all(sum(row[b] for row in cells.values())==totals[b] for b in range(periods))
                    assert min(value for row in cells.values() for value in row)>=1
                    assert all(max(row[b] for row in cells.values())-min(row[b] for row in cells.values())<=1 for b in range(periods))


def test_generated_fleets_use_approved_capacity_revision(tmp_path):
    manifest=read(PROJECT_ROOT/SOURCE/'manifest.json');seen=set()
    accepted={7:(7,140,3,60),10:(10,200,3,60),12:(12,240,3,60),
              20:(20,400,6,120),30:(30,600,6,120),50:(30,600,6,120),100:(60,1200,6,120)}
    for size_index,size in enumerate(('small','medium','large')):
        for index,base in enumerate(b for b in manifest['base_cases'] if b['size']==size):
            n=base['customer_count']
            if n in seen:continue
            seen.add(n);folder=tmp_path/base['id'];folder.mkdir()
            _,_,stats=make_reference(base,index,size_index,folder)
            fleet=read(folder/'fleet.json')
            assert tuple(fleet[k] for k in ('E1_Ck','E1_Qk','E2_Cv','E2_Qv'))==accepted[n]
            assert (stats['E1_volume'],stats['E1_weight'],stats['E2_volume'],stats['E2_weight'])==accepted[n]
            source=PROJECT_ROOT/SOURCE/'shared'/size/base['id']
            assert (folder/'economic.json').read_bytes()==(source/'economic.json').read_bytes()
