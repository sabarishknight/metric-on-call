"""Phase 3 tests: every deterministic tool is checked against the planted ground truth."""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest

from tools import decompose, dq, hypotheses, impact, monitor, registry, skeptic


# ---- monitor ----------------------------------------------------------------------------
def test_monitor_flags_all_13_incidents(wh, truth):
    missed = []
    for t in truth["incidents"]:
        hits = monitor.incidents_between(wh, t["date_from"], t["date_to"])
        if not hits:
            missed.append(t["id"])
    assert not missed, f"monitor missed incidents {missed}"


def test_monitor_false_alarms_in_quiet_weeks_at_most_1_in_10(wh, truth):
    false_alarms = 0
    for w in truth["quiet_weeks"]:
        a = pd.Timestamp(w)
        if monitor.incidents_between(wh, a, a + pd.Timedelta(days=6)):
            false_alarms += 1
    assert false_alarms <= 1


def test_monitor_headline_for_localised_incidents(wh):
    inc = monitor.incidents_between(wh, "2025-07-23", "2025-07-24")[0]
    assert inc["headline"]["slice"] == "WS"  # Western Sydney
    inc = monitor.incidents_between(wh, "2026-05-26", "2026-05-29")[0]
    assert inc["headline"]["slice"] == "oat_flat_white"


# ---- data quality gate ------------------------------------------------------------------
def test_dq_gate_stops_exactly_the_data_incidents(wh, truth):
    for t in truth["incidents"]:
        res = dq.run_checks(wh, t["date_from"], t["date_to"])
        assert (not res["clean"]) == t["expected_stop_at_data_gate"], f"incident {t['id']}"


def test_dq_clean_on_quiet_weeks(wh, truth):
    for w in truth["quiet_weeks"]:
        assert dq.run_checks(wh, w, pd.Timestamp(w) + pd.Timedelta(days=6))["clean"]


def test_dq_names_the_right_cause(wh):
    assert dq.run_checks(wh, "2025-08-26", "2025-08-26")["likely_causes"] == ["pipeline_duplicate_load"]
    assert dq.run_checks(wh, "2026-10-17", "2026-10-18")["likely_causes"] == ["pipeline_missing_data"]


# ---- decomposition ----------------------------------------------------------------------
def test_locate_gap_pos_outage(wh):
    g = decompose.locate_gap(wh, "net_sales", "2025-07-23", "2025-07-24")
    f = g["located_filters"]
    assert f["region_id"] == "WS" and f["payment_type"] == "card" and f["channel"] == "in_store"


def test_locate_gap_delivery_and_closure(wh):
    assert decompose.locate_gap(wh, "net_sales", "2026-07-01", "2026-07-01")["located_filters"]["channel"] == "delivery"
    assert decompose.locate_gap(wh, "net_sales", "2026-08-03", "2026-08-07")["located_filters"]["store_id"] == "IW03"


def test_locate_gap_rain_is_broad_based(wh):
    g = decompose.locate_gap(wh, "net_sales", "2025-11-24", "2025-11-28")
    assert g["broad_based"] and not g["steps"]


def test_locate_gap_stockout_finds_the_six_stores(wh):
    g = decompose.locate_gap(wh, "units", "2026-05-26", "2026-05-29", filters={"product_id": "oat_flat_white"})
    assert set(g["located_filters"]["store_id"]) == {"NS01", "NS02", "NS03", "NS04", "IW01", "IW02"}


def test_metric_tree_points_at_conversion_for_card_outage(wh):
    tw = decompose.metric_tree_walk(wh, "net_sales", "2025-07-23", "2025-07-24", filters={"store_id": ["WS01", "WS02", "WS03"]})
    assert tw["dominant_driver_path"][:2] == ["net_sales", "transactions"]


def test_simpsons_paradox_detected(wh):
    r = decompose.mix_rate_decomposition(wh, "avg_basket", "region_id", "2026-08-17", "2026-09-06",
                                         baseline_from="2026-07-27", baseline_to="2026-08-16")
    assert r["simpsons_paradox"] and r["overall_pct"] > 0 and r["segments_with_lower_rate"] == r["segments_total"]
    assert r["rate_effect_total"] < 0 < r["mix_effect_total"]


# ---- hypotheses -------------------------------------------------------------------------
def test_rank_hypotheses_top1_correct_for_every_incident(wh, truth):
    wrong = []
    for t in truth["incidents"]:
        ranked = hypotheses.rank_hypotheses(wh, t["date_from"], t["date_to"])["hypotheses"]
        if ranked[0]["category"] not in t["causes"]:
            wrong.append((t["id"], ranked[0]["category"]))
    assert not wrong, wrong


def test_two_causes_both_found_for_incident_13(wh):
    r = hypotheses.rank_hypotheses(wh, "2026-09-15", "2026-09-17")
    top2 = {h["category"] for h in r["hypotheses"][:2]}
    assert top2 == {"weather", "pos_outage"} and r["multi_cause"]


def test_unknown_cause_is_stated_honestly(wh):
    r = hypotheses.rank_hypotheses(wh, "2026-01-19", "2026-03-22")["hypotheses"][0]
    assert r["category"] == "external_unknown" and "not in our data" in r["summary"].lower()


def test_easter_is_a_calendar_false_alarm(wh):
    r = hypotheses.rank_hypotheses(wh, "2026-03-30", "2026-04-05")["hypotheses"][0]
    assert r["category"] == "calendar_shift"
    assert r["evidence"]["naive_yoy_net_sales_pct"] < -0.05  # the misleading year-on-year


def test_data_incidents_do_not_get_business_stories(wh):
    r = hypotheses.rank_hypotheses(wh, "2025-08-26", "2025-08-26")["hypotheses"]
    assert [h["category"] for h in r] == ["pipeline_duplicate_load"]


def test_distractor_promos_are_not_blamed(wh):
    # Loyalty/lunch/cookie promos exist in the log but have no effect in the data
    r = hypotheses.rank_hypotheses(wh, "2026-02-02", "2026-02-15")["hypotheses"]
    assert all(h["category"] != "promo_cannibalisation" for h in r)


def test_diff_in_diff_recovers_promo_effect(wh):
    stores = ["CB01", "CB02", "ES01", "ES02", "ES03", "NS01", "NS02", "NS03"]
    # units rise ~+50-60% (mix x1.6); net sales rise less because the 2-for-1 price is ~28% lower
    units = hypotheses.diff_in_diff(wh, "units", stores, "2025-09-15", "2025-10-12", "2025-10-13", "2025-10-26",
                                    filters={"product_id": "muffin"})
    sales = hypotheses.diff_in_diff(wh, "net_sales", stores, "2025-09-15", "2025-10-12", "2025-10-13", "2025-10-26",
                                    filters={"product_id": "muffin"})
    assert units["supported"] and units["effect_pct"] > 0.4
    assert sales["supported"] and 0.0 < sales["effect_pct"] < units["effect_pct"]


# ---- impact -----------------------------------------------------------------------------
@pytest.mark.parametrize("incident_id", [1, 6, 7, 10, 11])
def test_impact_within_15_percent_of_planted_truth(wh, by_id, incident_id):
    t = by_id[incident_id]
    est = impact.estimate_impact(wh, t["date_from"], t["date_to"])["total_impact_dollars"]
    assert abs(est - t["planted_impact_dollars"]) / abs(t["planted_impact_dollars"]) <= 0.15


@pytest.mark.parametrize("incident_id", [8, 12, 13])
def test_impact_within_25_percent_for_chain_wide_effects(wh, by_id, incident_id):
    t = by_id[incident_id]
    est = impact.estimate_impact(wh, t["date_from"], t["date_to"])["total_impact_dollars"]
    assert abs(est - t["planted_impact_dollars"]) / abs(t["planted_impact_dollars"]) <= 0.25


def test_impact_sign_is_right_for_promo(wh, by_id):
    t = by_id[4]
    est = impact.estimate_impact(wh, t["date_from"], t["date_to"])["total_impact_dollars"]
    assert est < 0  # accuracy for small effects is limited; see docs/decisions.md


def test_impact_zero_for_data_incidents(wh):
    r = impact.estimate_impact(wh, "2025-08-26", "2025-08-26")
    assert r["total_impact_dollars"] == 0.0 and r["reported_distortion_dollars"] > 0


def test_multi_cause_impact_is_split(wh):
    r = impact.estimate_impact(wh, "2026-09-15", "2026-09-17")
    cats = {c["category"] for c in r["by_cause"]}
    assert cats == {"weather", "pos_outage"}
    assert abs(sum(c["impact_dollars"] for c in r["by_cause"]) - r["total_impact_dollars"]) < 1.0


def test_projection_only_for_persistent_effects(wh):
    assert impact.estimate_impact(wh, "2026-01-19", "2026-03-22")["projection"]["effect_persists"]
    assert not impact.estimate_impact(wh, "2025-07-23", "2025-07-24")["projection"]["effect_persists"]


# ---- skeptic ----------------------------------------------------------------------------
def test_correct_answers_survive_the_skeptic(wh, truth):
    for t in truth["incidents"]:
        ranked = hypotheses.rank_hypotheses(wh, t["date_from"], t["date_to"])["hypotheses"]
        imp = impact.estimate_impact(wh, t["date_from"], t["date_to"], ranked)
        res = skeptic.challenge(wh, {"window": [t["date_from"], t["date_to"]], "top_cause": ranked[0]["category"],
                                     "metric": "net_sales", "impact": imp})
        assert res["survives"], (t["id"], res["objections"])


def test_skeptic_attacks_a_wrong_answer_for_simpsons_paradox(wh):
    res = skeptic.challenge(wh, {"window": ["2026-08-17", "2026-09-06"], "top_cause": "promo_cannibalisation",
                                 "metric": "net_sales", "impact": {}})
    assert not res["survives"] and any(o["check"] == "simpsons_paradox" for o in res["objections"])


def test_reconcile_dollars():
    assert skeptic.reconcile_dollars([-60, -40], -100)["reconciles"]
    assert not skeptic.reconcile_dollars([-60, -10], -100)["reconciles"]


# ---- registry and MCP -------------------------------------------------------------------
def test_registry_exposes_governed_tools_only(wh):
    assert len(registry.TOOLS) == 16
    assert not any("sql" in name for name in registry.TOOLS)


def test_registry_validates_arguments_and_never_raises(wh):
    assert registry.call_tool("locate_gap", {"metric": "net_sales"}, wh)["error"] == "invalid arguments"
    assert "error" in registry.call_tool("locate_gap", {"metric": "bogus", "date_from": "2025-07-23", "date_to": "2025-07-24"}, wh)
    assert "error" in registry.call_tool("nope", {}, wh)


def test_registry_caps_rows(wh):
    r = registry.call_tool("query_metric", {"metrics": ["net_sales"], "date_from": "2025-04-01", "date_to": "2026-10-18",
                                            "by": ["store_id"], "daily": True}, wh)
    assert r["truncated"] and len(r["rows"]) <= 50


def test_mcp_server_lists_and_calls_tools(wh):
    pytest.importorskip("mcp")
    from mcp.client import Client

    from tools.mcp_server import build_server

    registry.set_warehouse(wh)

    async def go():
        async with Client(build_server()) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            res = await c.call_tool("rank_hypotheses", {"date_from": "2025-07-23", "date_to": "2025-07-24"})
            return names, json.loads(res.content[0].text)

    names, payload = asyncio.run(go())
    assert names == set(registry.TOOLS)
    assert payload["hypotheses"][0]["category"] == "pos_outage"
