"""
database.py — the local DuckDB database: how data is STORED (writing) and how
the model READS it. No internet here.

Design choice (worth a sentence in your methodology/appendix):
all time series live in ONE "long" table

    observations(series_id, date, value, retrieved_at)

instead of one column per variable, because
  * a new variable = new rows, the table structure never changes;
  * every source is written with the same upsert() function;
  * the "wide" table the model needs is one PIVOT query away (monthly_panel).
Multi-dimensional data (trade flows, daily ship transits) get their own tables.

Typical use in a notebook:
    import sys; sys.path.append("../src")
    from database import monthly_panel, catalogue
    catalogue()                                         # what is in the DB?
    df = monthly_panel(["fao_ffpi", "wb_urea", "wb_ngas_eu"], start="2000-01-01", real=True)
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

import config

# ============================================================================
# SCHEMA
# ============================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS series_meta (
    series_id VARCHAR PRIMARY KEY, name VARCHAR, source VARCHAR, unit VARCHAR,
    frequency VARCHAR, source_code VARCHAR, last_updated TIMESTAMP
);
CREATE TABLE IF NOT EXISTS observations (
    series_id VARCHAR, date DATE, value DOUBLE, retrieved_at TIMESTAMP,
    PRIMARY KEY (series_id, date)                  -- monthly data dated on the 1st
);
CREATE TABLE IF NOT EXISTS chokepoint_daily (
    date DATE, portid VARCHAR, portname VARCHAR,
    n_total DOUBLE, n_tanker DOUBLE, n_dry_bulk DOUBLE, n_container DOUBLE, n_general_cargo DOUBLE,
    capacity DOUBLE, capacity_tanker DOUBLE, capacity_dry_bulk DOUBLE,   -- metric tons
    retrieved_at TIMESTAMP,
    PRIMARY KEY (date, portid)
);
CREATE TABLE IF NOT EXISTS trade_flows (
    period DATE, reporter_code INTEGER, reporter VARCHAR, partner_code INTEGER, partner VARCHAR,
    flow VARCHAR, hs_code VARCHAR, hs_desc VARCHAR, net_weight_kg DOUBLE, value_usd DOUBLE,
    retrieved_at TIMESTAMP,
    PRIMARY KEY (period, reporter_code, partner_code, flow, hs_code)
);
CREATE TABLE IF NOT EXISTS extraction_log (
    source VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
    status VARCHAR, n_rows INTEGER, message VARCHAR
);
"""


@contextmanager
def connect(read_only=False):
    """Open the database (creating tables if needed) and always close it again."""
    path = Path(config.DB_PATH)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    try:
        if not read_only:
            con.execute(SCHEMA)
        yield con
    finally:
        con.close()


# ============================================================================
# WRITING
# ============================================================================
def upsert(con, table: str, df: pd.DataFrame) -> int:
    """
    Insert rows, REPLACING rows with the same primary key:
    running the extraction twice never creates duplicates, and revised values
    (e.g. FAO revising the meat index) overwrite the old ones.
    """
    if df is None or df.empty:
        return 0
    cols_db = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    df = df.copy()
    if "retrieved_at" in cols_db and "retrieved_at" not in df.columns:
        df["retrieved_at"] = datetime.now()
    cols = [c for c in cols_db if c in df.columns]
    con.register("_incoming", df[cols])
    con.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _incoming")
    con.unregister("_incoming")
    return len(df)


def save_result(con, result) -> int:
    """Write a SourceResult (from data_sources.py) into the right tables."""
    n = 0
    if result.observations is not None and not result.observations.empty:
        obs = result.observations.assign(date=pd.to_datetime(result.observations["date"]).dt.date)
        n += upsert(con, "observations", obs)
    if result.meta:
        upsert(con, "series_meta", result.meta_frame().assign(last_updated=datetime.now()))
    for table, df in result.tables.items():
        n += upsert(con, table, df)
    return n


def log_run(con, source, started_at, status, n_rows, message=""):
    con.execute("INSERT INTO extraction_log VALUES (?, ?, ?, ?, ?, ?)",
                [source, started_at, datetime.now(), status, n_rows, message[:2000]])


# ============================================================================
# READING (what the model uses)
# ============================================================================
def monthly_panel(series=None, start=None, end=None, real=False) -> pd.DataFrame:
    """
    Wide monthly DataFrame indexed by date, one column per series_id.
    real=True -> World Bank $ prices divided by US CPI (rebased 2014-16 = 100, like the FAO index).

    Under the hood: DuckDB's PIVOT turns (series_id, date, value) into columns.
    """
    where = ["1=1"]
    if series:
        wanted = list(series) + (["fred_us_cpi"] if real and "fred_us_cpi" not in series else [])
        where.append("series_id IN (" + ",".join(f"'{s}'" for s in wanted) + ")")
    if start:
        where.append(f"date >= DATE '{start}'")
    if end:
        where.append(f"date <= DATE '{end}'")
    with connect(read_only=True) as con:
        df = con.execute(f"""
            PIVOT (SELECT series_id, date, value FROM observations WHERE {' AND '.join(where)})
            ON series_id USING first(value) GROUP BY date ORDER BY date
        """).df()
    df = df.set_index(pd.to_datetime(df.pop("date"))).asfreq("MS")
    if real:
        cpi = df["fred_us_cpi"] / df["fred_us_cpi"].loc["2014":"2016"].mean()
        for c in [c for c in df.columns if c.startswith("wb_")]:
            df[c] = df[c] / cpi
        if series and "fred_us_cpi" not in series:
            df = df.drop(columns="fred_us_cpi")
    return df


def trade_flows(hs_code=None) -> pd.DataFrame:
    sql = "SELECT * FROM trade_flows" + (f" WHERE hs_code = '{hs_code}'" if hs_code else "")
    with connect(read_only=True) as con:
        return con.execute(sql + " ORDER BY period").df()


def catalogue() -> pd.DataFrame:
    """Every series in the DB with source, unit and coverage."""
    with connect(read_only=True) as con:
        return con.execute("""
            SELECT m.series_id, m.name, m.source, m.unit, min(o.date) AS first, max(o.date) AS last, count(o.date) AS n
            FROM series_meta m LEFT JOIN observations o USING (series_id)
            GROUP BY ALL ORDER BY m.source, m.series_id
        """).df()


def sql(query: str) -> pd.DataFrame:
    """Run any SQL on the database, e.g. sql("SELECT * FROM extraction_log")."""
    with connect(read_only=True) as con:
        return con.execute(query).df()


if __name__ == "__main__":
    print(catalogue().to_string(index=False))
