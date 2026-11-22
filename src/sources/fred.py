"""
FRED API (Federal Reserve Bank of St. Louis).
Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html

This is the "classic" REST API pattern you will meet everywhere:
    GET <endpoint>?param1=...&param2=...&api_key=...  ->  JSON
"""
import json

import pandas as pd

import config
from sources._common import SeriesMeta, SourceResult, archive, filter_start, session, to_month_start

URL = "https://api.stlouisfed.org/fred/series/observations"


def parse(payload: dict, our_id: str) -> pd.DataFrame:
    df = pd.DataFrame(payload["observations"])[["date", "value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")   # FRED writes "." for missing
    df["date"] = to_month_start(df["date"])
    df["series_id"] = our_id
    return df.dropna(subset=["value"])[["series_id", "date", "value"]]


def extract() -> SourceResult:
    if not config.FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY missing in .env (free key: fred.stlouisfed.org/docs/api/api_key.html)")
    s = session()
    frames, meta = [], []
    for fred_id, (our_id, desc, unit) in config.FRED_SERIES.items():
        params = {"series_id": fred_id, "api_key": config.FRED_API_KEY, "file_type": "json",
                  "observation_start": config.START_DATE, "frequency": "m"}
        r = s.get(URL, params=params, timeout=60)
        r.raise_for_status()
        archive(r.content, "fred", f"{fred_id}.json")
        frames.append(parse(r.json(), our_id))
        meta.append(SeriesMeta(our_id, desc, "FRED", unit, "M", fred_id))
    return SourceResult(observations=filter_start(pd.concat(frames, ignore_index=True)), meta=meta)
