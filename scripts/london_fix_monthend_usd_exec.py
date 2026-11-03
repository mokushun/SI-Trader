"""
London Fix 第3パス: 月末日 16:00 London 直後に USD を買うトレードの正確な exec

第1/2パスで、月末日の T->T+30 は 5つの USD ペア全部で USD 高方向に揃い、
USD 合成（bp）で +2.74bp / t=2.70 だった。ここでは個別ペアで
「T で USD を買い T+30 で手仕舞い」を bid/ask の実価格で測る。
月末 = ロンドン日付（月〜金）の月内最終日。
"""

import os
import sqlite3
from math import sqrt

import pandas as pd

DB = os.environ.get("M1_DB_PATH", os.path.expanduser("~/workspace/oanda_json/m1.db"))
PAIRS = {"USD_JPY": (100.0, +1), "USD_CHF": (10000.0, +1), "GBP_USD": (10000.0, -1),
         "EUR_USD": (10000.0, -1), "AUD_USD": (10000.0, -1)}


def pf(s):
    s = pd.Series(s, dtype=float)
    p, n = s[s > 0].sum(), -s[s < 0].sum()
    return p / n if n > 0 else float("nan")


def line(name, s):
    s = pd.Series(s, dtype=float).dropna()
    if len(s) < 3:
        print(f"{name}: n={len(s)}")
        return
    t = s.mean() / (s.std(ddof=1) / sqrt(len(s)))
    print(f"{name}: n={len(s)} mean={s.mean():+.2f}pip PF={pf(s):.3f} t={t:+.2f}")


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for inst, (pip, sgn) in PAIRS.items():
        df = pd.read_sql_query(f"SELECT t,bo,ao FROM m1 WHERE inst='{inst}' ORDER BY t", con)
        price = {int(t): (bo, ao) for t, bo, ao in zip(df.t, df.bo, df.ao)}
        lon = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert("Europe/London")
        dates = sorted(d for d in set(lon.dt.date) if pd.Timestamp(d).dayofweek <= 4)
        sdf = pd.DataFrame({"d": dates})
        sdf["ym"] = sdf["d"].apply(lambda d: (d.year, d.month))
        last = sdf.groupby("ym")["d"].transform("max")
        me = dict(zip(sdf["d"], (sdf["d"] == last).values))
        rows = []
        for d in dates:
            if not me.get(d):
                continue
            T = int((pd.Timestamp(d) + pd.Timedelta(hours=16)).tz_localize("Europe/London").timestamp())
            p0, p30 = price.get(T), price.get(T + 1800)
            if p0 is None or p30 is None:
                continue
            if sgn > 0:
                ex = (p30[0] - p0[1]) * pip
                mid = ((p30[0] + p30[1]) - (p0[0] + p0[1])) / 2 * pip
            else:
                ex = (p0[0] - p30[1]) * pip
                mid = -((p30[0] + p30[1]) - (p0[0] + p0[1])) / 2 * pip
            rows.append(dict(date=d, year=d.year, ex=ex, mid=mid))
        R = pd.DataFrame(rows)
        print(f"=== {inst} month-end USD-buy T->T+30 ===")
        line("mid", R.mid)
        line("exec", R.ex)
        line("exec <=2023", R.loc[R.year <= 2023, "ex"])
        line("exec 2024+", R.loc[R.year >= 2024, "ex"])
    con.close()
    print("DONE")


if __name__ == "__main__":
    main()
