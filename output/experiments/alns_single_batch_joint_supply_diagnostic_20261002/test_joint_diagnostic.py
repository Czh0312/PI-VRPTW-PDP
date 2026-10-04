from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json, unittest
import joint_diagnostic as d

class JointDiagnosticChecks(unittest.TestCase):
    def setUp(self):
        self.state=dict(deliveries={'b1':[['S','T'],['U']]},pickup_order={'b1':['T','S','U']},
            pickup_vehicle={'b1':{'S':0}},e2=[
                dict(satellite='S',vehicle=0,tasks=[('a','d')]),
                dict(satellite='S',vehicle=0,tasks=[('a','p')]),
                dict(satellite='T',vehicle=0,tasks=[('b','d'),('b','p')])])
    def test_supply_relocation_preserves_sites_and_return_hints(self):
        original=deepcopy(self.state)
        changed=d.apply_e1(self.state,('delivery',('b1',0,0,1,1)))
        self.assertEqual(changed['deliveries']['b1'],[['T'],['U','S']])
        self.assertEqual(changed['pickup_vehicle'],original['pickup_vehicle'])
        self.assertEqual(self.state,original)
    def test_supply_same_vehicle_index_is_after_removal(self):
        changed=d.apply_e1(self.state,('delivery',('b1',0,0,0,1)))
        self.assertEqual(changed['deliveries']['b1'][0],['T','S'])
    def test_swap_keeps_delivery_coverage(self):
        changed=d.apply_e1(self.state,('delivery_swap',('b1',0,1,1,0)))
        self.assertEqual(changed['deliveries']['b1'],[['S','U'],['T']])
    def test_route_repair_preserves_task_ownership_and_empty_indices(self):
        original=deepcopy(self.state)
        changed=d.apply_e2(self.state,((0,[]),(1,[('a','d'),('a','p')])) )
        self.assertEqual(len(changed['e2']),3)
        self.assertEqual(d.inventory(changed),d.inventory(original))
        self.assertEqual(self.state,original)
    def test_cross_site_or_missing_task_cannot_pass_ownership_guard(self):
        for changes in [((0,[]),),((0,[]),(2,[('a','d'),('b','d'),('b','p')]))]:
            with self.assertRaises(AssertionError):d.apply_e2(self.state,changes)
    def test_vehicle_and_order_variants_keep_tasks_sites_and_input(self):
        original=deepcopy(self.state)
        problem=SimpleNamespace(fleet=SimpleNamespace(e2_per_sat_count=2))
        variants=d.vehicle_variants(problem,self.state,{0},16)
        self.assertTrue(any(op['kind']=='vehicle' for op,state in variants))
        self.assertTrue(any(op['kind']=='route_order' for op,state in variants))
        for op,state in variants:
            self.assertEqual(d.inventory(state),d.inventory(original))
            self.assertTrue(all(0<=r['vehicle']<2 for r in state['e2']))
            if op['kind']=='route_order':self.assertEqual(op['other'],1)
        self.assertEqual(self.state,original)
    def test_fresh_decoder_and_full_audit_reject_missing_or_invalid_schedule(self):
        item=json.loads((d.OUT/'incumbent_manifest.json').read_text())[0]
        config,fleet=d.load_routing_case(item['case']);p=d.load_instance({**config,'model':3},fleet)
        raw=json.loads((d.OUT/'incumbents'/item['name']).read_text())
        state=d.state_from_result(p,raw['result'])
        row,result,audit=d.measure(p,state)
        self.assertTrue(row['valid']);self.assertAlmostEqual(row['objective'],raw['result']['info']['objective'])
        bad=d.clone_state(state);bad['e2'][0]['vehicle']=p.fleet.e2_per_sat_count
        self.assertFalse(d.measure(p,bad)[0]['valid'])
        bad_result=deepcopy(result);bad_result['e2_trips'][0]['return_complete']=p.operating_policy.cycle_start
        self.assertFalse(d.audit_solution(p,bad_result)['valid'])

if __name__=='__main__':unittest.main()
