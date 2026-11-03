#!/usr/bin/env python3
"""HAND2 monthly executed volume distribution, round-trip, USD terms (2026-11-03).

Question: with the user's hand-rounded VOLS (HAND2), does the nakane bot
alone reach OANDA's 500k USD/month condition when volume is counted
round-trip (entry + exit)?
Basis: entry-day bars (a pair-day counts when its 00:00 JST bar exists);
units = (base + 3000 on gotobi days) x 1.4; USD conversion at the day's
00:00 mid (XXX_JPY / USD_JPY). Round-trip = 2 x entry side.
The completed-trade basis used earlier gives a near-identical average
(547k vs 553k); the entry-day basis is used here because an entry that
fills still generates its exit volume even when the 09:55 bar is missing
from the DB on holiday-shortened days.
"""
import os
import sqlite3
import statistics
import datetime
from collections import defaultdict

DB = os.path.expanduser("~/workspace/oanda_json/m1.db")
JST = datetime.timezone(datetime.timedelta(hours=9))
PAIRS = ["USD_JPY", "GBP_JPY", "AUD_JPY", "EUR_JPY", "CHF_JPY"]
HAND2 = {"USD_JPY": [0, 0, 3000, 3500, 2000, 100, 0], "GBP_JPY": [0, 0, 3000, 4000, 3000, 100, 0],
         "AUD_JPY": [0, 0, 100, 1500, 100, 100, 0], "EUR_JPY": [0, 0, 1500, 1500, 2000, 100, 0],
         "CHF_JPY": [0, 0, 100, 1000, 100, 3000, 0]}
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
entry_px = {}
dates = set()
for inst in PAIRS:
    for t, ac, bc in con.execute(
            "SELECT t,ac,bc FROM m1 WHERE inst=? AND CAST(((t/60+540)%1440) AS INT)=0 ORDER BY t",
            (inst,)):
        d = datetime.datetime.fromtimestamp(t, JST).date()
        entry_px[(inst, d)] = (ac + bc) / 2
        dates.add(d)
con.close()
usdpx = {d: entry_px[("USD_JPY", d)] for d in dates if ("USD_JPY", d) in entry_px}


def units(pair, d):
    base = HAND2[pair][(d.weekday() + 1) % 7]
    return (base + (3000 if d.day % 5 == 0 else 0)) * 1.4 if base > 0 else 0.0


for label, lo, hi in (("FULL", datetime.date(2015, 1, 1), datetime.date(2026, 10, 31)),
                      ("POST", datetime.date(2020, 10, 1), datetime.date(2026, 10, 31)),
                      ("RECENT 2024+", datetime.date(2024, 1, 1), datetime.date(2026, 10, 31))):
    vol = defaultdict(float)
    for d in sorted(dates):
        if not (lo <= d < hi) or d not in usdpx:
            continue
        for p in PAIRS:
            if (p, d) in entry_px:
                conv = 1.0 if p == "USD_JPY" else entry_px[(p, d)] / usdpx[d]
                vol[(d.year, d.month)] += 2 * units(p, d) * conv
    vals = sorted(vol.values())
    ok = sum(1 for v in vals if v >= 500000)
    print(f"{label}: months={len(vals)} avg={statistics.mean(vals):,.0f} median={statistics.median(vals):,.0f} "
          f"min={min(vals):,.0f} max={max(vals):,.0f} >=500k: {ok}/{len(vals)} ({ok / len(vals) * 100:.0f}%)")
    print("  below 500k:", [(k, round(v)) for k, v in sorted(vol.items()) if v < 500000])
