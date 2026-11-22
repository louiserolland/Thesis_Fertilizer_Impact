"""
IMF PortWatch — daily ship transits through maritime chokepoints, estimated
from satellite AIS signals (IMF & University of Oxford, UN Global Platform).

Why this matters for the thesis: instead of a 0/1 dummy you get a CONTINUOUS
measure of how closed the strait is (traffic partly recovered and collapsed
again during 2026). The BIS already uses this dataset for Hormuz:
BIS Working Paper 1378, "Maritime chokepoints and the global economy:
evidence from the Strait of Hormuz".

Granular urea and DAP travel on dry-bulk carriers; ammonia travels on gas
carriers, which PortWatch most likely counts under "tanker" — check the
PortWatch methodology page before you interpret the vessel types.

Technical lesson: ArcGIS REST APIs return max ~1000-2000 rows per request,
so we PAGINATE with resultOffset until the server says there is no more.
"""
import json

import pandas as pd

import config
from sources._common import SeriesMeta, SourceResult, archive, session

PAGE = 1000
FIELDS = ["n_total", "n_tanker", "n_dry_bulk", "n_container", "n_general_cargo",
          "capacity", "capacity_tanker", "capacity_dry_bulk"]


def fetch_chokepoint(keyword: str) -> list:
    s, rows, offset = session(), [], 0
    while True:
        params = {"where": f"portname LIKE '%{keyword}%'", "outFields": "*", "f": "json",
                  "orderByFields": "date", "resultOffset": offset, "resultRecordCount": PAGE}
        r = s.get(config.PORTWATCH_URL, params=params, timeout=120)
        r.raise_for_status()
        payload = r.json()
        if "error" in payload:
            raise RuntimeError(payload["error"])
        feats = [f["attributes"] for f in payload.get("features", [])]
        rows += feats
        if not feats or not payload.get("exceededTransferLimit", False):
            break
        offset += len(feats)
    archive(json.dumps(rows).encode(), "portwatch", f"{keyword}.json")
    return rows


def parse(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if {"year", "month", "day"} <= set(df.columns):
        df["date"] = pd.to_datetime(df[["year", "month", "day"]])
    elif pd.api.types.is_numeric_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"], unit="ms")      # ArcGIS epoch milliseconds
    else:
        df["date"] = pd.to_datetime(df["date"])
    df["date"] = df["date"].dt.date
    for c in FIELDS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    return df[["date", "portid", "portname", *FIELDS]].drop_duplicates(["date", "portid"])


def monthly(daily: pd.DataFrame, slug: str) -> pd.DataFrame:
    """Daily -> monthly AVERAGE transits per day (the current month is partial!)."""
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.to_period("M").dt.to_timestamp().dt.date
    m = d.groupby("date")[["n_total", "n_tanker", "n_dry_bulk"]].mean().reset_index()
    m = m.rename(columns={"n_total": f"pw_{slug}_total", "n_tanker": f"pw_{slug}_tanker",
                          "n_dry_bulk": f"pw_{slug}_drybulk"})
    return m.melt(id_vars="date", var_name="series_id", value_name="value").dropna()


def extract() -> SourceResult:
    dailies, monthlies, meta = [], [], []
    for keyword, slug in config.PORTWATCH_CHOKEPOINTS.items():
        daily = parse(fetch_chokepoint(keyword))
        dailies.append(daily)
        monthlies.append(monthly(daily, slug))
        for kind in ("total", "tanker", "drybulk"):
            meta.append(SeriesMeta(f"pw_{slug}_{kind}", f"{keyword}: avg daily transits ({kind})",
                                   "IMF PortWatch", "ships/day", "M", keyword))
    return SourceResult(observations=pd.concat(monthlies, ignore_index=True)[["series_id", "date", "value"]],
                        meta=meta, tables={"chokepoint_daily": pd.concat(dailies, ignore_index=True)})
