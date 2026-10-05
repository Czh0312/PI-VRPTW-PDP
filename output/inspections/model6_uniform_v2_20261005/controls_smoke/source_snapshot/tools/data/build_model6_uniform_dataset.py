"""Independent appointments for uniform M6; preserve the legacy fixed-slot suite.

No optimizer is called. B3 selects one physical source per family, not a clock
for the new appointments. Variants and sensitivity anchors are chosen ex ante.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from tools.data.build_model6_demo_suite import synthetic_profile
from tools.data.static_batch_dataset import load_manifest, verify_artifacts

VERSION = 'model6_uniform_suite_v2'
DEFAULT_DESIGN = 'config/datasets/model6_uniform_v2.json'
DEFAULT_OUTPUT = 'data/model6/uniform_v2_20261005'
ARMS = ('joint', 'fixed_k', 'fixed_cutoff', 'fixed_assignment', 'full_pool')


def reference(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def fraction(seed, family, stream, identifier):
    value = signature([VERSION, seed, family, stream, identifier])
    return int(value[:13], 16) / 16**13


def appointment_orders(source, family, policy, design, variant):
    """Use exogenous stratified appointment centers, independent of routes and K.

    Same seed/centers across widths, costs, fleets and K domains. Repeated visits
    conserve boxes and goods per customer; each visit retains a service duration.
    """
    seed, start = design['seed'], policy['cycle_start']
    low, high = design['appointment_offsets_minutes']
    by_site = defaultdict(list)
    for order in source:
        by_site[order['satellite_id']].append(order)
    centers, quantiles = {}, {}
    for site, orders in sorted(by_site.items()):
        ranked = sorted(orders, key=lambda o: (fraction(seed, family, 'rank', o['order_id']), o['order_id']))
        for index, order in enumerate(ranked):
            oid = order['order_id']
            u = fraction(seed, family, 'jitter', oid)
            q = ((index + (2*u-1)*design['stratum_jitter_fraction'])/(len(ranked)-1)
                 if len(ranked)>1 else u)
            q = min(1., max(0., q))
            if variant == 'peak_early':
                q = q*q
            elif variant == 'peak_late':
                q = math.sqrt(q)
            quantiles[oid] = q
            centers[oid] = start + round(low+(high-low)*q)
    width_name = variant.removeprefix('spread_') if variant.startswith('spread_') else 'medium'
    width = design['window_widths_minutes'][width_name]
    result, lineage = [], {}
    for original in sorted(source, key=lambda o: o['order_id']):
        oid = original['order_id']
        visits = [(deepcopy(original), centers[oid])]
        # Split every eligible order in this explicit stress arm. There is no
        # assumption that extra visits or service work are observed business data.
        if variant == 'repeat_orders' and original['container_count'] >= 2:
            a = original['container_count']//2
            b = original['container_count']-a
            weight_a = original['goods_weight']*a/original['container_count']
            offset = design['repeat_visit_offset_minutes']
            visits = [
                ({**original, 'order_id':oid+'__a', 'container_count':a, 'goods_weight':weight_a},
                 max(start+low, centers[oid]-offset)),
                ({**original, 'order_id':oid+'__b', 'container_count':b,
                  'goods_weight':original['goods_weight']-weight_a}, min(start+high, centers[oid]+offset))]
        choices = design['collection_delays_minutes']
        delay = choices[min(len(choices)-1, int(fraction(seed, family, 'collection_delay', oid)*len(choices)))]
        for order, center in visits:
            ready = center-width//2
            due = ready+width
            use_end = center+order['service_time']+order['min_use_time']+delay
            order.update(delivery_ready=ready, delivery_due=due,
                         pickup_ready=math.floor(use_end-design['pickup_early_slack_minutes']),
                         pickup_due=math.ceil(due+order['service_time']+order['min_use_time']+delay
                                              +design['pickup_late_slack_minutes']),
                         release_time=(max(start, ready-design['release_lead_minutes'])
                                       if variant=='release_staggered' else start),
                         allowed_batch_ids=[b['id'] for b in policy['batches']],
                         delivery_batch_id=policy['batches'][0]['id'])
            if not start <= order['delivery_ready'] <= order['delivery_due'] <= policy['cycle_end']:
                raise ValueError(f'{oid}: appointment outside cycle; revise design explicitly')
            if not start <= order['pickup_ready'] <= order['pickup_due'] <= policy['cycle_end']:
                raise ValueError(f'{oid}: pickup outside cycle; no automatic clipping')
            result.append(order)
            lineage[order['order_id']] = dict(source_order_id=oid, center_minutes=center,
                                             quantile=quantiles[oid], collection_delay_minutes=delay)
    return result, lineage


def uniform_policy(source, design):
    policy = deepcopy(source)
    k = design['reference_batch_count']
    length = (policy['cycle_end']-policy['cycle_start'])/k
    first = length*design['reference_cutoff_ratio']
    policy.update(batches=[dict(id=f'b{i+1}', delivery_duration=first, return_duration=length-first)
                           for i in range(k)],
                  cleaning_duration=design['cleaning_minutes']['base'],
                  description='Reference uniform calendar for controlled ablations; M6 reoptimizes K and shared cutoff.')
    return policy


def source_totals(orders):
    totals = defaultdict(lambda: [0, 0.])
    for o in orders:
        key = (o['customer_id'], o['satellite_id'])
        totals[key][0] += o['container_count']
        totals[key][1] += o['goods_weight']
    return totals


def build_suite(destination=DEFAULT_OUTPUT, source_manifest='data/manifest.json',
                design_path=DEFAULT_DESIGN, families=None):
    design_path = resolve_project_path(design_path)
    design = json.loads(design_path.read_text())
    if design['dataset_version'] != VERSION:
        raise ValueError('Unsupported design version')
    _, all_sources = load_manifest(source_manifest, batch_counts=[3])
    sources = [r for r in all_sources if families is None or r['family_id'] in families]
    if not sources or (families is not None and {r['family_id'] for r in sources} != set(families)):
        raise ValueError('Unknown or empty family selection')
    anchors = {}
    for r in sorted(all_sources, key=lambda r:r['family_id']):
        if r['source_kind'] != 'synthetic_mechanism':
            anchors.setdefault((r['source_kind'], r['customer_count']), r['family_id'])
    root = resolve_project_path(destination).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty dataset: {root}')
    root.mkdir(parents=True, exist_ok=True)
    write_json(root/'design.json', design)
    external = {reference(resolve_project_path(source_manifest)):digest(resolve_project_path(source_manifest))}
    cases = []
    for record in sorted(sources, key=lambda r:r['family_id']):
        cfg, loaded_fleet = load_routing_case(record['case_path'], use_pool_config=False)
        original_orders = json.loads(resolve_project_path(cfg['orders']).read_text())
        provenance = json.loads(resolve_project_path(cfg['dataset_metadata']).read_text())
        family = record['family_id']
        family_key = provenance.get('base_family_id', family)
        mechanism = record['source_kind']=='synthetic_mechanism'
        variants = (['legacy_control'] if mechanism else design['core_variants'] +
                    (design['sensitivity_variants'] if family in anchors.values() else []))
        source_paths = [resolve_project_path(record['case_path'])] + [resolve_project_path(cfg[k]) for k in
                        ('data','orders','routing_config','operating_policy','distance_matrix','duration_matrix','dataset_metadata')
                        if isinstance(cfg.get(k), str)]
        source_hashes = {reference(p):digest(p) for p in source_paths}
        external.update(source_hashes)
        for variant in variants:
            folder = root/family/variant
            policy = deepcopy(loaded_fleet['operating_policy']) if mechanism else uniform_policy(loaded_fleet['operating_policy'], design)
            fleet = {k:deepcopy(v) for k,v in loaded_fleet.items() if k!='operating_policy'}
            if mechanism:
                orders = deepcopy(original_orders)
                for o in orders:
                    o.update(release_time=policy['cycle_start'], allowed_batch_ids=[b['id'] for b in policy['batches']])
                lineage = {}
            else:
                orders, lineage = appointment_orders(original_orders, family_key, policy, design, variant)
            old, new = source_totals(original_orders), source_totals(orders)
            if old.keys()!=new.keys() or any(old[k][0]!=new[k][0] or not math.isclose(old[k][1],new[k][1],abs_tol=1e-9) for k in old):
                raise ValueError('Demand conservation failed')
            if variant in ('clean_fast','clean_slow'):
                policy['cleaning_duration'] = design['cleaning_minutes'][variant.removeprefix('clean_')]
            if variant == 'fleet_tight':
                fleet['E1_vehicle_count'] = max(1, math.ceil(fleet['E1_vehicle_count']*design['e1_tight_ratio']))
                fleet['vehicles_per_satellite'] = max(1, math.floor(fleet['vehicles_per_satellite']*design['e2_tight_ratio']))
            if variant == 'capacity_tight':
                for field in ('E2_Qv','E2_Cv'):
                    fleet[field] *= design['e2_capacity_tight_ratio']
            profile = synthetic_profile(design['pool_rates'].get(variant.removeprefix('pool_'),design['pool_rates']['base']))
            profile.update(evaluation_period=f"one {policy['cycle_end']-policy['cycle_start']:g}-minute cycle",
                           description='Synthetic objective coefficients. Zhou distance units are not empirical kilometres; no CNY savings claim.')
            if variant == 'work_high':
                for field in ('e1_work_cost_per_minute','e2_work_cost_per_minute'):
                    profile[field] *= design['work_cost_multiplier']
            for name, value in [('orders',orders),('fleet',fleet),('policy',policy),('economic',profile)]:
                write_json(folder/f'{name}.json', value)
            name = family+'__'+variant
            counts = design['extended_batch_count_candidates'] if variant=='extended_k' else design['batch_count_candidates']
            case = dict(schema_version=2, model=6, solver='alns', case_name=name,
                        data=reference(resolve_project_path(cfg['data'])), orders=reference(folder/'orders.json'),
                        routing_config=reference(folder/'fleet.json'), operating_policy=reference(folder/'policy.json'),
                        economic_config=reference(folder/'economic.json'), dataset_metadata=reference(folder/'provenance.json'),
                        source_kind=record['source_kind'], container_pool_size=record['total_container_uses'],
                        exact_backend='joint_batch_indexed', exact_time=30, alns_time=10, alns_iter=5000,
                        seed=42, save_plots=False,
                        joint_batch_options=dict(schedule_mode='uniform_cycle', batch_count_candidates=counts,
                                                 min_phase_minutes=design['min_phase_minutes'],pool_min=1,pool_max=record['total_container_uses']))
            for field in ('distance_matrix','duration_matrix'):
                if field in cfg:
                    case[field] = reference(resolve_project_path(cfg[field]))
            write_json(folder/'m6_case.json', case)
            # Public loader validation, not a feasibility/optimality claim.
            problem = load_instance(*load_routing_case(folder/'m6_case.json', use_pool_config=False))
            for count in counts:
                problem.for_count(count)
            metadata = dict(dataset_version=VERSION, family_id=family, variant=variant,
                            source_case=record['case_path'], source_input_sha256=source_hashes,
                            source_kind=record['source_kind'], source_instance=provenance.get('source_instance'),
                            spatial_block=(f"zhou:{provenance['source_instance']}" if provenance.get('source_instance')
                                           else 'lade:hangzhou_0823_0829' if not mechanism else family),
                            source_pair_id=family_key, source_scenario_index=provenance.get('scenario_index'),
                            seed=design['seed'], appointment_lineage=lineage,
                            appointment_rule='Legacy mechanism preserved' if mechanism else
                            'Hash-stratified external appointment centers; independent of candidate K, routes and solver results',
                            preserved=['network','road_matrices','customer_satellite_assignment','total_boxes_per_customer','total_goods_per_customer'],
                            assumptions=design['business_assumption'],
                            repeated_visit_service='Each synthetic visit retains the source service duration',
                            selection='No feasibility or cost filtering; all failures retained')
            write_json(folder/'provenance.json', metadata)
            semantic = dict(orders=orders, fleet=fleet, policy=policy, costs=profile,
                            options=case['joint_batch_options'], network=digest(resolve_project_path(cfg['data'])),
                            matrices={k:digest(resolve_project_path(cfg[k])) for k in ('distance_matrix','duration_matrix') if k in cfg})
            cases.append(dict(name=name, family_id=family, variant=variant,
                              m6_case=reference(folder/'m6_case.json'), case_path=reference(folder/'m6_case.json'),
                              size=record['size'], source_kind=record['source_kind'], customer_count=record['customer_count'],
                              orders=len(orders), total_container_uses=problem.total, currency='synthetic_units',
                              spatial_block=metadata['spatial_block'], source_pair_id=family_key,
                              sensitivity_anchor=family in anchors.values(),
                              paired_reference=family+'__spread_medium' if not mechanism else None,
                              batch_count_candidates=counts, semantic_sha256=signature(semantic)))
    # Verify the source stayed unchanged throughout generation.
    for path, expected in external.items():
        if digest(resolve_project_path(path))!=expected:
            raise ValueError(f'Source changed during generation: {path}')
    artifacts = {p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob('*.json'))}
    manifest = dict(schema_version=1, dataset_version=VERSION, schedule_mode='uniform_cycle',
                    information_mode='static_all_known', source_manifest=reference(resolve_project_path(source_manifest)),
                    design_sha256=digest(design_path), generator_sha256=digest(Path(__file__)),
                    source_family_count=len(sources), case_count=len(cases),
                    variant_counts=dict(Counter(r['variant'] for r in cases)),
                    sensitivity_anchors=sorted(set(anchors.values()) & {r['family_id'] for r in sources}),
                    cases=cases, artifact_sha256=artifacts, external_artifact_sha256=external,
                    semantic_dataset_sha256=signature([(r['name'],r['semantic_sha256']) for r in cases]),
                    limitations=['Synthetic appointments and returns, not measured operational records',
                                 'Paired widths, scenarios, nested scales, dates and seeds are not independent spatial samples',
                                 'No solver filtering or feasibility guarantee; blocked K and failed searches remain visible',
                                 'K bounds and time precision limit any optimality claim'])
    write_json(root/'manifest.json', manifest)
    return root/'manifest.json'


def load_suite(path):
    path = resolve_project_path(path)
    manifest = json.loads(path.read_text())
    if manifest.get('dataset_version')!=VERSION or not manifest.get('cases'):
        raise ValueError('Expected a nonempty model6_uniform_suite_v2 manifest')
    verify_artifacts(manifest, path.parent)
    for file, expected in manifest['external_artifact_sha256'].items():
        if digest(resolve_project_path(file))!=expected:
            raise ValueError(f'SOURCE_HASH_MISMATCH: {file}')
    if len({r['name'] for r in manifest['cases']}) != len(manifest['cases']):
        raise ValueError('Duplicate case names')
    return manifest


def treatment_config(config, arm, reference_count=3):
    """Nested same-cost controls: assignment -> cutoff -> K; plus full pool.

    Source calendars for redesigned cases already equal reference_count uniform
    partitions. The legacy controls deliberately keep their historical calendar.
    """
    if arm not in ARMS:
        raise ValueError('Unknown treatment')
    result = deepcopy(config)
    options = result['joint_batch_options']
    if arm == 'full_pool':
        options['pool_min'] = options['pool_max']
    elif arm == 'fixed_k':
        if reference_count not in options['batch_count_candidates']:
            raise ValueError('Reference K must belong to the joint domain')
        options['batch_count_candidates'] = [reference_count]
    elif arm in ('fixed_cutoff','fixed_assignment'):
        # These source calendar controls are valid only for redesigned cases.
        policy = json.loads(resolve_project_path(result['operating_policy']).read_text())
        spans = [b['delivery_duration']+b['return_duration'] for b in policy['batches']]
        if (len(spans)!=reference_count or len(set(spans))!=1
                or not math.isclose(sum(spans), policy['cycle_end']-policy['cycle_start'])):
            raise ValueError('Controlled ablations require a uniform source calendar covering the cycle')
        result['joint_batch_options'] = {k:options[k] for k in ('pool_min','pool_max')}
        if arm == 'fixed_assignment':
            # Latest individually legal slot is an explicit fixed baseline;
            # no legal slot remains a proven rejection, never a removed order.
            cfg, fleet = load_routing_case(config['_source_case'], use_pool_config=False)
            cfg.update(result)
            problem = load_instance(cfg, fleet)
            result['joint_batch_options']['fixed_assignment'] = {
                o.id:problem.legal_batches[o.id][-1] if problem.legal_batches[o.id] else o.batch
                for o in problem.orders}
    result.pop('_source_case', None)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--source-manifest', default='data/manifest.json')
    parser.add_argument('--design', default=DEFAULT_DESIGN)
    parser.add_argument('--families', nargs='+')
    args = parser.parse_args(argv)
    print(build_suite(args.output, args.source_manifest, args.design, args.families))


if __name__ == '__main__':
    main()
