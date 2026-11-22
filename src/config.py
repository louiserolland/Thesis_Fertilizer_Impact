"""
config.py — every setting of the data pipeline lives here.

Rule of thumb: if you ever need to change a URL, a date or add a series,
you should only have to touch THIS file, never the extraction code.
"""
from pathlib import Path
import os

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# config.py lives in src/, the repository root is one level up
PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"                          # every downloaded file is archived here (vintages)
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = PROCESSED_DIR / "thesis.duckdb"           # the local database (rebuildable -> not in Git)

# API keys are read from a .env file (never commit that file to Git!)
load_dotenv(PROJECT_DIR / ".env")
FRED_API_KEY = os.getenv("FRED_API_KEY")          # free: https://fred.stlouisfed.org/docs/api/api_key.html
COMTRADE_API_KEY = os.getenv("COMTRADE_API_KEY")  # free "comtrade - v1" product: https://comtradedeveloper.un.org

# ---------------------------------------------------------------------------
# Sample period (proposal: monthly, Jan 2000 → latest available)
# ---------------------------------------------------------------------------
START_DATE = "2000-01-01"

# Hormuz closure: strikes + IRGC warning on 28 Feb 2026 → first FULL month closed = March 2026.
# This is a modelling choice you should justify in the thesis (Feb vs Mar).
HORMUZ_START = "2026-03-01"

# ---------------------------------------------------------------------------
# FAO Food Price Index (dependent variables)
# The page https://www.fao.org/worldfoodsituation/foodpricesindex/en/ links the files.
# The "?sfvrsn=..." part of the link changes every month, so we use the base path.
# If a download fails: open the page, right-click "CSV" and paste the new link here.
# ---------------------------------------------------------------------------
FAO_CSV_URL = (
    "https://www.fao.org/media/docs/worldfoodsituationlibraries/"
    "wfs-library/food_price_indices_data.csv"
)
# column name in the FAO file  ->  our series_id
FAO_SERIES = {
    "Food Price Index": "fao_ffpi",
    "Cereals": "fao_cereals",
    "Oils": "fao_vegoils",          # called "Oils" / "Vegetable Oils" depending on the vintage
    "Vegetable Oils": "fao_vegoils",
    "Dairy": "fao_dairy",
    "Meat": "fao_meat",
    "Sugar": "fao_sugar",
}

# ---------------------------------------------------------------------------
# World Bank Pink Sheet (fertilizers, energy, grains)
# The document id (74e8be...-0050012026) changes every YEAR. In 2027 look it up on
# https://www.worldbank.org/en/research/commodity-markets and update the URL.
# ---------------------------------------------------------------------------
WB_MONTHLY_URL = (
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)
# Header text in the "Monthly Prices" sheet (lower-case, without '*' and spaces at the end)
#   -> (series_id, unit)
WB_SERIES = {
    # fertilizers (explanatory variables)
    "urea": ("wb_urea", "$/mt"),
    "dap": ("wb_dap", "$/mt"),
    "tsp": ("wb_tsp", "$/mt"),
    "phosphate rock": ("wb_phosrock", "$/mt"),
    "potassium chloride": ("wb_potash", "$/mt"),
    # energy (controls)
    "crude oil, brent": ("wb_brent", "$/bbl"),
    "crude oil, wti": ("wb_wti", "$/bbl"),
    "natural gas, us": ("wb_ngas_us", "$/mmbtu"),
    "natural gas, europe": ("wb_ngas_eu", "$/mmbtu"),
    "liquefied natural gas, japan": ("wb_lng_japan", "$/mmbtu"),
    # individual crops (useful for the "which commodity reacts most" question)
    "maize": ("wb_maize", "$/mt"),
    "wheat, us hrw": ("wb_wheat_hrw", "$/mt"),
    "rice, thai 5%": ("wb_rice", "$/mt"),
    "soybeans": ("wb_soybeans", "$/mt"),   # legume: needs little N fertilizer -> placebo!
    "soybean oil": ("wb_soyoil", "$/mt"),
    "palm oil": ("wb_palmoil", "$/mt"),
    "sugar, world": ("wb_sugar", "$/kg"),
}

# ---------------------------------------------------------------------------
# FRED (St. Louis Fed) — needs FRED_API_KEY
# ---------------------------------------------------------------------------
FRED_SERIES = {
    # series_id_in_FRED: (our_series_id, description, unit)
    "IGREA": ("fred_igrea", "Kilian index of global real economic activity (Dallas Fed)", "% dev. from trend"),
    "CPIAUCSL": ("fred_us_cpi", "US CPI all items (to deflate to real prices)", "index 1982-84=100"),
    "RBUSBIS": ("fred_usd_reer", "Real broad effective exchange rate USA (BIS)", "index 2020=100"),
}

# ---------------------------------------------------------------------------
# Weather / climate
# ---------------------------------------------------------------------------
NOAA_ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
NOAA_RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
# US drought (PDSI, contiguous US = location 110) from NOAA "Climate at a Glance".
# URL pattern documented by NCEI: .../{scope}/time-series/{location}/{parameter}/{surface}/{timescale}/{month}/{beg}-{end}/data.csv
# !! I could not test this exact URL from my side. If it returns 404, open
# https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/national/time-series/110/pdsi
# choose the options, and copy the CSV download link here.
NOAA_PDSI_URL = (
    "https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/"
    "national/time-series/110/pdsi/1/0/2000-2026/data.csv"
)

# ---------------------------------------------------------------------------
# Risk / supply-chain indices
# ---------------------------------------------------------------------------
GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls"
GPR_COLUMNS = {"GPR": "gpr_global", "GPRT": "gpr_threats", "GPRA": "gpr_acts"}

GSCPI_URL = "https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx"

# ---------------------------------------------------------------------------
# IMF PortWatch — daily transits through chokepoints (ArcGIS REST API, no key)
# ---------------------------------------------------------------------------
PORTWATCH_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
# keyword used in a  portname LIKE '%keyword%'  filter  ->  short slug for our series ids
PORTWATCH_CHOKEPOINTS = {"Hormuz": "hormuz", "Bab el-Mandeb": "babelmandeb", "Suez": "suez"}

# ---------------------------------------------------------------------------
# UN Comtrade — monthly physical fertilizer trade (needs COMTRADE_API_KEY)
# Strategy: Gulf exporters report late or not at all, so we ALSO pull "mirror"
# data: what big importers declare as imports FROM the Gulf.
# ---------------------------------------------------------------------------
COMTRADE_URL = "https://comtradeapi.un.org/data/v1/get/C/M/HS"
COMTRADE_HS = {
    "310210": "Urea",
    "281410": "Anhydrous ammonia",
    "310530": "Diammonium phosphate (DAP)",
    "310540": "Monoammonium phosphate (MAP)",
}
# Comtrade numeric (M49-based) codes
GULF = {682: "Saudi Arabia", 634: "Qatar", 784: "United Arab Emirates", 512: "Oman",
        48: "Bahrain", 414: "Kuwait", 364: "Iran"}
BIG_IMPORTERS = {699: "India", 76: "Brazil", 842: "USA", 36: "Australia", 764: "Thailand"}
COMTRADE_START_YEAR = 2015   # monthly Comtrade data before ~2010 is patchy anyway

# ---------------------------------------------------------------------------
# Manual files (data that has no free API, e.g. Baltic Dry Index from Bloomberg/LSEG)
# Put CSVs with two columns  date,value  in data/manual/  and list them here.
# data/manual/ is git-ignored: terminal data (Bloomberg/LSEG) may not be redistributed.
# ---------------------------------------------------------------------------
MANUAL_DIR = DATA_DIR / "manual"
MANUAL_SERIES = {
    # filename        : (series_id, description, unit)
    "bdi.csv": ("bdi", "Baltic Dry Index (monthly average)", "index"),
}
