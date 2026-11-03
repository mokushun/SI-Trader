#!/usr/bin/env python3
"""
Currency strength -- same-date/same-time match filter (fixed definition).

Question (user, 2026-11-02): on 1h bars, trade the strongest/weakest pair
ONLY when the same strongest/weakest combination occurred at the same
calendar date and hour in past years.

Definition:
- Strength identical to currency_strength_firstpass.py: mean log return
  of mid close over L bars vs the currency's other 4 pairs, 5 currencies
  USD/JPY/EUR/GBP/AUD, 10 pairs, m1.db, 1h bars UTC-aligned (JST hour
  alignment is identical, JST = UTC+9 whole hours).
- "Same date/time": same month-day and hour in JST calendar, 1..5 years
  ago. A past year is AVAILABLE if a bar exists at that exact timestamp
  and its strength was computable (contiguous lookback); weekend/holiday
  years are unavailable, not counted as non-matches.
- Base population: decision bars in 2021-01-01..2026-10-30 (JST) with
  >= 3 of the past 5 years available.
- Variant A (user's proposal): bucket by how many available past years
  had EXACTLY today's ordered (strongest, weakest) pair: 0 / 1 / 2 / 3+.
  Trade = today's pair, momentum direction (contrarian also computed).
- Variant B (secondary): if >= 3 available past years agree with EACH
  OTHER on one ordered pair (mode count >= 3), trade that historic pair
  in its momentum direction, regardless of today's pair.
- Entry next bar open (ask long / bid short), exit close of H-th bar
  (bid long / ask short): one spread paid. No swap/fees.
- Lookback/holding windows must not span gaps (contiguity as before).
- Grid (predeclared): L in {4, 12, 24}, H in {1, 4, 8}.
"""
import sqlite3, math, json, datetime, sys
import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "strict"   # strict | tolerant
# tolerant: strength lookback may span small in-week gaps (<=8h extra),
# only the weekend-scale span is excluded. Changes ONLY strength validity.

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

TF = 3600
T_START = 1420070400                       # 2015-01-01 UTC
T_EVAL = int(datetime.datetime(2021, 1, 1, tzinfo=datetime.timezone.utc).timestamp()) - 9 * 3600
T_END = 1793400000                         # > 2026-10-30
JST = datetime.timezone(datetime.timedelta(hours=9))

def load_bars():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    for p in PAIRS:
        first = {}
        for b, bo, ao, cnt, _ in con.execute(
                "SELECT (t/?)*? AS b, bo, ao, count(*), min(t) FROM m1 "
                "WHERE inst=? AND t>=? AND t<? GROUP BY b", (TF, TF, p, T_START, T_END)):
            first[b] = (bo, ao, cnt)
        last = {}
        for b, bc, ac, _ in con.execute(
                "SELECT (t/?)*? AS b, bc, ac, max(t) FROM m1 "
                "WHERE inst=? AND t>=? AND t<? GROUP BY b", (TF, TF, p, T_START, T_END)):
            last[b] = (bc, ac)
        d = {}
        for b, (bo, ao, cnt) in first.items():
            if cnt < 36 or b not in last:
                continue
            bc, ac = last[b]
            d[b] = (bo, ao, bc, ac)
        out[p] = d
    con.close()
    return out

bars = load_bars()
common = set(bars[PAIRS[0]])
for p in PAIRS[1:]:
    common &= set(bars[p])
ts = np.array(sorted(common), dtype=np.int64)
n = len(ts)
ts_index = {int(t): i for i, t in enumerate(ts)}
bid_o = np.empty((n, len(PAIRS))); ask_o = np.empty((n, len(PAIRS)))
bid_c = np.empty((n, len(PAIRS))); ask_c = np.empty((n, len(PAIRS)))
for j, p in enumerate(PAIRS):
    d = bars[p]
    for i, t in enumerate(ts):
        bo, ao, bc, ac = d[int(t)]
        bid_o[i, j], ask_o[i, j], bid_c[i, j], ask_c[i, j] = bo, ao, bc, ac
mid_c = (bid_c + ask_c) / 2.0
mid_o = (bid_o + ask_o) / 2.0
logmid = np.log(mid_c)
runlen = np.zeros(n, dtype=int)
for i in range(n):
    runlen[i] = runlen[i - 1] + 1 if (i > 0 and ts[i] - ts[i - 1] == TF) else 1

def trade_ret(i_dec, strong_cur, weak_cur, H, momentum=True):
    """PnL of strongest/weakest trade decided at bar i_dec; None if invalid."""
    pj, orient = PAIR_IDX[(strong_cur, weak_cur)]
    if not momentum:
        orient = -orient
    e, x = i_dec + 1, i_dec + H
    if x >= n or runlen[x] < H + 1:
        return None
    if orient == +1:
        net = bid_c[x, pj] / ask_o[e, pj] - 1.0
    else:
        net = (bid_o[e, pj] - ask_c[x, pj]) / bid_o[e, pj]
    gross = orient * (mid_c[x, pj] / mid_o[e, pj] - 1.0)
    return net, gross

def stats(rets):
    rets = np.array(rets)
    if len(rets) == 0:
        return None
    mu, sd = rets[:, 0].mean(), rets[:, 0].std(ddof=1) if len(rets) > 1 else 0.0
    return dict(n=int(len(rets)), net_pct=float(mu * 100),
                median_pct=float(np.median(rets[:, 0]) * 100),
                win_pct=float((rets[:, 0] > 0).mean() * 100),
                gross_pct=float(rets[:, 1].mean() * 100),
                t_naive=float(mu / (sd / math.sqrt(len(rets)))) if sd > 0 else 0.0)

results = []
for L in (4, 12, 24):
    r = logmid[L:] - logmid[:-L]
    strength = r @ INC.T / 4.0
    code = np.full(n, -1, dtype=int)          # ordered pair code s*5+w
    dec0 = np.arange(L, n)
    if MODE == "tolerant":
        valid_strength = (ts[dec0] - ts[dec0 - L]) <= (L + 8) * TF
    else:
        valid_strength = runlen[dec0] >= L + 1
    smax = np.argmax(strength, axis=1); smin = np.argmin(strength, axis=1)
    code[dec0[valid_strength]] = smax[valid_strength] * 5 + smin[valid_strength]
    # precompute historic codes for eval decisions
    for H in (1, 4, 8):
        buckets = {0: [], 1: [], 2: [], 3: []}       # variant A momentum
        buckets_con = {0: [], 1: [], 2: [], 3: []}
        varB, varB_con = [], []
        varB_early, varB_late = [], []               # 2021-23 / 2024-26 split
        base_n = 0
        for i in range(n):
            if ts[i] < T_EVAL or code[i] < 0:
                continue
            dt_jst = datetime.datetime.fromtimestamp(int(ts[i]), tz=datetime.timezone.utc).astimezone(JST)
            hist = []
            for k in range(1, 6):
                try:
                    tgt = dt_jst.replace(year=dt_jst.year - k)
                except ValueError:                    # Feb 29
                    continue
                j = ts_index.get(int(tgt.timestamp()))
                if j is None or code[j] < 0:
                    continue
                hist.append(int(code[j]))
            if len(hist) < 3:
                continue
            base_n += 1
            matches = sum(1 for c in hist if c == int(code[i]))
            b = min(matches, 3)
            strong, weak = CUR[int(code[i]) // 5], CUR[int(code[i]) % 5]
            tr = trade_ret(i, strong, weak, H, True)
            if tr is not None:
                buckets[b].append(tr)
                buckets_con[b].append(trade_ret(i, strong, weak, H, False))
            # variant B: historic consensus
            vals, counts = np.unique(hist, return_counts=True)
            if counts.max() >= 3:
                hc = int(vals[counts.argmax()])
                hs, hw = CUR[hc // 5], CUR[hc % 5]
                trB = trade_ret(i, hs, hw, H, True)
                if trB is not None:
                    varB.append(trB); varB_con.append(trade_ret(i, hs, hw, H, False))
                    yr = datetime.datetime.fromtimestamp(int(ts[i]), tz=datetime.timezone.utc).year
                    (varB_early if yr <= 2023 else varB_late).append(trB)
        row = dict(L=L, H=H, base_population=base_n)
        for b in (0, 1, 2, 3):
            s = stats(buckets[b])
            if s: row[f"A_match{b if b<3 else '3plus'}_mom"] = s
            s = stats(buckets_con[b])
            if s: row[f"A_match{b if b<3 else '3plus'}_con"] = s
        s = stats(varB)
        if s: row["B_consensus_mom"] = s
        s = stats(varB_early)
        if s: row["B_2021_2023"] = s
        s = stats(varB_late)
        if s: row["B_2024_2026"] = s
        s = stats(varB_con)
        if s: row["B_consensus_con"] = s
        # pooled thresholds for A momentum
        for th, keys in ((1, (1, 2, 3)), (2, (2, 3)), (3, (3,))):
            pooled = [t for kk in keys for t in buckets[kk]]
            s = stats(pooled)
            if s: row[f"A_match>={th}_mom"] = s
        results.append(row)
        print(f"L={L} H={H} base={base_n} " +
              " ".join(f"m{b}={len(buckets[b])}" for b in (0, 1, 2, 3)) +
              f" varB={len(varB)}")

print(json.dumps(results, indent=1))
with open(f"/home/hatch/workspace/SI-Trader/outputs/currency_strength_samedate_{MODE}.json", "w") as f:
    json.dump(results, f, indent=1)
print("\n=== Variant A momentum by match count (net% / gross%) ===")
for row in results:
    for b in ("0", "1", "2", "3plus"):
        s = row.get(f"A_match{b}_mom")
        if s: print(f"L={row['L']:>2} H={row['H']} match{b:>5} n={s['n']:>6} net={s['net_pct']:>8.4f} gross={s['gross_pct']:>8.4f} win={s['win_pct']:.1f} t={s['t_naive']:.2f}")
print("\n=== Variant B consensus ===")
for row in results:
    s = row.get("B_consensus_mom")
    if s: print(f"L={row['L']:>2} H={row['H']} n={s['n']:>6} net={s['net_pct']:>8.4f} gross={s['gross_pct']:>8.4f} win={s['win_pct']:.1f} t={s['t_naive']:.2f}")
