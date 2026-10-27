"""Hypothesis Tester tools: "form competing explanations and test them".

Building blocks (also exposed individually over MCP):
  diff_in_diff, pre_post, cannibalisation_check
and one convenience tool that runs every candidate explanation against the data and returns them
ranked with their evidence:
  rank_hypotheses

All thresholds are explicit constants so the behaviour is reproducible and testable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tools import context as ctx
from tools import dq
from tools.data import Warehouse, to_ts
from tools.decompose import mix_rate_decomposition
from tools.guards import jsonable

# thresholds -----------------------------------------------------------------------------
OUTAGE_DROP = -0.60          # card/delivery/closure style collapse vs same-weekday baseline
STOCKOUT_DROP = -0.60
BREADTH_MIN = 0.75           # share of stores moving the same way for a chain-wide cause
BROAD_VISIT_DROP = -0.05
PROMO_LIFT_MIN = 0.30
CANNIBAL_MAX = -0.10
PRICE_UP_MIN = 0.03
SHARE_DECLINE = -0.07
Z_SUPPORT = 3.0


# --------------------------------------------------------------------------------------
# Generic tests
# --------------------------------------------------------------------------------------
def _daily_mean(wh: Warehouse, metric: str, filters: dict | None, a, b) -> float:
    n_days = (to_ts(b) - to_ts(a)).days + 1
    v = wh.aggregate([metric], (), a, b, filters).iloc[0][metric]
    return float(v) / n_days if metric in ("net_sales", "transactions", "units", "visits") else float(v)


def _did_effect(wh, metric, tf, cf, pre, post) -> float | None:
    t0, t1 = _daily_mean(wh, metric, tf, *pre), _daily_mean(wh, metric, tf, *post)
    c0, c1 = _daily_mean(wh, metric, cf, *pre), _daily_mean(wh, metric, cf, *post)
    if min(t0, c0, c1) <= 0:
        return None
    return (t1 / t0) / (c1 / c0) - 1


def _placebo_effects(fn, pre, post, n: int, first_date: pd.Timestamp) -> list[float]:
    span = (to_ts(post[1]) - to_ts(pre[0])).days + 1
    step = 7 * int(np.ceil((span + 7) / 7))
    out = []
    for j in range(1, n + 1):
        sh = pd.Timedelta(days=step * j)
        p0, p1 = to_ts(pre[0]) - sh, to_ts(pre[1]) - sh
        q0, q1 = to_ts(post[0]) - sh, to_ts(post[1]) - sh
        if p0 < first_date:
            break
        try:
            e = fn((p0, p1), (q0, q1))
        except ValueError:
            continue
        if e is not None and np.isfinite(e):
            out.append(float(e))
    return out


def _z(effect: float | None, placebo: list[float], floor: float = 0.005) -> tuple[float | None, float | None]:
    if effect is None or len(placebo) < 3:
        return None, None
    arr = np.array(placebo)
    sd = max(1.4826 * np.median(np.abs(arr - np.median(arr))), float(arr.std()) * 0.5, floor)
    return float(effect / sd), float(sd)


def diff_in_diff(wh: Warehouse, metric: str, treated_stores: list[str], pre_from, pre_to, post_from, post_to,
                 control_stores: list[str] | None = None, filters: dict | None = None, n_placebo: int = 8) -> dict:
    """Relative effect of a treatment on treated stores vs control stores, with a placebo noise floor."""
    all_stores = wh.stores["store_id"].tolist()
    control_stores = control_stores or [s for s in all_stores if s not in treated_stores]
    tf = {**(filters or {}), "store_id": treated_stores}
    cf = {**(filters or {}), "store_id": control_stores}
    pre, post = (pre_from, pre_to), (post_from, post_to)
    effect = _did_effect(wh, metric, tf, cf, pre, post)
    placebo = _placebo_effects(lambda p, q: _did_effect(wh, metric, tf, cf, p, q), pre, post, n_placebo, wh.first_date)
    z, sd = _z(effect, placebo)
    return jsonable({
        "metric": metric, "design": "difference_in_differences",
        "n_treated": len(treated_stores), "n_control": len(control_stores),
        "effect_pct": effect, "placebo_sd": sd, "z": z, "n_placebo": len(placebo),
        "supported": bool(z is not None and abs(z) >= Z_SUPPORT),
    })


def pre_post(wh: Warehouse, metric: str, pre_from, pre_to, post_from, post_to,
             filters: dict | None = None, n_placebo: int = 8) -> dict:
    pre, post = (pre_from, pre_to), (post_from, post_to)

    def eff(p, q):
        a, b = _daily_mean(wh, metric, filters, *p), _daily_mean(wh, metric, filters, *q)
        return b / a - 1 if a > 0 else None

    effect = eff(pre, post)
    placebo = _placebo_effects(eff, pre, post, n_placebo, wh.first_date)
    z, sd = _z(effect, placebo)
    return jsonable({"metric": metric, "design": "pre_post", "effect_pct": effect, "placebo_sd": sd, "z": z,
                     "supported": bool(z is not None and abs(z) >= Z_SUPPORT)})


def cannibalisation_check(wh: Warehouse, promo_product: str, treated_stores: list[str], pre_from, pre_to,
                          post_from, post_to) -> dict:
    """Did the promoted product gain unit share at the expense of same-category products?"""
    cat = wh.products.loc[wh.products["product_id"] == promo_product, "category"].iloc[0]
    peers = wh.products.loc[wh.products["category"] == cat, "product_id"].tolist()
    controls = [s for s in wh.stores["store_id"] if s not in treated_stores]
    rows = []

    def share(stores, p, a, b):
        # use total store units (all categories) as the share denominator
        all_units = wh.aggregate(["units"], (), a, b, {"store_id": stores}).iloc[0]["units"]
        one = wh.aggregate(["units"], (), a, b, {"store_id": stores, "product_id": p}).iloc[0]["units"]
        return one / all_units if all_units else np.nan

    for p in peers:
        t0, t1 = share(treated_stores, p, pre_from, pre_to), share(treated_stores, p, post_from, post_to)
        c0, c1 = share(controls, p, pre_from, pre_to), share(controls, p, post_from, post_to)
        did = (t1 / t0) / (c1 / c0) - 1 if min(t0, c0, c1) > 0 else None
        rows.append({"product_id": p, "unit_share_did_pct": did})
    promo_row = next(r for r in rows if r["product_id"] == promo_product)
    cannibalised = [r["product_id"] for r in rows
                    if r["product_id"] != promo_product and r["unit_share_did_pct"] is not None
                    and r["unit_share_did_pct"] <= CANNIBAL_MAX]
    cat_effect = _did_effect(wh, "net_sales", {"store_id": treated_stores, "category": cat},
                             {"store_id": controls, "category": cat}, (pre_from, pre_to), (post_from, post_to))
    return jsonable({
        "promo_product": promo_product, "category": cat,
        "promo_product_share_lift_pct": promo_row["unit_share_did_pct"],
        "cannibalised_products": cannibalised, "products": rows,
        "category_net_sales_did_pct": cat_effect,
        "cannibalisation_supported": bool(
            promo_row["unit_share_did_pct"] is not None and promo_row["unit_share_did_pct"] >= PROMO_LIFT_MIN and cannibalised
        ),
    })


# --------------------------------------------------------------------------------------
# Candidate explanations
# --------------------------------------------------------------------------------------
def _hyp(category: str, score: float, summary: str, **evidence) -> dict:
    return {"category": category, "score": round(float(score), 3), "summary": summary, "evidence": jsonable(evidence)}


def _ops(wh, d0, d1, system: str) -> pd.DataFrame:
    ops = wh.overlapping("ctx_ops_log", d0, d1)
    return ops[(ops["system"] == system) & (ops["severity"].isin(["high", "medium"]))]


def eval_pos_outage(wh, d0, d1) -> dict | None:
    dc = wh.daily_compare("transactions", d0, d1, by=["store_id"], filters={"payment_type": "card", "channel": "in_store"})
    bad = dc[(dc["pct"] <= OUTAGE_DROP) & (dc["baseline"] >= 20)]
    if bad.empty or bad["store_id"].nunique() > 8:
        return None
    stores = sorted(bad["store_id"].unique())
    cash = wh.daily_compare("transactions", d0, d1, by=["store_id"], filters={"payment_type": "cash", "channel": "in_store"})
    cash_up = cash[cash["store_id"].isin(stores)]["pct"].mean()
    ops = _ops(wh, d0, d1, "pos")
    logged = sorted(set(ops["store_id"]) & set(stores))
    frac = len(logged) / len(stores)
    score = 0.5 + 0.40 * frac + (0.05 if cash_up > 0.02 else 0)
    return _hyp("pos_outage", min(score, 0.95),
                f"In-store card transactions collapsed at {len(stores)} store(s); "
                f"{'an IT ticket confirms it' if logged else 'no ticket found'}.",
                stores=stores, store_days=[{"store_id": r.store_id, "date": r.date} for r in bad.itertuples()],
                cash_shift_pct=float(cash_up) if pd.notna(cash_up) else None, ops_log_matched=logged)


def eval_partner_outage(wh, d0, d1) -> dict | None:
    dc = wh.daily_compare("net_sales", d0, d1, filters={"channel": "delivery"})
    bad = dc[(dc["pct"] <= -0.9) & (dc["baseline"] > 0)]
    if bad.empty:
        return None
    ops = _ops(wh, d0, d1, "delivery_partner")
    return _hyp("partner_outage", 0.95 if len(ops) else 0.6,
                "Delivery channel sales dropped to ~zero" + (" and the ops log shows the partner integration down." if len(ops) else "."),
                dates=[str(d.date()) for d in bad["date"]], ops_log_rows=int(len(ops)))


def eval_closure(wh, d0, d1) -> dict | None:
    dc = wh.daily_compare("visits", d0, d1, by=["store_id"])
    bad = dc[(dc["actual"] == 0) & (dc["baseline"] > 20)]
    if bad.empty:
        return None
    stores = sorted(bad["store_id"].unique())
    ev = wh.overlapping("ctx_store_events", d0, d1, stores)
    planned = ev[ev["event_type"] == "planned_closure"]
    matched = sorted(set(planned["store_id"]) & set(stores))
    cat = "planned_closure" if matched else "unplanned_closure"
    return _hyp(cat, 0.95 if matched else 0.7,
                f"Store(s) {', '.join(stores)} reported zero visits" + (" during a planned closure." if matched else " with no closure on record."),
                stores=stores, days=int(len(bad)), planned_event_matched=matched)


def eval_stockout(wh, d0, d1) -> dict | None:
    dc = wh.daily_compare("units", d0, d1, by=["store_id", "product_id"])
    bad = dc[(dc["pct"] <= STOCKOUT_DROP) & (dc["baseline"] >= 8)]
    if bad.empty:
        return None
    prods = bad.groupby("product_id")["store_id"].nunique()
    prod = prods.idxmax()
    stores = sorted(bad[bad["product_id"] == prod]["store_id"].unique())
    if len(prods) > 3 or len(stores) > 12:
        return None
    ops = _ops(wh, d0, d1, "supply")
    logged = sorted(set(ops["store_id"]) & set(stores))
    frac = len(logged) / len(stores)
    return _hyp("stockout", 0.55 + 0.35 * frac,
                f"Units of {prod} fell sharply at {len(stores)} store(s)" + (" and a supplier shortfall is logged." if logged else "."),
                product=prod, stores=stores, ops_log_matched=logged)


def eval_promo(wh, d0, d1) -> list[dict]:
    out = []
    promos = wh.overlapping("ctx_promos", d0, d1)
    promos = promos[promos["product_id"].notna()]
    for pid, g in promos.groupby("promo_id"):
        stores = sorted(g["store_id"].unique())
        if len(stores) >= len(wh.stores) * 0.6:
            continue
        p0 = g["date_from"].min()
        pre = (p0 - pd.Timedelta(days=28), p0 - pd.Timedelta(days=1))
        if pre[0] < wh.first_date:
            continue
        post = (max(to_ts(d0), p0), min(to_ts(d1), g["date_to"].max()))
        prod = g["product_id"].iloc[0]
        chk = cannibalisation_check(wh, prod, stores, pre[0], pre[1], post[0], post[1])
        lift = chk["promo_product_share_lift_pct"]
        if chk["cannibalisation_supported"]:
            out.append(_hyp("promo_cannibalisation", 0.9,
                            f"'{g['promo_name'].iloc[0]}' lifted {prod} unit share by {lift:+.0%} while "
                            f"{', '.join(chk['cannibalised_products'])} lost share; category net sales moved "
                            f"{(chk['category_net_sales_did_pct'] or 0):+.1%} vs control stores.",
                            promo_id=pid, stores=stores, **{k: chk[k] for k in ("promo_product", "promo_product_share_lift_pct", "cannibalised_products", "category_net_sales_did_pct")}))
        elif lift is not None and lift >= PROMO_LIFT_MIN:
            out.append(_hyp("promo_uplift", 0.7, f"'{g['promo_name'].iloc[0]}' lifted {prod} with no clear cannibalisation.",
                            promo_id=pid, stores=stores, promo_product_share_lift_pct=lift))
    return out


def eval_price_change(wh, d0, d1) -> dict | None:
    pc = wh.ctx["ctx_price_changes"]
    pc = pc[(pc["effective_date"] <= to_ts(d1)) & (pc["effective_date"] >= to_ts(d0) - pd.Timedelta(days=45))]
    best = None
    for r in pc.itertuples():
        f = {"product_id": r.product_id}
        a = wh.compare(["avg_price_per_unit", "units"], d0, d1, filters=f).iloc[0]
        tot = wh.compare(["units"], d0, d1).iloc[0]
        price_pct = a["avg_price_per_unit__pct"]
        share_now = a["units"] / tot["units"]
        share_base = a["units__base"] / tot["units__base"]
        share_pct = share_now / share_base - 1 if share_base else np.nan
        if price_pct >= PRICE_UP_MIN:
            score = 0.9 if share_pct <= -0.10 else 0.7
            best = _hyp("price_change", score,
                        f"{r.product_id} price rose {price_pct:+.1%} (logged change on {r.effective_date.date()})"
                        + (f"; its unit share fell {share_pct:+.0%} (mix shift to cheaper sizes)." if share_pct <= -0.10 else "."),
                        product=r.product_id, price_pct=price_pct, unit_share_pct=share_pct, old_price=r.old_price, new_price=r.new_price)
    return best


def _store_breadth(wh, d0, d1) -> tuple[float, float]:
    st = wh.compare(["visits"], d0, d1, by=["store_id"])
    active = st[st["visits__base"] > 0]
    breadth = float((active["visits__pct"] <= BROAD_VISIT_DROP).mean())
    chain = wh.compare(["visits"], d0, d1).iloc[0]["visits__pct"]
    return breadth, float(chain)


def eval_weather(wh, d0, d1) -> dict | None:
    breadth, chain = _store_breadth(wh, d0, d1)
    if not (breadth >= BREADTH_MIN and chain <= BROAD_VISIT_DROP):
        return None
    w = ctx.weather_summary(wh, d0, d1)
    wet = (w["mean_rain_mm"] - (w["baseline_mean_rain_mm"] or 0)) >= 8 or w["days_over_15mm"] >= 1
    hol = wh.overlapping("ctx_holidays", d0, d1)
    score = 0.9 if wet else 0.25
    if len(hol):
        score -= 0.25
    return _hyp("weather", max(score, 0.1),
                f"Visits are down {chain:.1%} across {breadth:.0%} of stores" + (f" with heavy rain ({w['mean_rain_mm']:.0f} mm/day vs {w['baseline_mean_rain_mm']:.0f} normal)." if wet else " but rainfall was normal."),
                chain_visits_pct=chain, store_breadth=breadth, weather=w)


def eval_calendar(wh, d0, d1) -> dict | None:
    hol = wh.overlapping("ctx_holidays", d0, d1)
    if hol.empty:
        return None
    breadth, chain = _store_breadth(wh, d0, d1)
    rows = []
    for r in hol.itertuples():
        dc = wh.daily_compare("visits", r.date, r.date)
        pct_now = float(dc["pct"].iloc[0]) if len(dc) and pd.notna(dc["pct"].iloc[0]) else None
        prior = wh.ctx["ctx_holidays"]
        prior = prior[(prior["holiday_name"] == r.holiday_name) & (prior["date"] < r.date)]
        prior_pct = None
        if len(prior):
            pdc = wh.daily_compare("visits", prior["date"].iloc[-1], prior["date"].iloc[-1], n=4)
            if len(pdc) and pd.notna(pdc["pct"].iloc[0]):
                prior_pct = float(pdc["pct"].iloc[0])
        rows.append({"holiday": r.holiday_name, "date": r.date, "visits_vs_baseline_pct": pct_now, "prior_occurrence_pct": prior_pct})
    dips = [x for x in rows if x["visits_vs_baseline_pct"] is not None and x["visits_vs_baseline_pct"] <= -0.05]
    if not dips:
        return None
    aligned = [x for x in dips if x["prior_occurrence_pct"] is not None and abs(x["visits_vs_baseline_pct"] - x["prior_occurrence_pct"]) <= 0.15]
    score = 0.92 if aligned else 0.65
    # naive year-on-year (same ISO week last year) for the story
    prev = (to_ts(d0) - pd.Timedelta(days=364), to_ts(d1) - pd.Timedelta(days=364))
    yoy = None
    if prev[0] >= wh.first_date:
        now = wh.aggregate(["net_sales"], (), d0, d1).iloc[0]["net_sales"]
        then = wh.aggregate(["net_sales"], (), *prev).iloc[0]["net_sales"]
        yoy = float(now / then - 1) if then else None
    return _hyp("calendar_shift", score,
                f"{', '.join(sorted({x['holiday'] for x in dips}))} fall in this window"
                + (" and the same holiday showed a similar dip last time (holiday-aligned)." if aligned else "; no earlier occurrence to compare."),
                holidays=rows, naive_yoy_net_sales_pct=yoy, chain_visits_pct=chain, store_breadth=breadth)


def store_share_decline(wh, d0, d1, min_days: int = 14) -> pd.DataFrame:
    d0, d1 = to_ts(d0), to_ts(d1)
    n = (d1 - d0).days + 1
    if n < min_days:
        return pd.DataFrame()
    now = wh.aggregate(["net_sales"], ["store_id"], d0, d1)
    now["share"] = now["net_sales"] / now["net_sales"].sum()
    base = []
    for k in (1, 2, 3):
        a, b = d0 - pd.Timedelta(days=n * k), d1 - pd.Timedelta(days=n * k)
        if a < wh.first_date:
            continue
        t = wh.aggregate(["net_sales"], ["store_id"], a, b)
        t["share"] = t["net_sales"] / t["net_sales"].sum()
        base.append(t.set_index("store_id")["share"])
    if not base:
        return pd.DataFrame()
    b = pd.concat(base, axis=1).median(axis=1)
    now = now.set_index("store_id")
    now["share_pct"] = now["share"] / b - 1
    return now.reset_index()


def eval_external_unknown(wh, d0, d1) -> dict | None:
    sd = store_share_decline(wh, d0, d1)
    if sd.empty:
        return None
    low = sd[sd["share_pct"] <= SHARE_DECLINE]
    if low.empty or len(low) > 6:
        return None
    stores = sorted(low["store_id"])
    context = ctx.lookup_context(wh, d0, d1, store_ids=stores)
    explained = bool(context["ops_log"]["rows"]) and any(r["severity"] in ("high", "medium") for r in context["ops_log"]["rows"])
    explained = explained or bool(context["store_events"]["rows"])
    if explained:
        return None
    return _hyp("external_unknown", 0.8,
                f"{', '.join(stores)} are losing share of chain sales ({low['share_pct'].min():+.0%} at worst) and NOTHING in our data "
                "(ops, promos, prices, events, weather) explains it. This is not in our data: it needs a local, on-the-ground check.",
                stores=stores, share_pct={r.store_id: float(r.share_pct) for r in low.itertuples()})


def eval_mix_shift(wh, d0, d1) -> dict | None:
    r = mix_rate_decomposition(wh, "avg_basket", "region_id", d0, d1)
    if not r["simpsons_paradox"]:
        return None
    direction = "rose" if r["overall_now"] > r["overall_base"] else "fell"
    return _hyp("mix_shift", 0.9,
                f"Average basket {direction} {r['overall_pct']:+.1%} overall but moved the other way in "
                f"{r['segments_with_lower_rate'] if r['overall_now'] > r['overall_base'] else r['segments_total'] - r['segments_with_lower_rate']} of {r['segments_total']} regions: "
                "Simpson's paradox. Traffic mix shifted toward higher-basket regions (mix effect "
                f"{r['mix_effect_total']:+.2f} vs rate effect {r['rate_effect_total']:+.2f}).",
                overall_pct=r["overall_pct"], rate_effect=r["rate_effect_total"], mix_effect=r["mix_effect_total"],
                segments=r["segments"])


def eval_pipeline(wh, d0, d1) -> list[dict]:
    res = dq.run_checks(wh, d0, d1)
    out = []
    for cause in res["likely_causes"]:
        out.append(_hyp(cause, 0.97, f"Data-quality gate failed: {res['ticket']['summary']}", dq_findings=res["findings"][:4]))
    return out


# --------------------------------------------------------------------------------------
def rank_hypotheses(wh: Warehouse, date_from, date_to) -> dict:
    """Run every candidate explanation against the data and return them ranked."""
    d0, d1 = to_ts(date_from), to_ts(date_to)
    hyps: list[dict] = []
    hyps += eval_pipeline(wh, d0, d1)
    if not hyps:  # only test business causes when the data can be trusted
        for fn in (eval_pos_outage, eval_partner_outage, eval_closure, eval_stockout, eval_price_change,
                   eval_weather, eval_calendar, eval_mix_shift, eval_external_unknown):
            try:
                h = fn(wh, d0, d1)
            except ValueError:
                h = None
            if h:
                hyps.append(h)
        hyps += eval_promo(wh, d0, d1)
    if not hyps or max(h["score"] for h in hyps) < 0.5:
        hyps.append(_hyp("unexplained", 0.4, "No explanation in the data reaches the confidence bar."))
    hyps.sort(key=lambda h: -h["score"])
    for i, h in enumerate(hyps, 1):
        h["rank"] = i
    return jsonable({"window": [str(d0.date()), str(d1.date())], "hypotheses": hyps,
                     "multi_cause": bool(len([h for h in hyps if h["score"] >= 0.7]) >= 2)})
