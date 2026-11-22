"""
Manual files + constructed variables.

1) Data without a free API (e.g. the Baltic Dry Index, which belongs to the
   Baltic Exchange). Export it from a terminal at the HSG library
   (Bloomberg ticker 'BDIY Index', or LSEG/Refinitiv), save as CSV with two
   columns 'date,value' in data/manual/bdi.csv — daily is fine, we average
   to monthly here.

2) The Hormuz dummy D_t (constructed, see config.HORMUZ_START).
"""
import pandas as pd

import config
from sources._common import SeriesMeta, SourceResult, filter_start, to_month_start


def parse_manual(path, series_id) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = to_month_start(pd.to_datetime(df["date"], dayfirst=False, errors="coerce"))
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    m = df.dropna().groupby("date", as_index=False)["value"].mean()   # daily -> monthly average
    m["series_id"] = series_id
    return m[["series_id", "date", "value"]]


def hormuz_dummy() -> pd.DataFrame:
    months = pd.date_range(config.START_DATE, pd.Timestamp.today(), freq="MS")
    return pd.DataFrame({"series_id": "dummy_hormuz", "date": months.date,
                         "value": (months >= pd.Timestamp(config.HORMUZ_START)).astype(float)})


def extract() -> SourceResult:
    frames = [hormuz_dummy()]
    meta = [SeriesMeta("dummy_hormuz", f"Hormuz closure dummy (1 from {config.HORMUZ_START})",
                       "constructed", "0/1", "M", "")]
    config.MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (sid, desc, unit) in config.MANUAL_SERIES.items():
        path = config.MANUAL_DIR / filename
        if not path.exists():
            print(f"  [manual] {filename} not found in {config.MANUAL_DIR} -> skipped")
            continue
        frames.append(parse_manual(path, sid))
        meta.append(SeriesMeta(sid, desc, "manual file", unit, "M", filename))
    return SourceResult(observations=filter_start(pd.concat(frames, ignore_index=True)), meta=meta)
