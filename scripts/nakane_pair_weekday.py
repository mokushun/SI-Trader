#!/usr/bin/env python3
"""Nakane: per-pair x per-weekday aggregates + AUD sizing scenarios (2026-11-03).

Question from the user: (1) can we get per-pair per-weekday stats?
(2) AUD_JPY is the weakest pair overall yet carries the 2nd-largest live
base size -- should it be cut?
Data: m1.db 2015-2026, executable prices, live logic (00:00 ask in,
09:55 bid out, Thu 05:35 out). NOTE: Thursday rows are the SHORT trade
(00:00->05:35), so Thu means are not directly comparable with Tue/Wed/Fri.
Part 1: pair x weekday stats, FULL and POST, all-days.
Part 2: AUD_JPY focus: weekday x era, and gotobi/non-gotobi per weekday (FULL).
Part 3: portfolio in yen with ACTUAL live weights (VOLS + 3000 bonus, x1.4):
 current vs AUD half vs AUD zero vs AUD non-gotobi-only-zero.
"""
import os
import sqlite3
import statistics
import datetime
from collections import defaultdict

DB = os.path.expanduser("~/workspace/oanda_json/m1.db")
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
VOLS = {"AUD_JPY": [0, 0, 2800, 2500, 2300, 2000, 0], "CHF_JPY": [0, 0, 100, 100, 100, 100, 0],
        "EUR_JPY": [0, 0, 2700, 2400, 2200, 1900, 0], "GBP_JPY": [0, 0, 3200, 2800, 2500, 2200, 0],
        "USD_JPY": [0, 0, 3100, 2800, 2500, 2200, 0]}
JST = datetime.timezone(datetime.timedelta(hours=9))
WD = {1: "Tue", 2: "Wed", 3: "Thu*", 4: "Fri"}
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
trades = {}
for inst in PAIRS:
    data = {}
    for t, ao, bo in con.execute(
            "SELECT t,ao,bo FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT) IN (0,335,595) ORDER BY t",
            (inst,)):
        dt = datetime.datetime.fromtimestamp(t, JST)
        data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
    for d, mins in data.items():
        if d.weekday() not in (1, 2, 3, 4) or 0 not in mins:
            continue
        outm = 335 if d.weekday() == 3 else 595
        if outm in mins:
            trades[(inst, d)] = (mins[outm][1] - mins[0][0]) * 100
con.close()

POST0 = datetime.date(2020, 10, 1)
END = datetime.date(2026, 10, 31)


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
    win = sum(1 for r in rets if r > 0) / n * 100
    return f"n={n:4d} mean={mean:+6.2f} PF={pf:.3f} t={t:+.2f} win={win:.0f}% sum={sum(rets):+8.0f}"


def sel(pair, wd=None, lo=None, hi=None, gotobi=None):
    out = []
    for (p, d), r in trades.items():
        if p != pair:
            continue
        if wd is not None and d.weekday() != wd:
            continue
        if lo and not (lo <= d < hi):
            continue
        if gotobi is not None and (d.day % 5 == 0) != gotobi:
            continue
        out.append(r)
    return out


print("=== Part 1. Pair x weekday (executable pip). *Thu = short trade 00:00->05:35 ===")
for lab, lo, hi in (("FULL 2015-26", datetime.date(2015, 1, 1), END), ("POST 2020.10-26", POST0, END)):
    print(f"--- {lab}")
    for p in PAIRS:
        for wd in (1, 2, 3, 4):
            print(f"  {p} {WD[wd]:4s}: {stats(sel(p, wd, lo, hi))}")
        print(f"  {p} ALL : {stats(sel(p, None, lo, hi))}")

print()
print("=== Part 2. AUD_JPY focus ===")
for lab, lo, hi in (("PRE", datetime.date(2015, 1, 1), POST0), ("POST", POST0, END)):
    for wd in (1, 2, 3, 4):
        print(f"  AUD {lab} {WD[wd]:4s}: {stats(sel('AUD_JPY', wd, lo, hi))}")
print("  AUD gotobi/non by weekday (FULL):")
for wd in (1, 2, 3, 4):
    print(f"  AUD {WD[wd]:4s} gotobi: {stats(sel('AUD_JPY', wd, None, None, True))}")
    print(f"  AUD {WD[wd]:4s} non   : {stats(sel('AUD_JPY', wd, None, None, False))}")

print()
print("=== Part 3. Live-weight portfolio (yen), AUD scenarios ===")


def wd_sun(d):
    return (d.weekday() + 1) % 7


def units(scheme, pair, d):
    base = VOLS[pair][wd_sun(d)]
    if base == 0:
        return 0.0
    g = d.day % 5 == 0
    if pair == "AUD_JPY":
        if scheme == "AUD-half":
            base = base / 2
        elif scheme == "AUD-zero":
            return 0.0
        elif scheme == "AUD-nongotobi-zero" and not g:
            return 0.0
    return (base + (3000 if g else 0)) * 1.4


DATES = sorted({d for _, d in trades})
for elab, lo, hi in (("PRE 2015-20.9", datetime.date(2015, 1, 1), POST0),
                     ("POST 2020.10-26", POST0, END), ("FULL 2015-26", datetime.date(2015, 1, 1), END)):
    print(f"--- {elab}")
    for s in ("current", "AUD-half", "AUD-zero", "AUD-nongotobi-zero"):
        daily = {}
        for d in DATES:
            if not (lo <= d < hi):
                continue
            tot = sum(units(s, p, d) * trades[(p, d)] for p in PAIRS if (p, d) in trades) / 100.0
            if any(units(s, p, d) > 0 and (p, d) in trades for p in PAIRS):
                daily[d] = tot
        months = defaultdict(float)
        for d, v in daily.items():
            months[(d.year, d.month)] += v
        mv = list(months.values())
        mean_m = statistics.mean(mv)
        sd_m = statistics.pstdev(mv)
        t_m = mean_m / (sd_m / len(mv) ** 0.5) if sd_m else 0.0
        pos = sum(1 for v in mv if v > 0) / len(mv) * 100
        print(f"  {s:20s} total={sum(mv):+10.0f}yen month mean={mean_m:+8.0f} worst={min(mv):+8.0f} pos={pos:.0f}% t={t_m:.2f}")
