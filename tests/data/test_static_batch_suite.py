"""Acceptance tests for fixed-batch data, provenance and feasibility witnesses."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.configuration.loader import load_routing_case, resolve_project_path
from tools.data.build_static_batch_suite import build_suite, digest, read_csv, stable_key
from tools.data.static_batch_dataset import check_pairing, load_manifest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = resolve_project_path(ROOT/'data/manifest.json')


class StaticBatchDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.cases = load_manifest(MANIFEST)

    def test_two_batch_groups_three_sizes_and_fixed_assignment_pairs(self):
        self.assertEqual(check_pairing(self.cases), 93)
        self.assertEqual(len(self.cases), 279)
        self.assertEqual({(r['group'], r['size']) for r in self.cases},
                         {(g, s) for g in ('single_batch', 'multi_batch')
                          for s in ('small', 'medium', 'large')})
        for record in self.cases:
            expected_size = {2: 'small', 7: 'small', 10: 'small', 12: 'small',
                             20: 'medium', 30: 'medium', 50: 'large', 100: 'large'}
            self.assertEqual(record['size'], expected_size[record['customer_count']])
            cfg, _ = load_routing_case(record['case_path'], use_pool_config=False)
            mode = 'single_batch' if record['batch_count'] == 1 else 'multi_batch'
            self.assertEqual(record['group'], mode)
            self.assertEqual(record['comparison_models'], [1, 2, 3, 4] if mode == 'single_batch' else [5])
            self.assertEqual(cfg['model'], 3 if mode == 'single_batch' else 5)
            directory = resolve_project_path(ROOT/'data'/mode/record['size']/record['family_id'])
            if record['batch_count'] > 1:
                directory = directory/f"b{record['batch_count']}"
            self.assertEqual(resolve_project_path(record['case_path']), directory/'case.json')
            self.assertEqual(resolve_project_path(cfg['orders']), directory/'orders.json')
            self.assertEqual(resolve_project_path(cfg['operating_policy']), directory/'policy.json')
            orders = json.loads(resolve_project_path(cfg['orders']).read_text())
            provenance = json.loads(resolve_project_path(cfg['dataset_metadata']).read_text())
            cohorts = provenance['cohort_by_order']
            for order in orders:
                cohort = cohorts[order['order_id']]
                expected = {1: 1, 2: 1 if cohort < 2 else 2, 3: cohort+1}[record['batch_count']]
                self.assertEqual(order['delivery_batch_id'], f'b{expected}')
                self.assertNotIn('pickup_batch_id', order)
            self.assertEqual(record['default_pool'], record['total_container_uses'])

    def test_zhou_ten_distinct_scenarios_per_customer_count_with_reproducible_subsets(self):
        zhou = [r for r in self.cases if r['source_kind'] == 'zhou_derived_scenario']
        self.assertEqual(len(zhou), 210)
        for size, n in [('small', 7), ('small', 10), ('small', 12), ('medium', 20),
                        ('medium', 30), ('large', 50), ('large', 100)]:
            geometries = set()
            effective_inputs = set()
            source_blocks = {}
            for batch in (1, 2, 3):
                records = [r for r in zhou if r['size'] == size and r['customer_count'] == n
                           and r['batch_count'] == batch]
                self.assertEqual(len(records), 10)
                for record in records:
                    cfg, _ = load_routing_case(record['case_path'], use_pool_config=False)
                    nodes = read_csv(resolve_project_path(cfg['data']))
                    customers = {r['node_id'] for r in nodes if r['node_type'] == 'c'}
                    orders = json.loads(resolve_project_path(cfg['orders']).read_text())
                    self.assertEqual(len(customers), n)
                    self.assertEqual(len(orders), n)
                    self.assertEqual({o['customer_id'] for o in orders}, customers)
                    self.assertEqual(record['customer_count'], n)
                    if batch == 1:
                        geometries.add(tuple(sorted((r['node_id'], r['x'], r['y'], r['assigned_sat'])
                                                    for r in nodes)))
                        effective_inputs.add(json.dumps(dict(network=nodes, orders=sorted(orders, key=lambda o: o['order_id'])),
                                                        sort_keys=True))
                        source_blocks.setdefault(record['base_family_id'], set()).add(record['scenario_index'])
                        provenance = json.loads(resolve_project_path(cfg['dataset_metadata']).read_text())
                        self.assertEqual(provenance['base_family_id'], record['base_family_id'])
                        self.assertEqual(provenance['scenario_index'], record['scenario_index'])
                    if n in (20, 30, 50):
                        large_name = 'zhou_'+record['source_instance']
                        if record['scenario_index'] == 2:
                            large_name += '_scenario02'
                        parent = next(r for r in zhou if r['family_id'] == large_name and r['batch_count'] == batch)
                        large_cfg, _ = load_routing_case(parent['case_path'], use_pool_config=False)
                        large_nodes = read_csv(resolve_project_path(large_cfg['data']))
                        parent_customers = [r['node_id'] for r in large_nodes if r['node_type'] == 'c']
                        expected = sorted(parent_customers, key=lambda c: stable_key(record['source_instance']+':nested_customers', c))[:n]
                        self.assertEqual(customers, set(expected))
                        self.assertEqual(sum(r['node_type']=='s' for r in nodes), {20:3,30:3,50:4}[n])
            self.assertEqual(len(effective_inputs), 10, 'A new filename alone is not a new data scenario')
            self.assertEqual(len(source_blocks), 5)
            self.assertTrue(all(scenarios == {1, 2} for scenarios in source_blocks.values()))
            self.assertEqual(len(geometries), 5)

    def test_reused_source_fields_and_source_hashes(self):
        for family in self.manifest['families']:
            provenance = json.loads(resolve_project_path(family['provenance']).read_text())
            for path, expected in provenance['input_source_sha256'].items():
                self.assertEqual(digest(resolve_project_path(path)), expected)
            if family['source_kind'] == 'synthetic_mechanism':
                continue
            source = next(p for p in provenance['input_source_sha256']
                          if '/instances/' in p or '/standard_same_day/' in p)
            original = {r['node_id']: r for r in read_csv(resolve_project_path(source))}
            folder = resolve_project_path(family['provenance']).parent
            for node in read_csv(folder/'network.csv'):
                old = original[node['node_id']]
                for key in ('x', 'y'):
                    self.assertEqual(float(node[key]), float(old[key]))
                if node['node_type']!='s' or family['source_kind']=='lade_derived_scenario':
                    self.assertEqual(float(node['service_time']), float(old['service_time']))
                if family['source_kind']=='lade_derived_scenario':
                    self.assertEqual(node['assigned_sat'], old['assigned_sat'])
            cfg, _ = load_routing_case(family['cases']['b3'], use_pool_config=False)
            for order in json.loads(resolve_project_path(cfg['orders']).read_text()):
                old = original[order['customer_id']]
                self.assertEqual(order['goods_weight'], float(old['delivery_demand']))
                if family['source_kind'] == 'lade_derived_scenario':
                    self.assertEqual(order['container_count'], int(old['delivery_container_count']))
                    for key in ('delivery_ready', 'delivery_due'):
                        self.assertEqual(float(provenance['original_windows'][order['order_id']][key]), float(old[key]))

    def test_v2_resource_pairing_and_window_meaning(self):
        from models.common.cycle import load_instance
        from models.common.cycle.audit import audit_solution
        from models.routing.model_5.alns.evaluator import evaluate
        from tools.audit.audit_static_batch_suite import constructive_state
        pairs, nonredundant, count, first_wave_early = {}, 0, 0, 0
        for family in self.manifest['families']:
            if family['source_kind']=='synthetic_mechanism': continue
            cfg, fleet = load_routing_case(family['cases']['b3'], use_pool_config=False)
            problem = load_instance(cfg, fleet)
            provenance = json.loads(resolve_project_path(family['provenance']).read_text())
            p = problem.operating_policy
            self.assertEqual((p.minutes(p.cycle_start),p.minutes(p.cycle_end)), (480,1260))
            self.assertTrue(all(any(o.satellite==s for o in problem.orders) for s in problem.satellites))
            for o in problem.orders:
                count += 1
                nonredundant += o.pickup_window[0] < o.delivery_window[1]+o.service_time+o.min_use_time
                self.assertLessEqual(o.delivery_window[1]+o.service_time+o.min_use_time,o.pickup_window[1])
                first_wave_early += (family['source_kind']=='lade_derived_scenario' and o.batch=='b1'
                                     and o.pickup_window[0]+o.service_time+problem.travel(o.customer,o.satellite)
                                         +p.e2_return_unload_time<=p.batches[0].cutoff)
            _, solution, errors = evaluate(problem, constructive_state(problem, provenance))
            self.assertFalse(errors)
            self.assertTrue(audit_solution(problem,solution)['valid'])
            if family['source_kind']=='zhou_derived_scenario':
                signature = (resolve_project_path(cfg['data']).read_bytes(),
                             resolve_project_path(cfg['routing_config']).read_bytes(),
                             tuple(sorted((o.id,o.satellite,o.batch,o.count,o.goods_weight) for o in problem.orders)))
                base=family['base_family_id']
                if base in pairs: self.assertEqual(signature,pairs[base])
                else: pairs[base]=signature
        self.assertGreater(nonredundant/count,.85)
        self.assertGreater(first_wave_early,0)

    def test_real_road_submatrices_and_nested_locations(self):
        source = ROOT/'data/real_case/processed/lade_hz_0823_0829_30aoi'
        matrix_sources = {name: {r['node_id']: r for r in read_csv(source/f'{name}.csv')}
                          for name in ('distance_km', 'duration_min')}
        customer_sets = {}
        for family in self.manifest['families']:
            if family['source_kind'] != 'lade_derived_scenario':
                continue
            folder = resolve_project_path(family['provenance']).parent
            nodes = read_csv(folder/'network.csv')
            ids = {r['node_id'] for r in nodes}
            day = family['family_id'].split('_')[1]
            customers = {r['node_id'] for r in nodes if r['node_type'] == 'c'}
            customer_sets[day, len(customers)] = customers
            for name, matrix in matrix_sources.items():
                for row in read_csv(folder/f'{name}.csv'):
                    self.assertEqual(set(row)-{'node_id'}, ids)
                    for other in ids:
                        self.assertEqual(float(row[other]), float(matrix[row['node_id']][other]))
        for day in {k[0] for k in customer_sets}:
            self.assertLess(customer_sets[day, 10], customer_sets[day, 20])
            self.assertLess(customer_sets[day, 20], customer_sets[day, 30])

    def test_pairing_rejects_changed_customer_window(self):
        pair = [copy.deepcopy(r) for r in self.cases if r['family_id'] == 'mechanism_forced_late'][:2]
        cfg = json.loads(resolve_project_path(pair[1]['case_path']).read_text())
        orders = json.loads(resolve_project_path(cfg['orders']).read_text())
        orders[0]['pickup_due'] += 1
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'orders.json').write_text(json.dumps(orders))
            cfg['orders'] = str(tmp/'orders.json')
            (tmp/'case.json').write_text(json.dumps(cfg))
            pair[1]['case_path'] = str(tmp/'case.json')
            with self.assertRaisesRegex(ValueError, 'PAIRING_MISMATCH'):
                check_pairing(pair)

    def test_mechanism_witnesses_distinguish_late_returns(self):
        from models.common.cycle.audit import audit_solution
        from models.routing.model_5.alns.evaluator import evaluate
        from tools.audit.audit_static_batch_suite import constructive_state, load_declared_data

        # Same six uses: the first forced-late return needs an extra box at
        # the second dispatch. These are fixed-schedule requirements only.
        expected = {'mechanism_forced_late_b1': 6, 'mechanism_forced_late_b3': 3,
                    'mechanism_cutoff_choice_b3': 2}
        for record in self.cases:
            if record['case_id'] not in expected:
                continue
            cfg, fleet = load_routing_case(record['case_path'], use_pool_config=False)
            problem = load_declared_data(cfg, fleet)
            _, result, issues = evaluate(problem, constructive_state(problem))
            audit = audit_solution(problem, result)
            self.assertEqual(issues, [])
            self.assertTrue(audit['valid'], audit['errors'])
            self.assertTrue(audit['periodic_replay']['valid'])
            self.assertEqual(audit['minimum_pool_for_schedule'], expected[record['case_id']])

    def test_generator_is_reproducible_and_preserves_changed_files(self):
        # A maintained generator has a new source hash. Preserve the old
        # dataset, and compare regenerated artifacts after rebasing paths.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            regenerated = build_suite(target)
            normalized_hashes = {}
            for relative in self.manifest['artifact_sha256']:
                payload = (target/relative).read_bytes().replace(str(target).encode(), b'data')
                self.assertEqual(payload, (MANIFEST.parent/relative).read_bytes(), relative)
                normalized_hashes[relative] = hashlib.sha256(payload).hexdigest()
            normalized = json.loads(json.dumps(regenerated).replace(str(target), 'data'))
            normalized['artifact_sha256'] = normalized_hashes
            expected = dict(self.manifest, generator_sha256=digest(ROOT/'tools/data/build_static_batch_suite.py'))
            self.assertEqual(normalized, expected)
            self.assertEqual(build_suite(target), regenerated)
            case = target/'single_batch/small/mechanism_forced_late/orders.json'
            case.write_text('user change\n')
            with self.assertRaisesRegex(ValueError, 'Refusing to overwrite changed dataset'):
                build_suite(target)
            self.assertEqual(case.read_text(), 'user change\n')

    def test_selection_returns_only_requested_real_multibatch_sizes(self):
        _, records = load_manifest(MANIFEST, groups=['multi_batch'], source_kinds=['lade_derived_scenario'],
                                   sizes=['medium'], batch_counts=[2, 3])
        self.assertEqual(len(records), 28)
        self.assertEqual({r['customer_count'] for r in records}, {20, 30})
        self.assertTrue(all(r['batch_count'] in (2, 3) for r in records))


if __name__ == '__main__':
    unittest.main()
