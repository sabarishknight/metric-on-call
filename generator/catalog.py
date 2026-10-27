"""Static catalogue for the fictional chain: Harbour Lane Café & Grocer."""

from __future__ import annotations

import datetime as dt

SEED = 20261021
WINDOW_END = dt.date(2026, 10, 18)  # Sunday
N_WEEKS = 81
WINDOW_START = WINDOW_END - dt.timedelta(days=7 * N_WEEKS - 1)  # 2025-03-31, Monday

CHANNELS = ["in_store", "delivery"]
DAYPARTS = ["morning", "midday", "afternoon"]
PAYMENTS = ["card", "cash", "wallet"]

# region_id: (name, traffic factor, units-per-transaction factor, weekday profile)
DEFAULT_DOW = [0.92, 0.95, 0.97, 1.00, 1.08, 1.14, 1.00]  # Mon..Sun
CBD_DOW = [1.05, 1.10, 1.10, 1.10, 1.08, 0.62, 0.50]
REGIONS = {
    "CB": ("CBD", 1.15, 1.35, CBD_DOW),
    "ES": ("Eastern Suburbs", 1.05, 1.05, DEFAULT_DOW),
    "IW": ("Inner West", 1.00, 0.95, DEFAULT_DOW),
    "NS": ("North Shore", 1.00, 1.00, DEFAULT_DOW),
    "WS": ("Western Sydney", 0.95, 0.82, DEFAULT_DOW),
    "SS": ("Southern Sydney", 0.90, 0.83, DEFAULT_DOW),
}

STORES = [
    ("CB01", "Martin Place", "CB"),
    ("CB02", "Barangaroo", "CB"),
    ("CB03", "Haymarket", "CB"),
    ("CB04", "Circular Quay", "CB"),
    ("ES01", "Bondi Junction", "ES"),
    ("ES02", "Randwick", "ES"),
    ("ES03", "Paddington", "ES"),
    ("ES04", "Coogee", "ES"),
    ("IW01", "Newtown", "IW"),
    ("IW02", "Balmain", "IW"),
    ("IW03", "Leichhardt", "IW"),
    ("IW04", "Marrickville", "IW"),
    ("NS01", "Chatswood", "NS"),
    ("NS02", "Lane Cove", "NS"),
    ("NS03", "Mosman", "NS"),
    ("NS04", "St Leonards", "NS"),
    ("WS01", "Parramatta", "WS"),
    ("WS02", "Blacktown", "WS"),
    ("WS03", "Penrith", "WS"),
    ("WS04", "Westmead", "WS"),
    ("SS01", "Sutherland", "SS"),
    ("SS02", "Cronulla", "SS"),
    ("SS03", "Hurstville", "SS"),
    ("SS04", "Rockdale", "SS"),
]

# product_id, name, category, base price (AUD), base unit-mix share, delivery mix multiplier
PRODUCTS = [
    ("coffee_small", "Small coffee", "coffee", 4.20, 0.07, 0.8),
    ("coffee_regular", "Regular coffee", "coffee", 4.80, 0.13, 0.8),
    ("coffee_large", "Large coffee", "coffee", 5.40, 0.10, 0.8),
    ("oat_flat_white", "Oat flat white", "coffee", 5.60, 0.06, 0.8),
    ("muffin", "Muffin", "bakery", 4.50, 0.07, 0.9),
    ("croissant", "Croissant", "bakery", 5.00, 0.07, 0.9),
    ("cookie", "Cookie", "bakery", 3.80, 0.04, 1.0),
    ("sandwich", "Sandwich", "food", 11.50, 0.08, 1.3),
    ("salad", "Salad bowl", "food", 13.00, 0.05, 1.4),
    ("pasta_pot", "Pasta pot", "food", 14.00, 0.03, 1.5),
    ("juice", "Cold-pressed juice", "cold_drinks", 6.50, 0.06, 1.0),
    ("soft_drink", "Soft drink", "cold_drinks", 4.00, 0.06, 1.1),
    ("milk_1l", "Milk 1L", "grocery", 2.60, 0.06, 1.2),
    ("bread_loaf", "Sourdough loaf", "grocery", 7.50, 0.06, 1.1),
    ("eggs_dozen", "Eggs dozen", "grocery", 8.20, 0.06, 1.2),
]

CHANNEL_SHARE_BASE = {"in_store": 330.0, "delivery": 72.0}  # visits/sessions per store per day
CHANNEL_DAYPART = {
    "in_store": [0.45, 0.35, 0.20],
    "delivery": [0.15, 0.55, 0.30],
}
CHANNEL_PAYMENT = {
    "in_store": [0.62, 0.22, 0.16],
    "delivery": [0.70, 0.00, 0.30],
}
CHANNEL_CONVERSION = {"in_store": 0.58, "delivery": 0.85}
CHANNEL_UPT = {"in_store": 2.3, "delivery": 3.1}

# Public holidays used in the calendar context. visits multiplier applies in the BASELINE world
# for everything except Easter (Easter is incident 8 and only applies when that incident is on).
HOLIDAYS = [
    (dt.date(2025, 4, 18), "Good Friday", "easter", None),
    (dt.date(2025, 4, 19), "Easter Saturday", "easter", None),
    (dt.date(2025, 4, 20), "Easter Sunday", "easter", None),
    (dt.date(2025, 4, 21), "Easter Monday", "easter", None),
    (dt.date(2025, 4, 25), "Anzac Day", "public", 0.90),
    (dt.date(2025, 6, 9), "King's Birthday", "public", 0.95),
    (dt.date(2025, 10, 6), "Labour Day", "public", 0.93),
    (dt.date(2025, 12, 25), "Christmas Day", "public", 0.45),
    (dt.date(2025, 12, 26), "Boxing Day", "public", 0.55),
    (dt.date(2026, 1, 1), "New Year's Day", "public", 0.60),
    (dt.date(2026, 1, 26), "Australia Day", "public", 0.90),
    (dt.date(2026, 4, 3), "Good Friday", "easter", None),
    (dt.date(2026, 4, 4), "Easter Saturday", "easter", None),
    (dt.date(2026, 4, 5), "Easter Sunday", "easter", None),
    (dt.date(2026, 4, 6), "Easter Monday", "easter", None),
    (dt.date(2026, 4, 25), "Anzac Day", "public", 0.90),
    (dt.date(2026, 6, 8), "King's Birthday", "public", 0.95),
    (dt.date(2026, 10, 5), "Labour Day", "public", 0.93),
]


def all_dates() -> list[dt.date]:
    return [WINDOW_START + dt.timedelta(days=i) for i in range(7 * N_WEEKS)]
