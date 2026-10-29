#!/usr/bin/env python3
"""Is the Friday 00:00 entry good when gotobi falls on Sat/Sun/Mon?
Current live config (IN JST 00:00 ask -> OUT 09:55 bid, Thu 05:35), long only,
price only, executable basis, exit dates Tue-Fri, data through 2026-10-22.
Friday classification:
  A: Friday itself is a calendar gotobi (day % 5 == 0)
  B: Friday is not gotobi, but Sat/Sun/Mon right after contains a gotobi date
     ("effective gotobi" - payment demand pulled forward to the last business day)
  C: other Fridays
Also checks Tuesdays right after a Monday gotobi (does the demand slip to Tue instead?).
"""
import os, sqlite3, statistics, datetime, math
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
JST = datetime.timezone(datetime.timedelta(hours=9))
PAIRS = ["USD_JPY","GBP_JPY","AUD_JPY","EUR_JPY","CHF_JPY"]
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
def load(inst):
    d = {}
    q = """SELECT t,bo,ao FROM m1 WHERE inst=? AND (
      (CAST(((t/60+540)%1440) AS INT)=0 AND bo IS NOT NULL) OR
      (CAST(((t/60+540)%1440) AS INT)=595) OR (CAST(((t/60+540)%1440) AS INT)=335)) ORDER BY t"""
    for t, bo, ao in con.execute(q, (inst,)):
        dt = datetime.datetime.fromtimestamp(t, JST)
        d.setdefault(dt.date(), {})[dt.hour*60+dt.minute] = (bo, ao)
    return d
def stats(v):
    if len(v) < 5: return f"n={len(v)} (too small)"
    m = statistics.mean(v); sd = statistics.pstdev(v)
    gp = sum(x for x in v if x > 0); gl = -sum(x for x in v if x < 0)
    return (f"n={len(v)} mean={m:+.2f}pip PF={gp/gl if gl else float('inf'):.3f} "
            f"t={m/(sd/math.sqrt(len(v))):.2f} win={sum(1 for x in v if x>0)/len(v)*100:.1f}%")
cutoff = datetime.date(2026, 10, 23)
for inst in PAIRS:
    data = load(inst)
    fri = {"A": [], "B": [], "C": []}
    tue_after_mon_gotobi, tue_other = [], []
    for d, mins in sorted(data.items()):
        if d >= cutoff: continue
        wd = d.weekday()
        if wd not in (1,2,3,4) or 0 not in mins: continue
        ex = 335 if wd == 3 else 595
        if ex not in mins: continue
        r = (mins[ex][0] - mins[0][1]) * 100  # bid out - ask in, pip
        if wd == 4:  # Friday
            if d.day % 5 == 0: fri["A"].append(r)
            elif any((d + datetime.timedelta(days=k)).day % 5 == 0 for k in (1,2,3)): fri["B"].append(r)
            else: fri["C"].append(r)
        if wd == 1:  # Tuesday
            (tue_after_mon_gotobi if (d - datetime.timedelta(days=1)).day % 5 == 0 else tue_other).append(r)
    print(f"== {inst} Fridays ==")
    print(f"  A calendar-gotobi Fri : {stats(fri['A'])}")
    print(f"  B effective-gotobi Fri: {stats(fri['B'])}")
    print(f"  C other Fri           : {stats(fri['C'])}")
    print(f"  Tue after Mon-gotobi  : {stats(tue_after_mon_gotobi)}")
    print(f"  Tue other             : {stats(tue_other)}")
con.close()
