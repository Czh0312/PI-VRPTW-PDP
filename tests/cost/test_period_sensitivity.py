import pytest

from experiments.analysis.single_batch_cost import incidental_cost_sensitivity


def test_incidental_costs_change_period_break_even():
    pair = dict(period_saving=50, period_single_use_purchase_price_threshold=2.5)
    study = incidental_cost_sensitivity(pair, packages=100, loss_rates=[0, .01, .025],
                                        replacement_cost_per_box=20, other_cost_per_use=.1)
    assert study['period_max_loss_rate'] == pytest.approx(.02)
    rows = study['scenarios']
    assert [r['period_incidental_cost'] for r in rows] == pytest.approx([10, 30, 60])
    assert [r['period_saving'] for r in rows] == pytest.approx([40, 20, -10])
    assert [r['period_paper_price_threshold'] for r in rows] == pytest.approx([2.6, 2.8, 3.1])


@pytest.mark.parametrize(('saving', 'price', 'packages', 'limit'), [
    (9, 20, 100, None), (10, 20, 100, 0), (10, 0, 100, 1),
    (50, .2, 100, 1), (0, 20, 0, None),
])
def test_incidental_loss_limit_respects_cost_and_rate_boundaries(saving, price, packages, limit):
    study = incidental_cost_sensitivity(dict(period_saving=saving), packages=packages,
                                        loss_rates=[0], replacement_cost_per_box=price, other_cost_per_use=.1)
    assert study['period_max_loss_rate'] == limit
