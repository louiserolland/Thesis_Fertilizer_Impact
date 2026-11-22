"""
Offline tests: they feed small FAKE files (in the formats the providers use)
to the parsers, and check the database logic. No internet needed.

Run with:   python -m pytest -q
If a provider changes its file format, one of these tests (or the real run)
will tell you which parser to adapt.
"""
import io
import zipfile
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
    rows = [["GSCPI data", None], [None, None], ["Date", "GSCPI"], ["2000-01-31", 0.3], ["2000-02-29", -0.1]]
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


def test_upsert_handles_duplicates_in_one_batch(tmp_db):
    obs = pd.DataFrame({"series_id": ["a", "a"], "date": pd.to_datetime(["2000-01-01"] * 2).date,
                        "value": [1.0, 9.0]})
    with database.connect() as con:
        database.upsert(con, "observations", obs)
        assert con.execute("SELECT value FROM observations").fetchall() == [(9.0,)]


# ---------------------------------------------------------------------------
# New sources (AMS, PSD, EIA, manual daily) and derived variables
# ---------------------------------------------------------------------------
def test_ams_parser_finds_prices_in_nested_json():
    payload = [{"reportSection": "Report Header", "results": [{"report_title": "Illinois Production Cost"}]},
               {"reportSection": "Report Detail", "results": [
                   {"report_end_date": "03/20/2026", "commodity": "Fertilizer", "class": "Urea 46-0-0", "avg_price": "822.50"},
                   {"report_end_date": "03/20/2026", "commodity": "Fertilizer", "class": "Anhydrous Ammonia", "avg_price": "1,050.00"},
                   {"report_end_date": "03/06/2026", "commodity": "Fertilizer", "class": "Urea 46-0-0", "avg_price": "800.00"},
                   {"report_end_date": "03/20/2026", "commodity": "Diesel", "class": "Off-road", "avg_price": "3.10"}]}]
    out = ds.parse_ams([payload]).set_index("series_id")["value"]
    assert out["ams_il_urea"] == pytest.approx(811.25)          # average of the two March reports
    assert out["ams_il_ammonia"] == 1050.0
    assert "Diesel" not in out.index


def test_ams_parser_explains_unknown_layout():
    with pytest.raises(RuntimeError, match="Keys seen"):
        ds.parse_ams([{"results": [{"some_date": "x", "foo": 1}]}])


def test_psd_stocks_to_use_world_and_exchina():
    rows = []
    for country, es, dc in [("China", 200, 300), ("United States", 50, 300)]:
        rows += [{"Commodity_Description": "Corn", "Country_Name": country, "Market_Year": 2020,
                  "Attribute_Description": "Ending Stocks", "Value": es},
                 {"Commodity_Description": "Corn", "Country_Name": country, "Market_Year": 2020,
                  "Attribute_Description": "Domestic Consumption", "Value": dc},
                 {"Commodity_Description": "Corn", "Country_Name": country, "Market_Year": 2020,
                  "Attribute_Description": "Production", "Value": 999}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("psd_grains_pulses.csv", pd.DataFrame(rows).to_csv(index=False))
    out = ds.parse_psd(buf.getvalue())
    jan21 = out[out["date"] == pd.Timestamp("2021-01-01").date()].set_index("series_id")["value"]
    assert jan21["psd_stu_maize_world"] == pytest.approx(250 / 600)    # market year 2020 shown in 2021
    assert jan21["psd_stu_maize_exchina"] == pytest.approx(50 / 300)
    assert out["date"].min() == pd.Timestamp("2021-01-01").date()


def test_eia_parser_selects_by_code_and_drops_annual_rows():
    csv = ("MSN,YYYYMM,Value,Column_Order,Description,Unit\n"
           "ENPRPUS,202601,33738,1,Fuel Ethanol Production,Thousand Barrels\n"
           "ENPRMUS,202601,1417,2,Fuel Ethanol Production,Million Gallons\n"
           "ENPRBUS,202601,119.9,3,Fuel Ethanol Production,Trillion Btu\n"
           "ENPRPUS,202602,31000,1,Fuel Ethanol Production,Thousand Barrels\n"
           "ENPRPUS,202613,64738,1,Fuel Ethanol Production,Thousand Barrels\n").encode()
    out = ds.parse_eia(csv, {"ENPRPUS": ("eia_ethanol_prod", "x")})
    assert out["value"].tolist() == [33738, 31000]          # same description, other units ignored


def test_manual_daily_to_monthly(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("date,value\n2026-02-26,490\n2026-02-27,495\n2026-03-02,600\n2026-03-02,610\n")
    d = ds.parse_manual_daily(f, "fut_urea_me")
    assert len(d) == 3                                                # duplicate day removed
    m = ds.to_monthly_mean(d).set_index("date")["value"]
    assert m[pd.Timestamp("2026-02-01").date()] == 492.5


def test_derive_cpi_fills_gap():
    obs = pd.DataFrame({"series_id": "fred_us_cpi", "value": [100.0, 102.01],
                        "date": pd.to_datetime(["2025-09-01", "2025-11-01"]).date})
    out = ds.derive_cpi(obs).set_index("date")["value"]
    assert out[pd.Timestamp("2025-10-01").date()] == pytest.approx(101.0, abs=1e-6)   # geometric midpoint


def test_derive_hormuz_intensity(monkeypatch):
    monkeypatch.setattr(config, "START_DATE", "2018-11-01")
    months = pd.date_range("2019-01-01", "2026-03-01", freq="MS")
    vals = [50.0] * (len(months) - 13) + [80.0] * 12 + [4.0]        # 2019 traffic lower than baseline
    obs = pd.DataFrame({"series_id": "pw_hormuz_total", "date": months.date, "value": vals})
    out = ds.derive_hormuz_intensity(obs)
    s = out[out.series_id == "hormuz_intensity"].set_index("date")["value"]
    assert s[pd.Timestamp("2026-03-01").date()] == pytest.approx(0.95)
    assert s[pd.Timestamp("2019-06-01").date()] == 0.0             # low traffic before closure != disruption
    assert s[pd.Timestamp("2018-11-01").date()] == 0.0             # starts at START_DATE
    r = out[out.series_id == "hormuz_traffic_ratio"].set_index("date")["value"]
    assert r[pd.Timestamp("2019-06-01").date()] == pytest.approx(50 / 80)


def test_monthly_panel_real_with_start_date(tmp_db):
    months = pd.date_range("2014-01-01", "2026-02-01", freq="MS")
    obs = pd.concat([
        pd.DataFrame({"series_id": "fred_us_cpi_filled", "date": months.date, "value": 100.0}),
        pd.DataFrame({"series_id": "wb_urea", "date": months.date, "value": 400.0})])
    with database.connect() as con:
        database.upsert(con, "observations", obs)
    df = database.monthly_panel(["wb_urea"], start="2026-01-01", real=True)
    assert list(df.columns) == ["wb_urea"] and df["wb_urea"].tolist() == [400.0, 400.0]


def test_derive_strait_imports_inside_outside_and_cutoff():
    def row(rep, par, per, kg, hs="310210"):
        return {"period": pd.Timestamp(per), "reporter_code": rep, "partner_code": par, "flow": "M",
                "hs_code": hs, "net_weight_kg": kg}
    trade = pd.DataFrame([row(699, 634, "2026-02-01", 100e6), row(699, 512, "2026-02-01", 50e6),
                          row(699, 512, "2026-03-01", 60e6), row(699, 634, "2026-04-01", 10e6),
                          *[row(r, 682, "2026-03-01", 1e6) for r in (76, 842, 36, 764)]])
    out = ds.derive_strait_imports(trade)
    s = out.set_index(["series_id", "date"])["value"]
    assert s[("ct_urea_imports_inside_kt", pd.Timestamp("2026-02-01").date())] == 100
    assert s[("ct_urea_imports_outside_kt", pd.Timestamp("2026-03-01").date())] == 60
    assert s[("ct_urea_imports_inside_kt", pd.Timestamp("2026-03-01").date())] == 4   # Saudi -> inside
    assert pd.Timestamp("2026-04-01").date() not in set(out["date"])                  # after common cutoff
