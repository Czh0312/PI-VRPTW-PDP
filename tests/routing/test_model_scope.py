"""Current runs must not silently select a retired or historical model."""
from types import SimpleNamespace

import pytest

from models.routing.service import solve_routing
from cli.route import DEFAULT_CASE, load_config, main


@pytest.mark.parametrize("model", [0, 9])
def test_unknown_models_are_rejected_before_loading_solver(model):
    with pytest.raises(ValueError, match="1–8"):
        solve_routing(model=model, method="alns", problem=SimpleNamespace())


def test_historical_policy_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="allow_baseline"):
        solve_routing(model=3, method="alns", problem=SimpleNamespace())
    with pytest.raises(ValueError, match="--baseline"):
        main(["--case", "config/baselines/zhou_small_demo.json"])


def test_default_is_current_single_batch():
    cfg, fleet = load_config(DEFAULT_CASE)
    assert cfg["model"] == 3
    assert fleet["operating_policy"]["policy_version"] == "multi_batch_cutoff_v1"
    assert len(fleet["operating_policy"]["batches"]) == 1


def test_current_cost_cli_prices_saved_cycle(tmp_path):
    from cli.cost import main as price
    result = price([
        "--route-result", "output/experiments/static_batch_suite_v1/data_audit_20260929/mechanism_forced_late_b1.json",
        "--economic-config", "config/cost/cases/multibatch_synthetic.json",
        "--output", str(tmp_path / "cost"),
    ])
    assert result["container_pool_size"] == 6
    assert result["container_uses"] == 6
    assert result["currency"] == "synthetic_units"
    assert not result["cost_is_optimized"]


@pytest.mark.parametrize("model", [1, 4])
def test_explicit_baseline_cli_remains_usable(tmp_path, monkeypatch, model):
    from cli import baseline_route
    monkeypatch.setattr(baseline_route, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(baseline_route, "plot_vrp_solution", lambda **kwargs: None)
    main(["--baseline", "--case", "config/baselines/zhou_small_demo.json",
          "--model", str(model), "--time", "0.05", "--iterations", "0"])
    logs = list(tmp_path.rglob("*_routes_detail.txt"))
    assert len(logs) == 1
    assert f"Model {model}" in logs[0].read_text()
