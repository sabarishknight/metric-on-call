"""Hard caps applied to every tool output: bounded rows, bounded bytes, JSON-safe values."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from common.config import get_settings

MAX_BYTES = 24_000


def jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, pd.Timestamp):
        return x.date().isoformat()
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, pd.DataFrame):
        return jsonable(x.to_dict("records"))
    if hasattr(x, "isoformat"):
        return x.isoformat()
    if x is pd.NaT:
        return None
    return x


def cap_rows(df: pd.DataFrame, limit: int | None = None) -> dict:
    limit = limit or get_settings().max_rows_returned
    return {
        "rows": jsonable(df.head(limit)),
        "n_rows_total": int(len(df)),
        "truncated": bool(len(df) > limit),
    }


def cap_bytes(payload: dict) -> dict:
    payload = jsonable(payload)
    raw = json.dumps(payload)
    if len(raw) <= MAX_BYTES:
        return payload
    # shrink the longest list until it fits
    for _ in range(20):
        longest = max(
            ((k, v) for k, v in payload.items() if isinstance(v, list) and len(v) > 3),
            key=lambda kv: len(json.dumps(kv[1])),
            default=None,
        )
        if longest is None:
            break
        payload[longest[0]] = longest[1][: max(3, len(longest[1]) // 2)]
        payload["truncated"] = True
        if len(json.dumps(payload)) <= MAX_BYTES:
            break
    return payload
