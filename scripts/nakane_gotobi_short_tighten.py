#!/usr/bin/env python3
"""Tightening the gotobi-only post-fixing USD_JPY short (2026-11-05).

Candidate found 2026-11-03 (scripts/nakane_usdjpy_pre2015.py Part 4):
gotobi-only (day%5==0), Tue/Wed/Fri, sell bid-open 09:55 -> buy ask-open
10:30, executable, pooled 2005-2026 +1.79pip PF 1.603 t=4.27 n=651.
This script does NOT search for a new strategy. It stress-tests that one
pre-stated definition on the questions a live trial would hinge on:

  1. Replication of the headline numbers (sanity).
  2. Entry delay: sell at 09:56..10:10 instead of 09:55 (exit 10:30 fixed).
     Proxies the fill risk at the fixing minute.
  3. Exit curve: buy back at 10:00..12:00 (entry 09:55 fixed). Is 10:30 a
     plateau or a single-point spike (post-hoc selection check)?
  4. Weekday scope: Mon and Thu were excluded only by inheritance from the
     long nakane trade (Mon 00:00 entry problem / Thu 05:35 long exit).
     The Tokyo fixing happens on Mon and Thu too; measure them separately.
  5. Other pairs 2015-2026 (main m1.db only): was USD-only consistency real?
  6. Slippage sensitivity: subtract an extra 0..2pip per trade and recompute.
  7. Distribution / risk: yearly means, worst trades, cumulative max DD,
     longest losing streak.
  8. Mechanism check (descriptive only): split by the morning's mid move
     00:00->09:55. A fixing-reversal should be larger after larger rises.

Data identical to nakane_usdjpy_pre2015.py: old DB (~/workspace/duka_hist/
m1_usdjpy.db, same OANDA feed) for <2015, main m1.db for >=2015, dedupe by
date. Prices are M1 bar OPEN (ao/bo), as in all nakane scripts here.
Weekday numbering: Mon=0 .. Fri=4 (datetime.date.weekday()).
"""
import os
import sqlite3
import statistics
import datetime

OLD = os.path.expanduser("~/workspace/duka_hist/m1_usdjpy.db")
MAIN = os.path.expanduser("~/workspace/oanda_json/m1.db")
JST = datetime.timezone(datetime.timedelta(hours=9))

# JST minutes of day we need: 00:00 (morning-move base), the whole
# 09:55..12:00 window (entry delays + exit curve).
WANT = {0} | set(range(595, 721))


def stats(rets):
    if not rets:
        return "n=0"
    n = len(rets)
    mean = statistics.mean(rets)
    gp = sum(r for r in rets if r > 0)
    gl = -sum(r for r in rets if r < 0)
    pf = gp / gl if gl > 0 else float("inf")
    sd = statistics.pstdev(rets) if n > 1 else 0.0
    t = mean / (sd / (n ** 0.5)) if sd > 0 else 0.0
    win = sum(1 for r in rets if r > 0) / n * 100
    return f"n={n:4d} mean={mean:+.2f}pip PF={pf:.3f} t={t:+.2f} win={win:.0f}%"


def load(db, inst="USD_JPY", want=WANT):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    data = {}
    wsql = ",".join(str(m) for m in sorted(want))
    for t, ao, bo in con.execute(
            f"SELECT t,ao,bo FROM m1 WHERE inst='{inst}' "
            f"AND CAST(((t/60+540)%1440) AS INT) IN ({wsql}) ORDER BY t"):
        dt = datetime.datetime.fromtimestamp(t, JST)
        data.setdefault(dt.date(), {})[dt.hour * 60 + dt.minute] = (ao, bo)
    con.close()
    return data


DATA = load(OLD)
for d, mins in load(MAIN).items():
    if d >= datetime.date(2015, 1, 1):
        DATA.setdefault(d, {}).update(mins)

GOTOBI = lambda d: d.day % 5 == 0
TWF = (1, 2, 4)
ALLWD = (0, 1, 2, 3, 4)
HALVES = [("2005-14", datetime.date(2005, 1, 1), datetime.date(2015, 1, 1)),
          ("2015-26", datetime.date(2015, 1, 1), datetime.date(2026, 10, 31))]


def short_rets(entry_m, exit_m, weekdays=TWF, lo=None, hi=None, cond=GOTOBI, data=None):
    """Sell at bid-open of entry_m, buy back at ask-open of exit_m. pip list
    in date order."""
    out = []
    for d in sorted((data or DATA).keys()):
        mins = (data or DATA)[d]
        if d.weekday() not in weekdays:
            continue
        if lo and not (lo <= d < hi):
            continue
        if cond and not cond(d):
            continue
        if entry_m in mins and exit_m in mins:
            out.append(((d), (mins[entry_m][1] - mins[exit_m][0]) * 100))
    return out


def just(rets):
    return [r for _, r in rets]


print("=== 1. Replication: gotobi, Tue/Wed/Fri, sell 09:55 -> buy 10:30 ===")
base = short_rets(595, 630)
print(f"  pooled 2005-26: {stats(just(base))}   (known: n=651 +1.79 PF1.603 t=4.27)")
for lab, lo, hi in HALVES:
    print(f"  {lab}: {stats(just(short_rets(595, 630, lo=lo, hi=hi)))}")
print(f"  non-gotobi control: {stats(just(short_rets(595, 630, cond=lambda d: d.day % 5 != 0)))}")
print(f"  all-days:           {stats(just(short_rets(595, 630, cond=None)))}")

print()
print("=== 2. Entry delay (gotobi, Tue/Wed/Fri, exit 10:30 fixed), pooled ===")
for em in (595, 596, 597, 598, 599, 600, 601, 602, 603, 604, 605, 610):
    hh, mm = divmod(em, 60)
    print(f"  entry {hh:02d}:{mm:02d}: {stats(just(short_rets(em, 630)))}")
print("  -- by half (entry 09:55 / 09:57 / 10:00 / 10:05)")
for em in (595, 597, 600, 605):
    cells = [f"{lab} {stats(just(short_rets(em, 630, lo=lo, hi=hi)))}" for lab, lo, hi in HALVES]
    print(f"  entry {em}: " + " | ".join(cells))

print()
print("=== 3. Exit curve (gotobi, Tue/Wed/Fri, entry 09:55 fixed), pooled ===")
for xm in (600, 605, 610, 615, 620, 625, 630, 635, 645, 660, 690, 720):
    hh, mm = divmod(xm, 60)
    print(f"  exit {hh:02d}:{mm:02d}: {stats(just(short_rets(595, xm)))}")

print()
print("=== 4. Weekday scope (gotobi, 09:55 -> 10:30), pooled 2005-26 ===")
for wd, lab in ((0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri")):
    print(f"  {lab}: {stats(just(short_rets(595, 630, weekdays=(wd,))))}")
print(f"  Tue/Wed/Fri (base): {stats(just(base))}")
print(f"  Mon-Fri all:        {stats(just(short_rets(595, 630, weekdays=ALLWD)))}")
print(f"  Tue-Fri (add Thu):  {stats(just(short_rets(595, 630, weekdays=(1, 2, 3, 4))))}")
print("  -- Mon and Thu by half")
for wd, lab in ((0, "Mon"), (3, "Thu")):
    cells = [f"{hlab} {stats(just(short_rets(595, 630, weekdays=(wd,), lo=lo, hi=hi)))}" for hlab, lo, hi in HALVES]
    print(f"  {lab}: " + " | ".join(cells))

print()
print("=== 5. Other pairs, main m1.db 2015-2026, gotobi Tue/Wed/Fri 09:55->10:30 ===")
for inst in ("USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "CHF_JPY"):
    pdata = load(MAIN, inst=inst, want={595, 630})
    for lab, lo, hi in (("2015-20.9", datetime.date(2015, 1, 1), datetime.date(2020, 10, 1)),
                        ("2020.10-26", datetime.date(2020, 10, 1), datetime.date(2026, 10, 31)),
                        ("2015-26", datetime.date(2015, 1, 1), datetime.date(2026, 10, 31))):
        r = short_rets(595, 630, lo=lo, hi=hi, data=pdata)
        print(f"  {inst} {lab}: {stats(just(r))}")

print()
print("=== 6. Slippage sensitivity (base trades, extra cost subtracted per trade) ===")
br = just(base)
for extra in (0.0, 0.25, 0.5, 1.0, 1.5, 1.79, 2.0):
    print(f"  extra {extra:.2f}pip: {stats([r - extra for r in br])}")

print()
print("=== 7. Distribution / risk (base trades, date order) ===")
cum, peak, maxdd = 0.0, 0.0, 0.0
streak = longest = 0
for _, r in base:
    cum += r
    peak = max(peak, cum)
    maxdd = min(maxdd, cum - peak)
    streak = streak + 1 if r < 0 else 0
    longest = max(longest, streak)
srt = sorted(br)
print(f"  total {cum:+.1f}pip over {len(br)} trades ({len(br)/21.8:.1f}/yr), "
      f"maxDD(cum) {maxdd:.1f}pip, longest losing streak {longest}")
print(f"  worst5: {[round(x,1) for x in srt[:5]]}  best5: {[round(x,1) for x in srt[-5:][::-1]]}")
print(f"  percentiles p10={srt[len(srt)//10]:+.1f} median={statistics.median(br):+.1f} p90={srt[9*len(srt)//10]:+.1f}")
print("  yearly:")
by_year = {}
for d, r in base:
    by_year.setdefault(d.year, []).append(r)
for y in sorted(by_year):
    print(f"    {y}: {stats(by_year[y])}")
neg_years = [y for y in sorted(by_year) if statistics.mean(by_year[y]) < 0]
print(f"  negative years: {neg_years}")

print()
print("=== 8. Mechanism check: by morning mid move 00:00->09:55 (descriptive) ===")
buckets = [("morning <=0", lambda m: m <= 0), ("0< m <=10", lambda m: 0 < m <= 10),
           ("10< m <=25", lambda m: 10 < m <= 25), ("m >25", lambda m: m > 25)]
morning = {}
for d, mins in DATA.items():
    if 0 in mins and 595 in mins:
        morning[d] = ((mins[595][0] + mins[595][1]) / 2 - (mins[0][0] + mins[0][1]) / 2) * 100
for lab, f in buckets:
    sel = [r for d, r in base if d in morning and f(morning[d])]
    print(f"  {lab}: {stats(sel)}")
nonsel = [r for d, r in short_rets(595, 630, cond=lambda d: d.day % 5 != 0) if d in morning]
for lab, f in buckets:
    sel = [r for d, r in short_rets(595, 630, cond=lambda d: d.day % 5 != 0)
           if d in morning and f(morning[d])]
    print(f"  non-gotobi {lab}: {stats(sel)}")

print()
print("=== 9. Entry-minute spread on gotobi days (09:55 bar, Tue/Wed/Fri) ===")
sp = [(mins[595][0] - mins[595][1]) * 100 for d, mins in DATA.items()
      if d.weekday() in TWF and GOTOBI(d) and 595 in mins]
spn = [(mins[595][0] - mins[595][1]) * 100 for d, mins in DATA.items()
       if d.weekday() in TWF and not GOTOBI(d) and 595 in mins]
print(f"  gotobi:     n={len(sp)} median spread {statistics.median(sp):.2f}pip mean {statistics.mean(sp):.2f}")
print(f"  non-gotobi: n={len(spn)} median spread {statistics.median(spn):.2f}pip mean {statistics.mean(spn):.2f}")

print()
print("=== 10. Pre-entry (gotobi, Tue/Wed/Fri, exit 10:30): sell BEFORE the print ===")
PRE = load(OLD, want=set(range(588, 631)))
for d, mins in load(MAIN, want=set(range(588, 631))).items():
    if d >= datetime.date(2015, 1, 1):
        PRE.setdefault(d, {}).update(mins)
for em in range(588, 596):
    hh, mm = divmod(em, 60)
    print(f"  entry {hh:02d}:{mm:02d}: {stats(just(short_rets(em, 630, data=PRE)))}")
lm = [((mins[595][0] + mins[595][1]) / 2 - (mins[594][0] + mins[594][1]) / 2) * 100
      for d, mins in PRE.items()
      if d.weekday() in TWF and GOTOBI(d) and 594 in mins and 595 in mins]
n = len(lm)
m = statistics.mean(lm)
sd = statistics.pstdev(lm)
print(f"  last-minute mid move 09:54->09:55 open on gotobi days: n={n} mean={m:+.2f}pip t={m/(sd/n**0.5):+.2f}")
