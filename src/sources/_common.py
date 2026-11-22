"""
Shared helpers for all sources:
  * a requests Session with automatic retries (servers sometimes fail once),
  * archiving every raw file in data/raw/<source>/ with today's date,
  * a small container (SourceResult) that every source returns.

Why archive raw files? Data providers REVISE their numbers. If your results
change in October, you want to be able to show which vintage you used.
"""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

HEADERS = {
    # some sites (FAO, World Bank) block the default python User-Agent
    "User-Agent": "Mozilla/5.0 (thesis data pipeline; University of St. Gallen)",
}


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s


def download(url: str, source: str, filename: str, params=None, headers=None, timeout=120) -> bytes:
    """Download a file, save a dated copy under data/raw/<source>/ and return its bytes."""
    r = session().get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    archive(r.content, source, filename)
    return r.content


def archive(content: bytes, source: str, filename: str) -> Path:
    folder = config.RAW_DIR / source
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date.today():%Y-%m-%d}_{filename}"
    path.write_bytes(content)
    return path


def to_month_start(s: pd.Series) -> pd.Series:
    """Any date -> first day of its month (our monthly convention)."""
    return pd.to_datetime(s).dt.to_period("M").dt.to_timestamp().dt.date


@dataclass
class SeriesMeta:
    series_id: str
    name: str
    source: str
    unit: str = ""
    frequency: str = "M"
    source_code: str = ""


@dataclass
class SourceResult:
    """What every source returns. The orchestrator (extract.py) writes it to DuckDB."""
    observations: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["series_id", "date", "value"]))
    meta: list = field(default_factory=list)
    tables: dict = field(default_factory=dict)   # extra tables, e.g. {"trade_flows": df}

    def meta_frame(self) -> pd.DataFrame:
        return pd.DataFrame([m.__dict__ for m in self.meta])


def long_frame(df_wide: pd.DataFrame, date_col: str, mapping: dict) -> pd.DataFrame:
    """
    Wide table (one column per variable) -> long table (series_id, date, value).
    mapping = {column_in_df: our_series_id}
    """
    keep = {c: sid for c, sid in mapping.items() if c in df_wide.columns}
    out = df_wide[[date_col, *keep]].rename(columns={date_col: "date", **keep})
    out = out.melt(id_vars="date", var_name="series_id", value_name="value")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["value"])
    return out[["series_id", "date", "value"]]


def filter_start(df: pd.DataFrame) -> pd.DataFrame:
    start = pd.Timestamp(config.START_DATE).date()
    return df[df["date"] >= start].reset_index(drop=True)
