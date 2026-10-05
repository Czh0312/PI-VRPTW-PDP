"""Cost contract shared by M6 solvers and independently recomputed by audit."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from configuration.loader import load_json
from models.routing.common.cycle.policy import nonnegative, integer

RATE_FIELDS = (
    'distance_scale_to_km', 'container_pool_cost_per_period',
    'e1_distance_cost_per_km', 'e2_distance_cost_per_km',
    'e1_work_cost_per_minute', 'e2_work_cost_per_minute',
    'e1_vehicle_cost_per_cycle', 'e2_vehicle_cost_per_cycle',
    'e1_trip_cost', 'e2_trip_cost', 'cleaning_cost_per_return',
    'tracking_cost_per_use', 'container_price',
)

CALIBRATED_UNITS = {
    'distance_scale_to_km':'km/matrix_unit',
    'container_pool_cost_per_period':'CNY/container/evaluation_period',
    'e1_distance_cost_per_km':'CNY/km', 'e2_distance_cost_per_km':'CNY/km',
    'e1_work_cost_per_minute':'CNY/occupied_vehicle_minute',
    'e2_work_cost_per_minute':'CNY/occupied_vehicle_minute',
    'e1_vehicle_cost_per_cycle':'CNY/activated_vehicle/evaluation_period',
    'e2_vehicle_cost_per_cycle':'CNY/activated_vehicle/evaluation_period',
    'e1_trip_cost':'CNY/trip', 'e2_trip_cost':'CNY/trip',
    'cleaning_cost_per_return':'CNY/returned_container_use',
    'tracking_cost_per_use':'CNY/container_issue',
    'container_price':'CNY/container', 'container_lifetime_uses':'uses/container',
}


class JointCostProfile:
    def __init__(self, config):
        self.config = deepcopy(config)
        if config.get('cost_basis') != 'disjoint_distance_time_fixed':
            raise ValueError('M6 requires disjoint_distance_time_fixed cost basis')
        for key in ('currency', 'source_type', 'description', 'price_basis',
                    'evaluation_period', 'pool_cost_basis', 'work_time_basis'):
            if not isinstance(config.get(key), str) or not config[key].strip():
                raise ValueError(f'Missing cost metadata: {key}')
        if config['work_time_basis'] != 'occupied_route_minutes':
            raise ValueError('Only occupied_route_minutes work costs are implemented')
        if config['pool_cost_basis'] not in ('rental', 'owned_capital_charge', 'synthetic_pool_charge'):
            raise ValueError('Unsupported pool_cost_basis; purchase depreciation is not a pool holding cost')
        self.rates = {key: nonnegative(config.get(key), key) for key in RATE_FIELDS}
        if self.rates['distance_scale_to_km'] <= 0:
            raise ValueError('distance_scale_to_km must be positive and justified')
        self.lifetime = config.get('container_lifetime_uses')
        if self.lifetime is not None:
            self.lifetime = integer(self.lifetime, 'container_lifetime_uses', 1)
        if config['source_type'] == 'synthetic_mechanism':
            if config['currency'] != 'synthetic_units':
                raise ValueError('Synthetic mechanism prices must use synthetic_units')
        elif config['source_type'] not in ('public_proxy_scenario', 'calibrated'):
            raise ValueError('Declare synthetic_mechanism, public_proxy_scenario or calibrated source_type')
        elif config['pool_cost_basis'] == 'synthetic_pool_charge':
            raise ValueError('Synthetic pool charge cannot be a monetary calibrated coefficient')
        included = config.get('rental_includes', [])
        if not isinstance(included, list) or any(k not in ('wear', 'cleaning', 'tracking') for k in included):
            raise ValueError('Invalid rental_includes')
        if included and config['pool_cost_basis'] != 'rental':
            raise ValueError('rental_includes requires rental pool cost basis')
        if 'wear' in included and self.lifetime is not None:
            raise ValueError('Rental-included wear cannot be depreciated again')
        for item, key in (('cleaning', 'cleaning_cost_per_return'), ('tracking', 'tracking_cost_per_use')):
            if item in included and self.rates[key]:
                raise ValueError(f'Rental-included {item} cannot be charged twice')
        self.evidence = None
        if config.get('evidence_file'):
            self.evidence = load_json(config['evidence_file'])
        if config['source_type'] == 'calibrated':
            if (config['currency']!='CNY' or not self.evidence
                    or self.evidence.get('model')!=6
                    or self.evidence.get('ready_for_calibrated_CNY_optimization') is not True):
                raise ValueError('Calibrated profile requires a ready evidence register')
            entries = self.evidence.get('parameter_register', {})
            checked = RATE_FIELDS + (('container_lifetime_uses',) if self.lifetime is not None else ())
            for key in checked:
                record = entries.get(key, {})
                value = self.lifetime if key == 'container_lifetime_uses' else self.rates[key]
                if (record.get('calibrated_value') != value or record.get('unit') != CALIBRATED_UNITS[key]
                        or record.get('applicability') not in ('matched_empirical', 'matched_public_reference', 'explicit_exclusion')
                        or (record['applicability'] != 'explicit_exclusion' and not record.get('sources'))):
                    raise ValueError(f'Uncalibrated or mismatched evidence coefficient: {key}')
                if record['applicability'] == 'explicit_exclusion' and (value != 0 or not record.get('reason')):
                    raise ValueError(f'Explicit exclusion must justify zero cost: {key}')
                if any(s not in self.evidence.get('sources', {}) for s in record.get('sources', [])):
                    raise ValueError(f'Unknown evidence source for {key}')
                for sid in record.get('sources', []):
                    source = self.evidence['sources'][sid]
                    if (any(not isinstance(source.get(k),str) or not source[k].strip()
                            for k in ('title','publisher','url','checked_on','verification'))
                            or not source['url'].startswith(('https://','http://'))):
                        raise ValueError(f'Incomplete source metadata: {sid}')
        self.hash = _hash(self.config)
        self.evidence_hash = _hash(self.evidence) if self.evidence is not None else None

    def constant_cost(self, total):
        values = dict(cleaning=total*self.rates['cleaning_cost_per_return'],
                      tracking=total*self.rates['tracking_cost_per_use'])
        values['container_wear'] = total*self.rates['container_price']/self.lifetime if self.lifetime else 0.0
        return values

    def evaluate(self, physical_audit, pool):
        """Physical metrics come from schedule replay, never solver expressions."""
        parts = dict(container_pool=pool*self.rates['container_pool_cost_per_period'])
        for layer in ('e1', 'e2'):
            parts[layer+'_distance'] = (physical_audit['distances'][layer]
                *self.rates['distance_scale_to_km']*self.rates[layer+'_distance_cost_per_km'])
            parts[layer+'_work'] = physical_audit['time_metrics']['working_minutes'][layer]*self.rates[layer+'_work_cost_per_minute']
            parts[layer+'_vehicles'] = physical_audit[layer+'_vehicle_count']*self.rates[layer+'_vehicle_cost_per_cycle']
            parts[layer+'_trips'] = physical_audit[layer+'_trip_count']*self.rates[layer+'_trip_cost']
        balance = physical_audit['container_balance']
        parts['cleaning'] = balance['returned_to_depot']*self.rates['cleaning_cost_per_return']
        parts['tracking'] = balance['issued']*self.rates['tracking_cost_per_use']
        parts['container_wear'] = balance['issued']*self.rates['container_price']/self.lifetime if self.lifetime else 0.0
        return dict(total=sum(parts.values()), components=parts, currency=self.config['currency'],
                    initial_pool_investment=(pool*self.rates['container_price']
                        if self.config['pool_cost_basis']!='rental' else 0.0),
                    cost_basis=self.config['cost_basis'], source_type=self.config['source_type'],
                    price_basis=self.config['price_basis'], evaluation_period=self.config['evaluation_period'],
                    work_time_basis=self.config['work_time_basis'], economic_profile_hash=self.hash,
                    evidence_hash=self.evidence_hash,
                    limitation='Occupied route time excludes inter-trip idle payroll; investment is separate.')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()
