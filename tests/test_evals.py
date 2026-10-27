from evals import holdout
from evals.scorers import score_incident, summarise


def test_scorer_catches_wrong_and_right():
    t = {"id": 1, "category": "pos_outage", "causes": ["pos_outage"], "planted_impact_dollars": -100.0}
    ok = score_incident(t, {"status": "investigated", "cause_category": "pos_outage", "causes": ["pos_outage"], "dollar_impact": -90.0, "ranked_hypotheses": []})
    bad = score_incident(t, {"status": "investigated", "cause_category": "weather", "causes": ["weather"], "dollar_impact": -300.0, "ranked_hypotheses": []})
    assert ok["top1"] and abs(ok["dollar_err"] - 0.1) < 1e-9 and not bad["top1"]
    assert summarise([ok, bad])["top1"] == 0.5


def test_gate_scoring():
    t = {"id": 2, "category": "pipeline_duplicate_load", "causes": ["pipeline_duplicate_load"], "expected_stop_at_data_gate": True}
    assert score_incident(t, {"status": "stopped_data_quality", "cause_category": "pipeline_duplicate_load", "causes": []})["data_gate_correct"]
    assert not score_incident(t, {"status": "investigated", "cause_category": "promo_uplift", "causes": []})["data_gate_correct"]


def test_holdout_lock_detects_prompt_change(tmp_path, monkeypatch):
    monkeypatch.setattr(holdout, "LOCK", tmp_path / "lock")
    assert not holdout.held_out_valid()
    holdout.lock()
    assert holdout.held_out_valid()
    monkeypatch.setattr(holdout, "prompt_hash", lambda: "changed")
    assert not holdout.held_out_valid()


def test_held_out_ids(truth):
    assert holdout.held_out_ids() == {3, 5, 9, 13}
