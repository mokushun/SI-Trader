#!/usr/bin/env python3
"""Nakane: keep the current pair/size mix, or specialize more in gotobi?
(2026-11-03)

Portfolio comparison on m1.db (2015-2026, 5 yen pairs, executable prices,
logic identical to nakane_5pairs_from2015.py Part B). EQUAL unit per pair
as a proxy for the live sizing (live VOLS differ by weekday/pair and add a
+3000 gotobi bonus x1.4; scheme rankings, not absolute yen, are the point).
Schemes (weight w applied to each day's trade):
  S0 all-days        : w=1 every entry day (current structure, flat proxy)
  S1 gotobi-only     : w=1 on gotobi days, 0 otherwise
  S2 tilt            : w=1 gotobi, w=0.5 non-gotobi
  S3 selective       : gotobi all pairs w=1; non-gotobi only USD_JPY/GBP_JPY
  S1x2 gotobi-only x2: w=2 on gotobi days (size-shifted variant)
Daily portfolio return = sum over pairs of w * r(pair, day). Monthly sums
give the lived experience: mean month, worst month, % positive months.
Eras: PRE 2015..2020-09, POST 2020-10..2026-10, FULL.
"""
import os
import sqlite3
import statistics
import datetime
from collections import defaultdict

DB = os.path.expanduser("~/workspace/oanda_json/m1.db")
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
JST = datetime.timezone(datetime.timedelta(hours=9))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

# trades[(pair, date)] = executable return in pip
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

DATES = sorted({d for _, d in trades})
ERAS = [("PRE 2015-20.9", datetime.date(2015, 1, 1), datetime.date(2020, 10, 1)),
        ("POST 2020.10-26", datetime.date(2020, 10, 1), datetime.date(2026, 10, 31)),
        ("FULL 2015-26", datetime.date(2015, 1, 1), datetime.date(2026, 10, 31))]


def weight(scheme, pair, d):
    g = d.day % 5 == 0
    if scheme == "S0 all":
        return 1.0
    if scheme == "S1 gotobi-only":
        return 1.0 if g else 0.0
    if scheme == "S1x2 gotobi x2":
        return 2.0 if g else 0.0
    if scheme == "S2 tilt .5":
        return 1.0 if g else 0.5
    if scheme == "S3 selective":
        if g:
            return 1.0
        return 1.0 if pair in ("USD_JPY", "GBP_JPY") else 0.0
    raise ValueError(scheme)


SCHEMES = ["S0 all", "S1 gotobi-only", "S1x2 gotobi x2", "S2 tilt .5", "S3 selective"]
for elab, lo, hi in ERAS:
    print(f"===== {elab} =====")
    for s in SCHEMES:
        daily = {}
        for d in DATES:
            if not (lo <= d < hi):
                continue
            tot = sum(weight(s, p, d) * trades[(p, d)] for p in PAIRS if (p, d) in trades)
            if any(weight(s, p, d) > 0 and (p, d) in trades for p in PAIRS):
                daily[d] = tot
        months = defaultdict(float)
        for d, v in daily.items():
            months[(d.year, d.month)] += v
        mv = list(months.values())
        dv = list(daily.values())
        mean_d = statistics.mean(dv)
        sd_d = statistics.pstdev(dv)
        ratio = mean_d / sd_d if sd_d else 0.0
        mean_m = statistics.mean(mv)
        sd_m = statistics.pstdev(mv)
        t_m = mean_m / (sd_m / len(mv) ** 0.5) if sd_m else 0.0
        pos = sum(1 for v in mv if v > 0) / len(mv) * 100
        tot_units = sum(weight(s, p, d) for d in DATES if lo <= d < hi for p in PAIRS if (p, d) in trades)
        print(f"{s:16s} days={len(dv):4d} units={tot_units:7.0f} | daily mean={mean_d:+7.2f} sd={sd_d:6.2f} "
              f"mean/sd={ratio:+.3f} worst-day={min(dv):+8.1f} | month mean={mean_m:+8.1f} worst={min(mv):+8.1f} "
              f"pos={pos:.0f}% t={t_m:.2f} total={sum(mv):+9.0f}")
    print()
