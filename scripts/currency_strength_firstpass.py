#!/usr/bin/env python3
"""
Currency-strength first pass (fixed definition, SI-Trader convention).

Currencies: USD, JPY, EUR, GBP, AUD -- all 10 crosses exist in m1.db.
Strength at bar close t: for each pair, log return of mid close over the
last L bars, r. Base currency gets +r, quote gets -r; a currency's score
is the mean over its 4 pairs (scores sum to zero by construction).
Trade: strongest vs weakest currency pair, direct cross only.
  momentum    = long strongest / short weakest
  contrarian  = the reverse trade (computed separately, incl. spread)
Entry: next bar open (ask if long the pair, bid if short).
Exit : close of the H-th bar after entry bar start... precisely:
       entry bar = i+1, exit at close of bar i+H (H bars held).
Costs: bid/ask used at entry and exit, so one full spread is paid.
       No swap, no fees/slippage beyond spread.
Contiguity: lookback window and holding window must be consecutive bars
(timestamps differ by exactly the timeframe) -- this drops trades whose
window spans a weekend/holiday gap.
Bars: aggregated from M1 (UTC-aligned). A bar needs >= 60% of its minutes.
Period: 2024-01-01 to 2026-10-30 (data end), warmup from 2023-12-01.
Timezone note: bars are UTC-aligned; no session filter in this pass.
"""
import sqlite3, math, json, sys
import numpy as np

DB = "/home/hatch/workspace/oanda_json/m1.db"
CUR = ["USD", "JPY", "EUR", "GBP", "AUD"]
PAIRS = ["USD_JPY", "EUR_USD", "GBP_USD", "AUD_USD", "EUR_JPY",
         "GBP_JPY", "AUD_JPY", "EUR_GBP", "EUR_AUD", "GBP_AUD"]
BASE = [p.split("_")[0] for p in PAIRS]
QUOT = [p.split("_")[1] for p in PAIRS]
PAIR_IDX = {}
for k, p in enumerate(PAIRS):
    PAIR_IDX[(BASE[k], QUOT[k])] = (k, +1)   # long pair = long base/short quote
    PAIR_IDX[(QUOT[k], BASE[k])] = (k, -1)

T_START = 1701388800   # 2023-12-01 UTC (warmup)
T_EVAL = 1704067200    # 2024-01-01 UTC
T_END = 1793400000     # > 2026-10-30

def load_bars(tf_sec):
    """Return dict pair -> dict bar_t -> (bid_o, ask_o, bid_c, ask_c, nmin)
    using SQLite's min/max bare-column behaviour."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    div = tf_sec // 60
    for p in PAIRS:
        first = {}
        q1 = ("SELECT (t/?)*? AS b, bo, ao, count(*), min(t) FROM m1 "
              "WHERE inst=? AND t>=? AND t<? GROUP BY b")
        for b, bo, ao, cnt, _ in con.execute(q1, (tf_sec, tf_sec, p, T_START, T_END)):
            first[b] = (bo, ao, cnt)
        last = {}
        q2 = ("SELECT (t/?)*? AS b, bc, ac, max(t) FROM m1 "
              "WHERE inst=? AND t>=? AND t<? GROUP BY b")
        for b, bc, ac, _ in con.execute(q2, (tf_sec, tf_sec, p, T_START, T_END)):
            last[b] = (bc, ac)
        d = {}
        min_min = int(0.6 * div)
        for b, (bo, ao, cnt) in first.items():
            if cnt < min_min or b not in last:
                continue
            bc, ac = last[b]
            d[b] = (bo, ao, bc, ac)
        out[p] = d
    con.close()
    return out

def build_arrays(tf_sec):
    bars = load_bars(tf_sec)
    common = set(bars[PAIRS[0]])
    for p in PAIRS[1:]:
        common &= set(bars[p])
    ts = np.array(sorted(common), dtype=np.int64)
    n, m = len(ts), len(PAIRS)
    bid_o = np.empty((n, m)); ask_o = np.empty((n, m))
    bid_c = np.empty((n, m)); ask_c = np.empty((n, m))
    for j, p in enumerate(PAIRS):
        d = bars[p]
        for i, t in enumerate(ts):
            bo, ao, bc, ac = d[int(t)]
            bid_o[i, j], ask_o[i, j], bid_c[i, j], ask_c[i, j] = bo, ao, bc, ac
    mid_c = (bid_c + ask_c) / 2.0
    return ts, bid_o, ask_o, bid_c, ask_c, mid_c

# incidence: strength = INC @ r / 4, INC[c, pair] = +1 base, -1 quote
INC = np.zeros((len(CUR), len(PAIRS)))
for j in range(len(PAIRS)):
    INC[CUR.index(BASE[j]), j] = +1.0
    INC[CUR.index(QUOT[j]), j] = -1.0

def run(tf_sec, tf_name, lookbacks, holds):
    ts, bid_o, ask_o, bid_c, ask_c, mid_c = build_arrays(tf_sec)
    n = len(ts)
    dt_ok = np.zeros(n, dtype=bool)          # dt_ok[i]: bar i follows i-1 by tf
    dt_ok[1:] = (np.diff(ts) == tf_sec)
    # contiguous run length ending at i
    runlen = np.zeros(n, dtype=int)
    for i in range(n):
        runlen[i] = runlen[i-1] + 1 if (i > 0 and dt_ok[i]) else 1
    logmid = np.log(mid_c)
    years = (ts // 31556952)  # not used; year computed below properly
    import datetime
    yr = np.array([datetime.datetime.utcfromtimestamp(int(t)).year for t in ts])
    results = []
    for L in lookbacks:
        if L >= n: continue
        r = logmid[L:] - logmid[:-L]                    # (n-L, pairs)
        strength = r @ INC.T / 4.0                      # (n-L, currencies)
        dec_i = np.arange(L, n)                         # decision bar index
        # need lookback contiguity: runlen at decision >= L ... window bars
        # decision-L+1..decision must be contiguous => runlen[dec] >= L
        ok = runlen[dec_i] >= L + 1  # bars dec-L..dec all consecutive
        s_max = np.argmax(strength, axis=1); s_min = np.argmin(strength, axis=1)
        spread_score = strength[np.arange(len(dec_i)), s_max] - strength[np.arange(len(dec_i)), s_min]
        for H in holds:
            exi_i = dec_i + H
            # holding contiguity: bars dec..dec+H all consecutive
            # (runlen at exit >= H+1) -- drops weekend-spanning trades
            valid = ok & (exi_i < n) & (ts[dec_i] >= T_EVAL) & (runlen[np.minimum(exi_i, n-1)] >= H + 1)
            idx = np.where(valid)[0]
            if len(idx) == 0: continue
            rets_mom = np.empty(len(idx)); rets_con = np.empty(len(idx))
            gross = np.empty(len(idx))
            pair_used = np.empty(len(idx), dtype=int)
            for k, ii in enumerate(idx):
                i = dec_i[ii]
                strong = CUR[s_max[ii]]; weak = CUR[s_min[ii]]
                pj, orient = PAIR_IDX[(strong, weak)]  # orient +1: strong is base
                pair_used[k] = pj
                e, x = i + 1, i + H
                if orient == +1:   # momentum: long the pair
                    rets_mom[k] = bid_c[x, pj] / ask_o[e, pj] - 1.0
                    rets_con[k] = (bid_o[e, pj] - ask_c[x, pj]) / bid_o[e, pj]
                else:              # momentum: short the pair
                    rets_mom[k] = (bid_o[e, pj] - ask_c[x, pj]) / bid_o[e, pj]
                    rets_con[k] = bid_c[x, pj] / ask_o[e, pj] - 1.0
                mid_o = (bid_o[e, pj] + ask_o[e, pj]) / 2.0
                gross[k] = orient * (mid_c[x, pj] / mid_o - 1.0)
            shares = np.bincount(pair_used, minlength=len(PAIRS)) / len(pair_used)
            for name, rets in (("momentum", rets_mom), ("contrarian", rets_con)):
                mu = rets.mean(); sd = rets.std(ddof=1)
                tstat = mu / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0
                results.append(dict(tf=tf_name, L=L, H=H, direction=name,
                                    n=int(len(rets)), mean_pct=float(mu*100),
                                    median_pct=float(np.median(rets)*100),
                                    win_pct=float((rets > 0).mean()*100),
                                    sum_pct=float(rets.sum()*100), t_naive=float(tstat),
                                    pair_share_max=float(shares.max()),
                                    gross_mean_pct=float(gross.mean()*100) if name == "momentum" else float(-gross.mean()*100)))
            # gap-quintile buckets (descriptive): does a bigger
            # strongest-weakest gap predict better forward returns?
            gaps = spread_score[idx]
            if len(idx) > 500:
                edges = np.quantile(gaps, [0.2, 0.4, 0.6, 0.8])
                bkt = np.digitize(gaps, edges)
                for q in range(5):
                    m_ = bkt == q
                    if m_.sum() > 30:
                        results.append(dict(tf=tf_name, L=L, H=H, direction=f"momentum_gapq{q+1}",
                                            n=int(m_.sum()), mean_pct=float(rets_mom[m_].mean()*100),
                                            median_pct=float(np.median(rets_mom[m_])*100),
                                            win_pct=float((rets_mom[m_] > 0).mean()*100),
                                            sum_pct=float(rets_mom[m_].sum()*100),
                                            t_naive=float(rets_mom[m_].mean() / (rets_mom[m_].std(ddof=1)/math.sqrt(m_.sum()))),
                                            gross_mean_pct=float(gross[m_].mean()*100)))
            # per-year for momentum only, stored separately
            yv = yr[dec_i[idx]]
            for y in (2024, 2025, 2026):
                m_ = yv == y
                if m_.sum() > 30:
                    results.append(dict(tf=tf_name, L=L, H=H, direction=f"momentum_{y}",
                                        n=int(m_.sum()), mean_pct=float(rets_mom[m_].mean()*100),
                                        median_pct=float(np.median(rets_mom[m_])*100),
                                        win_pct=float((rets_mom[m_] > 0).mean()*100),
                                        sum_pct=float(rets_mom[m_].sum()*100),
                                        t_naive=float(rets_mom[m_].mean() / (rets_mom[m_].std(ddof=1)/math.sqrt(m_.sum()))),
                                        gross_mean_pct=float(gross[m_].mean()*100)))
    return results

all_res = []
all_res += run(900, "15m", [4, 16, 32, 96], [1, 4, 8])
all_res += run(3600, "1h", [4, 12, 24, 72], [1, 4, 8])
print(json.dumps(all_res, indent=1))
with open("/home/hatch/workspace/SI-Trader/outputs/currency_strength_firstpass.json", "w") as f:
    json.dump(all_res, f, indent=1)
# compact table of main rows
print("\n=== main (momentum / contrarian) ===")
print(f"{'tf':>4} {'L':>4} {'H':>3} {'dir':>11} {'n':>7} {'mean%':>8} {'med%':>8} {'win%':>6} {'t':>7} {'gross%':>8}")
for r in all_res:
    if "_" in r["direction"] and r["direction"] not in ("momentum", "contrarian"):
        continue
    print(f"{r['tf']:>4} {r['L']:>4} {r['H']:>3} {r['direction']:>11} {r['n']:>7} {r['mean_pct']:>8.4f} {r['median_pct']:>8.4f} {r['win_pct']:>6.1f} {r['t_naive']:>7.2f} {r['gross_mean_pct']:>8.4f}")
print("\n=== momentum by year ===")
for r in all_res:
    if r["direction"].startswith("momentum_"):
        print(f"{r['tf']:>4} L={r['L']:>3} H={r['H']:>2} {r['direction']} n={r['n']:>6} mean%={r['mean_pct']:>8.4f} win%={r['win_pct']:>5.1f} t={r['t_naive']:>6.2f}")
