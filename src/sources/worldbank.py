"""
World Bank Commodity Price Data ("Pink Sheet"), sheet "Monthly Prices".

Layout of the sheet (roughly):
    a few title rows
    a row with commodity names  ("Crude oil, Brent", "Urea", ...)
    a row with units            ("($/bbl)", "($/mt)", ...)
    data rows                   "1960M01", 1.63, ...

Instead of hard-coding row numbers we SEARCH for the name row and for rows
whose first cell looks like "2000M01". Robust to small layout changes.

Note for the thesis: the Pink Sheet urea quote is "Urea, prill spot f.o.b.
Middle East" since March 2022 (Black Sea before). Check the footnote in the
PDF Pink Sheet and mention this series break in your data section.
"""
import io
import re

import pandas as pd

import config
from sources._common import SeriesMeta, SourceResult, download, filter_start

SHEET = "Monthly Prices"


def _norm(x) -> str:
    """'Urea  **' -> 'urea'   'Rice, Thai 5% ' -> 'rice, thai 5%'"""
    return re.sub(r"[\s\*]+$", "", str(x).replace("\xa0", " ")).strip().lower()


def parse(content: bytes) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(content), sheet_name=SHEET, header=None)

    # 1) the row that contains the commodity names
    name_row = next(i for i in range(min(20, len(raw)))
                    if any(_norm(v) == "crude oil, brent" for v in raw.iloc[i]))
    names = [_norm(v) for v in raw.iloc[name_row]]

    # 2) data rows: first column like 2000M01
    first = raw.iloc[:, 0].astype(str).str.strip()
    data = raw[first.str.match(r"^\d{4}M\d{2}$")].copy()
    dates = pd.to_datetime(first[data.index].str.replace("M", "-"), format="%Y-%m").dt.date

    # 3) pick the columns we want: exact name match first, then 'startswith'
    frames, missing = [], []
    for key, (sid, _unit) in config.WB_SERIES.items():
        col = next((j for j, n in enumerate(names) if n == key), None)
        if col is None:
            col = next((j for j, n in enumerate(names) if n.startswith(key)), None)
        if col is None:
            missing.append(key)
            continue
        vals = pd.to_numeric(data.iloc[:, col].replace("..", None), errors="coerce")
        frames.append(pd.DataFrame({"series_id": sid, "date": dates.values, "value": vals.values}))

    if missing:
        print(f"  [worldbank] WARNING: columns not found: {missing}")
        print(f"  [worldbank] available names: {[n for n in names if n and n != 'nan']}")

    out = pd.concat(frames, ignore_index=True).dropna(subset=["value"])
    return filter_start(out)


def extract() -> SourceResult:
    content = download(config.WB_MONTHLY_URL, "worldbank", "CMO-Historical-Data-Monthly.xlsx")
    obs = parse(content)
    meta = [SeriesMeta(sid, key, "World Bank Pink Sheet", unit, "M", key)
            for key, (sid, unit) in config.WB_SERIES.items() if sid in set(obs["series_id"])]
    return SourceResult(observations=obs, meta=meta)
