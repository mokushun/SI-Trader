#!/usr/bin/env python3
"""
Currency strength -- 4h / 8h bars (fixed definition, follow-up pass).

Same strength definition as currency_strength_firstpass.py (mean log
return vs the currency's other 4 pairs; 5 currencies, 10 pairs), but on
4h and 8h bars with longer lookbacks/holds.

Weekend handling (declared): at this scale the Friday->Monday move is
real price movement, so windows are SEQUENCE-based (last L available
bars), with a span cap that allows at most one weekend in a window:
  lookback span ts[i]-ts[i-L]      <= L*TF + 52h
  entry gap   ts[i+1]-ts[i]        <= TF + 52h
  hold span   ts[i+H]-ts[i+1]      <= (H-1)*TF + 52h
Trades whose entry->exit span includes a weekend are counted and
reported (share). Swap is NOT included -- caveat: holds up to 48h and
weekend holds occur; typical swap is a few bp/day at most for these
pairs, sign depends on direction/pair.

Bars: M1 aggregated, UTC-aligned (4h JST starts 01/05/09/13/17/21,
8h JST starts 01/09/17). Bar needs >= 60% of its minutes in every pair.
Entry next bar open (ask long / bid short), exit close of H-th bar
(bid long / ask short): one spread paid.
Grid (predeclared):
  4h: L in {2, 6, 12, 24} (=8h/24h/48h/96h), H in {1, 3, 6} (=4h/12h/24h)
  8h: L in {3, 6, 12}     (=24h/48h/96h),   H in {1, 3, 6} (=8h/24h/48h)
Period 2024-01-01..2026-10-30 (warmup 2023-11-01), per-year momentum
splits, plus a descriptive split by decision-bar JST start hour for one
mid config per TF (4h: L=12,H=6; 8h: L=6,H=3 -- declared in advance).
"""
import sqlite3, math, json, datetime
import numpy as np

DB = "/home/hatch/workspace/oanda_json/m1.db"
CUR = ["USD", "JPY", "EUR", "GBP", "AUD"]
PAIRS = ["USD_JPY", "EUR_USD", "GBP_USD", "AUD_USD", "EUR_JPY",
         "GBP_JPY", "AUD_JPY", "EUR_GBP", "EUR_AUD", "GBP_AUD"]
BASE = [p.split("_")[0] for p in PAIRS]
QUOT = [p.split("_")[1] for p in PAIRS]
PAIR_IDX = {}
for k, p in enumerate(PAIRS):
    PAIR_IDX[(BASE[k], QUOT[k])] = (k, +1)
    PAIR_IDX[(QUOT[k], BASE[k])] = (k, -1)
INC = np.zeros((len(CUR), len(PAIRS)))
for j in range(len(PAIRS)):
    INC[CUR.index(BASE[j]), j] = +1.0
    INC[CUR.index(QUOT[j]), j] = -1.0

T_START = 1698796800   # 2023-11-01 UTC
T_EVAL = 1704067200    # 2024-01-01 UTC
T_END = 1793400000
WKND = 52 * 3600

def load_bars(tf):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    for p in PAIRS:
        first = {}
        for b, bo, ao, cnt, _ in con.execute(
                "SELECT (t/?)*? AS b, bo, ao, count(*), min(t) FROM m1 "
                "WHERE inst=? AND t>=? AND t<? GROUP BY b", (tf, tf, p, T_START, T_END)):
            first[b] = (bo, ao, cnt)
        last = {}
        for b, bc, ac, _ in con.execute(
                "SELECT (t/?)*? AS b, bc, ac, max(t) FROM m1 "
                "WHERE inst=? AND t>=? AND t<? GROUP BY b", (tf, tf, p, T_START, T_END)):
            last[b] = (bc, ac)
        d = {}
        min_min = int(0.6 * (tf // 60))
        for b, (bo, ao, cnt) in first.items():
            if cnt < min_min or b not in last:
                continue
            bc, ac = last[b]
            d[b] = (bo, ao, bc, ac)
        out[p] = d
    con.close()
    return out

def build(tf):
    bars = load_bars(tf)
    common = set(bars[PAIRS[0]])
    for p in PAIRS[1:]:
        common &= set(bars[p])
    ts = np.array(sorted(common), dtype=np.int64)
    n = len(ts)
    bid_o = np.empty((n, len(PAIRS))); ask_o = np.empty((n, len(PAIRS)))
    bid_c = np.empty((n, len(PAIRS))); ask_c = np.empty((n, len(PAIRS)))
    for j, p in enumerate(PAIRS):
        d = bars[p]
        for i, t in enumerate(ts):
            bo, ao, bc, ac = d[int(t)]
            bid_o[i, j], ask_o[i, j], bid_c[i, j], ask_c[i, j] = bo, ao, bc, ac
    return ts, bid_o, ask_o, bid_c, ask_c, (bid_c + ask_c) / 2.0, (bid_o + ask_o) / 2.0

def stats(rets):
    rets = np.array(rets, dtype=float)
    if len(rets) == 0:
        return None
    mu = rets[:, 0].mean(); sd = rets[:, 0].std(ddof=1) if len(rets) > 1 else 0.0
    return dict(n=int(len(rets)), net_pct=float(mu * 100),
                median_pct=float(np.median(rets[:, 0]) * 100),
                win_pct=float((rets[:, 0] > 0).mean() * 100),
                gross_pct=float(rets[:, 1].mean() * 100),
                wknd_share=float(rets[:, 2].mean() * 100),
                t_naive=float(mu / (sd / math.sqrt(len(rets)))) if sd > 0 else 0.0)

def run(tf, tf_name, lookbacks, holds, mid_cfg):
    ts, bid_o, ask_o, bid_c, ask_c, mid_c, mid_o = build(tf)
    n = len(ts)
    logmid = np.log(mid_c)
    yrs = np.array([datetime.datetime.utcfromtimestamp(int(t)).year for t in ts])
    jst_hour = np.array([datetime.datetime.utcfromtimestamp(int(t) + 9 * 3600).hour for t in ts])
    results = []
    for L in lookbacks:
        r = logmid[L:] - logmid[:-L]
        strength = r @ INC.T / 4.0
        dec = np.arange(L, n)
        lb_ok = (ts[dec] - ts[dec - L]) <= L * tf + WKND
        smax = np.argmax(strength, axis=1); smin = np.argmin(strength, axis=1)
        for H in holds:
            mom, con, wk_flags = [], [], []
            mom_by_year = {2024: [], 2025: [], 2026: []}
            mom_by_hour = {}
            for k, i in enumerate(dec):
                if not lb_ok[k] or ts[i] < T_EVAL:
                    continue
                e, x = i + 1, i + H
                if x >= n:
                    continue
                if ts[e] - ts[i] > tf + WKND:
                    continue
                if ts[x] - ts[e] > (H - 1) * tf + WKND:
                    continue
                strong, weak = CUR[smax[k]], CUR[smin[k]]
                pj, orient = PAIR_IDX[(strong, weak)]
                wk = 1.0 if ts[x] - ts[e] > (H - 1) * tf + 3600 else 0.0
                if orient == +1:
                    net_m = bid_c[x, pj] / ask_o[e, pj] - 1.0
                    net_c = (bid_o[e, pj] - ask_c[x, pj]) / bid_o[e, pj]
                else:
                    net_m = (bid_o[e, pj] - ask_c[x, pj]) / bid_o[e, pj]
                    net_c = bid_c[x, pj] / ask_o[e, pj] - 1.0
                gross = orient * (mid_c[x, pj] / mid_o[e, pj] - 1.0)
                mom.append((net_m, gross, wk)); con.append((net_c, -gross, wk))
                if yrs[i] in mom_by_year:
                    mom_by_year[yrs[i]].append((net_m, gross, wk))
                if (tf_name, L, H) == mid_cfg:
                    mom_by_hour.setdefault(int(jst_hour[i]), []).append((net_m, gross, wk))
            row = dict(tf=tf_name, L=L, H=H)
            s = stats(mom)
            if s: row["momentum"] = s
            s = stats(con)
            if s: row["contrarian"] = s
            for y, lst in mom_by_year.items():
                s = stats(lst)
                if s: row[f"momentum_{y}"] = s
            if mom_by_hour:
                row["by_jst_hour"] = {str(h): stats(v) for h, v in sorted(mom_by_hour.items())}
            results.append(row)
            m = row.get("momentum"); c = row.get("contrarian")
            if m and c:
                print(f"{tf_name} L={L:>2} H={H} mom n={m['n']:>5} net={m['net_pct']:>8.4f} gross={m['gross_pct']:>8.4f} win={m['win_pct']:.1f} t={m['t_naive']:>6.2f} wknd={m['wknd_share']:.0f}% | con net={c['net_pct']:>8.4f} gross={c['gross_pct']:>8.4f}")
    return results

all_res = []
all_res += run(14400, "4h", [2, 6, 12, 24], [1, 3, 6], ("4h", 12, 6))
all_res += run(28800, "8h", [3, 6, 12], [1, 3, 6], ("8h", 6, 3))
with open("/home/hatch/workspace/SI-Trader/outputs/currency_strength_longtf.json", "w") as f:
    json.dump(all_res, f, indent=1)
print("\n=== by JST start hour (mid configs, momentum) ===")
for r in all_res:
    if "by_jst_hour" in r:
        print(f"-- {r['tf']} L={r['L']} H={r['H']}")
        for h, s in r["by_jst_hour"].items():
            if s: print(f"  start {int(h):02d} JST n={s['n']:>4} net={s['net_pct']:>8.4f} gross={s['gross_pct']:>8.4f} win={s['win_pct']:.1f}")
print("\n=== momentum by year ===")
for r in all_res:
    for y in (2024, 2025, 2026):
        s = r.get(f"momentum_{y}")
        if s: print(f"{r['tf']} L={r['L']:>2} H={r['H']} {y} n={s['n']:>5} net={s['net_pct']:>8.4f} gross={s['gross_pct']:>8.4f} t={s['t_naive']:>6.2f}")
