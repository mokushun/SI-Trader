#!/usr/bin/env python3
"""Nakane USD_JPY before 2015: was the 'old days' lore true? (2026-11-03)

Data: ~/workspace/duka_hist/m1_usdjpy.db (user-downloaded 2026-11-03; same
OANDA feed as m1.db -- the Jan-2015 overlap of 1,836 bars matches the main
DB EXACTLY, diff 0.0 on bid close and spread). Dense M1 from 2005-01-02;
2002-2004 hold only 1-2 stub rows/day and are excluded.
Merged timeline: this file for <2015, main m1.db for >=2015 (dedupe by date).
Eras: A 2005-2009 ('lore' era), B 2010-2014 (when the nostalgic tweets were
posted), C 2015..2020-09, D 2020-10..2026-10. C and D must reproduce the
known unconditional results (-0.19 / +3.71) as a merge sanity check.
Logic identical to nakane_5pairs_from2015.py Part B for the trade itself.
"""
import os
import sqlite3
import statistics
import datetime

OLD = os.path.expanduser("~/workspace/duka_hist/m1_usdjpy.db")
MAIN = os.path.expanduser("~/workspace/oanda_json/m1.db")
JST = datetime.timezone(datetime.timedelta(hours=9))
MARKS = [0, 335, 480, 540, 570, 595, 630, 660, 720]
ERAS = [("A 2005-09", datetime.date(2005, 1, 1), datetime.date(2010, 1, 1)),
        ("B 2010-14", datetime.date(2010, 1, 1), datetime.date(2015, 1, 1)),
        ("C 2015-20.9", datetime.date(2015, 1, 1), datetime.date(2020, 10, 1)),
        ("D 2020.10-26", datetime.date(2020, 10, 1), datetime.date(2026, 10, 31))]


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
    return f"n={n} mean={mean:+.2f}pip PF={pf:.3f} t={t:.2f} win={win:.0f}%"


def mean_t(vals):
    if len(vals) < 2:
        return f"n={len(vals)}"
    m = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    t = m / (sd / (len(vals) ** 0.5)) if sd > 0 else 0.0
    return f"n={len(vals)} mean={m:+.2f} t={t:+.2f}"


def load(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    data = {}
    marks_sql = ",".join(str(m) for m in MARKS)
    for t, ao, bo in con.execute(
            f"SELECT t,ao,bo FROM m1 WHERE inst='USD_JPY' AND CAST(((t/60+540)%1440) AS INT) IN ({marks_sql}) ORDER BY t"):
        dt = datetime.datetime.fromtimestamp(t, JST)
        data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
    con.close()
    return data


DATA = load(OLD)
for d, mins in load(MAIN).items():
    if d >= datetime.date(2015, 1, 1):
        DATA.setdefault(d, {}).update(mins)


def mid(mins, m):
    ao, bo = mins[m]
    return (ao + bo) / 2


def era_of(d):
    for lab, lo, hi in ERAS:
        if lo <= d < hi:
            return lab
    return None


print("=== Part 1. Unconditional nakane trade (ask 00:00 -> bid 09:55, Thu 05:35), executable ===")
for lab, lo, hi in ERAS:
    allr, gr, nr = [], [], []
    for d, mins in DATA.items():
        if not (lo <= d < hi) or d.weekday() not in (1, 2, 3, 4) or 0 not in mins:
            continue
        outm = 335 if d.weekday() == 3 else 595
        if outm not in mins:
            continue
        r = (mins[outm][1] - mins[0][0]) * 100
        allr.append(r)
        (gr if d.day % 5 == 0 else nr).append(r)
    print(f"{lab}: ALL {stats(allr)}")
    print(f"        gotobi {stats(gr)}")
    print(f"        non    {stats(nr)}")

print()
print("=== Part 2. Fine segments (MID pip), Tue/Wed/Fri ===")
SEGS = [(0, 480, "00:00-08:00"), (480, 540, "08:00-09:00"), (540, 570, "09:00-09:30"),
        (570, 595, "09:30-09:55"), (540, 595, "09:00-09:55"), (0, 595, "00:00-09:55")]


def seg(s, e, lo, hi, cond=None):
    vals = []
    for d, mins in DATA.items():
        if lo <= d < hi and d.weekday() in (1, 2, 4) and s in mins and e in mins:
            if cond and not cond(d):
                continue
            vals.append((mid(mins, e) - mid(mins, s)) * 100)
    return vals


for s, e, lab in SEGS:
    cells = [f"{elab}: {mean_t(seg(s, e, lo, hi))}" for elab, lo, hi in ERAS]
    print(f"  {lab}: " + " | ".join(cells))
print("-- gotobi only")
for s, e, lab in SEGS:
    cells = [f"{elab}: {mean_t(seg(s, e, lo, hi, cond=lambda d: d.day % 5 == 0))}" for elab, lo, hi in ERAS]
    print(f"  {lab}: " + " | ".join(cells))

print()
print("=== Part 3. Post-fixing (MID pip), Tue/Wed/Fri ===")
for e, lab in ((630, "09:55-10:30"), (660, "09:55-11:00"), (720, "09:55-12:00")):
    cells = [f"{elab}: {mean_t(seg(595, e, lo, hi))}" for elab, lo, hi in ERAS]
    print(f"  {lab}: " + " | ".join(cells))
print("-- gotobi only")
for e, lab in ((630, "09:55-10:30"), (660, "09:55-11:00"), (720, "09:55-12:00")):
    cells = [f"{elab}: {mean_t(seg(595, e, lo, hi, cond=lambda d: d.day % 5 == 0))}" for elab, lo, hi in ERAS]
    print(f"  {lab}: " + " | ".join(cells))

print()
print("=== Part 4. Executable SHORT after fixing (sell bid 09:55 -> buy ask), Tue/Wed/Fri ===")
for e, lab in ((630, "to 10:30"), (660, "to 11:00")):
    for elab, lo, hi in ERAS:
        for glab, cond in (("ALL", None), ("GOTOBI", lambda d: d.day % 5 == 0)):
            rets = [(mins[595][1] - mins[e][0]) * 100
                    for d, mins in DATA.items()
                    if lo <= d < hi and d.weekday() in (1, 2, 4) and 595 in mins and e in mins
                    and (cond is None or cond(d))]
            print(f"  {lab} {elab} {glab}: {stats(rets)}")
