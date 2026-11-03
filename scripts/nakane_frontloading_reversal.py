#!/usr/bin/env python3
"""Nakane: front-loading of the buying + post-fixing reversal, by era (2026-11-03).

User's image to test:
 (a) In the old days the buying started ~09:30; over the years it was
     front-run earlier (09:00, then 08:00, then from midnight).
 (b) After the unnatural rise into the 09:55 fixing, price used to fall.
Population: Tue/Wed/Fri JST dates only (as in nakane_morning_path_eras.py;
 Thursdays have no 09:55 exit in the live logic).
Eras: E1 2015-2017, E2 2018..2020-09, E3 2020-10..2023, E4 2024..2026-10.
Part 1: MID segment means (pip) for fine late segments + the share of the
 whole window move completed by 09:00 / 09:30 (ratio of segment means).
Part 2: post-fixing MID returns 09:55 -> 10:30 / 11:00 / 12:00, unconditional,
 gotobi-only (USD_JPY), and conditional on a strong 09:00-09:55 rise (>=+5pip).
Part 3: executable SHORT after the fixing: sell BID-open 09:55, buy back
 ASK-open at 10:30 / 11:00 / 12:00 (the user traded a 09:56-short live for
 ~6 months and reported it failed; this checks whether it ever worked,
 and in which era).
"""
import os
import sqlite3
import statistics
import datetime

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
JST = datetime.timezone(datetime.timedelta(hours=9))
MARKS = [0, 480, 540, 570, 595, 630, 660, 720]
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
ERAS = [("E1 2015-17", datetime.date(2015, 1, 1), datetime.date(2018, 1, 1)),
        ("E2 2018-20.9", datetime.date(2018, 1, 1), datetime.date(2020, 10, 1)),
        ("E3 2020.10-23", datetime.date(2020, 10, 1), datetime.date(2024, 1, 1)),
        ("E4 2024-26", datetime.date(2024, 1, 1), datetime.date(2026, 10, 31))]


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
    return f"n={n} mean={mean:+.2f}pip PF={pf:.3f} t={t:.2f}"


def mean_t(vals):
    if len(vals) < 2:
        return f"n={len(vals)}"
    m = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    t = m / (sd / (len(vals) ** 0.5)) if sd > 0 else 0.0
    return f"n={len(vals)} mean={m:+.2f} t={t:+.2f}"


def load(inst):
    data = {}
    marks_sql = ",".join(str(m) for m in MARKS)
    for t, ao, bo in con.execute(
            f"SELECT t,ao,bo FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT) IN ({marks_sql}) ORDER BY t",
            (inst,)):
        dt = datetime.datetime.fromtimestamp(t, JST)
        data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
    return {d: mins for d, mins in data.items() if d.weekday() in (1, 2, 4)}


DATA = {inst: load(inst) for inst in PAIRS}


def mid(mins, m):
    ao, bo = mins[m]
    return (ao + bo) / 2


def seg(inst, s, e, lo, hi, cond=None):
    vals = []
    for d, mins in DATA[inst].items():
        if lo <= d < hi and s in mins and e in mins:
            if cond and not cond(d, mins):
                continue
            vals.append((mid(mins, e) - mid(mins, s)) * 100)
    return vals


print("=== Part 1. Fine late segments (MID pip) by era ===")
SEGS = [(0, 480, "00:00-08:00"), (480, 540, "08:00-09:00"), (540, 570, "09:00-09:30"),
        (570, 595, "09:30-09:55"), (540, 595, "09:00-09:55"), (0, 595, "00:00-09:55")]
for inst in ["USD_JPY"] + PAIRS[1:]:
    print(f"-- {inst}")
    for s, e, lab in SEGS:
        cells = []
        for elab, lo, hi in ERAS:
            cells.append(f"{elab}: {mean_t(seg(inst, s, e, lo, hi))}")
        print(f"  {lab}: " + " | ".join(cells))
print("-- pair-average of segment means")
for s, e, lab in SEGS:
    cells = []
    for elab, lo, hi in ERAS:
        ms = [statistics.mean(seg(inst, s, e, lo, hi)) for inst in PAIRS]
        cells.append(f"{elab}: {statistics.mean(ms):+.2f}")
    print(f"  {lab}: " + " | ".join(cells))
print("-- share of window move completed by 09:00 / 09:30 (pair-avg, ratio of means)")
for elab, lo, hi in ERAS:
    tot = statistics.mean([statistics.mean(seg(i, 0, 595, lo, hi)) for i in PAIRS])
    by540 = statistics.mean([statistics.mean(seg(i, 0, 540, lo, hi)) for i in PAIRS])
    by570 = statistics.mean([statistics.mean(seg(i, 0, 570, lo, hi)) for i in PAIRS])
    print(f"  {elab}: total {tot:+.2f}pip, by09:00 {by540 / tot * 100:.0f}%, by09:30 {by570 / tot * 100:.0f}%")

print()
print("=== Part 1b. USD_JPY gotobi-only fine segments (MID pip) ===")
for s, e, lab in SEGS:
    cells = []
    for elab, lo, hi in ERAS:
        v = seg("USD_JPY", s, e, lo, hi, cond=lambda d, m: d.day % 5 == 0)
        cells.append(f"{elab}: {mean_t(v)}")
    print(f"  {lab}: " + " | ".join(cells))

print()
print("=== Part 2. Post-fixing MID returns (pip) by era ===")
for inst in PAIRS:
    print(f"-- {inst}")
    for e, lab in ((630, "09:55-10:30"), (660, "09:55-11:00"), (720, "09:55-12:00")):
        cells = []
        for elab, lo, hi in ERAS:
            cells.append(f"{elab}: {mean_t(seg(inst, 595, e, lo, hi))}")
        print(f"  {lab}: " + " | ".join(cells))
print("-- pair-average")
for e, lab in ((630, "09:55-10:30"), (660, "09:55-11:00"), (720, "09:55-12:00")):
    cells = []
    for elab, lo, hi in ERAS:
        ms = [statistics.mean(seg(inst, 595, e, lo, hi)) for inst in PAIRS]
        cells.append(f"{elab}: {statistics.mean(ms):+.2f}")
    print(f"  {lab}: " + " | ".join(cells))
print("-- USD_JPY gotobi-only post-fixing")
for e, lab in ((630, "09:55-10:30"), (660, "09:55-11:00"), (720, "09:55-12:00")):
    cells = []
    for elab, lo, hi in ERAS:
        v = seg("USD_JPY", 595, e, lo, hi, cond=lambda d, m: d.day % 5 == 0)
        cells.append(f"{elab}: {mean_t(v)}")
    print(f"  {lab}: " + " | ".join(cells))
print("-- USD_JPY conditional: 09:00-09:55 rise >= +5pip, then post-fixing")
for e, lab in ((630, "09:55-10:30"), (660, "09:55-11:00"), (720, "09:55-12:00")):
    cells = []
    for elab, lo, hi in ERAS:
        v = seg("USD_JPY", 595, e, lo, hi,
                cond=lambda d, m: 540 in m and 595 in m and (mid(m, 595) - mid(m, 540)) * 100 >= 5)
        cells.append(f"{elab}: {mean_t(v)}")
    print(f"  {lab}: " + " | ".join(cells))

print()
print("=== Part 3. Executable SHORT after fixing (sell bid-open 09:55 -> buy ask-open) ===")
for inst in PAIRS:
    print(f"-- {inst}")
    for e, lab in ((630, "to 10:30"), (660, "to 11:00"), (720, "to 12:00")):
        cells = []
        for elab, lo, hi in ERAS:
            rets = [(mins[595][1] - mins[e][0]) * 100
                    for d, mins in DATA[inst].items()
                    if lo <= d < hi and 595 in mins and e in mins]
            cells.append(f"{elab}: {stats(rets)}")
        print(f"  {lab}:")
        for c in cells:
            print(f"      {c}")

con.close()
