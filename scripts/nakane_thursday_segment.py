#!/usr/bin/env python3
"""Thursday 05:35 -> 09:55 segment measurement (nakane long, held position).

Question: if the Thursday exit moved from 05:35 to 09:55 JST, what does the
extra segment earn/ Lose, per pair? Executable basis: entry ask-open at
Thu 00:00 JST; exits bid-open at 05:35 / 09:55 JST. Segment = exitB - exitA.
Source: ~/workspace/oanda_json/m1.db (OANDA feed M1, t = epoch sec UTC).
"""
import sqlite3, math
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
import os
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CHF_JPY"]
PIP = 0.01


def epoch_jst(d, hh, mm):
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=JST).timestamp())


def stats(vals):
    n = len(vals)
    if n == 0:
        return None
    mean = sum(vals) / n
    pos = sum(v for v in vals if v > 0)
    neg = -sum(v for v in vals if v < 0)
    pf = (pos / neg) if neg > 0 else float("inf")
    if n > 1:
        var = sum((v - mean) ** 2 for v in vals) / (n - 1)
        sd = math.sqrt(var)
        t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t = 0.0
    return n, mean, pf, t


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    # all JST Thursdays in DB range: iterate dates 2015-01-01 .. 2026-11-05
    start = datetime(2015, 1, 1, tzinfo=JST).date()
    end = datetime(2026, 11, 5, tzinfo=JST).date()
    thursdays = []
    d = start
    while d <= end:
        if d.weekday() == 3:  # Thursday
            thursdays.append(d)
        d += timedelta(days=1)
    ts_needed = set()
    for d in thursdays:
        ts_needed.add(epoch_jst(d, 0, 0))
        ts_needed.add(epoch_jst(d, 5, 35))
        ts_needed.add(epoch_jst(d, 9, 55))
    ts_list = sorted(ts_needed)
    print(f"Thursdays: {len(thursdays)}  bars needed: {len(ts_list)}")

    for pair in PAIRS:
        rows = {}
        q = f"SELECT t, bo, ao FROM m1 WHERE inst=? AND t IN ({','.join('?' * len(ts_list))})"
        for t, bo, ao in cur.execute(q, [pair] + ts_list):
            rows[t] = (bo, ao)
        segs = {"FULL": [], "PRE": [], "POST": [], "GOTOBI": [], "NONGOTOBI": [],
                "POST_GOTOBI": [], "POST_NONGOTOBI": []}
        full_trade = {"CUR": [], "EXT": []}  # 00:00->05:35 vs 00:00->09:55, FULL
        for d in thursdays:
            t0, t1, t2 = epoch_jst(d, 0, 0), epoch_jst(d, 5, 35), epoch_jst(d, 9, 55)
            if t0 not in rows or t1 not in rows or t2 not in rows:
                continue
            ask_in = rows[t0][1]
            bid_a = rows[t1][0]
            bid_b = rows[t2][0]
            seg = (bid_b - bid_a) / PIP
            era = "PRE" if d < datetime(2020, 10, 1, tzinfo=JST).date() else "POST"
            gotobi = (d.day % 5 == 0)
            segs["FULL"].append(seg)
            segs[era].append(seg)
            segs["GOTOBI" if gotobi else "NONGOTOBI"].append(seg)
            if era == "POST":
                segs["POST_GOTOBI" if gotobi else "POST_NONGOTOBI"].append(seg)
            full_trade["CUR"].append((bid_a - ask_in) / PIP)
            full_trade["EXT"].append((bid_b - ask_in) / PIP)
        print(f"\n== {pair} ==")
        for k in ["FULL", "PRE", "POST", "GOTOBI", "NONGOTOBI", "POST_GOTOBI", "POST_NONGOTOBI"]:
            s = stats(segs[k])
            if s:
                print(f"  seg 05:35->09:55 {k:14s} n={s[0]:4d} mean={s[1]:+7.2f}pip PF={s[2]:.3f} t={s[3]:+.2f}")
        for k in ["CUR", "EXT"]:
            s = stats(full_trade[k])
            if s:
                print(f"  Thu trade {'00:00->05:35' if k=='CUR' else '00:00->09:55'} FULL n={s[0]:4d} mean={s[1]:+7.2f}pip PF={s[2]:.3f} t={s[3]:+.2f}")
    con.close()


if __name__ == "__main__":
    main()
