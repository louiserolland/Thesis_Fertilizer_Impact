"""
extract.py — download everything and store it in data/processed/thesis.duckdb

Usage (from the repository root):
    python src/extract.py                 # all sources
    python src/extract.py fao worldbank   # only some sources
    python src/extract.py --list          # show available sources

One failing source never stops the others: the error is printed and written
to the extraction_log table, and you can rerun just that source.
After the downloads, the derived variables (CPI gap, Hormuz intensity,
inside/outside-strait imports) are always recomputed from the database.
"""
import argparse
import traceback
from datetime import datetime

import database
from data_sources import DERIVED, SOURCES


def run(names=None, verbose=True) -> dict:
    """Download the given sources (default: all), then recompute the derived variables.
    Returns {source: 'ok' | 'error: ...'} so other scripts (run_pipeline.py) can use it."""
    names = [n for n in (names or list(SOURCES)) if n not in DERIVED]
    if unknown := set(names) - set(SOURCES):
        raise SystemExit(f"Unknown source(s): {unknown}. Use --list.")
    results = {}
    with database.connect() as con:
        for name, func in [*((n, SOURCES[n]) for n in names), *DERIVED.items()]:
            started = datetime.now()
            print(f"-> {name}")
            try:
                result = func(con) if name in DERIVED else func()     # derived: always last
                n = database.save_result(con, result)
                database.log_run(con, name, started, "ok", n)
                print(f"   ok: {n} rows")
                results[name] = "ok"
            except Exception as e:
                database.log_run(con, name, started, "error", 0, f"{e}\n{traceback.format_exc()}")
                print(f"   ERROR: {e}")
                results[name] = f"error: {e}"
    if verbose:
        print("\nDatabase content:")
        print(database.catalogue().to_string(index=False))
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sources", nargs="*", help="subset of sources (default: all)")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()
    if args.list:
        return print("\n".join([*SOURCES, *DERIVED]))
    run(args.sources)


if __name__ == "__main__":
    main()
