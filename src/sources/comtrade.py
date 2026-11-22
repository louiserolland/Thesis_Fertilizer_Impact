"""
UN Comtrade — monthly physical trade of fertilizers (tonnes & USD).
API docs: https://comtradedeveloper.un.org   (free key: product "comtrade - v1")

Two queries per year:
  A) DIRECT  : Gulf countries report their EXPORTS to the World
  B) MIRROR  : big importers report their IMPORTS FROM the Gulf
Why both? Gulf exporters often report late or not at all (check Iran in
particular), whereas India, Brazil, the US etc. report faster. Comparing the
two ("mirror statistics") is standard practice in trade economics.

HONEST LIMITATION: Comtrade monthly data arrives with a lag of several
months. For March–November 2026 expect many gaps when you run this. Use
PortWatch for the timing of the shock, and Comtrade for the pre-2026 structure
(who depends on Gulf fertilizer, how much).

Without an API key the code falls back to the public preview endpoint,
which returns at most 500 rows per call: enough for testing, not for the thesis.
"""
import json
import time
from datetime import date

import pandas as pd

import config
from sources._common import SourceResult, archive, session

PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"


def _call(params: dict) -> list:
    s = session()
    if config.COMTRADE_API_KEY:
        r = s.get(config.COMTRADE_URL, params=params, timeout=180,
                  headers={"Ocp-Apim-Subscription-Key": config.COMTRADE_API_KEY})
    else:
        r = s.get(PREVIEW_URL, params=params, timeout=180)
    r.raise_for_status()
    return r.json().get("data", []) or []


def _codes(d: dict) -> str:
    return ",".join(str(k) for k in d)


def parse(records: list) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    names = {**config.GULF, **config.BIG_IMPORTERS, 0: "World"}
    out = pd.DataFrame({
        "period": pd.to_datetime(df["period"].astype(str), format="%Y%m").dt.date,
        "reporter_code": df["reporterCode"].astype(int),
        "partner_code": df["partnerCode"].astype(int),
        "flow": df["flowCode"].astype(str),
        "hs_code": df["cmdCode"].astype(str),
        "net_weight_kg": pd.to_numeric(df.get("netWgt"), errors="coerce"),
        "value_usd": pd.to_numeric(df.get("primaryValue"), errors="coerce"),
    })
    out["reporter"] = df.get("reporterDesc", pd.Series(dtype=str)).fillna(out["reporter_code"].map(names))
    out["partner"] = df.get("partnerDesc", pd.Series(dtype=str)).fillna(out["partner_code"].map(names))
    out["hs_desc"] = out["hs_code"].map(config.COMTRADE_HS)
    return out.drop_duplicates(["period", "reporter_code", "partner_code", "flow", "hs_code"])


def extract() -> SourceResult:
    if not config.COMTRADE_API_KEY:
        print("  [comtrade] no COMTRADE_API_KEY -> using the 500-row preview endpoint (test only)")
    common = {"cmdCode": _codes(config.COMTRADE_HS), "partner2Code": 0, "motCode": 0,
              "customsCode": "C00", "includeDesc": True}
    records = []
    for year in range(config.COMTRADE_START_YEAR, date.today().year + 1):
        periods = ",".join(f"{year}{m:02d}" for m in range(1, 13))   # max 12 periods per call
        queries = {
            "direct": {**common, "reporterCode": _codes(config.GULF), "partnerCode": 0,
                       "flowCode": "X", "period": periods},
            "mirror": {**common, "reporterCode": _codes(config.BIG_IMPORTERS),
                       "partnerCode": _codes(config.GULF), "flowCode": "M", "period": periods},
        }
        for name, params in queries.items():
            data = _call(params)
            archive(json.dumps(data).encode(), "comtrade", f"{name}_{year}.json")
            print(f"  [comtrade] {year} {name}: {len(data)} rows")
            records += data
            time.sleep(1.5)        # be polite: the free tier is rate-limited
    return SourceResult(tables={"trade_flows": parse(records)})
