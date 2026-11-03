#!/usr/bin/env python3
"""Nakane 5-pair re-verification from 2015 on the 2015-build m1.db (2026-11-03).

Trade logic is IDENTICAL to scripts/nakane_5pairs_firstpass.py Part B:
  IN  JST 00:00 bar-open ASK, OUT JST 09:55 bar-open BID, Thursday OUT 05:35.
  Exit dates Tue-Fri (JST). Long only. Price only (no swap).
  Gotobi = JST date day-of-month % 5 == 0. pip = 0.01 yen.
  stats() copied verbatim (population stdev for t).
Only the reporting window/splits change:
  S1 replication check: 2020-10-01 <= d < 2026-10-23. Run against the OLD
     build (m1_2026-10-29.db) this reproduces the 2026-10-29 first-pass
     numbers exactly. Run against the 2015 build each pair gains exactly
     +1 trade in that window (a JST date whose target bars the old build
     lacked and the new build filled; old rows themselves are unchanged,
     overlap-verified), shifting means by at most 0.04pip.
  S2 full period: all dates < 2026-10-31.
  S3 era split: pre-2020-10-01 (never verified before) vs 2020-10-01 onward.
  S4 yearly (all trades) per pair + cross-pair yearly mean of pair means.
  S5 gotobi split within each era.
Data: ~/workspace/oanda_json/m1.db (2015 build: 2015-01-01..2026-10-30 UTC).
"""
import os
import sqlite3
import statistics
import datetime

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
JST = datetime.timezone(datetime.timedelta(hours=9))

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def stats(rets):
    if not rets:
        return "n=0"
    n = len(rets)
    mean = statistics.mean(rets)
    gp = sum(r for r in rets if r > 0)
    gl = -sum(r for r in rets if r < 0)
    pf = gp / gl if gl > 0 else float("inf")
    sd = statistics.pstdev(rets) if n > 1 else 0.0
    t = mean / (sd / (n ** 0.5)) if sd > 0 else 0.0
    wr = sum(1 for r in rets if r > 0) / n * 100
    return f"n={n} mean={mean:+.2f}pip PF={pf:.3f} t={t:.2f} win={wr:.1f}%"


def load_trades(inst):
    """Return list of (jst_date, return_pip) using the first-pass logic as-is."""
    data = {}
    for t, ao, bo in con.execute(
            "SELECT t,ao,bo FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT) IN (0,335,595) ORDER BY t",
            (inst,)):
        dt = datetime.datetime.fromtimestamp(t, JST)
        data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
    trades = []
    for d, mins in sorted(data.items()):
        if d.weekday() not in (1, 2, 3, 4):  # exit Tue-Fri
            continue
        if 0 not in mins:
            continue
        exitmin = 335 if d.weekday() == 3 else 595  # Thu 05:35, else 09:55
        if exitmin not in mins:
            continue
        r = (mins[exitmin][1] - mins[0][0]) * 100
        trades.append((d, r))
    return trades


ALL = {inst: load_trades(inst) for inst in PAIRS}
for inst in PAIRS:
    tr = ALL[inst]
    print(f"{inst}: loaded {len(tr)} trades, {tr[0][0]} .. {tr[-1][0]}")

END = datetime.date(2026, 10, 31)
ERA = datetime.date(2020, 10, 1)

print()
print("=== S1. Replication check (2020-10-01 <= d < 2026-10-23; must match 2026-10-29 first pass) ===")
for inst in PAIRS:
    tr = [r for d, r in ALL[inst] if ERA <= d < datetime.date(2026, 10, 23)]
    print(f"{inst}: {stats(tr)}")

print()
print("=== S2. Full period (d < 2026-10-31) ===")
for inst in PAIRS:
    tr = [r for d, r in ALL[inst] if d < END]
    got = [r for d, r in ALL[inst] if d < END and d.day % 5 == 0]
    non = [r for d, r in ALL[inst] if d < END and d.day % 5 != 0]
    print(f"{inst}: ALL {stats(tr)}")
    print(f"   gotobi {stats(got)} | non-gotobi {stats(non)}")

print()
print("=== S3. Era split: PRE (d < 2020-10-01, never verified) vs POST (2020-10-01 <= d < 2026-10-31) ===")
for inst in PAIRS:
    pre = [r for d, r in ALL[inst] if d < ERA]
    post = [r for d, r in ALL[inst] if ERA <= d < END]
    print(f"{inst}: PRE  {stats(pre)}")
    print(f"{inst}: POST {stats(post)}")

print()
print("=== S5. Gotobi split within each era ===")
for inst in PAIRS:
    for label, lo, hi in (("PRE", None, ERA), ("POST", ERA, END)):
        sel = [(d, r) for d, r in ALL[inst] if (lo is None or d >= lo) and d < hi]
        got = [r for d, r in sel if d.day % 5 == 0]
        non = [r for d, r in sel if d.day % 5 != 0]
        print(f"{inst} {label}: gotobi {stats(got)} | non-gotobi {stats(non)}")

print()
print("=== S4. Yearly means (pip) per pair, all trades ===")
years = range(2015, 2027)
print("year | " + " | ".join(f"{p:>7s}" for p in PAIRS) + " | pair-mean-avg | pooled(n)")
for y in years:
    cells = []
    means = []
    pooled = []
    for inst in PAIRS:
        tr = [r for d, r in ALL[inst] if d.year == y and d < END]
        pooled += tr
        if tr:
            m = statistics.mean(tr)
            means.append(m)
            cells.append(f"{m:+7.2f}")
        else:
            cells.append("      -")
    avg = statistics.mean(means) if means else float("nan")
    print(f"{y} | " + " | ".join(cells) + f" | {avg:+.2f} | {len(pooled)}")

print()
print("=== S4b. Yearly detail per pair (n / mean / PF / t) ===")
for inst in PAIRS:
    print(f"-- {inst}")
    for y in years:
        tr = [r for d, r in ALL[inst] if d.year == y and d < END]
        if tr:
            print(f"  {y}: {stats(tr)}")

con.close()
