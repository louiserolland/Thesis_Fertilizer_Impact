"""
db.py — everything that talks to the DuckDB database.

Design choice (worth explaining in your methodology/appendix):
We store all time series in ONE "long" table:

    observations(series_id, date, value, retrieved_at)

instead of one column per variable. Why?
  * adding a new variable = adding rows, never changing the table structure;
  * every source can be written with the same function;
  * the "wide" table the model needs (one column per variable) is produced
    with a single PIVOT query (see load_data.py).

Multi-dimensional data (trade flows: reporter x partner x product, daily
ship transits) get their own tables, because they don't fit "one value per
series and month".
"""
import pathlib
from contextlib import contextmanager
from datetime import datetime

import duckdb
import pandas as pd

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS series_meta (
    series_id    VARCHAR PRIMARY KEY,
    name         VARCHAR,
    source       VARCHAR,
    unit         VARCHAR,
    frequency    VARCHAR,
    source_code  VARCHAR,      -- the identifier used by the provider (e.g. 'IGREA' on FRED)
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS observations (
    series_id    VARCHAR,
    date         DATE,         -- convention: monthly data dated on the 1st of the month
    value        DOUBLE,
    retrieved_at TIMESTAMP,
    PRIMARY KEY (series_id, date)
);

CREATE TABLE IF NOT EXISTS chokepoint_daily (
    date              DATE,
    portid            VARCHAR,
    portname          VARCHAR,
    n_total           DOUBLE,
    n_tanker          DOUBLE,
    n_dry_bulk        DOUBLE,
    n_container       DOUBLE,
    n_general_cargo   DOUBLE,
    capacity          DOUBLE,  -- estimated transit volume, metric tons
    capacity_tanker   DOUBLE,
    capacity_dry_bulk DOUBLE,
    retrieved_at      TIMESTAMP,
    PRIMARY KEY (date, portid)
);

CREATE TABLE IF NOT EXISTS trade_flows (
    period         DATE,
    reporter_code  INTEGER,
    reporter       VARCHAR,
    partner_code   INTEGER,
    partner        VARCHAR,
    flow           VARCHAR,    -- 'X' export, 'M' import
    hs_code        VARCHAR,
    hs_desc        VARCHAR,
    net_weight_kg  DOUBLE,
    value_usd      DOUBLE,
    retrieved_at   TIMESTAMP,
    PRIMARY KEY (period, reporter_code, partner_code, flow, hs_code)
);

CREATE TABLE IF NOT EXISTS extraction_log (
    source      VARCHAR,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    status      VARCHAR,       -- 'ok' or 'error'
    n_rows      INTEGER,
    message     VARCHAR
);
"""


@contextmanager
def connect(db_path=None, read_only=False):
    """Open the database, create the tables if needed, and always close it again."""
    path = str(db_path or config.DB_PATH)
    if not read_only:
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(path, read_only=read_only)
    try:
        if not read_only:
            con.execute(SCHEMA)
        yield con
    finally:
        con.close()


def upsert(con, table: str, df: pd.DataFrame) -> int:
    """
    Insert rows, REPLACING existing rows with the same primary key.
    -> running the extraction twice never creates duplicates, and revised
       values (FAO revises the meat index, for example) overwrite old ones.
    """
    if df is None or df.empty:
        return 0
    df = df.copy()
    if "retrieved_at" in _columns(con, table) and "retrieved_at" not in df.columns:
        df["retrieved_at"] = datetime.now()
    cols = [c for c in _columns(con, table) if c in df.columns]
    con.register("_incoming", df[cols])
    con.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
        f"SELECT {', '.join(cols)} FROM _incoming"
    )
    con.unregister("_incoming")
    return len(df)


def _columns(con, table):
    return [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]


def log_run(con, source, started_at, status, n_rows, message=""):
    con.execute(
        "INSERT INTO extraction_log VALUES (?, ?, ?, ?, ?, ?)",
        [source, started_at, datetime.now(), status, n_rows, message[:2000]],
    )
