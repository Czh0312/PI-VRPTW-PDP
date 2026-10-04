import json,unittest
from copy import deepcopy
from queue_capacity_diagnostic import normalized_key
class KeyChecks(unittest.TestCase):
    def test_json_roundtrip_and_empty_route_keep_equivalent_key(self):
        state=dict(deliveries={'b1':[['S']]},pickup_order={'b1':['S']},pickup_vehicle={'b1':{}},
            e2=[dict(satellite='S',vehicle=0,tasks=[('a','d'),('a','p')]),dict(satellite='S',vehicle=1,tasks=[])])
        original=deepcopy(state);loaded=json.loads(json.dumps(state))
        self.assertEqual(normalized_key(state),normalized_key(loaded))
        self.assertEqual(hash(normalized_key(state)),hash(normalized_key(loaded)))
        self.assertEqual(state,original)
    def test_vehicle_order_remains_part_of_key(self):
        state=dict(deliveries={'b1':[['S']]},pickup_order={'b1':['S']},pickup_vehicle={'b1':{}},
            e2=[dict(satellite='S',vehicle=0,tasks=[('a','d')]),dict(satellite='S',vehicle=0,tasks=[('a','p')])])
        changed=deepcopy(state);changed['e2'].reverse()
        self.assertNotEqual(normalized_key(state),normalized_key(changed))
if __name__=='__main__':unittest.main()
