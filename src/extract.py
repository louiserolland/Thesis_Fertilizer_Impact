"""
extract.py — download everything and store it in data/processed/thesis.duckdb

Usage (from the repository root):
    python src/extract.py                 # all sources
    python src/extract.py fao worldbank   # only some sources
    python src/extract.py --list          # show available sources

One failing source never stops the others: the error is printed and written
to the extraction_log table, and you can rerun just that source.
"""
import argparse
import traceback
from datetime import datetime

import database
from data_sources import SOURCES


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sources", nargs="*", help="subset of sources (default: all)")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()
    if args.list:
        return print("\n".join(SOURCES))

    names = args.sources or list(SOURCES)
    if unknown := set(names) - set(SOURCES):
        raise SystemExit(f"Unknown source(s): {unknown}. Use --list.")

    with database.connect() as con:
        for name in names:
            started = datetime.now()
            print(f"-> {name}")
            try:
                n = database.save_result(con, SOURCES[name]())
                database.log_run(con, name, started, "ok", n)
                print(f"   ok: {n} rows")
            except Exception as e:
                database.log_run(con, name, started, "error", 0, f"{e}\n{traceback.format_exc()}")
                print(f"   ERROR: {e}")

    print("\nDatabase content:")
    print(database.catalogue().to_string(index=False))


if __name__ == "__main__":
    main()
