#!/usr/bin/env python3
"""
Seasonality first look (DESCRIPTIVE, not a fixed-definition verification).

Instruments in m1.db: SPX500_USD, NAS100_USD, JP225_USD (indices),
XAU_USD, WTICO_USD, NATGAS_USD (commodities).
Daily bars: UTC calendar day, last mid close. Daily log returns between
consecutive available days (weekend gaps included as one return).
Outputs:
 1) calendar-month seasonality: mean daily log return (bp/day) and
    average total return in that month per year (%), 2015-2026.
 2) US presidential cycle for indices: total return by calendar year,
    grouped by cycle year (Y1 = inauguration year 2017/2021/2025,
    Y2 = midterm 2018/2022/2026*, Y3 = 2015/2019/2023,
    Y4 = election 2016/2020/2024). *2026 is partial (to Oct 30).
 3) Post-midterm window: total return Nov(Y2)..Apr(Y3) for the two
    completed cycles (2018->19, 2022->23).
Sample warning: cycle buckets have n=3 years each; monthly buckets
n=11-12 years. This is a first look to size effects, not proof.
"""
import sqlite3, datetime, math
import numpy as np

DB = "/home/hatch/workspace/oanda_json/m1.db"
INSTS = ["SPX500_USD", "NAS100_USD", "JP225_USD", "XAU_USD", "WTICO_USD", "NATGAS_USD"]
T0 = int(datetime.datetime(2015, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
T1 = 1793400000

def daily(inst):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT (t/86400)*86400 AS d, bc, ac, max(t) FROM m1 WHERE inst=? AND t>=? AND t<? GROUP BY d",
        (inst, T0, T1)).fetchall()
    con.close()
    days = np.array([r[0] for r in rows], dtype=np.int64)
    mid = np.array([(r[1] + r[2]) / 2.0 for r in rows])
    return days, mid

data = {}
for inst in INSTS:
    d, m = daily(inst)
    data[inst] = (d, m)
    print(f"{inst}: {len(d)} daily bars, {datetime.datetime.utcfromtimestamp(d[0]).date()} .. {datetime.datetime.utcfromtimestamp(d[-1]).date()}")

print("\n=== 1) Monthly seasonality: bp/day (avg month total %/yr) ===")
months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
for inst in INSTS:
    d, m = data[inst]
    lr = np.diff(np.log(m))
    dts = [datetime.datetime.utcfromtimestamp(int(t)) for t in d[1:]]
    mon = np.array([x.month for x in dts]); yr = np.array([x.year for x in dts])
    cells = []
    for mo in range(1, 13):
        sel = mon == mo
        bp = lr[sel].mean() * 10000
        # average total in that month per year
        tots = [lr[sel & (yr == y)].sum() for y in sorted(set(yr[sel])) if (sel & (yr == y)).sum() >= 10]
        cells.append(f"{months[mo-1]} {bp:+6.1f} ({np.mean(tots)*100:+5.2f}%)")
    print(inst)
    for i in range(0, 12, 4):
        print("   " + " | ".join(cells[i:i+4]))

print("\n=== 2) Presidential cycle: total return % by calendar year (indices) ===")
def cycle_of(y):
    return {2015: 3, 2016: 4, 2017: 1, 2018: 2, 2019: 3, 2020: 4, 2021: 1,
            2022: 2, 2023: 3, 2024: 4, 2025: 1, 2026: 2}[y]
for inst in ["SPX500_USD", "NAS100_USD", "JP225_USD"]:
    d, m = data[inst]
    lr = np.diff(np.log(m))
    dts = [datetime.datetime.utcfromtimestamp(int(t)) for t in d[1:]]
    yr = np.array([x.year for x in dts])
    by_year = {y: lr[yr == y].sum() * 100 for y in range(2015, 2027)}
    print(inst, " ".join(f"{y}:{v:+.1f}%" for y, v in by_year.items()))
    for cy in (1, 2, 3, 4):
        vals = [v for y, v in by_year.items() if cycle_of(y) == cy]
        print(f"   cycle Y{cy}: years={['%+.1f' % v for v in vals]} mean={np.mean(vals):+.1f}%")

print("\n=== 3) Post-midterm window Nov(Y2)-Apr(Y3) total % ===")
for inst in INSTS:
    d, m = data[inst]
    logm = np.log(m)
    dts = [datetime.datetime.utcfromtimestamp(int(t)) for t in d]
    idx = {x.strftime("%Y-%m"): i for i, x in enumerate(dts)}
    out = []
    for y2 in (2018, 2022):
        a = idx.get(f"{y2}-10"); b = idx.get(f"{y2+1}-04")
        if a is not None and b is not None:
            out.append(f"{y2}->{y2+1}: {(logm[b]-logm[a])*100:+.1f}%")
    print(inst, " ".join(out))
