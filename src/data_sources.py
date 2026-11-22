"""
data_sources.py — downloading and parsing every data source of the thesis.

Structure of this file (search for "====" to jump between sections):
    0. Shared helpers      (HTTP with retries, raw-file archive, small data classes)
    1. FAO                 Food Price Index + 5 sub-indices        (dependent variables)
    2. World Bank          Pink Sheet: fertilizers, energy, crops   (explanatory + controls)
    3. FRED                Kilian activity index, US CPI, USD REER  (controls)
    4. NOAA                ENSO (ONI, RONI), US drought (PDSI)       (weather controls)
    5. Research indices    Geopolitical Risk, NY Fed GSCPI          (controls)
    6. IMF PortWatch       daily ship transits through Hormuz etc.  (shock intensity)
    7. UN Comtrade         monthly fertilizer trade flows           (physical supply)
    8. Manual + dummy      Baltic Dry Index file, Hormuz dummy
    9. SOURCES registry    name -> extract function (used by extract.py)

Every source follows the same pattern:
    parse_xxx(raw_bytes)  -> pure function, turns a file into a tidy DataFrame (tested offline)
    extract_xxx()         -> downloads, calls the parser, returns a SourceResult
Keeping "download" and "parse" apart is what makes the parsers testable without internet.

All settings (URLs, series lists, countries, dates) live in config.py.
"""
import io
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config


# ============================================================================
# 0. SHARED HELPERS
# ============================================================================
HEADERS = {"User-Agent": "Mozilla/5.0 (thesis data pipeline; University of St. Gallen)"}
# (some sites, e.g. FAO and the World Bank, block the default python User-Agent)


def session() -> requests.Session:
    """HTTP session that retries automatically (servers sometimes fail once)."""
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s


def archive(content: bytes, source: str, filename: str):
    """
    Save a dated copy of every downloaded file in data/raw/<source>/.
    Providers REVISE their data: this is your proof of which vintage you used.
    """
    folder = config.RAW_DIR / source
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date.today():%Y-%m-%d}_{filename}").write_bytes(content)


def download(url, source, filename, params=None, headers=None, timeout=120) -> bytes:
    r = session().get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    archive(r.content, source, filename)
    return r.content


def to_month_start(s: pd.Series) -> pd.Series:
    """Any date -> 1st day of its month (our monthly convention)."""
    return pd.to_datetime(s).dt.to_period("M").dt.to_timestamp().dt.date


def long_frame(df_wide: pd.DataFrame, date_col: str, mapping: dict) -> pd.DataFrame:
    """Wide table (one column per variable) -> long table (series_id, date, value)."""
    keep = {c: sid for c, sid in mapping.items() if c in df_wide.columns}
    out = df_wide[[date_col, *keep]].rename(columns={date_col: "date", **keep})
    out = out.melt(id_vars="date", var_name="series_id", value_name="value")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out.dropna(subset=["value"])[["series_id", "date", "value"]]


def filter_start(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["date"] >= pd.Timestamp(config.START_DATE).date()].reset_index(drop=True)


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
    """What every extract_xxx() returns; extract.py writes it to DuckDB."""
    observations: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["series_id", "date", "value"]))
    meta: list = field(default_factory=list)
    tables: dict = field(default_factory=dict)      # extra tables, e.g. {"trade_flows": df}

    def meta_frame(self) -> pd.DataFrame:
        return pd.DataFrame([m.__dict__ for m in self.meta])


# ============================================================================
# 1. FAO FOOD PRICE INDEX  (2014-2016 = 100)
# The CSV starts with title lines, so we search for the line starting with
# "Date" instead of hard-coding "skip 2 rows" -> survives small format changes.
# ============================================================================
FAO_NAMES = {"fao_ffpi": "FAO Food Price Index", "fao_cereals": "FAO Cereals Price Index",
             "fao_vegoils": "FAO Vegetable Oils Price Index", "fao_dairy": "FAO Dairy Price Index",
             "fao_meat": "FAO Meat Price Index", "fao_sugar": "FAO Sugar Price Index"}


def parse_fao(content: bytes) -> pd.DataFrame:
    lines = content.decode("utf-8-sig", errors="replace").splitlines()
    header = next(i for i, l in enumerate(lines) if l.strip().strip('"').lower().startswith("date"))
    df = pd.read_csv(io.StringIO("\n".join(lines[header:])))
    df.columns = [str(c).strip() for c in df.columns]
    s = df["Date"].astype(str).str.strip()
    # FAO has used "1990-01", "01/1990" and "1990-01-01" in different vintages
    if s.str.match(r"^\d{1,2}/\d{4}$").mean() > 0.5:
        df["Date"] = pd.to_datetime(s, format="%m/%Y", errors="coerce")
    elif s.str.match(r"^\d{4}-\d{2}$").mean() > 0.5:
        df["Date"] = pd.to_datetime(s, format="%Y-%m", errors="coerce")
    else:
        df["Date"] = pd.to_datetime(s, errors="coerce")
    df = df.dropna(subset=["Date"])                  # drops footnote lines
    df["Date"] = to_month_start(df["Date"])
    return filter_start(long_frame(df, "Date", config.FAO_SERIES))


def extract_fao() -> SourceResult:
    obs = parse_fao(download(config.FAO_CSV_URL, "fao", "food_price_indices_data.csv"))
    meta = [SeriesMeta(sid, FAO_NAMES[sid], "FAO", "index 2014-16=100", "M", sid)
            for sid in sorted(obs["series_id"].unique())]
    return SourceResult(observations=obs, meta=meta)


# ============================================================================
# 2. WORLD BANK PINK SHEET  (sheet "Monthly Prices")
# Layout: title rows / row of commodity names / row of units / data "1960M01".
# We SEARCH for the name row and the "YYYYMmm" rows instead of fixed row numbers.
# Thesis note: check the PDF footnotes for the urea quote (Black Sea vs Middle
# East) and mention any series break in your data section.
# ============================================================================
def _norm(x) -> str:
    """'Urea  **' -> 'urea'"""
    return re.sub(r"[\s\*]+$", "", str(x).replace("\xa0", " ")).strip().lower()


def parse_worldbank(content: bytes) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(content), sheet_name="Monthly Prices", header=None)
    name_row = next(i for i in range(min(20, len(raw)))
                    if any(_norm(v) == "crude oil, brent" for v in raw.iloc[i]))
    names = [_norm(v) for v in raw.iloc[name_row]]
    first = raw.iloc[:, 0].astype(str).str.strip()
    data = raw[first.str.match(r"^\d{4}M\d{2}$")]
    dates = pd.to_datetime(first[data.index].str.replace("M", "-"), format="%Y-%m").dt.date

    frames, missing = [], []
    for key, (sid, _unit) in config.WB_SERIES.items():
        col = next((j for j, n in enumerate(names) if n == key), None)          # exact match
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
    return filter_start(pd.concat(frames, ignore_index=True).dropna(subset=["value"]))


def extract_worldbank() -> SourceResult:
    obs = parse_worldbank(download(config.WB_MONTHLY_URL, "worldbank", "CMO-Historical-Data-Monthly.xlsx"))
    meta = [SeriesMeta(sid, key, "World Bank Pink Sheet", unit, "M", key)
            for key, (sid, unit) in config.WB_SERIES.items() if sid in set(obs["series_id"])]
    return SourceResult(observations=obs, meta=meta)


# ============================================================================
# 3. FRED API  — the classic REST pattern:  GET endpoint?params&api_key -> JSON
# Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
# ============================================================================
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def parse_fred(payload: dict, series_id: str) -> pd.DataFrame:
    df = pd.DataFrame(payload["observations"])[["date", "value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")     # FRED writes "." for missing
    df["date"] = to_month_start(df["date"])
    df["series_id"] = series_id
    return df.dropna(subset=["value"])[["series_id", "date", "value"]]


def extract_fred() -> SourceResult:
    if not config.FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY missing in .env (free key: fred.stlouisfed.org/docs/api/api_key.html)")
    s, frames, meta = session(), [], []
    for fred_id, (sid, desc, unit) in config.FRED_SERIES.items():
        r = s.get(FRED_URL, timeout=60, params={
            "series_id": fred_id, "api_key": config.FRED_API_KEY, "file_type": "json",
            "observation_start": config.START_DATE, "frequency": "m"})
        r.raise_for_status()
        archive(r.content, "fred", f"{fred_id}.json")
        frames.append(parse_fred(r.json(), sid))
        meta.append(SeriesMeta(sid, desc, "FRED", unit, "M", fred_id))
    return SourceResult(observations=filter_start(pd.concat(frames, ignore_index=True)), meta=meta)


# ============================================================================
# 4. NOAA — weather controls
# * ENSO (El Niño/La Niña): main global climate driver of crop yields
#   (Iizumi et al. 2014, Nature Communications 5:3712). ONI = classic index;
#   RONI = "relative" ONI, NOAA CPC's main index since Feb 2026. Both are
#   3-month means labelled by season ("DJF") -> dated on the centre month.
# * US Palmer Drought Severity Index (contiguous US) — US-only drought control.
# ============================================================================
SEASON_CENTRE = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
                 "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}


def parse_enso(content: bytes, series_id: str) -> pd.DataFrame:
    rows = []
    for line in content.decode("utf-8", errors="replace").splitlines():
        p = line.split()
        if len(p) < 3 or p[0].upper() not in SEASON_CENTRE:
            continue                                            # header line
        try:
            year, anom = int(p[1]), float(p[-1])                # anomaly = last column
        except ValueError:
            continue
        rows.append({"series_id": series_id, "value": anom,
                     "date": pd.Timestamp(year, SEASON_CENTRE[p[0].upper()], 1).date()})
    return pd.DataFrame(rows)


def parse_pdsi(content: bytes) -> pd.DataFrame:
    lines = content.decode("utf-8-sig", errors="replace").splitlines()
    start = next(i for i, l in enumerate(lines) if l.lower().startswith("date"))
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype={"Date": str})
    df["date"] = pd.to_datetime(df["Date"].str[:6], format="%Y%m").dt.date
    df["value"] = pd.to_numeric(df["Value"], errors="coerce").where(lambda v: v > -99)  # -99 = missing
    df["series_id"] = "noaa_pdsi_us"
    return df.dropna(subset=["value"])[["series_id", "date", "value"]]


def extract_noaa() -> SourceResult:
    frames = [parse_enso(download(config.NOAA_ONI_URL, "noaa", "oni.ascii.txt"), "noaa_oni"),
              parse_enso(download(config.NOAA_RONI_URL, "noaa", "RONI.ascii.txt"), "noaa_roni")]
    meta = [SeriesMeta("noaa_oni", "Oceanic Nino Index", "NOAA CPC", "deg C", "M", "ONI"),
            SeriesMeta("noaa_roni", "Relative Oceanic Nino Index", "NOAA CPC", "deg C", "M", "RONI")]
    try:   # the PDSI URL is the one I could not test -> never let it block ENSO
        frames.append(parse_pdsi(download(config.NOAA_PDSI_URL, "noaa", "pdsi_us.csv")))
        meta.append(SeriesMeta("noaa_pdsi_us", "Palmer Drought Severity Index, contiguous US",
                               "NOAA NCEI", "index", "M", "110/pdsi"))
    except Exception as e:
        print(f"  [noaa] PDSI skipped ({e}). Check NOAA_PDSI_URL in config.py")
    return SourceResult(observations=filter_start(pd.concat(frames, ignore_index=True)), meta=meta)


# ============================================================================
# 5. RESEARCH INDICES (Excel files)
# * GPR — Caldara & Iacoviello (2022), AER 112(4). GPRT = threats, GPRA = acts.
# * GSCPI — Benigno et al. (2022), NY Fed Staff Report 1017. Shipping costs
#   (incl. Baltic Dry Index) + PMI delivery times: free proxy for the BDI.
# ============================================================================
def parse_gpr(content: bytes) -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(content))                    # .xls -> needs 'xlrd'
    date_col = next(c for c in df.columns if str(c).lower() in ("month", "date"))
    df[date_col] = to_month_start(df[date_col])
    return long_frame(df, date_col, config.GPR_COLUMNS)


def parse_gscpi(content: bytes) -> pd.DataFrame:
    book = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    sheet = next((s for n, s in book.items() if "monthly" in n.lower()), next(iter(book.values())))
    hdr = next(i for i in range(min(15, len(sheet)))
               if any(str(v).strip().upper() == "GSCPI" for v in sheet.iloc[i]))
    df = sheet.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in sheet.iloc[hdr]]
    d = df.columns[0]
    df[d] = pd.to_datetime(df[d], errors="coerce")
    df = df.dropna(subset=[d])
    df[d] = to_month_start(df[d])
    return long_frame(df, d, {"GSCPI": "nyfed_gscpi"})


def extract_gpr() -> SourceResult:
    obs = filter_start(parse_gpr(download(config.GPR_URL, "gpr", "data_gpr_export.xls")))
    names = {"gpr_global": "Geopolitical Risk Index", "gpr_threats": "GPR - threats", "gpr_acts": "GPR - acts"}
    meta = [SeriesMeta(sid, names[sid], "Caldara & Iacoviello", "index 1985-2019=100", "M", col)
            for col, sid in config.GPR_COLUMNS.items() if sid in set(obs["series_id"])]
    return SourceResult(observations=obs, meta=meta)


def extract_gscpi() -> SourceResult:
    obs = filter_start(parse_gscpi(download(config.GSCPI_URL, "gscpi", "gscpi_data.xlsx")))
    return SourceResult(observations=obs, meta=[SeriesMeta(
        "nyfed_gscpi", "Global Supply Chain Pressure Index", "NY Fed", "std. dev.", "M", "GSCPI")])


# ============================================================================
# 6. IMF PORTWATCH — daily chokepoint transits (satellite AIS), ArcGIS REST API
# A CONTINUOUS measure of how closed Hormuz is, instead of a 0/1 dummy
# (used for Hormuz in BIS Working Paper 1378).
# Urea/DAP travel on dry-bulk ships; ammonia on gas carriers, probably counted
# as "tanker" — check the PortWatch methodology before interpreting.
# ArcGIS returns max ~1000 rows per request -> we PAGINATE with resultOffset.
# ============================================================================
PW_FIELDS = ["n_total", "n_tanker", "n_dry_bulk", "n_container", "n_general_cargo",
             "capacity", "capacity_tanker", "capacity_dry_bulk"]


def fetch_portwatch(keyword: str) -> list:
    s, rows, offset = session(), [], 0
    while True:
        r = s.get(config.PORTWATCH_URL, timeout=120, params={
            "where": f"portname LIKE '%{keyword}%'", "outFields": "*", "f": "json",
            "orderByFields": "date", "resultOffset": offset, "resultRecordCount": 1000})
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


def parse_portwatch(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if {"year", "month", "day"} <= set(df.columns):
        df["date"] = pd.to_datetime(df[["year", "month", "day"]])
    elif pd.api.types.is_numeric_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"], unit="ms")     # ArcGIS epoch milliseconds
    else:
        df["date"] = pd.to_datetime(df["date"])
    df["date"] = df["date"].dt.date
    for c in PW_FIELDS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    return df[["date", "portid", "portname", *PW_FIELDS]].drop_duplicates(["date", "portid"])


def portwatch_monthly(daily: pd.DataFrame, slug: str) -> pd.DataFrame:
    """Daily -> monthly AVERAGE transits per day (the current month is partial!)."""
    d = daily.assign(date=to_month_start(daily["date"]))
    m = d.groupby("date")[["n_total", "n_tanker", "n_dry_bulk"]].mean().reset_index()
    m.columns = ["date", f"pw_{slug}_total", f"pw_{slug}_tanker", f"pw_{slug}_drybulk"]
    return m.melt(id_vars="date", var_name="series_id", value_name="value").dropna()


def extract_portwatch() -> SourceResult:
    dailies, monthlies, meta = [], [], []
    for keyword, slug in config.PORTWATCH_CHOKEPOINTS.items():
        daily = parse_portwatch(fetch_portwatch(keyword))
        dailies.append(daily)
        monthlies.append(portwatch_monthly(daily, slug))
        meta += [SeriesMeta(f"pw_{slug}_{k}", f"{keyword}: avg daily transits ({k})",
                            "IMF PortWatch", "ships/day", "M", keyword) for k in ("total", "tanker", "drybulk")]
    return SourceResult(observations=pd.concat(monthlies, ignore_index=True)[["series_id", "date", "value"]],
                        meta=meta, tables={"chokepoint_daily": pd.concat(dailies, ignore_index=True)})


# ============================================================================
# 7. UN COMTRADE — monthly fertilizer trade (tonnes, USD)
# A) DIRECT: Gulf countries' exports to the World
# B) MIRROR: big importers' imports FROM the Gulf (Gulf reporting is late/patchy)
# LIMITATION: monthly data arrives months late -> 2026 will have gaps. Use
# PortWatch for the timing, Comtrade for the pre-2026 dependence structure.
# Without a key we fall back to the 500-row public preview (testing only).
# ============================================================================
COMTRADE_PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"


def _comtrade_call(params: dict) -> list:
    if config.COMTRADE_API_KEY:
        r = session().get(config.COMTRADE_URL, params=params, timeout=180,
                          headers={"Ocp-Apim-Subscription-Key": config.COMTRADE_API_KEY})
    else:
        r = session().get(COMTRADE_PREVIEW_URL, params=params, timeout=180)
    r.raise_for_status()
    return r.json().get("data", []) or []


def parse_comtrade(records: list) -> pd.DataFrame:
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


def extract_comtrade() -> SourceResult:
    if not config.COMTRADE_API_KEY:
        print("  [comtrade] no COMTRADE_API_KEY -> 500-row preview endpoint (test only)")
    codes = lambda d: ",".join(str(k) for k in d)
    common = {"cmdCode": codes(config.COMTRADE_HS), "partner2Code": 0, "motCode": 0,
              "customsCode": "C00", "includeDesc": True}
    records = []
    for year in range(config.COMTRADE_START_YEAR, date.today().year + 1):
        periods = ",".join(f"{year}{m:02d}" for m in range(1, 13))       # max 12 periods per call
        queries = {
            "direct": {**common, "reporterCode": codes(config.GULF), "partnerCode": 0,
                       "flowCode": "X", "period": periods},
            "mirror": {**common, "reporterCode": codes(config.BIG_IMPORTERS),
                       "partnerCode": codes(config.GULF), "flowCode": "M", "period": periods},
        }
        for name, params in queries.items():
            data = _comtrade_call(params)
            archive(json.dumps(data).encode(), "comtrade", f"{name}_{year}.json")
            print(f"  [comtrade] {year} {name}: {len(data)} rows")
            records += data
            time.sleep(1.5)                                   # free tier is rate-limited
    return SourceResult(tables={"trade_flows": parse_comtrade(records)})


# ============================================================================
# 8. MANUAL FILES + CONSTRUCTED VARIABLES
# * Data without a free API (Baltic Dry Index: Bloomberg 'BDIY Index'):
#   CSV 'date,value' in data/manual/ (daily is fine -> averaged to monthly).
# * Hormuz dummy D_t (start date in config.HORMUZ_START).
# ============================================================================
def parse_manual(path, series_id) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = to_month_start(pd.to_datetime(df["date"], errors="coerce"))
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    m = df.dropna().groupby("date", as_index=False)["value"].mean()
    return m.assign(series_id=series_id)[["series_id", "date", "value"]]


def hormuz_dummy() -> pd.DataFrame:
    months = pd.date_range(config.START_DATE, pd.Timestamp.today(), freq="MS")
    return pd.DataFrame({"series_id": "dummy_hormuz", "date": months.date,
                         "value": (months >= pd.Timestamp(config.HORMUZ_START)).astype(float)})


def extract_manual() -> SourceResult:
    frames = [hormuz_dummy()]
    meta = [SeriesMeta("dummy_hormuz", f"Hormuz closure dummy (1 from {config.HORMUZ_START})",
                       "constructed", "0/1", "M")]
    config.MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (sid, desc, unit) in config.MANUAL_SERIES.items():
        path = config.MANUAL_DIR / filename
        if not path.exists():
            print(f"  [manual] {filename} not found in {config.MANUAL_DIR} -> skipped")
            continue
        frames.append(parse_manual(path, sid))
        meta.append(SeriesMeta(sid, desc, "manual file", unit, "M", filename))
    return SourceResult(observations=filter_start(pd.concat(frames, ignore_index=True)), meta=meta)


# ============================================================================
# 9. REGISTRY — to add a source: write parse_xxx + extract_xxx above, add one line here
# ============================================================================
SOURCES = {
    "fao": extract_fao,
    "worldbank": extract_worldbank,
    "fred": extract_fred,
    "noaa": extract_noaa,
    "gpr": extract_gpr,
    "gscpi": extract_gscpi,
    "portwatch": extract_portwatch,
    "comtrade": extract_comtrade,
    "manual": extract_manual,
}
