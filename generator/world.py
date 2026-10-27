"""The simulation: a deterministic, vectorised model of the chain's trading.

`simulate(spec, noise, apply)` returns the world with only the incident effects listed in
`apply` switched on. All random draws live in `Noise` and are made once from the seed, so two
worlds that differ only in `apply` differ ONLY because of the incident effects. That is what
lets the generator compute each incident's true dollar impact as (actual - counterfactual).

Effect keys: "1","4","5","6","7","8","9","10","11","12","13w" (weather part of 13),
"13p" (card-outage part of 13). Incidents 2 and 3 are raw-data defects, injected in build.py.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from common.config import INCIDENTS_YAML
from generator import catalog as C

ALL_EFFECTS = frozenset({"1", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13w", "13p"})
UNIT_EFFECT_ALPHA = 0.5  # share of a mix-driven unit change that persists as a volume change


def load_spec(path: Path | None = None) -> dict:
    return yaml.safe_load((path or INCIDENTS_YAML).read_text())


@dataclass
class Noise:
    store_factor: np.ndarray  # [S]
    store_day: np.ndarray  # [S,D]
    chain_day: np.ndarray  # [D]
    cell: np.ndarray  # [S,D,CH,DP]
    conv: np.ndarray  # [S,D,CH,DP]
    pay: np.ndarray  # [S,D,CH,DP,PAY]
    upt: np.ndarray  # [S,D,CH]
    mix: np.ndarray  # [S,D,P]
    rain: np.ndarray  # [D,R] baseline rain (mm)
    temp: np.ndarray  # [D,R]
    u7: np.ndarray  # [D,R] uniform draws used only when incident 7 is on
    u13: np.ndarray  # [D,R] uniform draws used only when incident 13 (weather) is on


def make_noise(seed: int = C.SEED) -> Noise:
    rng = np.random.default_rng(seed)
    S, D = len(C.STORES), 7 * C.N_WEEKS
    CH, DP, PAY, P, R = len(C.CHANNELS), len(C.DAYPARTS), len(C.PAYMENTS), len(C.PRODUCTS), len(
        C.REGIONS
    )
    ln = lambda sigma, *shape: rng.lognormal(0.0, sigma, shape)  # noqa: E731
    store_factor = np.clip(ln(0.15, S), 0.78, 1.30)
    store_day = ln(0.045, S, D)
    chain_day = ln(0.025, D)
    cell = ln(0.05, S, D, CH, DP)
    conv = ln(0.02, S, D, CH, DP)
    pay = ln(0.03, S, D, CH, DP, PAY)
    upt = ln(0.015, S, D, CH)
    mix = ln(0.03, S, D, P)
    chain_event = rng.random(D) < 0.22
    regional_extra = rng.random((D, R)) < 0.05
    mm = rng.exponential(7.0, (D, R))
    rain = np.where(chain_event[:, None] | regional_extra, mm, 0.0)
    doy = np.array([d.timetuple().tm_yday for d in C.all_dates()])
    temp = 21 + 6 * np.sin(2 * np.pi * (doy - 20) / 365.25)[:, None] + rng.normal(0, 2, (D, R))
    u7 = rng.random((D, R))
    u13 = rng.random((D, R))
    return Noise(store_factor, store_day, chain_day, cell, conv, pay, upt, mix, rain, temp, u7, u13)


@dataclass
class Sim:
    dates: list[dt.date]
    store_ids: list[str]
    visits: np.ndarray  # [S,D,CH,DP] float
    txn: np.ndarray  # [S,D,CH,DP,PAY] int
    units_cell: np.ndarray  # [S,D,CH,DP,PAY]
    sales_cell: np.ndarray  # [S,D,CH,DP,PAY]
    lines_units: np.ndarray  # [S,D,CH,P]
    lines_sales: np.ndarray  # [S,D,CH,P]
    rain: np.ndarray  # [D,R]
    temp: np.ndarray  # [D,R]

    def date_mask(self, date_from: dt.date, date_to: dt.date) -> np.ndarray:
        return np.array([date_from <= d <= date_to for d in self.dates])

    def total_sales(self, date_from: dt.date, date_to: dt.date) -> float:
        return float(self.sales_cell[:, self.date_mask(date_from, date_to)].sum())


def _incidents(spec: dict) -> dict[int, dict]:
    return {i["id"]: i for i in spec["incidents"]}


def simulate(spec: dict, noise: Noise, apply: frozenset[str] = ALL_EFFECTS) -> Sim:  # noqa: C901
    cal = C.all_dates()
    D = len(cal)
    store_ids = [s[0] for s in C.STORES]
    S = len(store_ids)
    sidx = {s: i for i, s in enumerate(store_ids)}
    region_ids = list(C.REGIONS)
    reg = np.array([region_ids.index(s[2]) for s in C.STORES])
    inc = _incidents(spec)
    dow = np.array([d.weekday() for d in cal])
    doy = np.array([d.timetuple().tm_yday for d in cal])
    t_years = np.arange(D) / 365.25

    def dm(a: dt.date, b: dt.date) -> np.ndarray:
        return np.array([a <= d <= b for d in cal])

    def stores_of(i: int, key: str = "stores") -> list[int]:
        scope = inc[i]["scope"]
        names = scope[key]
        if names == "all":
            return list(range(S))
        return [sidx[n] for n in names]

    # ---- weather ----------------------------------------------------------------------
    rain = noise.rain.copy()
    for key, u in (("7", noise.u7), ("13w", noise.u13)):
        if key in apply:
            spec_i = inc[int(key.rstrip("wp"))]
            m = dm(spec_i["date_from"], spec_i["date_to"])
            lo, hi = spec_i["params"]["rain_mm_min"], spec_i["params"]["rain_mm_max"]
            rain[m, :] = lo + u[m, :] * (hi - lo)
    wx = 1.0 - np.minimum(0.16, 0.0045 * rain)  # [D,R]

    # ---- calendar multipliers ---------------------------------------------------------
    hol = np.ones(D)
    easter_keys = {
        "Good Friday": "good_friday",
        "Easter Saturday": "easter_saturday",
        "Easter Sunday": "easter_sunday",
        "Easter Monday": "easter_monday",
    }
    for date, name, kind, mult in C.HOLIDAYS:
        i = (date - C.WINDOW_START).days
        if not 0 <= i < D:
            continue
        if kind == "easter":
            if "8" in apply:
                hol[i] = inc[8]["params"][easter_keys[name]]
        else:
            hol[i] = mult
    season = 1 + 0.06 * np.sin(2 * np.pi * (doy - 60) / 365.25) + 0.03 * t_years

    # ---- visits -----------------------------------------------------------------------
    region_traffic = np.array([C.REGIONS[r][1] for r in region_ids])
    region_upt = np.array([C.REGIONS[r][2] for r in region_ids])
    dow_profile = np.array([C.REGIONS[r][3] for r in region_ids])  # [R,7]
    base_store = noise.store_factor * region_traffic[reg]  # [S]
    ch_base = np.array([C.CHANNEL_SHARE_BASE[c] for c in C.CHANNELS])  # [CH]
    chdp = np.array([C.CHANNEL_DAYPART[c] for c in C.CHANNELS])  # [CH,DP]
    dowf = dow_profile[reg][:, dow]  # [S,D]

    V = (
        base_store[:, None, None, None]
        * ch_base[None, None, :, None]
        * chdp[None, None, :, :]
        * dowf[:, :, None, None]
        * season[None, :, None, None]
        * hol[None, :, None, None]
        * wx.T[reg][:, :, None, None]
        * noise.store_day[:, :, None, None]
        * noise.chain_day[None, :, None, None]
        * noise.cell
    )

    M = np.ones((S, D, len(C.CHANNELS)))  # visit multipliers per store/day/channel
    if "6" in apply:
        p = inc[6]
        d0, d1 = p["date_from"], p["date_to"]
        span = max(1, (d1 - d0).days)
        final = p["params"]["final_visit_multiplier"]
        for s in stores_of(6):
            for i, d in enumerate(cal):
                if d >= d0:
                    frac = min(1.0, (d - d0).days / span)
                    M[s, i, :] *= 1 + (final - 1) * frac
    if "10" in apply:
        m = dm(inc[10]["date_from"], inc[10]["date_to"])
        M[:, m, C.CHANNELS.index("delivery")] *= inc[10]["params"]["delivery_multiplier"]
    if "11" in apply:
        m = dm(inc[11]["date_from"], inc[11]["date_to"])
        for s in stores_of(11):
            M[s, m, :] = 0.0
    if "12" in apply:
        p = inc[12]
        m = dm(p["date_from"], p["date_to"])
        for s in range(S):
            f = (
                p["params"]["cbd_visit_multiplier"]
                if store_ids[s].startswith("CB")
                else p["params"]["other_visit_multiplier"]
            )
            M[s, m, :] *= f
    V = V * M[:, :, :, None]

    # ---- transactions -----------------------------------------------------------------
    conv = np.array([C.CHANNEL_CONVERSION[c] for c in C.CHANNELS])
    T0 = V * conv[None, None, :, None] * noise.conv
    paysh = np.array([C.CHANNEL_PAYMENT[c] for c in C.CHANNELS])  # [CH,PAY]
    Tp = T0[..., None] * paysh[None, None, :, None, :] * noise.pay

    PM = np.ones((S, D, len(C.CHANNELS), len(C.PAYMENTS)))
    card, cash = C.PAYMENTS.index("card"), C.PAYMENTS.index("cash")
    ins = C.CHANNELS.index("in_store")
    if "1" in apply:
        p = inc[1]
        m = dm(p["date_from"], p["date_to"])
        for s in stores_of(1):
            PM[s, m, ins, card] *= p["params"]["card_multiplier"]
            PM[s, m, ins, cash] *= p["params"]["cash_multiplier"]
    if "13p" in apply:
        p = inc[13]
        m = dm(p["params"]["outage_day"], p["params"]["outage_day"])
        for n in p["scope"]["outage_stores"]:
            PM[sidx[n], m, ins, card] *= p["params"]["card_multiplier"]
            PM[sidx[n], m, ins, cash] *= p["params"]["cash_multiplier"]
    Tp = Tp * PM[:, :, :, None, :]
    # rounding: counts are whole transactions
    T = np.rint(Tp).astype(np.int64)

    # ---- basket: units per transaction x average price per unit ----------------------
    upt0 = np.array([C.CHANNEL_UPT[c] for c in C.CHANNELS])
    UPT = upt0[None, None, :] * region_upt[reg][:, None, None] * noise.upt
    if "12" in apply:
        m = dm(inc[12]["date_from"], inc[12]["date_to"])
        UPT[:, m, :] *= inc[12]["params"]["basket_multiplier"]

    pid = [p[0] for p in C.PRODUCTS]
    pidx = {p: i for i, p in enumerate(pid)}
    P = len(pid)
    u0 = np.array([p[4] for p in C.PRODUCTS])
    chm = np.ones((len(C.CHANNELS), P))
    chm[C.CHANNELS.index("delivery")] = [p[5] for p in C.PRODUCTS]
    u_noeff = u0[None, None, None, :] * chm[None, None, :, :] * noise.mix[:, :, None, :]

    MM = np.ones((S, D, P))  # mix multipliers
    if "4" in apply:
        p = inc[4]
        m = dm(p["date_from"], p["date_to"])
        for s in stores_of(4):
            MM[s, m, pidx["muffin"]] *= p["params"]["muffin_mix_multiplier"]
            MM[s, m, pidx["croissant"]] *= p["params"]["croissant_mix_multiplier"]
    if "5" in apply:
        p = inc[5]
        m = np.array([d >= p["date_from"] for d in cal])
        MM[:, m, pidx["coffee_large"]] *= p["params"]["large_mix_multiplier"]
        MM[:, m, pidx["coffee_regular"]] *= p["params"]["regular_mix_multiplier"]
    if "9" in apply:
        p = inc[9]
        m = dm(p["date_from"], p["date_to"])
        for s in stores_of(9):
            MM[s, m, pidx["oat_flat_white"]] *= p["params"]["oat_mix_multiplier"]
    u_raw = u_noeff * MM[:, :, None, :]
    ratio = u_raw.sum(-1) / u_noeff.sum(-1)  # [S,D,CH]
    u = u_raw / u_raw.sum(-1, keepdims=True)
    UPT = UPT * ratio**UNIT_EFFECT_ALPHA

    infl = 1 + 0.025 * t_years
    price = np.array([p[3] for p in C.PRODUCTS])[None, None, :] * infl[None, :, None]
    price = np.broadcast_to(price, (S, D, P)).copy()
    if "5" in apply:
        p = inc[5]
        m = np.array([d >= p["date_from"] for d in cal])
        price[:, m, pidx["coffee_large"]] += p["params"]["price_delta"]
    if "4" in apply:
        p = inc[4]
        m = dm(p["date_from"], p["date_to"])
        for s in stores_of(4):
            price[s, m, pidx["muffin"]] *= p["params"]["muffin_price_multiplier"]

    avg_price = (u * price[:, :, None, :]).sum(-1)  # [S,D,CH]
    basket = UPT * avg_price
    sales_cell = T * basket[:, :, :, None, None]
    units_cell = T * UPT[:, :, :, None, None]

    n_txn = T.sum(axis=(3, 4))  # [S,D,CH]
    lines_units = (n_txn * UPT)[..., None] * u
    lines_sales = lines_units * price[:, :, None, :]

    return Sim(
        dates=cal,
        store_ids=store_ids,
        visits=V,
        txn=T,
        units_cell=units_cell,
        sales_cell=sales_cell,
        lines_units=lines_units,
        lines_sales=lines_sales,
        rain=rain,
        temp=noise.temp,
    )
