#!/usr/bin/env python3
"""Should the gotobi bonus vary by pair / weekday / cell? (2026-11-04)

Base table: HAND2 (user's adopted draft). Bonus variants, all normalized so
the average total bonus per gotobi day stays 15,000 (= 5 pairs x 3000), i.e.
volume- and exposure-neutral on average:
  B0 uniform 3000
  B-pair: per-day 15,000 split across pairs by pair gotobi score
  B-wd:   bonus per pair scaled by weekday gotobi score (Thu up, Tue down)
  B-cell: bonus proportional to pair x weekday gotobi score
Score = (FULL gotobi mean + POST gotobi mean) / 2, same blend as the base
construction. Floors: 0 (negative-score cells get no bonus). Rounded to 100.
Caution printed with the results: gotobi cells have n~120 (FULL) / ~60 (POST),
so cell means carry SE ~3-4 pip; fine-grained differentiation chases noise.
"""
import os
import sqlite3
import statistics
import datetime
from collections import defaultdict

DB = os.path.expanduser("~/workspace/oanda_json/m1.db")
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
JST = datetime.timezone(datetime.timedelta(hours=9))
POST0 = datetime.date(2020, 10, 1)
END = datetime.date(2026, 10, 31)
BASE = {"USD_JPY": [0, 0, 3000, 3500, 2000, 100, 0], "GBP_JPY": [0, 0, 3000, 4000, 3000, 100, 0],
        "AUD_JPY": [0, 0, 100, 1500, 100, 100, 0], "EUR_JPY": [0, 0, 1500, 1500, 2000, 100, 0],
        "CHF_JPY": [0, 0, 100, 1000, 100, 3000, 0]}
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


def gmean(pair, wd, lo, hi):
    v = [r for (p, d), r in trades.items()
         if p == pair and d.weekday() == wd and lo <= d < hi and d.day % 5 == 0]
    return (statistics.mean(v), len(v)) if v else (0.0, 0)


print("Gotobi means (pip) FULL / POST / score, n(FULL/POST):")
SCORE = {}
for p in PAIRS:
    SCORE[p] = {}
    for wd in (1, 2, 3, 4):
        f, nf = gmean(p, wd, datetime.date(2015, 1, 1), END)
        po, npo = gmean(p, wd, POST0, END)
        SCORE[p][wd] = (f + po) / 2
        print(f"  {p} wd={wd+1}: FULL {f:+.2f} (n={nf}) | POST {po:+.2f} (n={npo}) | score {SCORE[p][wd]:+.2f}")

WD = (1, 2, 3, 4)


def r100(x):
    return max(0, round(x / 100) * 100)


# B-pair: split each day's 15,000 by pair score (pair score = weekday average)
pair_score = {p: statistics.mean(SCORE[p][wd] for wd in WD) for p in PAIRS}
tot_pair = sum(max(0.0, s) for s in pair_score.values())
B_PAIR = {p: {wd: r100(15000 * max(0.0, pair_score[p]) / tot_pair) for wd in WD} for p in PAIRS}
# B-wd: per-pair bonus scaled by weekday score
wd_score = {wd: statistics.mean(SCORE[p][wd] for p in PAIRS) for wd in WD}
avg_wd = statistics.mean(wd_score.values())
B_WD = {p: {wd: r100(3000 * max(0.0, wd_score[wd]) / avg_wd) for wd in WD} for p in PAIRS}
# B-cell: proportional to cell score, average bonus = 3000
allcells = [SCORE[p][wd] for p in PAIRS for wd in WD]
avg_cell = statistics.mean(allcells)
B_CELL = {p: {wd: r100(3000 * max(0.0, SCORE[p][wd]) / avg_cell) for wd in WD} for p in PAIRS}
B0 = {p: {wd: 3000 for wd in WD} for p in PAIRS}

for name, tab in (("B-pair", B_PAIR), ("B-wd", B_WD), ("B-cell", B_CELL)):
    print(f"{name} bonus table (Tue/Wed/Thu/Fri):")
    for p in PAIRS:
        print(f"  {p}: {[tab[p][wd] for wd in WD]}")


def make_units(bonustab):
    def u(pair, d):
        base = BASE[pair][(d.weekday() + 1) % 7]
        if base <= 0:
            return 0.0
        b = bonustab[pair][d.weekday()] if d.day % 5 == 0 else 0
        return (base + b) * 1.4
    return u


DATES = sorted({d for _, d in trades})


def run(u, lo, hi):
    daily = {}
    for d in DATES:
        if not (lo <= d < hi):
            continue
        tot = sum(u(p, d) * trades[(p, d)] for p in PAIRS if (p, d) in trades) / 100.0
        if any(u(p, d) > 0 and (p, d) in trades for p in PAIRS):
            daily[d] = tot
    months = defaultdict(float)
    for d, v in daily.items():
        months[(d.year, d.month)] += v
    mv = list(months.values())
    mm = statistics.mean(mv)
    sm = statistics.pstdev(mv)
    return (f"total={sum(mv):+10.0f} worst-month={min(mv):+8.0f} t={mm / (sm / len(mv) ** 0.5):.2f} "
            f"worst-day={min(daily.values()):+8.0f}")


def maxexp(u):
    return max(sum(u(p, d) for p in PAIRS) for d in DATES if d.day % 5 == 0)


for elab, lo, hi in (("PRE", datetime.date(2015, 1, 1), POST0), ("POST", POST0, END),
                     ("FULL", datetime.date(2015, 1, 1), END)):
    print(f"===== {elab} =====")
    for name, tab in (("B0 uniform", B0), ("B-pair", B_PAIR), ("B-wd", B_WD), ("B-cell", B_CELL)):
        u = make_units(tab)
        print(f"  {name:10s}: {run(u, lo, hi)}  max-gotobi-exposure={maxexp(u):,.0f}")
