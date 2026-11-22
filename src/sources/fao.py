"""
FAO Food Price Index + 5 sub-indices (dependent variables, 2014-2016 = 100).

The CSV has a few title lines before the real header, so we look for the
line that starts with "Date" instead of hard-coding "skip 2 rows" — that
makes the parser survive small format changes between monthly releases.
"""
import io
import re

import pandas as pd

import config
from sources._common import SeriesMeta, SourceResult, download, filter_start, long_frame, to_month_start

NAMES = {
    "fao_ffpi": "FAO Food Price Index",
    "fao_cereals": "FAO Cereals Price Index",
    "fao_vegoils": "FAO Vegetable Oils Price Index",
    "fao_dairy": "FAO Dairy Price Index",
    "fao_meat": "FAO Meat Price Index",
    "fao_sugar": "FAO Sugar Price Index",
}


def parse(content: bytes) -> pd.DataFrame:
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_idx = next(i for i, l in enumerate(lines) if l.strip().strip('"').lower().startswith("date"))
    df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all", axis=1)

    df["Date"] = _parse_dates(df["Date"].astype(str).str.strip())
    df = df.dropna(subset=["Date"])            # drops footnote lines at the bottom
    df["Date"] = to_month_start(df["Date"])
    return filter_start(long_frame(df, "Date", config.FAO_SERIES))


def _parse_dates(s: pd.Series) -> pd.Series:
    # FAO has used "1990-01", "01/1990" and "1990-01-01" in different vintages
    if s.str.match(r"^\d{1,2}/\d{4}$").mean() > 0.5:
        return pd.to_datetime(s, format="%m/%Y", errors="coerce")
    if s.str.match(r"^\d{4}-\d{2}$").mean() > 0.5:
        return pd.to_datetime(s, format="%Y-%m", errors="coerce")
    return pd.to_datetime(s, errors="coerce")


def extract() -> SourceResult:
    content = download(config.FAO_CSV_URL, "fao", "food_price_indices_data.csv")
    obs = parse(content)
    meta = [SeriesMeta(sid, NAMES[sid], "FAO", "index 2014-16=100", "M", sid)
            for sid in sorted(obs["series_id"].unique())]
    return SourceResult(observations=obs, meta=meta)
