#!/usr/bin/env python3
"""Nakane long re-cut for manga vol.01 (2026-11-26).

Purpose: reader-friendly period cuts for the manga script. Same population
and definitions as nakane_morning_path_eras.py Part 2 (USD_JPY, exit dates
Tue/Wed/Fri JST, executable ask-in at entry -> bid-out at 09:55, m1.db),
but periods are calendar blocks a reader can understand:
  FULL 2015-2026 / last-10y 2017-2026 / 2015-2020 / 2021-2026,
plus the original era split (PRE ..2020-09 / POST 2020-10..) for reference.
Entries: 09:00 (the SBI textbook time) and 00:00. Subsets: all days, gotobi
(JST date day % 5 == 0). Yen conversion used in the manga: at 10,000 units
of USD/JPY, 1 pip = 100 yen.
"""
import os
import sqlite3
import statistics
import datetime

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
JST = datetime.timezone(datetime.timedelta(hours=9))
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

data = {}
for t, ao, bo in con.execute(
        "SELECT t,ao,bo FROM m1 WHERE inst='USD_JPY' "
        "AND CAST(((t/60+540)%1440) AS INT) IN (0,540,595) ORDER BY t"):
    dt = datetime.datetime.fromtimestamp(t, JST)
    data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
# Tue/Wed/Fri exit dates only (Thu exits 05:35 in the live logic)
data = {d: m for d, m in data.items() if d.weekday() in (1, 2, 4)}


def stats(rets):
    n = len(rets)
    mean = statistics.mean(rets)
    gp = sum(r for r in rets if r > 0)
    gl = -sum(r for r in rets if r < 0)
    pf = gp / gl if gl > 0 else float("inf")
    sd = statistics.pstdev(rets) if n > 1 else 0.0
    t = mean / (sd / (n ** 0.5)) if sd > 0 else 0.0
    return (f"n={n} mean={mean:+.2f}pip ({mean * 100:+,.0f}yen/trade) "
            f"PF={pf:.3f} t={t:.2f} total={sum(rets) * 100:+,.0f}yen")


WINDOWS = [
    ("FULL 2015-2026", datetime.date(2015, 1, 1), datetime.date(2026, 10, 31)),
    ("LAST10Y 2017-2026", datetime.date(2017, 1, 1), datetime.date(2026, 10, 31)),
    ("2015-2020 cal", datetime.date(2015, 1, 1), datetime.date(2021, 1, 1)),
    ("2021-2026 cal", datetime.date(2021, 1, 1), datetime.date(2026, 10, 31)),
    ("PRE ..2020-09", datetime.date(2015, 1, 1), datetime.date(2020, 10, 1)),
    ("POST 2020-10..", datetime.date(2020, 10, 1), datetime.date(2026, 10, 31)),
]

for entry in (540, 0):
    print(f"== IN {entry // 60:02d}:{entry % 60:02d} -> 09:55 exit, USD_JPY, Tue/Wed/Fri ==")
    for lab, lo, hi in WINDOWS:
        for glab, g in (("all", lambda d: True), ("gotobi", lambda d: d.day % 5 == 0)):
            rets = [(m[595][1] - m[entry][0]) * 100
                    for d, m in data.items()
                    if lo <= d < hi and g(d) and entry in m and 595 in m]
            print(f"  {lab} {glab}: {stats(rets)}")

con.close()
