"""
run_pipeline.py — ONE command that checks that everything works.

    python src/run_pipeline.py                  # tests -> download all -> check -> verdict
    python src/run_pipeline.py --no-download    # tests + check only (fast, offline)
    python src/run_pipeline.py fao worldbank    # only refresh some sources

Steps
  1. Tests        the offline unit tests (pytest): is the code itself OK?
  2. Download     every source (or the ones given), then the derived variables
  3. Check        the six data checks of check_data.py + the overview figure
  4. Verdict      READY / READY WITH WARNINGS / NOT READY

Run it whenever you change the code, before a commit, and before you
estimate the models on fresh data.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def step(n, title):
    print("\n" + "#" * 90 + f"\n#  STEP {n}: {title}\n" + "#" * 90, flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sources", nargs="*", help="sources to refresh (default: all)")
    p.add_argument("--no-download", action="store_true", help="skip step 2 (offline)")
    p.add_argument("--skip-tests", action="store_true", help="skip step 1")
    args = p.parse_args()
    t0, problems, warnings = time.time(), [], []

    # 1. tests ---------------------------------------------------------------
    if not args.skip_tests:
        step(1, "unit tests")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")], cwd=ROOT)
        if r.returncode != 0:
            print("\nNOT READY: the tests fail, so the code is broken. Fix this first (nothing was downloaded).")
            sys.exit(1)

    import check_data
    import extract

    # 2. download ------------------------------------------------------------
    if not args.no_download:
        step(2, "download " + (" ".join(args.sources) if args.sources else "all sources"))
        results = extract.run(args.sources or None, verbose=False)
        failed = {k: v for k, v in results.items() if v != "ok"}
        for k, v in failed.items():
            problems.append(f"source '{k}' failed: {v[:120]}")

    # 3. checks --------------------------------------------------------------
    step(3, "data checks")
    status = check_data.main()
    for name, level in status.items():
        if level == "PROBLEM":
            problems.append(f"check '{name}' = PROBLEM")
        elif level == "WARNING":
            warnings.append(f"check '{name}' = WARNING")

    # 4. verdict -------------------------------------------------------------
    step(4, "verdict")
    print(f"   time: {time.time() - t0:.0f} s")
    for x in problems:
        print(f"   PROBLEM  {x}")
    for x in warnings:
        print(f"   WARNING  {x}")
    if problems:
        print("\n   ==> NOT READY: fix the problems above (rerun a single source with python src/extract.py <name>)")
        sys.exit(1)
    if warnings:
        print("\n   ==> READY WITH WARNINGS: usable, but read the warnings above")
    else:
        print("\n   ==> READY: code tested, data complete and checked. "
              "Look at outputs/figures/data_overview.png, then commit.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
