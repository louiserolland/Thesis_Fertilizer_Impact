"""
Offline tests: they feed small FAKE files (in the formats the providers use)
to the parsers, and check the database logic. No internet needed.

Run with:   python -m pytest -q
If a provider changes its file format, one of these tests (or the real run)
will tell you which parser to adapt.
"""
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config  # noqa: E402
import data_sources as ds  # noqa: E402
import database  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.duckdb")
    return tmp_path / "test.duckdb"


def test_fao_parser():
    csv = (
        "FAO Food Price Index\n\n"
        "Date,Food Price Index,Meat,Dairy,Cereals,Oils,Sugar\n"
        "1999-12,90,80,70,60,50,40\n"
        "2000-01,91.5,81,71,61,51,41\n"
        "2000-02,92,82,72,62,52,42\n"
        "Note: values are provisional,,,,,,\n"
    ).encode()
    out = ds.parse_fao(csv)
    assert set(out.series_id) == {"fao_ffpi", "fao_meat", "fao_dairy", "fao_cereals", "fao_vegoils", "fao_sugar"}
    assert out.date.min() == pd.Timestamp("2000-01-01").date()       # START_DATE filter works
    assert out.query("series_id=='fao_ffpi'").value.tolist() == [91.5, 92]


def test_worldbank_parser():
    rows = [
        ["World Bank Commodity Price Data (The Pink Sheet)"] + [None] * 4,
        ["Monthly prices in nominal US dollars"] + [None] * 4,
        [None, "Crude oil, Brent", "Natural gas, Europe", "Urea ", "Potassium chloride **"],
        [None, "($/bbl)", "($/mmbtu)", "($/mt)", "($/mt)"],
        ["2000M01", 25.5, 2.6, 100, ".."],
        ["2000M02", 27.0, 2.7, 105, 110],
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, sheet_name="Monthly Prices", header=False, index=False)
    out = ds.parse_worldbank(buf.getvalue())
    assert out.query("series_id=='wb_urea'").value.tolist() == [100, 105]
    assert out.query("series_id=='wb_potash'").value.tolist() == [110]     # '..' -> missing
    assert "wb_brent" in set(out.series_id)


def test_fred_parser():
    payload = {"observations": [{"date": "2000-01-01", "value": "1.5"}, {"date": "2000-02-01", "value": "."}]}
    out = ds.parse_fred(payload, "fred_igrea")
    assert len(out) == 1 and out.value.iloc[0] == 1.5


def test_enso_and_pdsi_parsers():
    oni = b" SEAS  YR   TOTAL   ANOM\n  DJF 2000  24.72  -1.53\n  NDJ 2000  25.00  -0.70\n"
    out = ds.parse_enso(oni, "noaa_oni")
    assert out.date.tolist() == [pd.Timestamp("2000-01-01").date(), pd.Timestamp("2000-12-01").date()]
    assert out.value.tolist() == [-1.53, -0.70]

    pdsi = b"Contiguous U.S. PDSI\nUnits: Index\nMissing: -99\nDate,Value\n200001,-1.2\n200002,-99.99\n"
    out = ds.parse_pdsi(pdsi)
    assert out.value.tolist() == [-1.2]


def test_gscpi_parser():
    rows = [["GSCPI data"], [None], ["Date", "GSCPI"], ["2000-01-31", 0.3], ["2000-02-29", -0.1]]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, sheet_name="GSCPI Monthly Data", header=False, index=False)
    out = ds.parse_gscpi(buf.getvalue())
    assert out.date.tolist() == [pd.Timestamp("2000-01-01").date(), pd.Timestamp("2000-02-01").date()]


def test_portwatch_parse_and_monthly():
    rows = [{"year": 2026, "month": 2, "day": d, "portid": "chokepoint6", "portname": "Strait of Hormuz",
             "n_total": 100 + d, "n_tanker": 50, "n_dry_bulk": 20} for d in (1, 2)]
    rows += [{"year": 2026, "month": 3, "day": 1, "portid": "chokepoint6", "portname": "Strait of Hormuz",
              "n_total": 4, "n_tanker": 1, "n_dry_bulk": 1}]
    daily = ds.parse_portwatch(rows)
    m = ds.portwatch_monthly(daily, "hormuz")
    feb = m.query("series_id=='pw_hormuz_total' and date==@pd.Timestamp('2026-02-01').date()").value.iloc[0]
    assert feb == 101.5


def test_hormuz_dummy():
    d = ds.hormuz_dummy().set_index("date").value
    assert d[pd.Timestamp("2026-02-01").date()] == 0 and d[pd.Timestamp("2026-03-01").date()] == 1


def test_upsert_is_idempotent_and_pivot(tmp_db):
    obs = pd.DataFrame({"series_id": ["a", "a", "b"],
                        "date": pd.to_datetime(["2000-01-01", "2000-02-01", "2000-01-01"]).date,
                        "value": [1.0, 2.0, 3.0]})
    with database.connect() as con:
        database.upsert(con, "observations", obs)
        database.upsert(con, "observations", obs.assign(value=[1.0, 2.5, 3.0]))   # revised value
        assert con.execute("SELECT count(*) FROM observations").fetchone()[0] == 3
        assert con.execute("SELECT value FROM observations WHERE series_id='a' AND date='2000-02-01'").fetchone()[0] == 2.5

    wide = database.monthly_panel()
    assert list(wide.columns) == ["a", "b"] and wide.loc["2000-02-01", "a"] == 2.5
