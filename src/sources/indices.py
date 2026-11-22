"""
Two research indices published as Excel files:

* Geopolitical Risk Index (GPR) — Caldara & Iacoviello (2022), "Measuring
  Geopolitical Risk", American Economic Review 112(4), 1194-1225.
  Newspaper-based, monthly. GPRT = threats, GPRA = acts. Useful as a control
  or to identify the "geopolitical" part of the 2026 shock.

* Global Supply Chain Pressure Index (GSCPI) — Benigno, di Giovanni, Groen &
  Noble (2022), NY Fed Staff Report 1017. Combines shipping costs (incl. the
  Baltic Dry Index) and PMI delivery-time components. A free, citable
  substitute/complement for the Baltic Dry Index.
"""
import io

import pandas as pd

import config
from sources._common import SeriesMeta, SourceResult, download, filter_start, long_frame, to_month_start


def parse_gpr(content: bytes) -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(content))          # .xls -> needs the 'xlrd' package
    date_col = next(c for c in df.columns if str(c).lower() in ("month", "date"))
    df[date_col] = to_month_start(df[date_col])
    return long_frame(df, date_col, config.GPR_COLUMNS)


def parse_gscpi(content: bytes) -> pd.DataFrame:
    book = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    sheet = next((s for name, s in book.items() if "monthly" in name.lower()), next(iter(book.values())))
    # find the header row that contains 'GSCPI'
    hdr = next(i for i in range(min(15, len(sheet)))
               if any(str(v).strip().upper() == "GSCPI" for v in sheet.iloc[i]))
    df = sheet.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in sheet.iloc[hdr]]
    date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    df[date_col] = to_month_start(df[date_col])
    return long_frame(df, date_col, {"GSCPI": "nyfed_gscpi"})


def extract_gpr() -> SourceResult:
    obs = filter_start(parse_gpr(download(config.GPR_URL, "gpr", "data_gpr_export.xls")))
    names = {"gpr_global": "Geopolitical Risk Index", "gpr_threats": "GPR - threats", "gpr_acts": "GPR - acts"}
    meta = [SeriesMeta(sid, names[sid], "Caldara & Iacoviello", "index 1985-2019=100", "M", col)
            for col, sid in config.GPR_COLUMNS.items() if sid in set(obs["series_id"])]
    return SourceResult(observations=obs, meta=meta)


def extract_gscpi() -> SourceResult:
    obs = filter_start(parse_gscpi(download(config.GSCPI_URL, "gscpi", "gscpi_data.xlsx")))
    meta = [SeriesMeta("nyfed_gscpi", "Global Supply Chain Pressure Index", "NY Fed", "std. dev. from mean", "M", "GSCPI")]
    return SourceResult(observations=obs, meta=meta)
