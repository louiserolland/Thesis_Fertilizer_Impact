"""
Weather controls from NOAA.

1) ENSO (El Niño / La Niña): the single most important global climate driver
   of crop yields — see Iizumi et al. (2014), "Impacts of El Niño Southern
   Oscillation on the global yields of major crops", Nature Communications 5:3712.
   * ONI  = classic Oceanic Niño Index
   * RONI = "relative" ONI. NOAA's Climate Prediction Center adopted RONI as
     its main ENSO index in February 2026 (it removes the warming trend of
     the tropical ocean). We store both; use RONI as baseline, ONI as robustness.
   Both are 3-month running means labelled by season ("DJF" = Dec-Jan-Feb):
   we date each value on the CENTRE month (DJF -> January).

2) US Palmer Drought Severity Index (contiguous US) — a drought control for the
   main maize/soy/wheat exporter. It is US-only: for other regions see README.
"""
import io

import pandas as pd

import config
from sources._common import SeriesMeta, SourceResult, download, filter_start

SEASON_CENTRE = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
                 "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}


def parse_enso(content: bytes, series_id: str) -> pd.DataFrame:
    rows = []
    for line in content.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0].upper() not in SEASON_CENTRE:
            continue                                   # skips the header line
        try:
            year, anom = int(parts[1]), float(parts[-1])   # anomaly is the last column
        except ValueError:
            continue
        rows.append({"series_id": series_id,
                     "date": pd.Timestamp(year, SEASON_CENTRE[parts[0].upper()], 1).date(),
                     "value": anom})
    return pd.DataFrame(rows)


def parse_pdsi(content: bytes) -> pd.DataFrame:
    lines = content.decode("utf-8-sig", errors="replace").splitlines()
    start = next(i for i, l in enumerate(lines) if l.lower().startswith("date"))
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype={"Date": str})
    df["date"] = pd.to_datetime(df["Date"].str[:6], format="%Y%m").dt.date
    df["value"] = pd.to_numeric(df["Value"], errors="coerce").where(lambda v: v > -99)
    df["series_id"] = "noaa_pdsi_us"
    return df.dropna(subset=["value"])[["series_id", "date", "value"]]


def extract() -> SourceResult:
    frames = [
        parse_enso(download(config.NOAA_ONI_URL, "noaa", "oni.ascii.txt"), "noaa_oni"),
        parse_enso(download(config.NOAA_RONI_URL, "noaa", "RONI.ascii.txt"), "noaa_roni"),
    ]
    meta = [SeriesMeta("noaa_oni", "Oceanic Nino Index (3-month mean SST anomaly Nino3.4)", "NOAA CPC", "deg C", "M", "ONI"),
            SeriesMeta("noaa_roni", "Relative Oceanic Nino Index", "NOAA CPC", "deg C", "M", "RONI")]
    try:
        frames.append(parse_pdsi(download(config.NOAA_PDSI_URL, "noaa", "pdsi_us.csv")))
        meta.append(SeriesMeta("noaa_pdsi_us", "Palmer Drought Severity Index, contiguous US", "NOAA NCEI", "index", "M", "110/pdsi"))
    except Exception as e:   # PDSI URL is the one I could not test — don't let it block ENSO
        print(f"  [noaa] PDSI skipped ({e}). Check NOAA_PDSI_URL in config.py")
    return SourceResult(observations=filter_start(pd.concat(frames, ignore_index=True)), meta=meta)
