"""Cut a two-week sample from the LBNL PFPU data into data/sample/, small enough to commit.

Usage: python benchmark/setup/make_sample.py

Needs data/lbnl/PFPU/ (python benchmark/setup/fetch.py FPU). Copies rows unchanged, so the
sample is a valid Trend File: two fault-free weeks and the same weeks with VAV-S's damper
stuck closed. Two weeks is one full Persistence lookback (config/dispatch.yaml), the least
that lets `analyze` decide a problem is persistent enough to dispatch.
"""

from datetime import date, timedelta

from hvac_fdd_agents import paths

SRC = paths.LBNL / "PFPU"
OUT = paths.SAMPLE

CASES = ["pfpu__fault_free.csv", "pfpu__damper_stuck__0pct.csv"]
START, DAYS = date(2018, 7, 2), 14  # two weeks, Monday to Sunday: one full Persistence lookback


def main() -> None:
    days = tuple(f"{START + timedelta(d):%m/%d/%Y}" for d in range(DAYS))
    OUT.mkdir(parents=True, exist_ok=True)
    for name in CASES:
        with open(SRC / name) as src, open(OUT / name, "w", newline="") as dst:
            dst.write(next(src))
            rows = 0
            for line in src:
                if line.startswith(days):
                    dst.write(line)
                    rows += 1
        print(f"{name}: {rows} rows")


if __name__ == "__main__":
    main()
