"""Independent Held-Karp check of the enumerated transport lower bound."""
from pathlib import Path
import importlib.util,math
import pytest
spec=importlib.util.spec_from_file_location('certificate_test',Path(__file__).with_name('certify_reductions.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

def test_TSP_lower_bound_matches_independent_subset_dynamic_program():
    cfg,fleet=c.trial.d.load_routing_case(c.trial.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    p=c.trial.d.load_instance(cfg,fleet);cert=c.certificate(p,34.905055668684454)
    site=next(iter(p.satellites));customers=sorted({o.customer for o in p.orders});n=len(customers)
    dp={(1<<j,j):p.distance(site,customers[j]) for j in range(n)}
    for mask in range(1,1<<n):
        for j in range(n):
            if (mask,j) not in dp:continue
            for k in range(n):
                if mask>>k&1:continue
                key=(mask|1<<k,k)
                dp[key]=min(dp.get(key,math.inf),dp[mask,j]+p.distance(customers[j],customers[k]))
    best=min(dp[(1<<n)-1,j]+p.distance(customers[j],site) for j in range(n))
    assert best==pytest.approx(cert['TSP_distance_lower_bound'])
    assert cert['N_ge_5_total_cost_lower_bound']>cert['incumbent_cost']

def test_audited_incumbent_obeys_all_derived_conditions():
    cfg,fleet=c.trial.d.load_routing_case(c.trial.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    p=c.trial.d.load_instance(cfg,fleet);seed=c.trial.read(c.trial.SEED)
    audit=c.trial.d.audit_solution(p,seed);assert audit['valid']
    cert=c.certificate(p,audit['objective'])
    assert seed['container_pool_size']==cert['pool_at_least']==cert['pool_at_most_in_optimum']
    assert seed['batch_boundaries'][1]>=cert['second_batch_start_lower_bound']
    assert seed['batch_cutoffs']['b1']>=cert['first_cutoff_lower_bound']
    assert all(seed['order_batch_assignment'][oid]=='b1' for oid in cert['forced_first_batch_orders'])
    assert all(seed['order_batch_assignment'][oid]=='b2' for oid in cert['forced_second_batch_orders'])

def test_second_grid_implies_same_optimum_partition():
    cfg,fleet=c.trial.d.load_routing_case(c.trial.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    p=c.trial.d.load_instance(cfg,fleet);seed=c.trial.read(c.trial.SEED)
    assert p.count_clock(2)[2]==1
    seconds=c.certificate(p,seed['info']['objective'],grid_seconds=1)
    minutes=c.certificate(p,seed['info']['objective'],grid_seconds=60)
    assert seconds['first_cutoff_lower_bound']==34252
    assert seconds['second_batch_start_lower_bound']==35822
    assert seconds['forced_first_batch_orders']==minutes['forced_first_batch_orders']
    assert seconds['forced_second_batch_orders']==minutes['forced_second_batch_orders']
    assert seconds['N_ge_5_total_cost_lower_bound']>seed['info']['objective']
    c.trial.save('second_grid_reduction_certificate.json',seconds)
