#!/usr/bin/env python3
"""Nakane morning path by era (2026-11-03).

Question (user): lore says nakane "worked in the old days", and the buying
felt concentrated late in the morning (from 08:00-09:00 toward the 09:55
fixing). The 00:00-entry re-verification found no PRE-2020 edge - but a
00:00 entry would miss a purely late-morning effect if the early hours
drifted the other way. This script decomposes the morning by era.

Population: exit dates Tue/Wed/Fri only (JST) - Thursdays are excluded so
the whole path runs to 09:55 (Thu exits 05:35 in the live logic).
Eras: PRE19 = 2015..2019, PRE = 2015-01-06..2020-09-30, POST = 2020-10-01..2026-10-30.

Part 1 (price movement, MID bar-open, pip): segment means per era.
  Segments are computed on the sessions where both endpoint bars exist
  (n reported per segment; same approach as usdjpy_nakane_path.py -
  06:00/07:00 bars are missing in many sessions around the daily break,
  so early segments have smaller n).
Part 2 (executable trades): entry ASK at 00:00 / 06:00 / 07:00 / 08:00 /
  08:30 / 09:00 / 09:30 -> exit BID at 09:55, same Tue/Wed/Fri population,
  stats identical to nakane_5pairs_firstpass.py (PF, population-sd t).
Part 3: USD_JPY gotobi-only segment decomposition PRE vs POST.
"""
import os
import sqlite3
import statistics
import datetime

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
JST = datetime.timezone(datetime.timedelta(hours=9))
MARKS = [0, 60, 120, 180, 240, 300, 360, 420, 480, 510, 540, 570, 595]
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
    return f"n={n} mean={mean:+.2f}pip PF={pf:.3f} t={t:.2f}"


def mean_t(vals):
    if len(vals) < 2:
        return f"n={len(vals)}"
    m = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    t = m / (sd / (len(vals) ** 0.5)) if sd > 0 else 0.0
    return f"n={len(vals)} mean={m:+.2f}pip t={t:+.2f}"


def load(inst):
    data = {}
    marks_sql = ",".join(str(m) for m in MARKS)
    for t, ao, bo in con.execute(
            f"SELECT t,ao,bo FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT) IN ({marks_sql}) ORDER BY t",
            (inst,)):
        dt = datetime.datetime.fromtimestamp(t, JST)
        data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
    # Tue/Wed/Fri only
    return {d: mins for d, mins in data.items() if d.weekday() in (1, 2, 4)}


DATA = {inst: load(inst) for inst in PAIRS}
ERAS = [("PRE19 2015-19", datetime.date(2015, 1, 1), datetime.date(2020, 1, 1)),
        ("PRE ..2020-09", datetime.date(2015, 1, 1), datetime.date(2020, 10, 1)),
        ("POST 2020-10..", datetime.date(2020, 10, 1), datetime.date(2026, 10, 31))]
SEGMENTS = [(0, 480, "00:00-08:00"), (480, 540, "08:00-09:00"), (540, 595, "09:00-09:55"),
            (480, 595, "08:00-09:55"), (0, 595, "00:00-09:55")]

print("=== Part 1. Morning segments (MID, pip), Tue/Wed/Fri, by era ===")
for inst in PAIRS:
    print(f"-- {inst}")
    for s, e, lab in SEGMENTS:
        cells = []
        for elab, lo, hi in ERAS:
            vals = [((mins[e][0] + mins[e][1]) / 2 - (mins[s][0] + mins[s][1]) / 2) * 100
                    for d, mins in DATA[inst].items() if lo <= d < hi and s in mins and e in mins]
            cells.append(f"{elab}: {mean_t(vals)}")
        print(f"  {lab}: " + " | ".join(cells))

print()
print("=== Part 1b. Pair-average of segment means (pip, simple average over 5 pairs) ===")
for s, e, lab in SEGMENTS:
    cells = []
    for elab, lo, hi in ERAS:
        ms = []
        for inst in PAIRS:
            vals = [((mins[e][0] + mins[e][1]) / 2 - (mins[s][0] + mins[s][1]) / 2) * 100
                    for d, mins in DATA[inst].items() if lo <= d < hi and s in mins and e in mins]
            if vals:
                ms.append(statistics.mean(vals))
        cells.append(f"{elab}: {statistics.mean(ms):+.2f}")
    print(f"  {lab}: " + " | ".join(cells))

print()
print("=== Part 1c. Hourly segments for USD_JPY (MID, pip) ===")
HOURLY = [(h * 60, (h + 1) * 60, f"{h:02d}:00-{h+1:02d}:00") for h in range(0, 9)] + [(540, 595, "09:00-09:55")]
for s, e, lab in HOURLY:
    cells = []
    for elab, lo, hi in ERAS:
        vals = [((mins[e][0] + mins[e][1]) / 2 - (mins[s][0] + mins[s][1]) / 2) * 100
                for d, mins in DATA["USD_JPY"].items() if lo <= d < hi and s in mins and e in mins]
        cells.append(f"{elab}: {mean_t(vals)}")
    print(f"  {lab}: " + " | ".join(cells))

print()
print("=== Part 2. Late entries -> 09:55 exit (executable ask-in/bid-out), Tue/Wed/Fri ===")
for inst in PAIRS:
    print(f"-- {inst}")
    for entry in (0, 360, 420, 480, 510, 540, 570):
        cells = []
        for elab, lo, hi in ERAS:
            rets = [(mins[595][1] - mins[entry][0]) * 100
                    for d, mins in DATA[inst].items() if lo <= d < hi and entry in mins and 595 in mins]
            cells.append(f"{elab}: {stats(rets)}")
        hh, mm = divmod(entry, 60)
        print(f"  IN {hh:02d}:{mm:02d}:")
        for c in cells:
            print(f"      {c}")

print()
print("=== Part 3. USD_JPY gotobi-only segments (MID, pip) ===")
for s, e, lab in SEGMENTS:
    cells = []
    for elab, lo, hi in (ERAS[1], ERAS[2]):
        vals = [((mins[e][0] + mins[e][1]) / 2 - (mins[s][0] + mins[s][1]) / 2) * 100
                for d, mins in DATA["USD_JPY"].items()
                if lo <= d < hi and d.day % 5 == 0 and s in mins and e in mins]
        cells.append(f"{elab}: {mean_t(vals)}")
    print(f"  {lab}: " + " | ".join(cells))

con.close()
