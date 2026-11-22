"""
extract.py — download everything and store it in data/thesis.duckdb

Usage (from the repository root):
    python src/extract.py                 # all sources
    python src/extract.py fao worldbank   # only some sources
    python src/extract.py --list          # show available sources

One failing source never stops the others: the error is printed and
written to the extraction_log table, and you can rerun just that source.
"""
import argparse
import traceback
from datetime import datetime

import pandas as pd

import db
from sources import SOURCES


def run_source(con, name: str) -> int:
    result = SOURCES[name]()
    n = 0
    if result.observations is not None and not result.observations.empty:
        obs = result.observations.copy()
        obs["date"] = pd.to_datetime(obs["date"]).dt.date
        n += db.upsert(con, "observations", obs)
    if result.meta:
        meta = result.meta_frame()
        meta["last_updated"] = datetime.now()
        db.upsert(con, "series_meta", meta)
    for table, df in result.tables.items():
        n += db.upsert(con, table, df)
    return n


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sources", nargs="*", help="subset of sources (default: all)")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.list:
        print("\n".join(SOURCES))
        return
    names = args.sources or list(SOURCES)
    unknown = set(names) - set(SOURCES)
    if unknown:
        raise SystemExit(f"Unknown source(s): {unknown}. Use --list.")

    with db.connect() as con:
        for name in names:
            started = datetime.now()
            print(f"-> {name}")
            try:
                n = run_source(con, name)
                db.log_run(con, name, started, "ok", n)
                print(f"   ok: {n} rows")
            except Exception as e:
                db.log_run(con, name, started, "error", 0, f"{e}\n{traceback.format_exc()}")
                print(f"   ERROR: {e}")

        print("\nSummary of the database:")
        print(con.execute("""
            SELECT o.series_id, m.source, min(o.date) AS first, max(o.date) AS last, count(*) AS n
            FROM observations o LEFT JOIN series_meta m USING (series_id)
            GROUP BY ALL ORDER BY m.source, o.series_id
        """).df().to_string(index=False))


if __name__ == "__main__":
    main()
