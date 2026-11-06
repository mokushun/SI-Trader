#!/usr/bin/env python3
"""Thursday swap-point hypothesis test (v2, rollover-relative buckets).

User hypothesis: on Thursdays, swap-seeking longs buy into the rollover
(3-day swap grant at NY Wed 17:00 = 06:00 JST in US DST / 07:00 JST in
winter), lifting prices toward the grant; after the grant they close,
pressuring prices into 09:55. Predictions:
  - Thursday: R-35..R-5 positive, R+5..R+35 / R+35..R+65 negative.
  - Control days (Tue/Wed/Fri, 1-day grant): weaker or no such pattern.
OANDA feed has no bars at the rollover minute itself (daily break), so
marks use R-65/-35/-5/+5/+35/+65; each bucket is computed on the days
where both endpoint bars exist. Prices: mid open of the minute bar.
"""
import sqlite3, math, os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

JST = timezone(timedelta(hours=9))
NY = ZoneInfo("America/New_York")
DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CHF_JPY"]
PIP = 0.01
OFFSETS = [-65, -35, -5, 5, 35, 65]
BUCKETS = [("R-65..R-35", -65, -35), ("R-35..R-5", -35, -5), ("R-5..R+5", -5, 5),
           ("R+5..R+35", 5, 35), ("R+35..R+65", 35, 65)]


def mean_t(vals):
    n = len(vals)
    if n == 0:
        return (0, float("nan"), float("nan"))
    m = sum(vals) / n
    if n > 1:
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
        t = m / (sd / math.sqrt(n)) if sd > 0 else 0.0
    else:
        t = 0.0
    return (n, m, t)


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    start = datetime(2015, 1, 1, tzinfo=JST).date()
    end = datetime(2026, 11, 5, tzinfo=JST).date()
    days = []
    d = start
    while d <= end:
        if d.weekday() in (1, 2, 3, 4):
            days.append(d)
        d += timedelta(days=1)

    def rollover(d):
        wed = d - timedelta(days=1)
        return datetime(wed.year, wed.month, wed.day, 17, 0, tzinfo=NY).astimezone(JST)

    need = set()
    for d in days:
        r = rollover(d)
        for off in OFFSETS:
            need.add(int((r + timedelta(minutes=off)).timestamp()))
        need.add(int(datetime(d.year, d.month, d.day, 9, 55, tzinfo=JST).timestamp()))
        need.add(int(datetime(d.year, d.month, d.day, 0, 0, tzinfo=JST).timestamp()))
    need = sorted(need)
    print(f"days={len(days)} bars/pair={len(need)}")

    for pair in PAIRS:
        rows = {}
        # chunk the IN query to stay under variable limits
        for i in range(0, len(need), 400):
            chunk = need[i:i + 400]
            q = f"SELECT t, bo, ao FROM m1 WHERE inst=? AND t IN ({','.join('?' * len(chunk))})"
            for t, bo, ao in cur.execute(q, [pair] + chunk):
                rows[t] = (bo + ao) / 2.0
        data = {era: {g: {} for g in ("THU", "CTL")} for era in ("FULL", "POST")}
        for d in days:
            r = rollover(d)
            marks = {}
            for off in OFFSETS:
                tt = int((r + timedelta(minutes=off)).timestamp())
                if tt in rows:
                    marks[off] = rows[tt]
            t955 = int(datetime(d.year, d.month, d.day, 9, 55, tzinfo=JST).timestamp())
            g = "THU" if d.weekday() == 3 else "CTL"
            eras = ["FULL"] + (["POST"] if d >= datetime(2020, 10, 1, tzinfo=JST).date() else [])
            for name, a, b in BUCKETS + [("R+65..09:55", 65, None)]:
                if a in marks and (b in marks if b is not None else t955 in rows):
                    va = marks[a]
                    vb = marks[b] if b is not None else rows[t955]
                    chg = (vb - va) / PIP
                    for era in eras:
                        data[era][g].setdefault(name, []).append(chg)
        print(f"\n== {pair} ==")
        for era in ("POST", "FULL"):
            print(f" [{era}]")
            for name, _, _ in BUCKETS + [("R+65..09:55", 65, None)]:
                cells = []
                for g in ("THU", "CTL"):
                    n, m, t = mean_t(data[era][g].get(name, []))
                    cells.append(f"{g}: {m:+6.2f} (t={t:+.1f}, n={n})" if n else f"{g}: -")
                print(f"  {name:12s} " + " | ".join(cells))
    con.close()


if __name__ == "__main__":
    main()
