#!/usr/bin/env python3
"""Nakane VOLS: POST-aware strict allocation normalized to ~10,000/day (2026-11-03).

User instruction: the strict table ignored POST; rebuild it including POST,
scaled so one day's orders are around 10,000 units.
Method (stated so the user can change the blend):
  score(pair, weekday) = (FULL non-gotobi mean + POST non-gotobi mean) / 2
    -- POST is a subset of FULL, so this double-weights the recent era
       without discarding the era-insurance of the long run.
  Per weekday: total base = 10,000, split in proportion to positive scores;
  non-positive cells get the 100 floor; a single cell is capped at 5,000
  (half the day) with the excess redistributed by score.
  Friday has only ONE positive cell (CHF) under this score, so the cap binds
  and Friday lands at 5,400 -- forcing it to 10,000 would put 9,600 on a
  single statistically weak cell; that variant (ADJ-Fri10k) is evaluated
  separately for comparison.
Evaluation: live-weight portfolio (bonus +3000, x1.4) vs CUR and PROP2.
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


def nonmean(pair, wd, lo, hi):
    v = [r for (p, d), r in trades.items()
         if p == pair and d.weekday() == wd and lo <= d < hi and d.day % 5 != 0]
    return statistics.mean(v) if v else 0.0


print("Non-gotobi weekday means (pip): FULL / POST / score=(FULL+POST)/2")
SCORE = {}
for p in PAIRS:
    SCORE[p] = {}
    for wd in (1, 2, 3, 4):
        f = nonmean(p, wd, datetime.date(2015, 1, 1), END)
        po = nonmean(p, wd, POST0, END)
        SCORE[p][wd] = (f + po) / 2
        print(f"  {p} wd={wd+1}: FULL {f:+.2f} | POST {po:+.2f} | score {SCORE[p][wd]:+.2f}")

FLOOR, CAP, TARGET = 100.0, 5000.0, 10000.0


def allocate(scores, target=TARGET, cap=CAP):
    """scores: {pair: score} for one weekday -> {pair: base units}"""
    pos = {p: s for p, s in scores.items() if s > 0}
    alloc = {p: FLOOR for p in scores}
    budget = target - FLOOR * (len(scores) - len(pos))
    capped = {}
    active = dict(pos)
    while active:
        tot = sum(active.values())
        over = [p for p, s in active.items() if budget * s / tot > cap]
        if not over:
            for p, s in active.items():
                alloc[p] = budget * s / tot
            break
        for p in over:
            alloc[p] = cap
            capped[p] = cap
            budget -= cap
            del active[p]
    return {p: round(v / 10) * 10 for p, v in alloc.items()}


ADJ = {p: [0, 0, 0, 0, 0, 0, 0] for p in PAIRS}
for wd in (1, 2, 3, 4):
    a = allocate({p: SCORE[p][wd] for p in PAIRS})
    for p in PAIRS:
        ADJ[p][wd + 1] = int(a[p])
ADJ_FRI10K = {p: list(v) for p, v in ADJ.items()}
fri = allocate({p: SCORE[p][4] for p in PAIRS}, target=10000.0, cap=10**9)
for p in PAIRS:
    ADJ_FRI10K[p][5] = int(fri[p])

CUR = {"AUD_JPY": [0, 0, 2800, 2500, 2300, 2000, 0], "CHF_JPY": [0, 0, 100, 100, 100, 100, 0],
       "EUR_JPY": [0, 0, 2700, 2400, 2200, 1900, 0], "GBP_JPY": [0, 0, 3200, 2800, 2500, 2200, 0],
       "USD_JPY": [0, 0, 3100, 2800, 2500, 2200, 0]}
PROP2 = {"AUD_JPY": [0, 0, 100, 1000, 200, 100, 0], "CHF_JPY": [0, 0, 100, 100, 100, 1000, 0],
         "EUR_JPY": [0, 0, 3200, 3200, 3200, 1500, 0], "GBP_JPY": [0, 0, 3400, 3400, 3400, 100, 0],
         "USD_JPY": [0, 0, 3600, 3600, 3600, 1500, 0]}

print()
print("Adjusted table (base VOLS, Tue/Wed/Thu/Fri) and day totals:")
for wd, lab in ((2, "Tue"), (3, "Wed"), (4, "Thu"), (5, "Fri")):
    print(f"  {lab} total = {sum(ADJ[p][wd] for p in PAIRS)}")
for p in PAIRS:
    print(f"  {p}: {[ADJ[p][wd] for wd in (2, 3, 4, 5)]}")
print("ADJ-Fri10k Friday:", {p: ADJ_FRI10K[p][5] for p in PAIRS})


def wd_sun(d):
    return (d.weekday() + 1) % 7


def units(tab, pair, d):
    base = tab[pair][wd_sun(d)]
    return (base + (3000 if d.day % 5 == 0 else 0)) * 1.4 if base > 0 else 0.0


DATES = sorted({d for _, d in trades})


def run(tab, lo, hi):
    daily = {}
    for d in DATES:
        if not (lo <= d < hi):
            continue
        tot = sum(units(tab, p, d) * trades[(p, d)] for p in PAIRS if (p, d) in trades) / 100.0
        if any(units(tab, p, d) > 0 and (p, d) in trades for p in PAIRS):
            daily[d] = tot
    months = defaultdict(float)
    for d, v in daily.items():
        months[(d.year, d.month)] += v
    mv = list(months.values())
    mean_m = statistics.mean(mv)
    sd_m = statistics.pstdev(mv)
    return (f"total={sum(mv):+10.0f}yen month={mean_m:+7.0f} worst-month={min(mv):+8.0f} "
            f"pos={sum(1 for v in mv if v > 0) / len(mv) * 100:.0f}% t={mean_m / (sd_m / len(mv) ** 0.5):.2f} "
            f"worst-day={min(daily.values()):+8.0f}")


print()
for elab, lo, hi in (("PRE", datetime.date(2015, 1, 1), POST0), ("POST", POST0, END),
                     ("FULL", datetime.date(2015, 1, 1), END)):
    print(f"===== {elab} =====")
    for name, tab in (("CUR", CUR), ("PROP2", PROP2), ("ADJ", ADJ), ("ADJ-Fri10k", ADJ_FRI10K)):
        print(f"  {name:10s}: {run(tab, lo, hi)}")
print()
print("Exposure (units/day incl x1.4, base/gotobi):")
for name, tab in (("CUR", CUR), ("PROP2", PROP2), ("ADJ", ADJ)):
    cells = []
    for wd in (2, 3, 4, 5):
        b = sum(tab[p][wd] for p in PAIRS) * 1.4
        g = (sum(tab[p][wd] for p in PAIRS) + 15000) * 1.4
        cells.append(f"wd{wd}: {b:,.0f}/{g:,.0f}")
    print(f"  {name:10s} " + " | ".join(cells))
