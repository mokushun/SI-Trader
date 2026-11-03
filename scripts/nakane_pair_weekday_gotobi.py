#!/usr/bin/env python3
"""Nakane: pair x weekday aggregates split by gotobi / non-gotobi (2026-11-03).

User request: the pair x weekday table, but split into gotobi days and
non-gotobi days. Data: m1.db 2015-2026, executable prices, live logic
(00:00 ask in, 09:55 bid out, Thu 05:35 out = short trade, marked Thu*).
Eras: PRE 2015..2020-09, POST 2020-10..2026-10, FULL.
Also, for Fridays, the known weekend-gotobi structure (2026-10-29 finding)
is reproduced inside the non-gotobi bucket: Fri before Sat-gotobi
(front-run demand), Fri before Sun-gotobi (worst bucket), Fri before
Mon-gotobi, and plain Fridays -- shown in Part 2 so the split table's
Friday non-gotobi cells can be read correctly.
"""
import os
import sqlite3
import statistics
import datetime

DB = os.path.expanduser("~/workspace/oanda_json/m1.db")
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
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


ERAS = [("PRE 2015-20.9", datetime.date(2015, 1, 1), datetime.date(2020, 10, 1)),
        ("POST 2020.10-26", datetime.date(2020, 10, 1), datetime.date(2026, 10, 31)),
        ("FULL 2015-26", datetime.date(2015, 1, 1), datetime.date(2026, 10, 31))]

print("=== Part 1. Pair x weekday x gotobi/non (executable pip). *Thu = 00:00->05:35 ===")
for elab, lo, hi in ERAS:
    print(f"--- {elab}")
    for p in PAIRS:
        for wd in (1, 2, 3, 4):
            for glab, g in (("GOTOBI", True), ("non   ", False)):
                rets = [r for (pp, d), r in trades.items()
                        if pp == p and d.weekday() == wd and lo <= d < hi and (d.day % 5 == 0) == g]
                print(f"  {p} {WD[wd]:4s} {glab}: {stats(rets)}")
    print()

print("=== Part 2. Friday non-gotobi sub-split (FULL, executable pip) ===")
print("Fri-before-Sat-gotobi: (d+1).day%5==0 | Sun: (d+2)%5==0 | Mon: (d+3)%5==0 | plain: rest")


def fri_bucket(d):
    if d.day % 5 == 0:
        return "gotobi-Fri"
    if (d + datetime.timedelta(days=1)).day % 5 == 0:
        return "pre-Sat"
    if (d + datetime.timedelta(days=2)).day % 5 == 0:
        return "pre-Sun"
    if (d + datetime.timedelta(days=3)).day % 5 == 0:
        return "pre-Mon"
    return "plain"


for p in PAIRS:
    for b in ("gotobi-Fri", "pre-Sat", "pre-Sun", "pre-Mon", "plain"):
        rets = [r for (pp, d), r in trades.items()
                if pp == p and d.weekday() == 4 and fri_bucket(d) == b]
        print(f"  {p} Fri {b:10s}: {stats(rets)}")
