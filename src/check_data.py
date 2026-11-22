"""
check_data.py — is the data in the database complete and plausible?

Run from the repository root (after python src/extract.py):
    python src/check_data.py

It never downloads anything: it only reads data/processed/thesis.duckdb and
prints six checks, each ending with OK / WARNING / PROBLEM:

  1. Extraction log   did every source run, and did the last run succeed?
  2. Coverage         first/last month and number of observations per series
  3. Gaps             missing months INSIDE a series (a VAR cannot have holes)
  4. Freshness        series that stopped updating (last value too old)
  5. Plausibility     negative prices and extreme month-on-month jumps
  6. Model sample     common period where all core VAR variables exist

It also saves a picture of the key series in outputs/figures/data_overview.png
-> LOOK at it: most data problems are obvious on a chart.
"""
from datetime import date

import numpy as np
import pandas as pd

import config
import database
from data_sources import DERIVED, SOURCES

# Variables you plan to put in the core SVAR (edit as the model evolves)
CORE = ["fao_ffpi", "wb_urea", "wb_ngas_eu", "wb_brent", "fred_igrea", "noaa_roni", "dummy_hormuz"]
# Series that are prices (must be > 0)
PRICE_PREFIXES = ("wb_", "fao_", "ams_", "fred_ppi", "fred_us_cpi", "fut_", "bdi")
# Series that SHOULD update every month (others are annual, PDF-based, or naturally lagged)
MAX_AGE_MONTHS = 3
SLOW_SERIES = ("psd_", "ct_", "eia_")          # annual data, trade data and EIA have longer delays
JUMP = 0.5                                     # |log change| > 0.5 = price moved by > ~65% in one month
# Known, explained gaps: they do not count as a warning (documented in the data report)
KNOWN_GAPS = {"fred_us_cpi": ["2025-10"]}      # BLS did not publish Oct 2025 (shutdown) -> fred_us_cpi_filled

pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 200)
status = {}


def header(title):
    print("\n" + "=" * 90 + f"\n{title}\n" + "=" * 90)


def verdict(name, level, msg=""):
    status[name] = level
    print(f"--> {level}{': ' + msg if msg else ''}")


def months_between(a, b):
    return (b.year - a.year) * 12 + b.month - a.month


def main():
    with database.connect(read_only=True) as con:
        log = con.execute("SELECT * FROM extraction_log").df()
        obs = con.execute("SELECT series_id, date, value FROM observations").df()
        meta = con.execute("SELECT series_id, source, unit FROM series_meta").df()
    obs["date"] = pd.to_datetime(obs["date"])
    today = pd.Timestamp(date.today())

    # ------------------------------------------------------------------ 1
    header("1. EXTRACTION LOG - last run of every source")
    last = (log.sort_values("finished_at").groupby("source").tail(1)
               .set_index("source")[["finished_at", "status", "n_rows", "message"]])
    last["message"] = last["message"].fillna("").str.split("\n").str[0].str[:70]
    expected = list(SOURCES) + list(DERIVED)
    never = [s for s in expected if s not in last.index]
    print(last.reindex([s for s in expected if s in last.index]).to_string())
    failed = last.index[last["status"] != "ok"].tolist()
    if failed or never:
        verdict("log", "WARNING", f"failed: {failed or 'none'} | never run: {never or 'none'} "
                                  f"-> python src/extract.py {' '.join(failed + never)}")
    else:
        verdict("log", "OK", "every source ran successfully the last time")

    # ------------------------------------------------------------------ 2
    header("2. COVERAGE - one line per series")
    cov = obs.groupby("series_id")["date"].agg(first="min", last="max", n="count").reset_index()
    cov["expected"] = cov.apply(lambda r: months_between(r["first"], r["last"]) + 1, axis=1)
    cov = cov.merge(meta, on="series_id", how="left").sort_values(["source", "series_id"])
    cov["first"], cov["last"] = cov["first"].dt.strftime("%Y-%m"), cov["last"].dt.strftime("%Y-%m")
    print(cov[["series_id", "source", "unit", "first", "last", "n", "expected"]].to_string(index=False))
    late_start = cov[cov["first"] > config.START_DATE[:7]]
    print(f"\n{len(cov)} series. {len(late_start)} start after {config.START_DATE[:7]} "
          f"(fine for robustness checks, NOT for the 2000-2026 VAR):")
    print("   " + ", ".join(f"{r.series_id} ({r.first})" for r in late_start.itertuples()))
    verdict("coverage", "OK" if len(cov) else "PROBLEM", f"{len(cov)} series in the database")

    # ------------------------------------------------------------------ 3
    header("3. GAPS - missing months inside a series")
    gaps = cov[cov["n"] < cov["expected"]]
    unexplained = 0
    for r in gaps.itertuples():
        s = obs[obs.series_id == r.series_id].set_index("date")["value"].sort_index()
        full = pd.date_range(s.index.min(), s.index.max(), freq="MS")
        missing = list(full.difference(s.index).strftime("%Y-%m"))
        new_gaps = [m for m in missing if m not in KNOWN_GAPS.get(r.series_id, [])]
        unexplained += len(new_gaps)
        tag = "known, explained" if not new_gaps else "NEW"
        print(f"   {r.series_id:32s} {len(missing):3d} missing ({tag}): {', '.join(missing[:6])}"
              f"{' ...' if len(missing) > 6 else ''}")
    if unexplained == 0:
        verdict("gaps", "OK", "no unexplained holes" + (" (only known gaps, see KNOWN_GAPS)" if len(gaps) else ""))
    else:
        verdict("gaps", "WARNING", "new gaps: interpolate or explain them before modelling")

    # ------------------------------------------------------------------ 4
    header(f"4. FRESHNESS - last value older than {MAX_AGE_MONTHS} months")
    obs_last = obs.groupby("series_id")["date"].max()
    age = obs_last.apply(lambda d: months_between(d, today))
    stale = age[(age > MAX_AGE_MONTHS) & ~age.index.str.startswith(SLOW_SERIES)]
    if stale.empty:
        verdict("freshness", "OK", "all monthly series are recent")
    else:
        print(stale.rename("months old").to_string())
        verdict("freshness", "WARNING", "these stopped updating: provider stopped, URL changed, or source not re-run")

    # ------------------------------------------------------------------ 5
    header("5. PLAUSIBILITY - negative prices and extreme monthly jumps")
    prices = obs[obs.series_id.str.startswith(PRICE_PREFIXES)].copy()
    neg = prices[prices["value"] <= 0]
    if not neg.empty:
        print("   Non-positive prices (impossible -> parsing error?):")
        print(neg.head(20).to_string(index=False))
    prices = prices[prices["value"] > 0].sort_values(["series_id", "date"])
    prices["dlog"] = prices.groupby("series_id")["value"].transform(lambda v: np.log(v).diff())
    jumps = prices[prices["dlog"].abs() > JUMP]
    if not jumps.empty:
        print(f"   Months where a price jumped by more than {np.exp(JUMP) - 1:.0%} or fell by more than {1 - np.exp(-JUMP):.0%} (check each one: "
              f"real event or data error?):")
        out = jumps.assign(change=(np.exp(jumps["dlog"]) - 1).map("{:+.0%}".format))
        print(out[["series_id", "date", "value", "change"]].to_string(index=False))
    level = "PROBLEM" if not neg.empty else ("REVIEW" if not jumps.empty else "OK")
    verdict("plausibility", level, f"{len(neg)} non-positive prices, {len(jumps)} large moves to review once "
                                   "(2008, 2020, 2022, 2026 moves are real market events)")

    # ------------------------------------------------------------------ 6
    header("6. MODEL SAMPLE - common period of the core VAR variables")
    have = [c for c in CORE if c in set(obs.series_id)]
    missing_core = sorted(set(CORE) - set(have))
    panel = database.monthly_panel(have)
    complete = panel.dropna()
    print(f"   core variables: {have}")
    if missing_core:
        print(f"   NOT in the database: {missing_core}")
    if complete.empty:
        verdict("sample", "PROBLEM", "no month where all core variables exist")
    else:
        post = complete.loc[config.HORMUZ_START:]
        print(f"   complete sample: {complete.index.min():%Y-%m} -> {complete.index.max():%Y-%m} "
              f"= {len(complete)} months, of which {len(post)} after the closure")
        print("   last month available per variable:")
        print("   " + ", ".join(f"{c}: {panel[c].last_valid_index():%Y-%m}" for c in have))
        verdict("sample", "OK" if not missing_core else "WARNING",
                "the slowest variable sets the end of your estimation sample")

    # ------------------------------------------------------------------ figure
    plot_overview(obs)

    header("SUMMARY")
    for k, v in status.items():
        print(f"   {k:14s} {v}")
    return dict(status)


def plot_overview(obs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = {
        "Food prices (FAO, 2014-16=100)": ["fao_ffpi", "fao_cereals", "fao_vegoils"],
        "Fertilizer prices (World Bank, $/t)": ["wb_urea", "wb_dap", "wb_potash"],
        "Fertilizer PPI US (index)": ["fred_ppi_nitrogen", "fred_ppi_phosphate"],
        "Energy (World Bank)": ["wb_brent", "wb_ngas_eu"],
        "Hormuz (ships/day; intensity 0-1 right axis)": ["pw_hormuz_total"],
        "Imports from Gulf (kt): inside vs outside strait": ["ct_urea_imports_inside_kt", "ct_urea_imports_outside_kt"],
        "Illinois retail fertilizer ($/short t)": ["ams_il_ammonia", "ams_il_urea", "ams_il_dap"],
        "Controls (standardised: mean 0, sd 1)": ["fred_igrea", "noaa_roni", "nyfed_gscpi"],
    }
    fig, axes = plt.subplots(4, 2, figsize=(14, 15))
    for ax, (title, ids) in zip(axes.flat, groups.items()):
        drawn = 0
        for sid in ids:
            s = obs[obs.series_id == sid].set_index("date")["value"].sort_index()
            if s.empty:
                continue
            if "standardised" in title:
                s = (s - s.mean()) / s.std()
            ax.plot(s.index, s.values, label=sid, linewidth=1)
            drawn += 1
        if not drawn:
            ax.text(0.5, 0.5, "not in the database yet\n" + "\n".join(ids), ha="center", va="center",
                    transform=ax.transAxes, fontsize=9, color="grey")
        if "Hormuz" in title:
            s = obs[obs.series_id == "hormuz_intensity"].set_index("date")["value"].sort_index()
            if not s.empty:
                ax2 = ax.twinx()
                ax2.plot(s.index, s.values, color="black", linestyle=":", label="hormuz_intensity")
                ax2.set_ylim(0, 1.05)
            ax.set_xlim(pd.Timestamp("2019-01-01"), None)
        if drawn:
            ax.axvline(pd.Timestamp(config.HORMUZ_START), color="red", linewidth=0.8, alpha=0.6)
            ax.legend(fontsize=7, loc="upper left")
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.3)
    fig.suptitle("Data overview - red line = Hormuz closure", fontsize=13)
    fig.tight_layout()
    out = config.PROJECT_DIR / "outputs" / "figures" / "data_overview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    print(f"\nFigure saved: {out}")


if __name__ == "__main__":
    main()
