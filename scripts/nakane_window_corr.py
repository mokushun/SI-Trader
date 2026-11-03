"""Nakane-window pairwise correlations, m1.db mid closes.
Entry 00:00 JST, exit 09:55 JST (Thu 05:35 JST), entry weekday Tue-Fri JST.
Mid = (bid close + ask close)/2. Price only; no spread, no swap.
Run: python3 nakane_window_corr.py  -> prints correlations + means.
"""
import sqlite3, math, itertools
from datetime import datetime, timezone, timedelta

con = sqlite3.connect('/home/hatch/workspace/oanda_json/m1.db')
JST = timezone(timedelta(hours=9))
pairs = ['USD_JPY', 'EUR_JPY', 'GBP_JPY', 'AUD_JPY', 'CHF_JPY']
data = {p: {} for p in pairs}
for p in pairs:
    cur = con.execute(
        "SELECT t,(bc+ac)/2.0 FROM m1 WHERE inst=? "
        "AND ((t+32400)%86400 IN (0,20100,35700))", (p,))
    for t, mid in cur:
        dt = datetime.fromtimestamp(t, tz=JST)
        tod = dt.hour * 3600 + dt.minute * 60
        if dt.weekday() not in (1, 2, 3, 4):
            continue
        rec = data[p].setdefault(dt.date().isoformat(), {})
        if tod == 0:
            rec['in'] = mid
        elif tod == 35700 and dt.weekday() != 3:
            rec['out'] = mid
        elif tod == 20100 and dt.weekday() == 3:
            rec['out'] = mid

rets = {p: {d: math.log(v['out'] / v['in'])
            for d, v in data[p].items() if 'in' in v and 'out' in v}
        for p in pairs}
common = sorted(set.intersection(*[set(r) for r in rets.values()]))
print('common Tue-Fri entry dates:', len(common), common[0], common[-1])


def corr(a, b):
    xs = [rets[a][d] for d in common]
    ys = [rets[b][d] for d in common]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            / math.sqrt(sum((x - mx) ** 2 for x in xs)
                        * sum((y - my) ** 2 for y in ys)))


cs = []
for a, b in itertools.combinations(pairs, 2):
    c = corr(a, b)
    cs.append(c)
    print(f"{a} / {b}: {c:+.3f}")
print('mean pairwise r:', round(sum(cs) / len(cs), 3))
for p in pairs:
    xs = [rets[p][d] for d in common]
    print(p, 'mean bp:', round(sum(xs) / len(xs) * 10000, 2))
