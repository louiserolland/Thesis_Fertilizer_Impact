"""
load_data.py — what your MODEL code imports. It never touches the internet,
only the local DuckDB file.

    import sys; sys.path.append("../src")      # from a notebook in notebooks/
    from load_data import monthly_panel
    df = monthly_panel(["fao_ffpi", "fao_cereals", "wb_urea", "wb_ngas_eu", "wb_brent"])
    df.head()

The trick is DuckDB's PIVOT: long table (series_id, date, value) -> one
column per series. You could do the same in pandas with df.pivot(), but it's
good to see it in SQL once.
"""
import pandas as pd

import config
import db


def monthly_panel(series=None, start=None, end=None, real=False) -> pd.DataFrame:
    """
    Wide monthly DataFrame indexed by date, one column per series_id.
    real=True  -> divides all $-denominated World Bank prices by US CPI
                  (rebased so that 2014-2016 = 100, like the FAO index).
    """
    where = ["1=1"]
    if series:
        wanted = list(series) + (["fred_us_cpi"] if real and "fred_us_cpi" not in series else [])
        where.append("series_id IN (" + ",".join(f"'{s}'" for s in wanted) + ")")
    if start:
        where.append(f"date >= DATE '{start}'")
    if end:
        where.append(f"date <= DATE '{end}'")

    with db.connect(read_only=True) as con:
        df = con.execute(f"""
            PIVOT (SELECT series_id, date, value FROM observations WHERE {' AND '.join(where)})
            ON series_id USING first(value) GROUP BY date ORDER BY date
        """).df()

    df = df.set_index(pd.to_datetime(df.pop("date"))).asfreq("MS")
    if real:
        cpi = df["fred_us_cpi"]
        cpi = cpi / cpi.loc["2014":"2016"].mean()
        for c in [c for c in df.columns if c.startswith("wb_")]:
            df[c] = df[c] / cpi
        if series and "fred_us_cpi" not in series:
            df = df.drop(columns="fred_us_cpi")
    return df


def trade_flows(hs_code=None) -> pd.DataFrame:
    sql = "SELECT * FROM trade_flows" + (f" WHERE hs_code = '{hs_code}'" if hs_code else "")
    with db.connect(read_only=True) as con:
        return con.execute(sql + " ORDER BY period").df()


def catalogue() -> pd.DataFrame:
    """What is in the database? (series, source, unit, coverage)"""
    with db.connect(read_only=True) as con:
        return con.execute("""
            SELECT m.series_id, m.name, m.source, m.unit, min(o.date) first, max(o.date) last, count(*) n
            FROM series_meta m LEFT JOIN observations o USING (series_id)
            GROUP BY ALL ORDER BY m.source, m.series_id
        """).df()


if __name__ == "__main__":
    print(catalogue().to_string(index=False))
