"""
Independent audit re-computation for London Fix manga vol.01.
Replicates the SI-Trader scripts' logic (fix = 16:00 Europe/London, open prices,
mid=(bid+ask)/2) but restricted to the findings' stated window
2020-10-01 .. 2026-10-28 (London dates, Mon-Fri), because the committed
scripts contain no start-date filter while the current m1.db starts 2015-01-01.
"""
import sqlite3, os
from math import sqrt
from datetime import date, timedelta
from zoneinfo import ZoneInfo
import statistics as st

DB = os.path.expanduser("~/workspace/oanda_json/m1.db")
LON = ZoneInfo("Europe/London")
PAIRS = {"GBP_USD": 10000.0, "EUR_USD": 10000.0, "USD_JPY": 100.0,
         "EUR_GBP": 10000.0, "GBP_JPY": 100.0, "AUD_USD": 10000.0, "USD_CHF": 10000.0}
USD_SIGN = {"GBP_USD": -1, "EUR_USD": -1, "USD_JPY": +1, "AUD_USD": -1, "USD_CHF": +1}
START, END = date(2020, 10, 1), date(2026, 10, 28)
# EUR_USD stated window starts 2020-01-01 in findings
START_EUR = date(2020, 1, 1)

def stats(v):
    v = [x for x in v if x is not None]
    if len(v) < 3: return (len(v), float('nan'), float('nan'), float('nan'))
    m = st.mean(v); sd = st.stdev(v)
    pos = sum(x for x in v if x > 0); neg = -sum(x for x in v if x < 0)
    return (len(v), m, pos/neg if neg > 0 else float('nan'), m/(sd/sqrt(len(v))) if sd > 0 else float('nan'))

def fmt(name, v):
    n, m, pf, t = stats(v)
    print(f"  {name:<34} n={n:>5} mean={m:+7.2f} PF={pf:.3f} t={t:+.2f}")

def fix_epoch(d):
    import datetime
    return int(datetime.datetime(d.year, d.month, d.day, 16, 0, tzinfo=LON).timestamp())

def last_bd_flags(dates):
    by = {}
    for d in dates: by[(d.year, d.month)] = d  # dates sorted, last wins (Mon-Fri only list)
    flags = {d: (by[(d.year, d.month)] == d) for d in dates}
    # The window ends 2026-10-28, mid-month: Oct 2026 is incomplete, so its
    # last in-window day is NOT a month-end (original outputs: ME n=72 =
    # Oct 2020..Sep 2026). Drop the spurious final-month flag.
    for d in dates:
        if (d.year, d.month) == (2026, 10): flags[d] = False
    return flags

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
usd_days = {}
for inst, pip in PAIRS.items():
    start = START_EUR if inst == "EUR_USD" else START
    # load only needed window (plus buffer)
    t0 = fix_epoch(start) - 3*3600; t1 = fix_epoch(END) + 3*3600
    rows = con.execute("SELECT t,bo,ao FROM m1 WHERE inst=? AND t>=? AND t<=? ORDER BY t", (inst, t0, t1)).fetchall()
    price = {int(t): (bo, ao) for t, bo, ao in rows}
    dates = []
    d = start
    while d <= END:
        if d.weekday() <= 4: dates.append(d)
        d += timedelta(days=1)
    me_flags = last_bd_flags(dates)
    pre, post, fade_mid, fade_exec, big_fade_exec, t5 = [], [], [], [], [], []
    pre_all = []  # for big threshold
    recs = []
    for d in dates:
        T = fix_epoch(d)
        def at(off): return price.get(T + off*60)
        p_m30, p_0, p_p30, p_m5 = at(-30), at(0), at(30), at(-5)
        if None in (p_m30, p_0, p_p30, p_m5): continue
        mid = lambda p: (p[0]+p[1])/2
        pre_mid = (mid(p_0)-mid(p_m30))*pip; post_mid = (mid(p_p30)-mid(p_0))*pip
        if pre_mid > 0: fex = (p_0[0]-p_p30[1])*pip
        elif pre_mid < 0: fex = (p_p30[0]-p_0[1])*pip
        else: fex = 0.0
        recs.append((d, pre_mid, post_mid, fex))
        pre.append(pre_mid); post.append(post_mid)
        fade_mid.append(- (1 if pre_mid>0 else -1 if pre_mid<0 else 0) * post_mid)
        fade_exec.append(fex); t5.append((mid(p_0)-mid(p_m5))*pip)
        if inst in USD_SIGN:
            lvl = mid(p_0)
            usd_days.setdefault(d, dict(me=me_flags[d], qe=me_flags[d] and d.month in (3,6,9,12), year=d.year, pre=[], post=[]))
            usd_days[d]["pre"].append(USD_SIGN[inst]*pre_mid/pip/lvl*10000)
            usd_days[d]["post"].append(USD_SIGN[inst]*post_mid/pip/lvl*10000)
    if recs:
        thr = sorted(abs(r[1]) for r in recs)[int(len(recs)*0.75)]
        big_fade_exec = [r[3] for r in recs if abs(r[1]) >= thr]
    print(f"\n=== {inst} (audit window {start}..{END}) ===")
    fmt("pre30 mid (T-30->T)", pre); fmt("post30 mid (T->T+30)", post)
    fmt("FADE30 mid gross", fade_mid); fmt("FADE30 exec exact", fade_exec)
    fmt("FADE30 big exec exact", big_fade_exec); fmt("T-5->T mid", t5)
    # month-end post mid in USD direction if applicable
    me_post_raw = [r[2] for r in recs if me_flags[r[0]]]
    if inst in USD_SIGN:
        fmt("ME post30 mid (USD dir)", [USD_SIGN[inst]*x for x in me_post_raw])
        # month-end exec USD buy
        me_exec = []
        for d, pm, pom, _ in recs:
            if not me_flags[d]: continue
            T = fix_epoch(d); p0 = price.get(T); p30 = price.get(T+1800)
            if p0 is None or p30 is None: continue
            if USD_SIGN[inst] > 0: me_exec.append((p30[0]-p0[1])*pip)
            else: me_exec.append((p0[0]-p30[1])*pip)
        fmt("ME USD-buy exec T->T+30", me_exec)
        fmt("ME USD-buy exec <=2023", [x for (d, *_), x in zip([r for r in recs if me_flags[r[0]]], me_exec) if d.year <= 2023])
        fmt("ME USD-buy exec 2024+", [x for (d, *_), x in zip([r for r in recs if me_flags[r[0]]], me_exec) if d.year >= 2024])

recs = []
for d, v in sorted(usd_days.items()):
    if len(v["pre"]) < 4: continue
    recs.append((d, v))
print(f"\n=== USD composite (bp) ===")
fmt("post30 USD month-end", [sum(v['post'])/len(v['post']) for d, v in recs if v['me']])
fmt("post30 USD non-ME", [sum(v['post'])/len(v['post']) for d, v in recs if not v['me']])
fmt("pre30 USD month-end", [sum(v['pre'])/len(v['pre']) for d, v in recs if v['me']])
fmt("post30 USD quarter-end", [sum(v['post'])/len(v['post']) for d, v in recs if v['qe']])
fmt("post30 USD ME <=2023", [sum(v['post'])/len(v['post']) for d, v in recs if v['me'] and v['year'] <= 2023])
fmt("post30 USD ME 2024+", [sum(v['post'])/len(v['post']) for d, v in recs if v['me'] and v['year'] >= 2024])
con.close()
print("\nAUDIT DONE")
